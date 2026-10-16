//! NumPy `Generator` / PCG64 / SeedSequence, enough for the P0 prototype.
//!
//! Delay lines, the FDN feedback matrix, and the decorrelators are drawn with
//! `numpy.random.default_rng(seed)`. Matching that stream keeps the reverb and
//! allpasses aligned with the prototype.

use crate::ziggurat_tables::{FI, KI, NOR_INV_R, NOR_R, WI};

const INIT_A: u32 = 0x43b0_d7e5;
const MULT_A: u32 = 0x931e_8875;
const INIT_B: u32 = 0x8b51_f9dd;
const MULT_B: u32 = 0x58f3_8ded;
const MIX_MULT_L: u32 = 0xca01_f9dd;
const MIX_MULT_R: u32 = 0x4973_f715;
const XSHIFT: u32 = 16;

const PCG_MULT_HIGH: u64 = 2_549_297_995_355_413_924;
const PCG_MULT_LOW: u64 = 4_865_540_595_714_422_341;

fn hashmix(mut value: u32, hash_const: &mut u32) -> u32 {
    value ^= *hash_const;
    *hash_const = hash_const.wrapping_mul(MULT_A);
    value = value.wrapping_mul(*hash_const);
    value ^= value >> XSHIFT;
    value
}

fn mix(x: u32, y: u32) -> u32 {
    let result = MIX_MULT_L
        .wrapping_mul(x)
        .wrapping_sub(MIX_MULT_R.wrapping_mul(y));
    result ^ (result >> XSHIFT)
}

/// NumPy `SeedSequence` for a single non-negative integer and an empty spawn key.
struct SeedSequence {
    pool: [u32; 4],
}

impl SeedSequence {
    fn new(entropy: u64) -> Self {
        let mut words = Vec::new();
        if entropy == 0 {
            words.push(0u32);
        } else {
            let mut n = entropy;
            while n > 0 {
                words.push((n & 0xffff_ffff) as u32);
                n >>= 32;
            }
        }
        let mut pool = [0u32; 4];
        let mut hash_const = INIT_A;
        for i in 0..4 {
            let value = if i < words.len() { words[i] } else { 0 };
            pool[i] = hashmix(value, &mut hash_const);
        }
        for i_src in 0..4 {
            for i_dst in 0..4 {
                if i_src != i_dst {
                    let mixed = hashmix(pool[i_src], &mut hash_const);
                    pool[i_dst] = mix(pool[i_dst], mixed);
                }
            }
        }
        for value in words.iter().skip(4) {
            for i_dst in 0..4 {
                let mixed = hashmix(*value, &mut hash_const);
                pool[i_dst] = mix(pool[i_dst], mixed);
            }
        }
        Self { pool }
    }

    fn generate_u64(&self, n_words: usize) -> Vec<u64> {
        let mut hash_const = INIT_B;
        let n32 = n_words * 2;
        let mut state = vec![0u32; n32];
        for (i_dst, slot) in state.iter_mut().enumerate() {
            let mut data = self.pool[i_dst % 4];
            data ^= hash_const;
            hash_const = hash_const.wrapping_mul(MULT_B);
            data = data.wrapping_mul(hash_const);
            data ^= data >> XSHIFT;
            *slot = data;
        }
        let (chunks, _) = state.as_chunks::<2>();
        chunks
            .iter()
            .map(|c| u64::from(c[0]) | (u64::from(c[1]) << 32))
            .collect()
    }
}

/// NumPy `numpy.random.Generator` backed by PCG XSL RR 128/64.
#[derive(Clone)]
pub struct NumpyGenerator {
    state: u128,
    inc: u128,
    has_u32: bool,
    cached_u32: u32,
}

impl NumpyGenerator {
    pub fn new(seed: u64) -> Self {
        let seq = SeedSequence::new(seed);
        let words = seq.generate_u64(4);
        let init_state = (u128::from(words[0]) << 64) | u128::from(words[1]);
        let init_seq = (u128::from(words[2]) << 64) | u128::from(words[3]);
        let mut rng = Self {
            state: 0,
            inc: (init_seq << 1) | 1,
            has_u32: false,
            cached_u32: 0,
        };
        rng.step();
        rng.state = rng.state.wrapping_add(init_state);
        rng.step();
        rng
    }

    fn step(&mut self) {
        let mult = (u128::from(PCG_MULT_HIGH) << 64) | u128::from(PCG_MULT_LOW);
        self.state = self.state.wrapping_mul(mult).wrapping_add(self.inc);
    }

    pub fn next_u64(&mut self) -> u64 {
        self.step();
        let high = (self.state >> 64) as u64;
        let low = self.state as u64;
        let rot = (high >> 58) as u32;
        (high ^ low).rotate_right(rot)
    }

