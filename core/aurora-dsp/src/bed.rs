//! Causal band lift on the wide/rear bed.
//!
//! `butter(2, [lo, hi], btype="band")` then `y + (g-1) * sosfilt(x)`. The
//! lift is skipped when `|gain_db| <= 0.05`, which is how the room preset
//! (0 dB) leaves the bed untouched.

use num_complex::Complex;

const SECTIONS: usize = 2;

pub struct BandLift {
    enabled: bool,
    mix: f64,
    sos: [[f64; 6]; SECTIONS],
    zi: [[[f64; 2]; SECTIONS]; 2],
}

impl BandLift {
    pub fn new(sample_rate: f64, lo_hz: f64, hi_hz: f64, gain_db: f64) -> Self {
        let enabled = gain_db.abs() > 0.05 && hi_hz > lo_hz && lo_hz > 0.0;
        let sos = if enabled {
            butter_band_sos(lo_hz, hi_hz, sample_rate)
        } else {
            [[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]; SECTIONS]
        };
        Self {
            enabled,
            mix: 10.0_f64.powf(gain_db / 20.0) - 1.0,
            sos,
            zi: [[[0.0; 2]; SECTIONS]; 2],
        }
    }

    pub fn reset(&mut self) {
        self.zi = [[[0.0; 2]; SECTIONS]; 2];
    }

    pub fn process(&mut self, left: &mut [f64], right: &mut [f64]) {
        if !self.enabled {
            return;
        }
        let n = left.len().min(right.len());
        for i in 0..n {
            let bl = df2t(&self.sos, &mut self.zi[0], left[i]);
            let br = df2t(&self.sos, &mut self.zi[1], right[i]);
            left[i] += self.mix * bl;
            right[i] += self.mix * br;
        }
    }
}

fn df2t(sos: &[[f64; 6]; SECTIONS], state: &mut [[f64; 2]; SECTIONS], x: f64) -> f64 {
    let mut y = x;
    for (section, st) in sos.iter().zip(state.iter_mut()) {
        let yn = section[0] * y + st[0];
        st[0] = section[1] * y - section[4] * yn + st[1];
        st[1] = section[2] * y - section[5] * yn;
        y = yn;
    }
    y
}

