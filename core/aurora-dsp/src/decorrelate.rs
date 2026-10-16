//! Seeded cascaded allpass decorrelator.
//!
//! Matches `prototypes/aurora_p0/upmix.py` `decorrelate`: four sections,
//! `numpy.random.default_rng`, delay in `[0.002 s, 0.011 s)`, gain in `[0.4, 0.7)`.
//! Coefficient draws alternate `integers` then `uniform`, one section at a time.
//! Delay lines are sized in `new`; `process` does not allocate.

use crate::rng::NumpyGenerator;

const N_AP: usize = 4;
const MAX_DELAY: usize = 528;

struct Section {
    g: f64,
    delay: usize,
    state: Vec<f64>,
    pos: usize,
}

pub struct Decorrelator {
    sections: [Section; N_AP],
}

impl Decorrelator {
    pub fn new(sample_rate: u32, seed: u64) -> Self {
        let mut rng = NumpyGenerator::new(seed);
        let lo = (0.002 * f64::from(sample_rate)) as i64;
        let hi = (0.011 * f64::from(sample_rate)) as i64;
        let mut sections = Vec::with_capacity(N_AP);
        for _ in 0..N_AP {
            let d = rng.integers(lo, hi).clamp(1, MAX_DELAY as i64) as usize;
            let g = rng.uniform(0.4, 0.7);
            sections.push(Section {
                g,
                delay: d,
                state: vec![0.0; d],
                pos: 0,
            });
        }
        let sections: [Section; N_AP] = sections.try_into().ok().expect("four sections");
        Self { sections }
    }

    pub fn reset(&mut self) {
        for section in &mut self.sections {
            section.state.fill(0.0);
            section.pos = 0;
        }
    }

    pub fn process(&mut self, input: &[f64], output: &mut [f64]) {
        let n = input.len().min(output.len());
        for i in 0..n {
            let mut x = if input[i].is_finite() { input[i] } else { 0.0 };
            for section in &mut self.sections {
                let d = section.delay;
                let g = section.g;
                let y = -g * x + section.state[section.pos];
                // Shift the delay: the sample that just left is replaced by x + g*y
                // after the remaining samples move toward the read point.
                // State is a ring: index `pos` is the oldest sample (zi[0]).
                section.state[section.pos] = x + g * y;
                section.pos += 1;
                if section.pos == d {
                    section.pos = 0;
                }
                x = y;
            }
            output[i] = x;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn impulse_matches_numpy_product_of_gains() {
        let mut d = Decorrelator::new(48_000, 20_261_019);
        let mut y = [0.0; 8];
        d.process(&[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], &mut y);
        assert!((y[0] - 0.098_928_488_312_898_2).abs() < 1e-12, "{}", y[0]);
    }

    #[test]
    fn blocks_match_one_shot() {
        let x: Vec<f64> = (0..300).map(|i| (i as f64 * 0.13).sin()).collect();
        let mut a = Decorrelator::new(48_000, 20_261_016);
        let mut b = Decorrelator::new(48_000, 20_261_016);
        let mut whole = vec![0.0; x.len()];
        let mut parts = vec![0.0; x.len()];
        a.process(&x, &mut whole);
        b.process(&x[..32], &mut parts[..32]);
        b.process(&x[32..], &mut parts[32..]);
        for i in 0..x.len() {
            assert!((whole[i] - parts[i]).abs() < 1e-12);
        }
    }
}
