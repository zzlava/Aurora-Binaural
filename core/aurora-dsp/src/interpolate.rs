//! Direction interpolation.
//!
//! Time-domain inverse-distance weighting matches the P0 prototype's
//! `interpolate_hrir`. The plan's minimum-phase path interpolates magnitudes
//! of the stored minimum-phase HRIRs and applies ITD as a fractional delay.
//! Direction changes crossfade in the convolver, not inside this function.

use crate::coords::angular_distance_deg;
use crate::hrir::HrirPack;

pub fn idw_render(
    pack: &HrirPack,
    azimuth: f64,
    elevation: f64,
    k: usize,
    out_l: &mut [f64],
    out_r: &mut [f64],
) {
    let taps = out_l.len().min(out_r.len());
    out_l[..taps].fill(0.0);
    out_r[..taps].fill(0.0);
    let (idx, w) = nearest_weights(pack, azimuth, elevation, k);
    for (i, weight) in idx.into_iter().zip(w) {
        let dir = &pack.dirs[i];
        let n = taps.min(dir.render_l.len());
        for t in 0..n {
            out_l[t] += f64::from(dir.render_l[t]) * weight;
            out_r[t] += f64::from(dir.render_r[t]) * weight;
        }
    }
}

/// Inverse-distance weighting of minimum-phase magnitudes, then a fractional ITD.
///
/// `out_*` receives the delayed minimum-phase impulse. Spectra are rebuilt with
/// the caller-provided real FFT workspace (`nfft` power of two, `nfft >= taps`).
#[allow(clippy::too_many_arguments)]
pub fn idw_minphase(
    pack: &HrirPack,
    azimuth: f64,
    elevation: f64,
    k: usize,
    nfft: usize,
    time: &mut [f64],
    mag_acc_l: &mut [f64],
    mag_acc_r: &mut [f64],
    spec_re: &mut [f64],
    spec_im: &mut [f64],
    out_l: &mut [f64],
    out_r: &mut [f64],
) {
    let taps = out_l.len().min(out_r.len()).min(pack.n_taps());
    let bins = nfft / 2 + 1;
    mag_acc_l[..bins].fill(0.0);
    mag_acc_r[..bins].fill(0.0);
    let (idx, w) = nearest_weights(pack, azimuth, elevation, k);
    let mut itd_l = 0.0;
    let mut itd_r = 0.0;
    for (i, weight) in idx.iter().zip(w.iter()) {
        let dir = &pack.dirs[*i];
        accumulate_mag(&dir.minphase_l, time, spec_re, spec_im, nfft);
        for b in 0..bins {
            let mag = (spec_re[b] * spec_re[b] + spec_im[b] * spec_im[b]).sqrt();
            mag_acc_l[b] += mag * weight;
        }
        accumulate_mag(&dir.minphase_r, time, spec_re, spec_im, nfft);
        for b in 0..bins {
            let mag = (spec_re[b] * spec_re[b] + spec_im[b] * spec_im[b]).sqrt();
            mag_acc_r[b] += mag * weight;
        }
        itd_l += dir.itd_left * weight;
        itd_r += dir.itd_right * weight;
    }
    minphase_from_mag(&mag_acc_l[..bins], time, spec_re, spec_im, nfft);
    fractional_delay(&time[..taps], itd_l, &mut out_l[..taps]);
    minphase_from_mag(&mag_acc_r[..bins], time, spec_re, spec_im, nfft);
    fractional_delay(&time[..taps], itd_r, &mut out_r[..taps]);
}

