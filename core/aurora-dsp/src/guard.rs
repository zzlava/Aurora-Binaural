//! Streaming timbre-protection FIR.
//!
//! Coefficients are chosen when the guard is built (`prepare` / `set_params`).
//! Disabled means a dedicated copy, not a dry/wet mix. The route B regression
//! leaves the guard disabled; the prototype's whole-file guard is off by default.

pub struct TimbreGuard {
    enabled: bool,
    coeff: Vec<f64>,
    hist_l: Vec<f64>,
    hist_r: Vec<f64>,
    pos: usize,
}

impl TimbreGuard {
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            coeff: vec![1.0],
            hist_l: vec![0.0; 1],
            hist_r: vec![0.0; 1],
            pos: 0,
        }
    }

    pub fn from_fir(coeff: &[f64]) -> Self {
        let n = coeff.len().max(1);
        let mut c = vec![0.0; n];
        let m = coeff.len().min(n);
        c[..m].copy_from_slice(&coeff[..m]);
        if coeff.is_empty() {
            c[0] = 1.0;
        }
        Self {
            enabled: true,
            coeff: c,
            hist_l: vec![0.0; n],
            hist_r: vec![0.0; n],
            pos: 0,
        }
    }

    pub fn reset(&mut self) {
        self.hist_l.fill(0.0);
        self.hist_r.fill(0.0);
        self.pos = 0;
    }

    pub fn process(&mut self, in_l: &[f64], in_r: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = in_l.len().min(in_r.len()).min(out_l.len()).min(out_r.len());
        if !self.enabled {
            out_l[..n].copy_from_slice(&in_l[..n]);
            out_r[..n].copy_from_slice(&in_r[..n]);
            return;
        }
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::conv::DirectStereoFir;

    #[test]
    fn disabled_copies() {
        let mut g = TimbreGuard::disabled();
        let x = [0.2, f64::NAN, -0.5];
        let mut l = [0.0; 3];
        let mut r = [0.0; 3];
        g.process(&x, &x, &mut l, &mut r);
        assert_eq!(l[0], x[0]);
        assert!(l[1].is_nan());
        assert_eq!(l[2], x[2]);
        assert_eq!(r[0], 0.2);
        assert!(r[1].is_nan());
    }

    #[test]
    fn streaming_fir_matches_direct_convolution() {
        let coeff = [0.2, -0.1, 0.05, 0.4];
        let x: Vec<f64> = (0..400).map(|i| (i as f64 * 0.2).sin()).collect();
        let mut guard = TimbreGuard::from_fir(&coeff);
        let mut gl = vec![0.0; x.len()];
        let mut gr = vec![0.0; x.len()];
        guard.process(&x[..50], &x[..50], &mut gl[..50], &mut gr[..50]);
        guard.process(&x[50..], &x[50..], &mut gl[50..], &mut gr[50..]);
        let mut fir = DirectStereoFir::new(coeff.len());
        fir.set_ir(&coeff, &coeff);
        let mut fl = vec![0.0; x.len()];
        let mut fr = vec![0.0; x.len()];
        fir.process(&x, &mut fl, &mut fr);
        for i in 0..x.len() {
            assert!((gl[i] - fl[i]).abs() < 1e-12, "{i}");
            assert!((gr[i] - fr[i]).abs() < 1e-12, "{i}");
        }
    }
}
