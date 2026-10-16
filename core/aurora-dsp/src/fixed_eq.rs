//! 512-tap minimum-phase FIR from a 1/3-octave gain curve.
//!
//! Matches `prototypes/aurora_p0/fixed_eq.py`. The curve is chosen upstream
//! from HRTF, preset and strength. Nothing here looks at the program.

use num_complex::Complex;
use realfft::RealFftPlanner;

pub const EQ_TAPS: usize = 512;
const NFFT: usize = 8192;

pub struct StereoFir {
    coeff: Vec<f64>,
    hist_l: Vec<f64>,
    hist_r: Vec<f64>,
    pos: usize,
}

impl StereoFir {
    pub fn bypass() -> Self {
        Self::new(&[1.0])
    }

    pub fn new(coeff: &[f64]) -> Self {
        let coeff = if coeff.is_empty() {
            vec![1.0]
        } else {
            coeff.to_vec()
        };
        let n = coeff.len();
        Self {
            coeff,
            hist_l: vec![0.0; n],
            hist_r: vec![0.0; n],
            pos: 0,
        }
    }

    pub fn set_coeffs(&mut self, coeff: &[f64]) {
        if coeff.is_empty() {
            return;
        }
        if coeff.len() != self.coeff.len() {
            *self = Self::new(coeff);
            return;
        }
        self.coeff.copy_from_slice(coeff);
    }

    pub fn reset(&mut self) {
        self.hist_l.fill(0.0);
        self.hist_r.fill(0.0);
        self.pos = 0;
    }

    pub fn process(&mut self, in_l: &[f64], in_r: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = in_l.len().min(in_r.len()).min(out_l.len()).min(out_r.len());
        let taps = self.coeff.len();
        for i in 0..n {
            self.hist_l[self.pos] = if in_l[i].is_finite() { in_l[i] } else { 0.0 };
            self.hist_r[self.pos] = if in_r[i].is_finite() { in_r[i] } else { 0.0 };
            let mut acc_l = 0.0;
            let mut acc_r = 0.0;
            let mut h = self.pos;
            for k in 0..taps {
                acc_l += self.hist_l[h] * self.coeff[k];
                acc_r += self.hist_r[h] * self.coeff[k];
                h = if h == 0 { taps - 1 } else { h - 1 };
            }
            out_l[i] = acc_l;
            out_r[i] = acc_r;
            self.pos += 1;
            if self.pos == taps {
                self.pos = 0;
            }
        }
    }
}

/// Minimum-phase FIR. `taps` is 512 and the cepstrum length is 8192, matching
/// the prototype. Gains outside the band list are 0 dB (`numpy.interp` left
/// and right), then a 20–40 Hz fade-in and an 16–18 kHz fade-out.
pub fn design_minphase_fir(bands_hz: &[f64], gain_db: &[f64], sample_rate: f64) -> Vec<f64> {
    let mut planner = RealFftPlanner::<f64>::new();
    let forward = planner.plan_fft_forward(NFFT);
    let inverse = planner.plan_fft_inverse(NFFT);
    let mut time = forward.make_input_vec();
    let mut spec = forward.make_output_vec();
    let bins = NFFT / 2 + 1;
    for k in 0..bins {
        let freq = k as f64 * sample_rate / NFFT as f64;
        let gdb = lerp_bands(bands_hz, gain_db, freq) * fade_lo(freq) * fade_hi(freq);
        let amp = 10.0_f64.powf(gdb / 20.0).max(1e-8);
        spec[k] = Complex::new(amp.ln(), 0.0);
    }
    let mut inv_scratch = inverse.make_scratch_vec();
    let mut fwd_scratch = forward.make_scratch_vec();
    inverse
        .process_with_scratch(&mut spec, &mut time, &mut inv_scratch)
        .expect("inverse fft");
    let scale = 1.0 / NFFT as f64;
    for sample in &mut time {
        *sample *= scale;
    }
    for i in 1..NFFT / 2 {
        time[i] *= 2.0;
    }
    for i in NFFT / 2 + 1..NFFT {
        time[i] = 0.0;
    }
    forward
        .process_with_scratch(&mut time, &mut spec, &mut fwd_scratch)
        .expect("forward fft");
    for bin in &mut spec {
        let mag = bin.re.exp();
        let (s, c) = bin.im.sin_cos();
        *bin = Complex::new(mag * c, mag * s);
    }
    inverse
        .process_with_scratch(&mut spec, &mut time, &mut inv_scratch)
        .expect("inverse fft");
    for sample in &mut time {
        *sample *= scale;
    }
    let mut fir = vec![0.0; EQ_TAPS];
    let m = 2 * EQ_TAPS;
    for i in 0..EQ_TAPS {
        let n = (EQ_TAPS + i) as f64;
        let window = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * n / (m as f64 - 1.0)).cos();
        fir[i] = time[i] * window;
    }
    fir
}