fn nearest_weights(
    pack: &HrirPack,
    azimuth: f64,
    elevation: f64,
    k: usize,
) -> (Vec<usize>, Vec<f64>) {
    let k = k.max(1).min(pack.dirs.len());
    let mut order: Vec<usize> = (0..pack.dirs.len()).collect();
    order.sort_by(|&a, &b| {
        let da = angular_distance_deg(
            pack.dirs[a].azimuth_deg,
            pack.dirs[a].elevation_deg,
            azimuth,
            elevation,
        );
        let db = angular_distance_deg(
            pack.dirs[b].azimuth_deg,
            pack.dirs[b].elevation_deg,
            azimuth,
            elevation,
        );
        // Equal distances are a real tie on this grid (22.5° is exactly as
        // far from 21° as from 24°). NumPy's default argsort keeps the higher
        // index; a lower-index tie-break drops the other neighbour and the
        // k=3 set no longer matches the prototype. Differences this small
        // are the tie itself — 22° versus 23° still differ by ~1e-12°.
        if da.is_nan() || db.is_nan() || (da - db).abs() <= 1e-13 {
            b.cmp(&a)
        } else {
            da.total_cmp(&db)
        }
    });
    order.truncate(k);
    let mut w = Vec::with_capacity(k);
    let mut sum = 0.0;
    for &i in &order {
        let d = angular_distance_deg(
            pack.dirs[i].azimuth_deg,
            pack.dirs[i].elevation_deg,
            azimuth,
            elevation,
        );
        let weight = 1.0 / d.max(1e-3);
        w.push(weight);
        sum += weight;
    }
    for weight in &mut w {
        *weight /= sum;
    }
    (order, w)
}

fn accumulate_mag(hrir: &[f32], time: &mut [f64], re: &mut [f64], im: &mut [f64], nfft: usize) {
    time[..nfft].fill(0.0);
    for (i, s) in hrir.iter().enumerate().take(nfft) {
        time[i] = f64::from(*s);
    }
    rust_rfft(time, re, im, nfft);
}

fn minphase_from_mag(mag: &[f64], time: &mut [f64], re: &mut [f64], im: &mut [f64], nfft: usize) {
    let bins = nfft / 2 + 1;
    for b in 0..bins {
        re[b] = mag[b].max(1e-8).ln();
        im[b] = 0.0;
    }
    rust_irfft(re, im, time, nfft);
    // Real cepstrum fold: keep DC and Nyquist, double the causal half.
    for i in 1..nfft / 2 {
        time[i] *= 2.0;
    }
    for i in nfft / 2 + 1..nfft {
        time[i] = 0.0;
    }
    rust_rfft(time, re, im, nfft);
    for b in 0..bins {
        let e = re[b].exp();
        // cepstrum of log-mag is real; the reconstructed spectrum is exp(c) with phase in im.
        let (s, c) = im[b].sin_cos();
        re[b] = e * c;
        im[b] = e * s;
    }
    rust_irfft(re, im, time, nfft);
}

/// 4-point Lagrange fractional delay. `delay` is in samples and may be fractional.
pub fn fractional_delay(input: &[f64], delay: f64, output: &mut [f64]) {
    let n = input.len().min(output.len());
    output[..n].fill(0.0);
    if delay <= -1.0 {
        return;
    }
    let d = delay.max(0.0);
    let base = d.floor() as usize;
    let f = d - base as f64;
    for i in 0..n {
        let src = i as isize - base as isize;
        output[i] = lagrange4(input, src, f);
    }
}