/// Order-2 Butterworth bandpass as two SOS, matching `scipy.signal.butter`.
fn butter_band_sos(lo_hz: f64, hi_hz: f64, sample_rate: f64) -> [[f64; 6]; SECTIONS] {
    let nyquist = sample_rate / 2.0;
    let w0 = prewarp(lo_hz / nyquist);
    let w1 = prewarp(hi_hz / nyquist);
    let bw = w1 - w0;
    let wo = (w0 * w1).sqrt();
    // buttap(2): m = -1, +1. p = -exp(j * pi * m / 4).
    let mut proto = [Complex::new(0.0, 0.0); 2];
    for (i, m) in [-1.0_f64, 1.0].into_iter().enumerate() {
        let ang = std::f64::consts::PI * m / 4.0;
        proto[i] = -Complex::from_polar(1.0, ang);
    }
    let half_bw = bw / 2.0;
    let mut poles = [Complex::new(0.0, 0.0); 4];
    for (i, p) in proto.iter().enumerate() {
        let scaled = *p * half_bw;
        let root = (scaled * scaled - wo * wo).sqrt();
        poles[i] = scaled + root;
        poles[i + 2] = scaled - root;
    }
    // lp2bp puts `degree` zeros at the origin. degree of the prototype is 2.
    let analog_zeros = [Complex::new(0.0, 0.0); 2];
    let k = bw * bw;
    // bilinear with fs = 2 (Wn already normalised to Nyquist = 1).
    let fs2 = 4.0;
    let mut z_num = Complex::new(1.0, 0.0);
    let mut z_den = Complex::new(1.0, 0.0);
    let mut zeros = [Complex::new(0.0, 0.0); 4];
    for (i, z) in analog_zeros.iter().enumerate() {
        zeros[i] = (fs2 + z) / (fs2 - z);
        z_num *= fs2 - z;
    }
    // Relative degree 2 → two zeros at z = -1.
    zeros[2] = Complex::new(-1.0, 0.0);
    zeros[3] = Complex::new(-1.0, 0.0);
    let mut dpoles = [Complex::new(0.0, 0.0); 4];
    for (i, p) in poles.iter().enumerate() {
        dpoles[i] = (fs2 + p) / (fs2 - p);
        z_den *= fs2 - p;
    }
    let k_z = (k * (z_num / z_den)).re;

    let mut pairs = pair_conjugates(&dpoles);
    // Nearest-style: poles closest to the unit circle take the Nyquist zeros,
    // which is what scipy's cascade does for this band. Order does not change
    // the product, but it keeps section gains modest.
    pairs.sort_by(|a, b| {
        let da = (1.0 - a.norm()).abs();
        let db = (1.0 - b.norm()).abs();
        da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut sos = [[0.0; 6]; SECTIONS];
    // Section 0: double zero at z = -1, gain k_z. Section 1: double zero at z = 1.
    sos[0] = section_from_pole(pairs[0], k_z, true);
    sos[1] = section_from_pole(pairs[1], 1.0, false);
    sos
}

fn prewarp(wn_norm: f64) -> f64 {
    // fs = 2, warped = 2 * fs * tan(pi * Wn / fs) = 4 * tan(pi * Wn / 2).
    4.0 * (std::f64::consts::PI * wn_norm / 2.0).tan()
}

fn pair_conjugates(poles: &[Complex<f64>; 4]) -> [Complex<f64>; 2] {
    let mut used = [false; 4];
    let mut out = [Complex::new(0.0, 0.0); 2];
    let mut n = 0;
    for i in 0..4 {
        if used[i] {
            continue;
        }
        used[i] = true;
        let p = poles[i];
        let mut best = None;
        let mut best_d = f64::MAX;
        for j in 0..4 {
            if used[j] {
                continue;
            }
            let d = (poles[j] - p.conj()).norm();
            if d < best_d {
                best_d = d;
                best = Some(j);
            }
        }
        if let Some(j) = best {
            used[j] = true;
        }
        out[n] = p;
        n += 1;
        if n == 2 {
            break;
        }
    }
    out
}

fn section_from_pole(pole: Complex<f64>, gain: f64, nyquist_zeros: bool) -> [f64; 6] {
    let a1 = -2.0 * pole.re;
    let a2 = pole.norm_sqr();
    if nyquist_zeros {
        [gain, 2.0 * gain, gain, 1.0, a1, a2]
    } else {
        [gain, -2.0 * gain, gain, 1.0, a1, a2]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bandpass_impulse_matches_scipy() {
        let sos = butter_band_sos(1000.0, 1600.0, 48_000.0);
        let mut state = [[0.0; 2]; SECTIONS];
        let mut got = [0.0; 8];
        let x = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        for i in 0..8 {
            got[i] = df2t(&sos, &mut state, x[i]);
        }
        let expect = [
            0.001_460_316_305_527_733_9,
            0.005_601_506_681_791_372_5,
            0.010_431_624_167_272_892,
            0.014_111_557_837_121_744,
            0.016_502_490_266_254_1,
            0.017_535_736_731_251_948,
            0.017_211_443_241_782_71,
            0.015_594_516_026_750_025,
        ];
        for (g, e) in got.iter().zip(expect.iter()) {
            assert!((g - e).abs() < 1e-9, "{g} vs {e}");
        }
    }

    #[test]
    fn zero_db_does_not_move_the_bed() {
        let mut bed = BandLift::new(48_000.0, 1000.0, 1600.0, 0.0);
        let mut l = [0.2, -0.1, 0.05];
        let mut r = [0.1, 0.0, -0.2];
        let lc = l;
        let rc = r;
        bed.process(&mut l, &mut r);
        assert_eq!(l, lc);
        assert_eq!(r, rc);
    }
}
