//! Zero-phase raised-cosine fade on the side feed, as a linear-phase FIR.
//!
//! The prototype multiplies the whole-file spectrum by
//! `1 - clip((f - (hz-500)) / 1000, 0, 1)`. That multiply is zero phase and
//! depends on the file length. A symmetric FIR with the same magnitude,
//! delayed by `(taps-1)/2` and compensated in the side alignment delay, is
//! block-size invariant and matches the passband. The cutoff is a preset
//! constant. Below 1 kHz the prototype leaves the side feed alone.

use num_complex::Complex;
use realfft::RealFftPlanner;

pub const SIDE_TAPS: usize = 1025;

pub fn design_side_lpf(sample_rate: f64, hz: f64) -> Option<(Vec<f64>, usize)> {
    if hz <= 1000.0 {
        return None;
    }
    let nfft = 8192usize;
    let mut planner = RealFftPlanner::<f64>::new();
    let inverse = planner.plan_fft_inverse(nfft);
    let mut spec = inverse.make_input_vec();
    let mut time = inverse.make_output_vec();
    let bins = nfft / 2 + 1;
    let lo = hz - 500.0;
    for k in 0..bins {
        let freq = k as f64 * sample_rate / nfft as f64;
        let weight = 1.0 - ((freq - lo) / 1000.0).clamp(0.0, 1.0);
        spec[k] = Complex::new(weight, 0.0);
    }
    let mut scratch = inverse.make_scratch_vec();
    inverse
        .process_with_scratch(&mut spec, &mut time, &mut scratch)
        .expect("side lpf ifft");
    let scale = 1.0 / nfft as f64;
    let half = SIDE_TAPS / 2;
    let mut fir = vec![0.0; SIDE_TAPS];
    for i in 0..SIDE_TAPS {
        let src = if i >= half {
            i - half
        } else {
            nfft - half + i
        };
        fir[i] = time[src] * scale;
    }
    let sum: f64 = fir.iter().sum();
    if sum.abs() > 1e-12 {
        for c in &mut fir {
            *c /= sum;
        }
    }
    Some((fir, half))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn magnitude_at(fir: &[f64], sample_rate: f64, freq: f64) -> f64 {
        let mut acc = Complex::new(0.0, 0.0);
        for (n, c) in fir.iter().enumerate() {
            let ang = -2.0 * std::f64::consts::PI * freq * n as f64 / sample_rate;
            let (s, coss) = ang.sin_cos();
            acc += *c * Complex::new(coss, s);
        }
        acc.norm()
    }

    #[test]
    fn room_side_fade_hits_the_prototype_shape() {
        let (fir, delay) = design_side_lpf(48_000.0, 6000.0).unwrap();
        assert_eq!(delay, 512);
        assert_eq!(fir.len(), SIDE_TAPS);
        let dc = magnitude_at(&fir, 48_000.0, 0.0);
        let mid = magnitude_at(&fir, 48_000.0, 6000.0);
        let stop = magnitude_at(&fir, 48_000.0, 8000.0);
        assert!((dc - 1.0).abs() < 1e-6, "dc {dc}");
        assert!((mid - 0.5).abs() < 0.02, "6 kHz {mid}");
        assert!(stop < 0.02, "8 kHz {stop}");
        // Symmetric, so the group delay is exactly the center tap.
        assert!((fir[0] - fir[fir.len() - 1]).abs() < 1e-12);
    }

    #[test]
    fn below_1k_is_absent() {
        assert!(design_side_lpf(48_000.0, 800.0).is_none());
    }
}