fn lagrange4(input: &[f64], at: isize, frac: f64) -> f64 {
    // Interpolate at position `at - frac` using samples at-1, at, at+1, at+2?
    // We want the value of the continuous signal at index `i - delay` =
    // `i - base - frac` = (i - base) - frac. `at` is i-base, read x[at - frac].
    let f = frac;
    let x0 = sample(input, at - 1);
    let x1 = sample(input, at);
    let x2 = sample(input, at + 1);
    let x3 = sample(input, at + 2);
    // 4-point Lagrange on offsets -1,0,1,2 evaluated at -f (between 0 and -1 if f in 0..1,
    // i.e. just before sample `at`).
    let p = -f;
    let l0 = (p - 0.0) * (p - 1.0) * (p - 2.0) / ((-1.0 - 0.0) * (-1.0 - 1.0) * (-1.0 - 2.0));
    let l1 = (p + 1.0) * (p - 1.0) * (p - 2.0) / ((0.0 + 1.0) * (0.0 - 1.0) * (0.0 - 2.0));
    let l2 = (p + 1.0) * (p - 0.0) * (p - 2.0) / ((1.0 + 1.0) * (1.0 - 0.0) * (1.0 - 2.0));
    let l3 = (p + 1.0) * (p - 0.0) * (p - 1.0) / ((2.0 + 1.0) * (2.0 - 0.0) * (2.0 - 1.0));
    l0 * x0 + l1 * x1 + l2 * x2 + l3 * x3
}

fn sample(input: &[f64], i: isize) -> f64 {
    if i < 0 || i as usize >= input.len() {
        0.0
    } else {
        input[i as usize]
    }
}

/// In-place-ish radix-2 DIT real FFT written as a full complex FFT on a zero-imag buffer.
/// Not the fastest path; the streaming upmix uses `realfft`. This helper stays
/// allocation-free for the interpolator tests and for designing a minimum-phase IR.
pub fn rust_rfft(time: &[f64], re: &mut [f64], im: &mut [f64], nfft: usize) {
    let bins = nfft / 2 + 1;
    for k in 0..bins {
        let mut sr = 0.0;
        let mut si = 0.0;
        let step = -2.0 * std::f64::consts::PI * k as f64 / nfft as f64;
        for (n, x) in time.iter().take(nfft).enumerate() {
            let ang = step * n as f64;
            let (s, c) = ang.sin_cos();
            sr += x * c;
            si += x * s;
        }
        re[k] = sr;
        im[k] = si;
    }
}

