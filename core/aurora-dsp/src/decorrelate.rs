//! Seeded interaural delay.
//!
//! Matches `prototypes/aurora_p0/upmix.py` `decorrelate`. The delay table
//! comes from the preset (`engine.decorrelator_delays_ms`). The seed picks
//! the tap and the sign. Magnitude is unity. `process` does not allocate.

const MAX_DELAY: usize = 2048;

pub struct Decorrelator {
    delay: usize,
    sign: f64,
    state: Vec<f64>,
    pos: usize,
}

impl Decorrelator {
    pub fn new(sample_rate: u32, seed: u64, delays_ms: &[f64]) -> Self {
        if delays_ms.is_empty() {
            return Self {
                delay: 0,
                sign: 1.0,
                state: Vec::new(),
                pos: 0,
            };
        }
        let ms = delays_ms[(seed % delays_ms.len() as u64) as usize];
        let delay = (ms * 1e-3 * f64::from(sample_rate))
            .round()
            .clamp(0.0, MAX_DELAY as f64) as usize;
        let sign = if (seed / 7) % 2 == 0 { -1.0 } else { 1.0 };
        Self {
            delay,
            sign,
            state: vec![0.0; delay],
            pos: 0,
        }
    }

    pub fn reset(&mut self) {
        self.state.fill(0.0);
        self.pos = 0;
    }

    pub fn process(&mut self, input: &[f64], output: &mut [f64]) {
        let n = input.len().min(output.len());
        if self.delay == 0 {
            for i in 0..n {
                let x = if input[i].is_finite() { input[i] } else { 0.0 };
                output[i] = x * self.sign;
            }
            return;
        }
        for i in 0..n {
            let x = if input[i].is_finite() { input[i] } else { 0.0 };
            let y = self.state[self.pos];
            self.state[self.pos] = x;
            self.pos += 1;
            if self.pos == self.delay {
                self.pos = 0;
            }
            output[i] = y * self.sign;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DELAYS_MS: [f64; 12] = [
        3.2, 6.4, 10.0, 13.5, 4.4, 7.6, 11.2, 14.8, 3.8, 6.0, 9.2, 12.4,
    ];

    #[test]
    fn side_seed_matches_prototype_tap() {
        // seed 20261016 + 11 → delays[11] = 12.4 ms → 595 samples, negative.
        let mut d = Decorrelator::new(48_000, 20_261_027, &DELAYS_MS);
        let mut x = vec![0.0; 800];
        x[0] = 1.0;
        let mut y = vec![0.0; 800];
        d.process(&x, &mut y);
        let hit = y.iter().position(|s| s.abs() > 0.5).unwrap();
        assert_eq!(hit, 595);
        assert!((y[595] - (-1.0)).abs() < 1e-12);
    }

    #[test]
    fn blocks_match_one_shot() {
        let x: Vec<f64> = (0..300).map(|i| (i as f64 * 0.13).sin()).collect();
        let mut a = Decorrelator::new(48_000, 20_261_016, &DELAYS_MS);
        let mut b = Decorrelator::new(48_000, 20_261_016, &DELAYS_MS);
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
