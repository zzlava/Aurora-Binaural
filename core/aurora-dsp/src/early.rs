//! Shoebox image-source early reflections.
//!
//! Geometry, absorption, and the −12 dB tap level match `RoomParams` in the
//! prototype. Image indices use Euclidean remainder so negative orders match
//! Python's `%`. Reflections are sorted by delay and truncated to 12. The
//! first six are also written as separate taps for binaural HRIR convolution.

use crate::coords::cart_to_az_el;

pub const N_TAPS: usize = 12;
pub const N_DIRECTED: usize = 6;

#[derive(Clone, Copy)]
pub struct Tap {
    pub delay: usize,
    pub gain: f64,
    pub azimuth_deg: f64,
    pub elevation_deg: f64,
}

pub struct EarlyReflections {
    taps: Vec<Tap>,
    hist: Vec<f64>,
    pos: usize,
    level: f64,
}

impl EarlyReflections {
    pub fn new(sample_rate: u32) -> Self {
        let sr = f64::from(sample_rate);
        let level = 10.0_f64.powf(-12.0 / 20.0);
        let listener = [2.0, 2.5, 1.6];
        let source = [listener[0] + 1.5, listener[1], listener[2]];
        let size = [4.0, 5.0, 2.8];
        let absorption = 0.25_f64;
        let c = 343.0;
        let max_order = 1i32;
        let mut raw: Vec<(f64, f64, f64, f64)> = Vec::new();
        for nx in -max_order..=max_order {
            for ny in -max_order..=max_order {
                for nz in -max_order..=max_order {
                    if nx == 0 && ny == 0 && nz == 0 {
                        continue;
                    }
                    let sx = image_coord(nx, size[0], source[0]);
                    let sy = image_coord(ny, size[1], source[1]);
                    let sz = image_coord(nz, size[2], source[2]);
                    let vec = [sx - listener[0], sy - listener[1], sz - listener[2]];
                    let (az, el, dist) = cart_to_az_el(vec);
                    if dist < 1e-6 {
                        continue;
                    }
                    let n_walls = nx.unsigned_abs() + ny.unsigned_abs() + nz.unsigned_abs();
                    let gain = (1.0 - absorption).powi(n_walls as i32) / dist;
                    raw.push((dist / c, gain, az, el));
                }
            }
        }
        raw.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        raw.truncate(N_TAPS);
        let taps: Vec<Tap> = raw
            .into_iter()
            .map(|(delay_s, gain, az, el)| Tap {
                delay: crate::rng::numpy_round(delay_s * sr).max(0.0) as usize,
                gain: gain * level,
                azimuth_deg: az,
                elevation_deg: el,
            })
            .collect();
        let max_delay = taps.iter().map(|t| t.delay).max().unwrap_or(1) + 1;
        Self {
            taps,
            hist: vec![0.0; max_delay.max(1)],
            pos: 0,
            level,
        }
    }

    pub fn taps(&self) -> &[Tap] {
        &self.taps
    }

    pub fn level(&self) -> f64 {
        self.level
    }

    pub fn reset(&mut self) {
        self.hist.fill(0.0);
        self.pos = 0;
    }

    /// `directed` is tap-major with `stride` samples per tap. The first
    /// [`N_DIRECTED`] taps are written. `mono` is the sum of all taps.
    pub fn process(
        &mut self,
        input: &[f64],
        mono: &mut [f64],
        directed: &mut [f64],
        stride: usize,
    ) {
        let n = input.len().min(mono.len());
        let hist_len = self.hist.len();
        let n_dir = N_DIRECTED.min(directed.len().checked_div(stride).unwrap_or(0));
        for t in 0..n_dir {
            for i in 0..n {
                directed[t * stride + i] = 0.0;
            }
        }
        for i in 0..n {
            mono[i] = 0.0;
            let x = if input[i].is_finite() { input[i] } else { 0.0 };
            self.hist[self.pos] = x;
            for (t_i, tap) in self.taps.iter().enumerate() {
                let src = if self.pos >= tap.delay {
                    self.pos - tap.delay
                } else {
                    self.pos + hist_len - tap.delay
                };
                let sample = self.hist[src % hist_len] * tap.gain;
                mono[i] += sample;
                if t_i < n_dir {
                    directed[t_i * stride + i] = sample;
                }
            }
            self.pos += 1;
            if self.pos == hist_len {
                self.pos = 0;
            }
        }
    }
}

fn image_coord(n: i32, length: f64, source: f64) -> f64 {
    let even = n.rem_euclid(2) == 0;
    (2.0 * f64::from(n) * length) + if even { source } else { 2.0 * length - source }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_taps_match_prototype() {
        let er = EarlyReflections::new(48_000);
        let t = er.taps();
        assert!(t.len() >= 6);
        assert_eq!(
            t[0].delay,
            (0.010_303_555_1_f64 * 48_000.0).round() as usize
        );
        assert!((t[0].azimuth_deg - 0.0).abs() < 1e-6);
        assert!((t[0].elevation_deg - (-64.885_165_113_9)).abs() < 1e-4);
        let bare = t[0].gain / er.level();
        assert!((bare - 0.212_216_938_12).abs() < 1e-6, "{bare}");
        assert!((t[2].azimuth_deg - 180.0).abs() < 1e-4 || (t[2].azimuth_deg + 180.0).abs() < 1e-4);
    }

    #[test]
    fn impulse_energy_is_block_invariant() {
        let mut a = EarlyReflections::new(48_000);
        let mut b = EarlyReflections::new(48_000);
        let mut x = vec![0.0; 2000];
        x[0] = 1.0;
        let mut mono_a = vec![0.0; x.len()];
        let mut mono_b = vec![0.0; x.len()];
        let mut dir_a = vec![0.0; 2 * x.len()];
        let mut dir_b = vec![0.0; 2 * x.len()];
        a.process(&x, &mut mono_a, &mut dir_a, x.len());
        let mut chunk = vec![0.0; 2 * 64];
        b.process(&x[..64], &mut mono_b[..64], &mut chunk, 64);
        for t in 0..2 {
            dir_b[t * x.len()..t * x.len() + 64].copy_from_slice(&chunk[t * 64..(t + 1) * 64]);
        }
        let rest = x.len() - 64;
        let mut chunk = vec![0.0; 2 * rest];
        b.process(&x[64..], &mut mono_b[64..], &mut chunk, rest);
        for t in 0..2 {
            dir_b[t * x.len() + 64..t * x.len() + x.len()]
                .copy_from_slice(&chunk[t * rest..(t + 1) * rest]);
        }
        for i in 0..x.len() {
            assert!((mono_a[i] - mono_b[i]).abs() < 1e-12);
            assert!((dir_a[i] - dir_b[i]).abs() < 1e-12);
        }
    }
}
