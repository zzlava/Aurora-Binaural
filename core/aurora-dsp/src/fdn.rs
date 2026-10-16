//! Sample-wise 8-line FDN. Every random value comes from the NumPy PCG64 stream.
//!
//! The prototype bakes this into a truncated IR. Running it sample by sample
//! matches that IR inside the baked length and stays block-size exact.
//! Delay memory is allocated in `new` up to 85 ms. `process` only walks it.

use crate::rng::{numpy_round, NumpyGenerator};

const N: usize = 8;
const MAX_MS: f64 = 85.0;

pub struct FdnReverb {
    len: [usize; N],
    buf: Vec<f64>,
    stride: usize,
    idx: [usize; N],
    feedback: [f64; N * N],
    gains: [f64; N],
    b_l: [f64; N],
    b_r: [f64; N],
    lp_l: f64,
    lp_r: f64,
    a_l: f64,
    a_r: f64,
    scale: f64,
}

impl FdnReverb {
    pub fn new(sample_rate: u32, t60: f64, seed: u64) -> Self {
        let stride = (MAX_MS * 1e-3 * f64::from(sample_rate)).ceil() as usize + 8;
        let mut fdn = Self {
            len: [2; N],
            buf: vec![0.0; N * stride],
            stride,
            idx: [0; N],
            feedback: [0.0; N * N],
            gains: [0.0; N],
            b_l: [0.0; N],
            b_r: [0.0; N],
            lp_l: 0.0,
            lp_r: 0.0,
            a_l: 0.82,
            a_r: 0.80,
            scale: 0.12,
        };
        fdn.reseed(sample_rate, t60, seed);
        fdn
    }

