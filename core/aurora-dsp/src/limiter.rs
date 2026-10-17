//! 4× oversampled true-peak limiter, ceiling −1 dBTP.
//!
//! Stereo channels share one gain. Attack is instant inside the lookahead
//! window; release is about 40 ms at the oversampled rate, matching the
//! prototype. While the gain stays at unity the output is a pure delay of the
//! input, so material already under the ceiling is not resampled.
//! The lookahead is [`LOOKAHEAD`] samples and is part of `latency_samples`.

pub const LOOKAHEAD: usize = 32;
const OS: usize = 4;
const TAPS: usize = 8;

pub struct TruePeakLimiter {
    ceiling: f64,
    release: f64,
    gain: f64,
    hist_l: [f64; TAPS],
    hist_r: [f64; TAPS],
    hist_pos: usize,
    coeff: [[f64; TAPS]; OS],
    delay_l: [f64; LOOKAHEAD],
    delay_r: [f64; LOOKAHEAD],
    delay_pos: usize,
    dec_hist_l: [f64; TAPS * OS],
    dec_hist_r: [f64; TAPS * OS],
    dec_pos: usize,
    dec_coeff: [f64; TAPS * OS],
}

impl TruePeakLimiter {
    pub fn new(sample_rate: u32) -> Self {
        Self::with_ceiling(sample_rate, -1.0)
    }

    /// `ceiling_dbtp` is the detector ceiling. Route V passes a lower value
    /// because this 8-tap 4× filter reads about 0.7 dB under
    /// `scipy.signal.resample_poly`, which is how the prototype meters.
    pub fn with_ceiling(sample_rate: u32, ceiling_dbtp: f64) -> Self {
        let release = (f64::from(sample_rate) * OS as f64 * 40.0 / 1000.0).max(1.0);
        let mut coeff = [[0.0; TAPS]; OS];
        design_polyphase(&mut coeff);
        let mut dec_coeff = [0.0; TAPS * OS];
        design_decimator(&mut dec_coeff);
        Self {
            ceiling: 10.0_f64.powf(ceiling_dbtp / 20.0),
            release,
            gain: 1.0,
            hist_l: [0.0; TAPS],
            hist_r: [0.0; TAPS],
            hist_pos: 0,
            coeff,
            delay_l: [0.0; LOOKAHEAD],
            delay_r: [0.0; LOOKAHEAD],
            delay_pos: 0,
            dec_hist_l: [0.0; TAPS * OS],
            dec_hist_r: [0.0; TAPS * OS],
            dec_pos: 0,
            dec_coeff,
        }
    }

    pub fn latency(&self) -> usize {
        LOOKAHEAD
    }

    pub fn reset(&mut self) {
        self.gain = 1.0;
        self.hist_l = [0.0; TAPS];
        self.hist_r = [0.0; TAPS];
        self.hist_pos = 0;
        self.delay_l = [0.0; LOOKAHEAD];
        self.delay_r = [0.0; LOOKAHEAD];
        self.delay_pos = 0;
        self.dec_hist_l = [0.0; TAPS * OS];
        self.dec_hist_r = [0.0; TAPS * OS];
        self.dec_pos = 0;
    }

    pub fn process(&mut self, left: &[f64], right: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = left
            .len()
            .min(right.len())
            .min(out_l.len())
            .min(out_r.len());
        for i in 0..n {
            let xl = finite(left[i]);
            let xr = finite(right[i]);
            self.hist_l[self.hist_pos] = xl;
            self.hist_r[self.hist_pos] = xr;
            let mut hot = false;
            for phase in 0..OS {
                let (ul, ur) = self.upsample(phase);
                let peak = ul.abs().max(ur.abs());
                let target = if peak > self.ceiling {
                    self.ceiling / peak
                } else {
                    1.0
                };
                if target < self.gain {
                    self.gain = target;
                    hot = true;
                } else {
                    self.gain += (1.0 - self.gain) / self.release;
                    if self.gain > 1.0 {
                        self.gain = 1.0;
                    }
                }
                self.push_dec(ul * self.gain, ur * self.gain);
            }
            self.hist_pos = (self.hist_pos + 1) % TAPS;
            let dry_l = self.delay_l[self.delay_pos];
            let dry_r = self.delay_r[self.delay_pos];
            self.delay_l[self.delay_pos] = xl;
            self.delay_r[self.delay_pos] = xr;
            self.delay_pos = (self.delay_pos + 1) % LOOKAHEAD;
            if !hot && self.gain >= 1.0 - 1e-15 {
                out_l[i] = dry_l;
                out_r[i] = dry_r;
            } else {
                let (wl, wr) = self.downsample();
                out_l[i] = wl;
                out_r[i] = wr;
            }
        }
    }