fn fade_lo(freq: f64) -> f64 {
    ((freq - 20.0) / 20.0).clamp(0.0, 1.0)
}

fn fade_hi(freq: f64) -> f64 {
    ((18_000.0 - freq) / 2000.0).clamp(0.0, 1.0)
}

fn lerp_bands(bands: &[f64], gains: &[f64], x: f64) -> f64 {
    if bands.is_empty() || gains.len() != bands.len() || x < bands[0] || x > bands[bands.len() - 1]
    {
        return 0.0;
    }
    if x == bands[bands.len() - 1] {
        return gains[gains.len() - 1];
    }
    let mut i = 0;
    while i + 1 < bands.len() && bands[i + 1] < x {
        i += 1;
    }
    if i + 1 >= bands.len() {
        return gains[gains.len() - 1];
    }
    let span = bands[i + 1] - bands[i];
    if span <= 0.0 {
        return gains[i];
    }
    let t = (x - bands[i]) / span;
    gains[i] * (1.0 - t) + gains[i + 1] * t
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flat_curve_is_an_impulse() {
        let bands = [100.0, 1000.0, 10_000.0];
        let gains = [0.0, 0.0, 0.0];
        let fir = design_minphase_fir(&bands, &gains, 48_000.0);
        assert!((fir[0] - 0.999_997_642_302_161_6).abs() < 1e-9, "{}", fir[0]);
        assert!(fir[1].abs() < 1e-12, "{}", fir[1]);
    }

    #[test]
    fn room_ku100_matches_prototype_head() {
        let bands = [
            49.606, 62.5, 78.745, 99.213, 125.0, 157.49, 198.425, 250.0, 314.98, 396.85, 500.0,
            629.961, 793.701, 1000.0, 1259.921, 1587.401, 2000.0, 2519.842, 3174.802, 4000.0,
            5039.684, 6349.604, 8000.0, 10079.368, 12699.208, 16000.0,
        ];
        // Room / KU100 / strength 0.4 row from presets/p0_draft.toml.
        let gains = [
            -0.58, -0.71, -0.57, 0.60, 0.51, 0.24, 1.93, -0.46, 0.37, 2.23, 1.51, 1.84, 0.75,
            -0.10, -0.64, 0.09, 0.34, 0.13, 0.50, 0.08, 0.85, -1.06, -2.58, -2.01, -2.56, -2.05,
        ];
        let fir = design_minphase_fir(&bands, &gains, 48_000.0);
        let expect = [
            0.909_961_601_737_520_9,
            -0.001_202_600_897_134_615_1,
            0.153_775_988_377_815_42,
            0.033_425_100_358_665_75,
            -0.030_038_224_478_493_507,
            0.006_928_743_576_255_210_4,
        ];
        for (got, exp) in fir.iter().zip(expect.iter()) {
            assert!((got - exp).abs() < 1e-8, "{got} vs {exp}");
        }
    }

    #[test]
    fn fir_blocks_match_one_shot() {
        let fir = design_minphase_fir(&[100.0, 1000.0], &[0.0, 3.0], 48_000.0);
        let x: Vec<f64> = (0..300).map(|i| (i as f64 * 0.17).sin()).collect();
        let mut whole = StereoFir::new(&fir);
        let mut parts = StereoFir::new(&fir);
        let z = vec![0.0; x.len()];
        let mut a = vec![0.0; x.len()];
        let mut b = vec![0.0; x.len()];
        let mut scratch = vec![0.0; x.len()];
        whole.process(&x, &z, &mut a, &mut scratch);
        parts.process(&x[..40], &z[..40], &mut b[..40], &mut scratch[..40]);
        parts.process(&x[40..], &z[40..], &mut b[40..], &mut scratch[40..]);
        for i in 0..x.len() {
            assert!((a[i] - b[i]).abs() < 1e-12, "{i}");
        }
    }
}