pub fn rust_irfft(re: &[f64], im: &[f64], time: &mut [f64], nfft: usize) {
    let bins = nfft / 2 + 1;
    for n in 0..nfft {
        let mut acc = re[0];
        let step = 2.0 * std::f64::consts::PI * n as f64 / nfft as f64;
        for k in 1..bins - 1 {
            let ang = step * k as f64;
            let (s, c) = ang.sin_cos();
            acc += 2.0 * (re[k] * c - im[k] * s);
        }
        if nfft.is_multiple_of(2) {
            let ang = step * (bins - 1) as f64;
            acc += re[bins - 1] * ang.cos() - im[bins - 1] * ang.sin();
        }
        time[n] = acc / nfft as f64;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fractional_delay_of_an_impulse_peaks_at_the_delay() {
        let mut input = vec![0.0; 64];
        input[0] = 1.0;
        let mut out = vec![0.0; 64];
        fractional_delay(&input, 3.0, &mut out);
        assert!((out[3] - 1.0).abs() < 1e-9, "{}", out[3]);
        fractional_delay(&input, 3.25, &mut out);
        let peak_at = out
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
            .map(|(i, _)| i)
            .unwrap();
        assert!(
            peak_at == 3 || peak_at == 4,
            "peak at {peak_at} {:?}",
            &out[..8]
        );
        assert!(out[3] > 0.2 && out[4] > 0.05, "{:?}", &out[..8]);
    }

    #[test]
    fn idw_on_a_grid_point_returns_that_hrir() {
        let pack = HrirPack {
            sample_rate: 48_000,
            eq_mode: "frontal+power".into(),
            source_sha256: [0; 32],
            dirs: vec![
                dir(0.0, &[1.0, 0.0, 0.0, 0.0], &[0.5, 0.0, 0.0, 0.0]),
                dir(90.0, &[0.0, 1.0, 0.0, 0.0], &[0.0, 0.2, 0.0, 0.0]),
                dir(-90.0, &[0.0, 0.0, 1.0, 0.0], &[0.0, 0.0, 0.3, 0.0]),
            ],
        };
        let mut l = [0.0; 4];
        let mut r = [0.0; 4];
        idw_render(&pack, 90.0, 0.0, 3, &mut l, &mut r);
        // Distance is floored at 1e-3, so an exact grid point still takes a
        // little weight from the other neighbours, same as the prototype.
        assert!((l[1] - 1.0).abs() < 1e-3, "{l:?}");
        assert!((r[1] - 0.2).abs() < 1e-3, "{r:?}");
        assert!(l[1] > l[0] && l[1] > l[2]);
    }

    #[test]
    fn tie_at_22_5_keeps_the_higher_index_neighbour() {
        // 22.5° is equidistant from 21° and 24°. NumPy keeps 24° (the higher
        // index). The first sample of each HRIR is a marker.
        let pack = HrirPack {
            sample_rate: 48_000,
            eq_mode: "frontal+power".into(),
            source_sha256: [0; 32],
            dirs: vec![
                marker(21.0, 21.0),
                marker(22.0, 22.0),
                marker(23.0, 23.0),
                marker(24.0, 24.0),
            ],
        };
        let mut l = [0.0; 1];
        let mut r = [0.0; 1];
        idw_render(&pack, 22.5, 0.0, 3, &mut l, &mut r);
        let d22 = crate::coords::angular_distance_deg(22.0, 0.0, 22.5, 0.0);
        let d23 = crate::coords::angular_distance_deg(23.0, 0.0, 22.5, 0.0);
        let d24 = crate::coords::angular_distance_deg(24.0, 0.0, 22.5, 0.0);
        let w22 = 1.0 / d22;
        let w23 = 1.0 / d23;
        let w24 = 1.0 / d24;
        let sum = w22 + w23 + w24;
        let expect = (22.0 * w22 + 23.0 * w23 + 24.0 * w24) / sum;
        assert!(
            (l[0] - expect).abs() < 1e-9,
            "got {} expect {expect} (a 21° neighbour would not land here)",
            l[0]
        );
    }

    #[test]
    fn minphase_interpolation_stays_finite() {
        let pack = HrirPack {
            sample_rate: 48_000,
            eq_mode: "frontal+power".into(),
            source_sha256: [0; 32],
            dirs: vec![dir(90.0, &[0.0, 1.0, 0.0, 0.0], &[0.0, 0.2, 0.0, 0.0])],
        };
        let nfft = 16;
        let bins = nfft / 2 + 1;
        let mut time = vec![0.0; nfft];
        let mut mag_l = vec![0.0; bins];
        let mut mag_r = vec![0.0; bins];
        let mut re = vec![0.0; bins];
        let mut im = vec![0.0; bins];
        let mut out_l = [0.0; 4];
        let mut out_r = [0.0; 4];
        idw_minphase(
            &pack, 90.0, 0.0, 1, nfft, &mut time, &mut mag_l, &mut mag_r, &mut re, &mut im,
            &mut out_l, &mut out_r,
        );
        assert!(out_l.iter().chain(out_r.iter()).all(|v| v.is_finite()));
        let energy: f64 = out_l.iter().map(|v| v * v).sum();
        assert!(energy > 1e-8, "{energy} {out_l:?}");
    }

    fn marker(az: f64, tag: f64) -> crate::hrir::Direction {
        dir(az, &[tag], &[tag])
    }

    fn dir(az: f64, l: &[f64], r: &[f64]) -> crate::hrir::Direction {
        crate::hrir::Direction {
            azimuth_deg: az,
            elevation_deg: 0.0,
            itd_left: 0.0,
            itd_right: 0.0,
            minphase_l: l.iter().map(|v| *v as f32).collect(),
            minphase_r: r.iter().map(|v| *v as f32).collect(),
            render_l: l.iter().map(|v| *v as f32).collect(),
            render_r: r.iter().map(|v| *v as f32).collect(),
        }
    }
}