    fn upsample(&self, phase: usize) -> (f64, f64) {
        let mut l = 0.0;
        let mut r = 0.0;
        for k in 0..TAPS {
            let idx = (self.hist_pos + TAPS - k) % TAPS;
            l += self.hist_l[idx] * self.coeff[phase][k];
            r += self.hist_r[idx] * self.coeff[phase][k];
        }
        (l, r)
    }

    fn push_dec(&mut self, left: f64, right: f64) {
        self.dec_hist_l[self.dec_pos] = left;
        self.dec_hist_r[self.dec_pos] = right;
        self.dec_pos = (self.dec_pos + 1) % self.dec_hist_l.len();
    }

    fn downsample(&self) -> (f64, f64) {
        let mut l = 0.0;
        let mut r = 0.0;
        let len = self.dec_hist_l.len();
        for k in 0..len {
            let idx = (self.dec_pos + len - 1 - k) % len;
            l += self.dec_hist_l[idx] * self.dec_coeff[k];
            r += self.dec_hist_r[idx] * self.dec_coeff[k];
        }
        (l, r)
    }
}

fn finite(x: f64) -> f64 {
    if x.is_finite() {
        x
    } else {
        0.0
    }
}

fn design_polyphase(coeff: &mut [[f64; TAPS]; OS]) {
    let n = TAPS * OS;
    let mid = (n as f64 - 1.0) / 2.0;
    let fc = 0.125;
    let mut h = vec![0.0; n];
    for i in 0..n {
        let x = i as f64 - mid;
        let window = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / n as f64).cos();
        let sinc = if x.abs() < 1e-12 {
            2.0 * fc
        } else {
            (2.0 * std::f64::consts::PI * fc * x).sin() / (std::f64::consts::PI * x)
        };
        h[i] = window * sinc * OS as f64;
    }
    for phase in 0..OS {
        let mut sum = 0.0;
        for k in 0..TAPS {
            coeff[phase][k] = h[phase + k * OS];
            sum += coeff[phase][k];
        }
        if sum.abs() > 1e-12 {
            for k in 0..TAPS {
                coeff[phase][k] /= sum;
            }
        }
    }
}

fn design_decimator(coeff: &mut [f64]) {
    let n = coeff.len();
    let mid = (n as f64 - 1.0) / 2.0;
    let fc = 0.125;
    let mut sum = 0.0;
    for i in 0..n {
        let x = i as f64 - mid;
        let window = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / n as f64).cos();
        let sinc = if x.abs() < 1e-12 {
            2.0 * fc
        } else {
            (2.0 * std::f64::consts::PI * fc * x).sin() / (std::f64::consts::PI * x)
        };
        coeff[i] = window * sinc;
        sum += coeff[i];
    }
    if sum.abs() > 1e-12 {
        for c in coeff.iter_mut() {
            *c /= sum;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn under_ceiling_is_a_pure_delay() {
        let mut lim = TruePeakLimiter::new(48_000);
        let n = 1000;
        let mut l = vec![0.0; n];
        let mut r = vec![0.0; n];
        for i in 0..n {
            l[i] = 0.2 * (2.0 * std::f64::consts::PI * 1000.0 * i as f64 / 48_000.0).sin();
            r[i] = -0.15 * l[i];
        }
        let mut ol = vec![0.0; n];
        let mut or_ = vec![0.0; n];
        lim.process(&l, &r, &mut ol, &mut or_);
        for i in LOOKAHEAD..n {
            assert!((ol[i] - l[i - LOOKAHEAD]).abs() < 1e-12, "l {i}");
            assert!((or_[i] - r[i - LOOKAHEAD]).abs() < 1e-12, "r {i}");
        }
    }

    #[test]
    fn full_scale_is_reduced_and_block_invariant() {
        let n = 2000;
        let mut l = vec![0.0; n];
        for i in 0..n {
            l[i] = (2.0 * std::f64::consts::PI * 1000.0 * i as f64 / 48_000.0).sin();
        }
        let run = |block: usize| {
            let mut lim = TruePeakLimiter::new(48_000);
            let mut o = vec![0.0; n];
            let z = vec![0.0; n];
            let mut done = 0;
            while done < n {
                let m = block.min(n - done);
                let mut scratch = vec![0.0; m];
                lim.process(
                    &l[done..done + m],
                    &z[done..done + m],
                    &mut o[done..done + m],
                    &mut scratch,
                );
                done += m;
            }
            o
        };
        let a = run(256);
        let b = run(n);
        let mut peak = 0.0_f64;
        for (u, v) in a.iter().zip(b.iter()) {
            let db = 20.0 * (u - v).abs().max(1e-20).log10();
            assert!(db <= -100.0, "{db}");
            peak = peak.max(u.abs());
        }
        assert!(peak < 0.98, "peak {peak}");
        assert!(peak > 0.2, "limiter produced silence, peak {peak}");
    }
}
