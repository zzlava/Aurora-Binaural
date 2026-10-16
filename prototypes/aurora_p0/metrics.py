"""Objective metrics for P0 QA."""

from __future__ import annotations

import numpy as np
from scipy.signal import correlate, butter, sosfilt, spectrogram

from .hrtf import HrtfDataset, render_mono_at
from .loudness import integrate_loudness, true_peak_dbtp


def check_nan_inf(x: np.ndarray) -> dict:
    return {
        "has_nan": bool(np.isnan(x).any()),
        "has_inf": bool(np.isinf(x).any()),
        "clip_rate": float(np.mean(np.abs(x) >= 1.0)),
        "peak": float(np.max(np.abs(x))),
    }


def iacc(stereo: np.ndarray, sr: int, max_lag_ms: float = 1.0) -> float:
    """Inter-aural cross-correlation (max |ρ| within ±1 ms)."""
    l, r = stereo[0], stereo[1]
    l = l - np.mean(l)
    r = r - np.mean(r)
    max_lag = int(max_lag_ms * 1e-3 * sr)
    corr = correlate(l, r, mode="full")
    mid = len(l) - 1
    seg = corr[mid - max_lag : mid + max_lag + 1]
    denom = np.sqrt(np.sum(l**2) * np.sum(r**2)) + 1e-12
    return float(np.max(np.abs(seg)) / denom)


def estimate_itd_ild(stereo: np.ndarray, sr: int) -> tuple[float, float]:
    """ITD in microseconds (positive => left leads), ILD in dB (positive => left louder)."""
    l, r = stereo[0], stereo[1]
    # Band-limit 500–1500 Hz for ITD
    sos = butter(2, [500, 1500], btype="band", fs=sr, output="sos")
    lb = sosfilt(sos, l)
    rb = sosfilt(sos, r)
    corr = correlate(lb, rb, mode="full")
    mid = len(lb) - 1
    max_lag = int(0.001 * sr)  # ±1 ms
    seg = corr[mid - max_lag : mid + max_lag + 1]
    lag = int(np.argmax(seg) - max_lag)
    # numpy correlate(l,r): positive lag ⇒ r leads l. Flip so +ITD ⇒ left leads (az+).
    itd_us = -lag / sr * 1e6
    # ILD broadband energy
    el = np.mean(l**2) + 1e-20
    er = np.mean(r**2) + 1e-20
    ild_db = 10 * np.log10(el / er)
    return float(itd_us), float(ild_db)


def direction_tests(ds: HrtfDataset, sr: int, n: int = 4800) -> list[dict]:
    """Render impulses at standard positions; check ITD/ILD sign."""
    rng = np.random.default_rng(0)
    # short noise burst
    burst = rng.standard_normal(n) * np.hanning(n)
    positions = [
        (0.0, 0.0, "front"),
        (30.0, 0.0, "front_left"),
        (-30.0, 0.0, "front_right"),
        (90.0, 0.0, "left"),
        (-90.0, 0.0, "right"),
        (180.0, 0.0, "back"),
        (0.0, 45.0, "up"),
        (0.0, -45.0, "down"),
    ]
    results = []
    for az, el, name in positions:
        bi = render_mono_at(burst, ds, az, el)
        itd, ild = estimate_itd_ild(bi, sr)
        # Sign expectations (az +left): left positions => ITD>0 and ILD>0
        # Lateral only; front/back/elev use |ITD| smallness checks
        expect_left = 5.0 < az < 175.0
        expect_right = -175.0 < az < -5.0
        itd_ok = True
        ild_ok = True
        if expect_left:
            itd_ok = itd > 0
            ild_ok = ild > 0
        elif expect_right:
            itd_ok = itd < 0
            ild_ok = ild < 0
        elif abs(az) < 5.0 or abs(abs(az) - 180.0) < 5.0:
            itd_ok = abs(itd) < 150.0
        results.append(
            {
                "position": name,
                "azimuth": az,
                "elevation": el,
                "itd_us": itd,
                "ild_db": ild,
                "itd_sign_ok": bool(itd_ok),
                "ild_sign_ok": bool(ild_ok),
            }
        )
    return results


def block_consistency_error(outs: dict[int, np.ndarray], ref_key: int | None = None) -> dict:
    """Max abs diff vs reference block size, in dBFS."""
    keys = sorted(outs.keys())
    ref_key = ref_key or keys[-1]
    ref = outs[ref_key]
    report = {"reference_block": ref_key, "errors_dbfs": {}}
    for k in keys:
        if k == ref_key:
            continue
        a, b = outs[k], ref
        n = min(a.shape[1], b.shape[1])
        diff = a[:, :n] - b[:, :n]
        peak = float(np.max(np.abs(diff)) + 1e-20)
        report["errors_dbfs"][str(k)] = 20 * np.log10(peak)
    return report


