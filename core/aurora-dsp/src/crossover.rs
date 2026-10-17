//! Linkwitz–Riley 4 crossover.
//!
//! LR4 is two cascaded Butterworth 2nd-order sections. A single Butterworth
//! of order 4 is −3 dB at the cutoff on each side and the in-phase sum peaks
//! about +3 dB, so it is not a complementary pair. Initial state is rest.

const SECTIONS: usize = 2;

#[derive(Clone)]
pub struct Lr4Crossover {
    lp: [[f64; 6]; SECTIONS],
    hp: [[f64; 6]; SECTIONS],
    zi_lp: [[[f64; 2]; SECTIONS]; 2],
    zi_hp: [[[f64; 2]; SECTIONS]; 2],
}

impl Lr4Crossover {
    pub fn new(sample_rate: f64, cutoff_hz: f64) -> Self {
        let lp_one = butter2(cutoff_hz, sample_rate, false);
        let hp_one = butter2(cutoff_hz, sample_rate, true);
        Self {
            lp: [lp_one, lp_one],
            hp: [hp_one, hp_one],
            zi_lp: [[[0.0; 2]; SECTIONS]; 2],
            zi_hp: [[[0.0; 2]; SECTIONS]; 2],
        }
    }

    pub fn reset(&mut self) {
        self.zi_lp = [[[0.0; 2]; SECTIONS]; 2];
        self.zi_hp = [[[0.0; 2]; SECTIONS]; 2];
    }

    pub fn process(
        &mut self,
        left: &[f64],
        right: &[f64],
        low_l: &mut [f64],
        low_r: &mut [f64],
        high_l: &mut [f64],
        high_r: &mut [f64],
    ) {
        let n = left.len();
        for i in 0..n {
            low_l[i] = df2t(&self.lp, &mut self.zi_lp[0], left[i]);
            high_l[i] = df2t(&self.hp, &mut self.zi_hp[0], left[i]);
            low_r[i] = df2t(&self.lp, &mut self.zi_lp[1], right[i]);
            high_r[i] = df2t(&self.hp, &mut self.zi_hp[1], right[i]);
        }
    }
}

fn butter2(fc: f64, fs: f64, highpass: bool) -> [f64; 6] {
    let k = (std::f64::consts::PI * fc / fs).tan();
    let q = std::f64::consts::SQRT_2;
    let k2 = k * k;
    let a0 = k2 + q * k + 1.0;
    let a1 = 2.0 * (k2 - 1.0) / a0;
    let a2 = (k2 - q * k + 1.0) / a0;
    if highpass {
        [1.0 / a0, -2.0 / a0, 1.0 / a0, 1.0, a1, a2]
    } else {
        let b0 = k2 / a0;
        [b0, 2.0 * b0, b0, 1.0, a1, a2]
    }
}

pub(crate) fn butterworth2_low(fc: f64, fs: f64) -> [f64; 6] {
    butter2(fc, fs, false)
}