    fn next_u32(&mut self) -> u32 {
        if self.has_u32 {
            self.has_u32 = false;
            self.cached_u32
        } else {
            let next = self.next_u64();
            self.has_u32 = true;
            self.cached_u32 = (next >> 32) as u32;
            next as u32
        }
    }

    pub fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
    }

    /// NumPy `Generator.uniform` scalar, half-open in the continuous sense.
    pub fn uniform(&mut self, low: f64, high: f64) -> f64 {
        low + (high - low) * self.next_f64()
    }

    /// NumPy `Generator.integers(low, high)` scalar, half-open `[low, high)`.
    pub fn integers(&mut self, low: i64, high: i64) -> i64 {
        debug_assert!(high > low);
        let span = (high - low) as u64;
        let rng = span - 1;
        low + self.bounded_u32(rng as u32) as i64
    }

    fn bounded_u32(&mut self, rng: u32) -> u32 {
        if rng == 0 {
            return 0;
        }
        if rng == u32::MAX {
            return self.next_u32();
        }
        let rng_excl = rng + 1;
        let mut m = u64::from(self.next_u32()) * u64::from(rng_excl);
        let mut leftover = m as u32;
        if leftover < rng_excl {
            let threshold = u32::MAX.wrapping_sub(rng) % rng_excl;
            while leftover < threshold {
                m = u64::from(self.next_u32()) * u64::from(rng_excl);
                leftover = m as u32;
            }
        }
        (m >> 32) as u32
    }

    /// NumPy `Generator.standard_normal` via the Ziggurat method.
    pub fn standard_normal(&mut self) -> f64 {
        loop {
            let r0 = self.next_u64();
            let idx = (r0 & 0xff) as usize;
            let r = r0 >> 8;
            let sign = (r & 1) != 0;
            let rabs = (r >> 1) & 0x000f_ffff_ffff_ffff;
            let mut x = rabs as f64 * WI[idx];
            if sign {
                x = -x;
            }
            if rabs < KI[idx] {
                return x;
            }
            if idx == 0 {
                loop {
                    let xx = -NOR_INV_R * (-self.next_f64()).ln_1p();
                    let yy = -(-self.next_f64()).ln_1p();
                    if yy + yy > xx * xx {
                        let mag = NOR_R + xx;
                        return if (rabs >> 8) & 1 != 0 { -mag } else { mag };
                    }
                }
            } else {
                let edge = (FI[idx - 1] - FI[idx]) * self.next_f64() + FI[idx];
                if edge < (-0.5 * x * x).exp() {
                    return x;
                }
            }
        }
    }
}

/// NumPy-compatible round-half-to-even, used for FDN delay lengths.
pub fn numpy_round(x: f64) -> f64 {
    let floor = x.floor();
    let diff = x - floor;
    let even = (floor as i64) % 2 == 0;
    if diff > 0.5 || (diff == 0.5 && !even) {
        floor + 1.0
    } else {
        floor
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pcg64_matches_numpy_raw_stream() {
        let mut rng = NumpyGenerator::new(20_261_016);
        let got = [
            rng.next_u64(),
            rng.next_u64(),
            rng.next_u64(),
            rng.next_u64(),
            rng.next_u64(),
            rng.next_u64(),
        ];
        let expect = [
            6_366_799_204_154_583_462,
            10_269_578_466_516_699_864,
            11_543_551_414_707_274_759,
            9_178_136_229_106_210_744,
            13_330_838_688_014_461_827,
            4_736_178_510_020_202_444,
        ];
        assert_eq!(got, expect);
    }

    #[test]
    fn uniform_integers_and_normal_match_numpy() {
        let mut rng = NumpyGenerator::new(20_261_016);
        let mut delays = [0.0; 8];
        for d in &mut delays {
            *d = rng.uniform(20.0, 85.0);
        }
        assert!((delays[0] - 42.434_416_969_000_985).abs() < 1e-12);
        assert!((delays[4] - 66.973_303_866_447_04).abs() < 1e-9);
        assert!((delays[7] - 55.747_251_641_021_32).abs() < 1e-9);

        let mut rng = NumpyGenerator::new(20_261_016);
        for _ in 0..8 {
            let _ = rng.uniform(20.0, 85.0);
        }
        let n0 = rng.standard_normal();
        assert!((n0 + 0.862_679_277_416_734_8).abs() < 1e-12);

        let mut rng = NumpyGenerator::new(20_261_019);
        let d0 = rng.integers(96, 528);
        let g0 = rng.uniform(0.4, 0.7);
        assert_eq!(d0, 243);
        assert!((g0 - 0.621_522_179_452_677_7).abs() < 1e-12);
    }
}