def frontal_response_deviation(
    ds: HrtfDataset, sr: int, n_fft: int = 8192
) -> dict:
    """1/3-octave frontal L/R mean response deviation from flat (dB)."""
    # Use equalized frontal HRIR spectrum
    from .hrtf import nearest_hrir

    hl, hr, _ = nearest_hrir(ds, 0.0, 0.0)
    # Average power
    Hl = np.fft.rfft(hl, n=n_fft)
    Hr = np.fft.rfft(hr, n=n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    p = 0.5 * (np.abs(Hl) ** 2 + np.abs(Hr) ** 2)
    # 1/3 octave bands 100–10k
    bands = []
    f = 100.0
    while f < 10000:
        f1, f2 = f / 2 ** (1 / 6), f * 2 ** (1 / 6)
        mask = (freqs >= f1) & (freqs < f2)
        if np.any(mask):
            bands.append((f, float(10 * np.log10(np.mean(p[mask]) + 1e-20))))
        f *= 2 ** (1 / 3)
    # Deviation from band mean
    levels = np.array([b[1] for b in bands])
    levels -= np.mean(levels)
    return {
        "bands_hz": [b[0] for b in bands],
        "deviation_db": levels.tolist(),
        "max_abs_db": float(np.max(np.abs(levels))),
        "within_pm2db": bool(np.max(np.abs(levels)) <= 2.0),
    }


def review_tone_deviation(
    processed: np.ndarray,
    reference: np.ndarray,
    sr: int,
    f_lo: float = 40.0,
    f_hi: float = 16000.0,
) -> dict:
    """1/3-octave deviation used by the P0 review.

    Spectrum is the power of (L+R)/2. One median across 20 Hz–Nyquist is
    removed so an overall level difference is not counted as coloration.
    """
    n = min(processed.shape[1], reference.shape[1])

    def levels(x: np.ndarray) -> dict[float, float]:
        mono = np.mean(np.asarray(x[:, :n], dtype=np.float64), axis=0)
        spec = np.abs(np.fft.rfft(mono)) ** 2
        freqs = np.fft.rfftfreq(mono.shape[0], d=1.0 / sr)
        centers = 1000.0 * (2.0 ** (np.arange(-17, 15) / 3.0))
        out: dict[float, float] = {}
        for fc in centers:
            if fc * 2.0 ** (1.0 / 6.0) >= sr / 2.0:
                break
            if fc < 20.0:
                continue
            sel = (freqs >= fc * 2.0 ** (-1.0 / 6.0)) & (freqs < fc * 2.0 ** (1.0 / 6.0))
            if np.any(sel):
                out[float(fc)] = float(10.0 * np.log10(np.mean(spec[sel]) + 1e-30))
        return out

    wet, dry = levels(processed), levels(reference)
    dev = {fc: wet[fc] - dry[fc] for fc in wet if fc in dry}
    if not dev:
        return {"max_abs_db": float("nan"), "within_pm3db": False, "offset_db": 0.0, "delta_db": []}
    offset = float(np.median(list(dev.values())))
    band = [(fc, v - offset) for fc, v in dev.items() if f_lo <= fc <= f_hi]
    deltas = [v for _, v in band]
    max_abs = float(np.max(np.abs(deltas))) if deltas else float("nan")
    return {
        "centers_hz": [fc for fc, _ in band],
        "delta_db": deltas,
        "offset_db": offset,
        "max_abs_db": max_abs,
        "within_pm3db": bool(deltas and max_abs <= 3.0),
    }


def band_deltas_db(
    processed: np.ndarray,
    reference: np.ndarray,
    sr: int,
    f_lo: float = 4000.0,
    f_hi: float = 16000.0,
) -> dict:
    """1/3-octave power difference (sum of ear powers), processed minus reference, in dB."""

    def levels(x: np.ndarray) -> list[tuple[float, float]]:
        n = min(x.shape[1], reference.shape[1], processed.shape[1])
        spec = np.zeros(n // 2 + 1, dtype=np.float64)
        for ch in range(x.shape[0]):
            spec += np.abs(np.fft.rfft(np.asarray(x[ch, :n], dtype=np.float64))) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)
        out = []
        f = f_lo
        while f < f_hi * 1.01:
            lo, hi = f / 2.0 ** (1.0 / 6.0), f * 2.0 ** (1.0 / 6.0)
            mask = (freqs >= lo) & (freqs < hi)
            lvl = float(10.0 * np.log10(np.mean(spec[mask]) + 1e-20)) if np.any(mask) else float("nan")
            out.append((f, lvl))
            f *= 2.0 ** (1.0 / 3.0)
        return out

    ref = levels(reference)
    pro = levels(processed)
    deltas = [p[1] - r[1] for r, p in zip(ref, pro)]
    return {
        "centers_hz": [r[0] for r in ref],
        "delta_db": deltas,
        "max_abs_db": float(np.max(np.abs(deltas))) if deltas else float("nan"),
        "within_pm3db": bool(deltas and max(abs(v) for v in deltas) <= 3.0),
    }


def lateral_energy_ratio(stereo: np.ndarray) -> float:
    """Energy in (L−R) over total energy. Rises when the image widens."""
    mid = 0.5 * (stereo[0] + stereo[1])
    side = 0.5 * (stereo[0] - stereo[1])
    return float(np.sum(side**2) / (np.sum(mid**2) + np.sum(side**2) + 1e-20))


def sample_metrics(stereo: np.ndarray, sr: int) -> dict:
    safe = check_nan_inf(stereo)
    return {
        **safe,
        "iacc": iacc(stereo, sr),
        "loudness_lufs": integrate_loudness(stereo, sr),
        "true_peak_dbtp": true_peak_dbtp(stereo, sr),
        "itd_us": estimate_itd_ild(stereo, sr)[0],
        "ild_db": estimate_itd_ild(stereo, sr)[1],
        "lateral_energy_ratio": lateral_energy_ratio(stereo),
    }