/// One biquad, direct form II transposed, rest state. Matches `scipy.signal.sosfilt`.
pub(crate) fn apply_sos(sos: &[f64; 6], input: &[f64], output: &mut [f64]) {
    let mut s0 = 0.0;
    let mut s1 = 0.0;
    let n = input.len().min(output.len());
    for i in 0..n {
        let x = input[i];
        let yn = sos[0] * x + s0;
        s0 = sos[1] * x - sos[4] * yn + s1;
        s1 = sos[2] * x - sos[5] * yn;
        output[i] = yn;
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

/// Magnitude of `low + high` in dB at `freq_hz`.
pub fn sum_magnitude_db(sample_rate: f64, cutoff_hz: f64, freq_hz: f64) -> f64 {
    let lp = butter2(cutoff_hz, sample_rate, false);
    let hp = butter2(cutoff_hz, sample_rate, true);
    // Two identical sections: H_lp^2 + H_hp^2.
    let h_lp = biquad_complex(&lp, sample_rate, freq_hz);
    let h_hp = biquad_complex(&hp, sample_rate, freq_hz);
    let lp2 = complex_mul(h_lp, h_lp);
    let hp2 = complex_mul(h_hp, h_hp);
    let sum = (lp2.0 + hp2.0, lp2.1 + hp2.1);
    let mag = (sum.0 * sum.0 + sum.1 * sum.1).sqrt();
    20.0 * mag.max(1e-20).log10()
}

fn biquad_complex(section: &[f64; 6], fs: f64, freq: f64) -> (f64, f64) {
    let w = 2.0 * std::f64::consts::PI * freq / fs;
    let z1 = (w.cos(), -w.sin());
    let z2 = complex_mul(z1, z1);
    let num = (
        section[0] + section[1] * z1.0 + section[2] * z2.0,
        section[1] * z1.1 + section[2] * z2.1,
    );
    let den = (
        1.0 + section[4] * z1.0 + section[5] * z2.0,
        section[4] * z1.1 + section[5] * z2.1,
    );
    complex_div(num, den)
}

fn complex_mul(a: (f64, f64), b: (f64, f64)) -> (f64, f64) {
    (a.0 * b.0 - a.1 * b.1, a.0 * b.1 + a.1 * b.0)
}

fn complex_div(a: (f64, f64), b: (f64, f64)) -> (f64, f64) {
    let d = b.0 * b.0 + b.1 * b.1;
    ((a.0 * b.0 + a.1 * b.1) / d, (a.1 * b.0 - a.0 * b.1) / d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rng::NumpyGenerator;

    #[test]
    fn coefficients_match_scipy_butter() {
        let lp = butter2(150.0, 48_000.0, false);
        assert!((lp[0] - 9.506_002_944_922_903e-5).abs() < 1e-18);
        assert!((lp[4] - (-1.972_233_729_195_266_3)).abs() < 1e-14);
        let hp = butter2(150.0, 48_000.0, true);
        assert!((hp[0] - 0.986_211_924_627_082_3).abs() < 1e-14);
    }

    #[test]
    fn samples_match_scipy_sosfilt() {
        let mut rng = NumpyGenerator::new(1);
        let mut x = [0.0; 64];
        for s in &mut x {
            *s = rng.standard_normal();
        }
        let mut xo = Lr4Crossover::new(48_000.0, 150.0);
        let mut low = [0.0; 64];
        let mut high = [0.0; 64];
        let zeros = [0.0; 64];
        xo.process(
            &x,
            &zeros,
            &mut low,
            &mut [0.0; 64],
            &mut high,
            &mut [0.0; 64],
        );
        let expect = [
            3.122_840_172_164_609_7e-9,
            3.223_377_987_542_381e-8,
            1.603_444_467_082_570_8e-7,
            5.135_783_054_410_902e-7,
            1.222_716_893_482_450_4e-6,
            2.396_403_095_184_771_7e-6,
            4.152_945_925_713_7e-6,
            6.634_351_168_560_728e-6,
        ];
        for (got, exp) in low.iter().zip(expect.iter()) {
            assert!((got - exp).abs() < 1e-18, "{got} vs {exp}");
        }
        // Blocked processing matches one shot.
        let mut a = Lr4Crossover::new(48_000.0, 150.0);
        let mut b = Lr4Crossover::new(48_000.0, 150.0);
        let mut whole = [0.0; 64];
        let mut piece = [0.0; 64];
        a.process(
            &x,
            &zeros,
            &mut whole,
            &mut [0.0; 64],
            &mut [0.0; 64],
            &mut [0.0; 64],
        );
        b.process(
            &x[..32],
            &zeros[..32],
            &mut piece[..32],
            &mut [0.0; 32],
            &mut [0.0; 32],
            &mut [0.0; 32],
        );
        b.process(
            &x[32..],
            &zeros[32..],
            &mut piece[32..],
            &mut [0.0; 32],
            &mut [0.0; 32],
            &mut [0.0; 32],
        );
        for i in 0..64 {
            assert!((whole[i] - piece[i]).abs() < 1e-18);
        }
    }

    #[test]
    fn resynthesis_is_flat_within_half_db() {
        let mut worst = 0.0_f64;
        let mut f = 20.0;
        while f <= 20_000.0 {
            let db = sum_magnitude_db(48_000.0, 150.0, f);
            worst = worst.max(db.abs());
            f *= 2.0_f64.powf(1.0 / 24.0);
        }
        assert!(worst <= 0.5, "worst deviation {worst} dB");
    }
}
