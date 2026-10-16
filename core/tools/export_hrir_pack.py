#!/usr/bin/env python3
"""Turn a SOFA file into an Aurora HRIR pack.

The pack stores, per direction: azimuth, elevation, fractional ITD, a
minimum-phase HRIR, and the equalized time-domain HRIR the route B chain
convolves. The source file's SHA-256 is recorded. The Rust loader checks
that hash and refuses to fall back to any other dataset.

Equalization is the P0 prototype's frontal filter plus directional power
flattening, so the render HRIRs match ``apply_equalization``.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from prototypes.aurora_p0.hrtf import apply_equalization, load_sofa  # noqa: E402


def sha256_file(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.digest()


def minphase_and_itd(hrir: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Minimum-phase rebuild plus the lag that best aligns it to ``hrir``."""
    n_taps = hrir.shape[1]
    nfft = 1 << int(np.ceil(np.log2(max(2048, n_taps * 4))))
    spec = np.fft.rfft(hrir, n=nfft, axis=1)
    log_mag = np.log(np.maximum(np.abs(spec), 1e-8))
    cep = np.fft.irfft(log_mag, n=nfft, axis=1)
    cep[:, 1 : nfft // 2] *= 2.0
    cep[:, nfft // 2 + 1 :] = 0.0
    minimum = np.fft.irfft(np.exp(np.fft.rfft(cep, axis=1)), n=nfft, axis=1)[:, :n_taps]
    corr = np.fft.irfft(spec * np.conj(np.fft.rfft(minimum, n=nfft, axis=1)), n=nfft, axis=1)
    lag = np.argmax(np.abs(corr), axis=1).astype(np.float64)
    lag = np.where(lag > nfft / 2, lag - nfft, lag)
    return minimum, lag


def write_pack(
    path: Path,
    sample_rate: int,
    eq_mode: str,
    source_sha: bytes,
    azimuth: np.ndarray,
    elevation: np.ndarray,
    itd_l: np.ndarray,
    itd_r: np.ndarray,
    min_l: np.ndarray,
    min_r: np.ndarray,
    render_l: np.ndarray,
    render_r: np.ndarray,
) -> None:
    n_dirs, n_taps = render_l.shape
    eq = eq_mode.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"AHRP")
        handle.write(struct.pack("<IIII", 1, sample_rate, n_dirs, n_taps))
        handle.write(struct.pack("<I", len(eq)))
        handle.write(eq)
        handle.write(source_sha)
        for i in range(n_dirs):
            handle.write(
                struct.pack(
                    "<dddd",
                    float(azimuth[i]),
                    float(elevation[i]),
                    float(itd_l[i]),
                    float(itd_r[i]),
                )
            )
            for bank in (min_l[i], min_r[i], render_l[i], render_r[i]):
                handle.write(np.asarray(bank, dtype="<f4").tobytes())


def export_one(source: Path, dest: Path, name: str) -> None:
    print(f"loading {source}", flush=True)
    raw = load_sofa(source, name=name, target_sr=48_000, n_taps=256)
    eq = apply_equalization(raw, mode="frontal", directional_power=True)
    print(f"min-phase {eq.hrir_l.shape}", flush=True)
    min_l, itd_l = minphase_and_itd(eq.hrir_l)
    min_r, itd_r = minphase_and_itd(eq.hrir_r)
    digest = sha256_file(source)
    write_pack(
        dest,
        eq.sr,
        eq.eq_mode,
        digest,
        eq.azimuths,
        eq.elevations,
        itd_l,
        itd_r,
        min_l,
        min_r,
        eq.hrir_l,
        eq.hrir_r,
    )
    print(f"wrote {dest} ({dest.stat().st_size} bytes) sha256 {digest.hex()}", flush=True)


def main() -> None:
    jobs = [
        (
            ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa",
            ROOT / "data/hrtf/ku100_frontal.ahrp",
            "ku100",
        ),
        (
            ROOT / "data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa",
            ROOT / "data/hrtf/sadie_d1_frontal.ahrp",
            "sadie",
        ),
    ]
    if len(sys.argv) > 1:
        wanted = set(sys.argv[1:])
        jobs = [job for job in jobs if job[2] in wanted]
    for source, dest, name in jobs:
        export_one(source, dest, name)


if __name__ == "__main__":
    main()
