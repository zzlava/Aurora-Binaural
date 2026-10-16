"""Claude 独立验收脚本：P0 样片的客观空间与音色指标。

用法：uv run --no-project --with numpy --with scipy --with soundfile python qa/tools/p0_metrics.py <samples_dir> [--json out.json]
对每个类别（文件名前缀，例如 voice／music／ambience），以 <cat>_dry_*.wav 为原声参照：
- 宽带 IACC（±1 ms 内互相关峰值）
- 分频带 IACC（500 Hz、2 kHz、8 kHz 倍频程）
- 1/3 倍频程能量相对原声的偏差（取 4–16 kHz 的最大绝对值，以及全频段的最大绝对值）
- 积分响度（简化的 K 加权，仅用于检查是否等响度；正式 LUFS 以代理报告为准）、采样峰值、NaN／Inf 检查
"""
from __future__ import annotations
import argparse, glob, json, os, re
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt, correlate

def stereo(x):
    return np.stack([x, x], 1) if x.ndim == 1 else x[:, :2]

def iacc(l, r, fs):
    m = int(fs * 0.001)
    c = correlate(l, r, mode="full", method="fft")
    mid = len(r) - 1
    c = c[mid - m: mid + m + 1] / np.sqrt(np.sum(l * l) * np.sum(r * r) + 1e-30)
    return float(np.max(np.abs(c)))

def band(x, fs, fc):
    lo, hi = fc / np.sqrt(2), min(fc * np.sqrt(2), fs / 2 * 0.99)
    sos = butter(4, [lo, hi], btype="band", fs=fs, output="sos")
    return sosfiltfilt(sos, x, axis=0)

def third_oct_levels(x, fs):
    mono = x.mean(1)
    X = np.abs(np.fft.rfft(mono)) ** 2
    f = np.fft.rfftfreq(len(mono), 1 / fs)
    centers = 1000 * 2 ** (np.arange(-17, 15) / 3)
    out = {}
    for fc in centers:
        if fc * 2 ** (1 / 6) > fs / 2:
            break
        sel = (f >= fc * 2 ** (-1 / 6)) & (f < fc * 2 ** (1 / 6))
        if sel.any():
            out[float(fc)] = 10 * np.log10(np.mean(X[sel]) + 1e-30)
    return out

def metrics(x, fs):
    x = stereo(x)
    return {
        "iacc": iacc(x[:, 0], x[:, 1], fs),
        "iacc_bands": {str(fc): iacc(*band(x, fs, fc).T, fs) for fc in (500, 2000, 8000)},
        "peak_dbfs": float(20 * np.log10(np.max(np.abs(x)) + 1e-30)),
        "rms_dbfs": float(10 * np.log10(np.mean(x ** 2) + 1e-30)),
        "finite": bool(np.isfinite(x).all()),
        "_levels": third_oct_levels(x, fs),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("samples")
    ap.add_argument("--json")
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.samples, "*.wav")))
    cats = sorted({re.split(r"_(?:dry|route)", os.path.basename(f))[0] for f in files})
    report = {}
    for cat in cats:
        dry = [f for f in files if re.match(rf"{cat}_dry", os.path.basename(f))]
        if not dry:
            continue
        d, fs = sf.read(dry[0], always_2d=True)
        md = metrics(d, fs)
        report[cat] = {"dry": {k: v for k, v in md.items() if k != "_levels"}, "renders": {}}
        print(f"\n[{cat}] dry IACC={md['iacc']:.3f} bands={ {k: round(v, 3) for k, v in md['iacc_bands'].items()} }")
        for f in files:
            name = os.path.basename(f)
            if re.split(r"_(?:dry|route)", name)[0] != cat or name in map(os.path.basename, dry):
                continue
            x, fs2 = sf.read(f, always_2d=True)
            mx = metrics(x, fs2)
            dev = {fc: mx["_levels"][fc] - md["_levels"].get(fc, mx["_levels"][fc]) for fc in mx["_levels"]}
            # 去掉整体电平差，只看频响形状
            off = np.median(list(dev.values()))
            dev = {fc: v - off for fc, v in dev.items()}
            hf = [abs(v) for fc, v in dev.items() if 4000 <= fc <= 16000]
            r = {k: v for k, v in mx.items() if k != "_levels"}
            r["tone_dev_max_db"] = float(max(abs(v) for fc, v in dev.items() if 50 <= fc <= 16000))
            r["tone_dev_4k16k_max_db"] = float(max(hf)) if hf else None
            report[cat]["renders"][name] = r
            print(f"  {name[len(cat)+1:-4]:52s} IACC={r['iacc']:.3f} HFdev={r['tone_dev_4k16k_max_db']:.1f}dB "
                  f"dev={r['tone_dev_max_db']:.1f}dB peak={r['peak_dbfs']:.1f} finite={r['finite']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