    pub fn reseed(&mut self, sample_rate: u32, t60: f64, seed: u64) {
        let mut rng = NumpyGenerator::new(seed);
        let mut delays_ms = [0.0; N];
        for d in &mut delays_ms {
            *d = rng.uniform(20.0, 85.0);
        }
        let mut order: [usize; N] = [0, 1, 2, 3, 4, 5, 6, 7];
        order.sort_by(|&a, &b| {
            delays_ms[a]
                .partial_cmp(&delays_ms[b])
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let sr = f64::from(sample_rate);
        for (i, slot) in order.iter().enumerate() {
            let samples = numpy_round(delays_ms[*slot] * 1e-3 * sr).max(2.0) as usize;
            self.len[i] = samples.min(self.stride);
        }
        for i in 1..N {
            if self.len[i] <= self.len[i - 1] {
                self.len[i] = (self.len[i - 1] + 1).min(self.stride);
            }
        }
        let mut m = [0.0; N * N];
        for row in 0..N {
            for col in 0..N {
                m[row * N + col] = rng.standard_normal();
            }
        }
        self.feedback = householder_q(&m);
        let t60 = t60.max(1e-3);
        for i in 0..N {
            self.gains[i] = 10.0_f64.powf(-3.0 * self.len[i] as f64 / (t60 * sr));
        }
        let mut norm_l = 0.0;
        let mut norm_r = 0.0;
        for i in 0..N {
            self.b_l[i] = rng.standard_normal();
            norm_l += self.b_l[i] * self.b_l[i];
        }
        for i in 0..N {
            self.b_r[i] = rng.standard_normal();
            norm_r += self.b_r[i] * self.b_r[i];
        }
        norm_l = norm_l.sqrt() + 1e-12;
        norm_r = norm_r.sqrt() + 1e-12;
        for i in 0..N {
            self.b_l[i] /= norm_l;
            self.b_r[i] /= norm_r;
        }
        self.reset();
    }

    pub fn delays(&self) -> [usize; N] {
        self.len
    }

    pub fn reset(&mut self) {
        self.buf.fill(0.0);
        self.idx = [0; N];
        self.lp_l = 0.0;
        self.lp_r = 0.0;
    }

    pub fn process(&mut self, input: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = input.len().min(out_l.len()).min(out_r.len());
        let mut taps = [0.0; N];
        let mut fb = [0.0; N];
        for i in 0..n {
            for k in 0..N {
                taps[k] = self.buf[k * self.stride + self.idx[k]];
            }
            let mut dot_l = 0.0;
            let mut dot_r = 0.0;
            for k in 0..N {
                dot_l += taps[k] * self.b_l[k];
                dot_r += taps[k] * self.b_r[k];
            }
            self.lp_l = (1.0 - self.a_l) * self.lp_l + self.a_l * dot_l;
            self.lp_r = (1.0 - self.a_r) * self.lp_r + self.a_r * dot_r;
            out_l[i] = self.lp_l * self.scale;
            out_r[i] = self.lp_r * self.scale;
            // `feedback` holds Q transposed relative to `numpy.linalg.qr`.
            // (Q v)[i] = sum_j feedback[j, i] * v[j].
            for i in 0..N {
                let mut acc = 0.0;
                for j in 0..N {
                    acc += self.feedback[j * N + i] * (taps[j] * self.gains[j]);
                }
                fb[i] = acc;
            }
            let x = if input[i].is_finite() { input[i] } else { 0.0 };
            let inj = x / N as f64;
            for k in 0..N {
                self.buf[k * self.stride + self.idx[k]] = fb[k] + inj;
                self.idx[k] += 1;
                if self.idx[k] == self.len[k] {
                    self.idx[k] = 0;
                }
            }
        }
    }
}

/// Thin QR, matching `numpy.linalg.qr` on a square matrix.
/// The last column is left as a 1×1 (no reflector).
fn householder_q(a_in: &[f64; N * N]) -> [f64; N * N] {
    let mut r = *a_in;
    let mut q = [0.0; N * N];
    for i in 0..N {
        q[i * N + i] = 1.0;
    }
    let mut v = [0.0; N];
    for k in 0..N - 1 {
        let mut norm = 0.0;
        for r_i in k..N {
            let x = r[r_i * N + k];
            norm += x * x;
        }
        norm = norm.sqrt();
        let x0 = r[k * N + k];
        let sign = if x0 >= 0.0 { 1.0 } else { -1.0 };
        for r_i in 0..N {
            v[r_i] = 0.0;
        }
        for r_i in k..N {
            v[r_i - k] = r[r_i * N + k];
        }
        v[0] += sign * norm;
        let mut vnorm = 0.0;
        let rows = N - k;
        for r_i in 0..rows {
            vnorm += v[r_i] * v[r_i];
        }
        vnorm = vnorm.sqrt();
        if vnorm < 1e-15 {
            continue;
        }
        for r_i in 0..rows {
            v[r_i] /= vnorm;
        }
        // R[k:, k:] -= 2 v (v^T R[k:, k:])
        for col in k..N {
            let mut dot = 0.0;
            for row in 0..rows {
                dot += v[row] * r[(k + row) * N + col];
            }
            for row in 0..rows {
                r[(k + row) * N + col] -= 2.0 * v[row] * dot;
            }
        }
        // Q = H @ Q, so Q[k:, :] -= 2 v (v^T Q[k:, :]).
        for col in 0..N {
            let mut dot = 0.0;
            for row in 0..rows {
                dot += v[row] * q[(k + row) * N + col];
            }
            for row in 0..rows {
                q[(k + row) * N + col] -= 2.0 * v[row] * dot;
            }
        }
    }
    q
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delays_match_sorted_numpy_draws() {
        let fdn = FdnReverb::new(48_000, 0.35, 20_261_016);
        assert_eq!(
            fdn.delays(),
            [1582, 1761, 2037, 2512, 2676, 2697, 2912, 3215]
        );
    }

    #[test]
    fn impulse_matches_prototype_samples() {
        let mut fdn = FdnReverb::new(48_000, 0.35, 20_261_016);
        let n = 4000;
        let mut x = vec![0.0; n];
        x[0] = 1.0;
        let mut yl = vec![0.0; n];
        let mut yr = vec![0.0; n];
        fdn.process(&x, &mut yl, &mut yr);
        for i in 0..8 {
            assert_eq!(yl[i], 0.0);
            assert_eq!(yr[i], 0.0);
        }
        assert!(
            (yl[1582] - 0.003_529_505_594_380_343_6).abs() < 1e-9,
            "{}",
            yl[1582]
        );
        assert!(
            (yr[1582] - 0.003_645_741_293_040_408_4).abs() < 1e-9,
            "{}",
            yr[1582]
        );
        assert!(
            (yl[1761] - (-0.003_906_414_255_268_871)).abs() < 1e-9,
            "{}",
            yl[1761]
        );
        assert!(
            (yr[1761] - 0.001_434_499_833_796_95).abs() < 1e-9,
            "{}",
            yr[1761]
        );
        let mut sum = 0.0;
        let mut energy = 0.0;
        for i in 0..n {
            sum += yl[i] + yr[i];
            energy += yl[i] * yl[i] + yr[i] * yr[i];
        }
        assert!(
            (sum - 0.002_973_335_978_070_267_7).abs() < 1e-8,
            "sum {sum} energy {energy} q0 {:?}",
            &fdn.feedback[..4]
        );
        assert!(
            (energy - 0.000_314_290_452_231_924_27).abs() < 1e-10,
            "{energy}"
        );
    }

    #[test]
    fn block_sizes_agree() {
        let x: Vec<f64> = (0..1500).map(|i| if i == 10 { 1.0 } else { 0.0 }).collect();
        let run = |block: usize| {
            let mut fdn = FdnReverb::new(48_000, 0.35, 20_261_016);
            let mut yl = vec![0.0; x.len()];
            let mut o = 0;
            while o < x.len() {
                let n = block.min(x.len() - o);
                let mut yr = vec![0.0; n];
                fdn.process(&x[o..o + n], &mut yl[o..o + n], &mut yr);
                o += n;
            }
            yl
        };
        let a = run(32);
        let b = run(x.len());
        for (u, v) in a.iter().zip(b.iter()) {
            let db = 20.0 * (u - v).abs().max(1e-20).log10();
            assert!(db <= -100.0, "{db}");
        }
    }
}
