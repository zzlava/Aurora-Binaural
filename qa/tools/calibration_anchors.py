"""Claude 的听感校准锚点（QA 工具，不是产品代码）。

目的：用户反馈 v3 样片“强度最高也和原声差别不大、没有空间感”。这里用几种简单但效果明显的
经典做法生成参照，帮助确定用户想要的“空间感”属于哪一类：
  B 当前引擎（路线 B，强度 1.0）
  C 虚拟音箱 ±30° + 明显的房间混响（头外化）
  D 宽声场包围：±70° 加侧后方延时副本 + 房间
  E 8D 环绕：整体下混后绕头旋转（运动感最明显）
所有版本都按 BS.1770 匹配到 −20 LUFS，并做简单的峰值保护。
用法：python calibration_anchors.py <repo_root> <out_dir>
"""
import sys
from pathlib import Path
import numpy as np, soundfile as sf, sofar, pyloudnorm as pyln
from scipy.signal import fftconvolve, butter, sosfilt

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
SR = 48000; rng = np.random.default_rng(20261017)
sofa = sofar.read_sofa(str(ROOT / "data/hrtf/thk_ku100/HRIR_L2702.sofa"), verbose=False)
IR = np.asarray(sofa.Data_IR)[:, :, :384]; POS = np.asarray(sofa.SourcePosition)

def hrir(az, el=0.0):
    a, e = np.radians(POS[:, 0]), np.radians(POS[:, 1])
    v = np.stack([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)], 1)
    t = np.array([np.cos(np.radians(el)) * np.cos(np.radians(az)), np.cos(np.radians(el)) * np.sin(np.radians(az)), np.sin(np.radians(el))])
    return IR[int(np.argmax(v @ t))]

def place(mono, az, el=0.0):
    h = hrir(az, el); return np.stack([fftconvolve(mono, h[0]), fftconvolve(mono, h[1])], 1)[: len(mono)]

def reverb(stereo, t60=0.9, pre_ms=18):
    n = int(SR * t60 * 1.2); t = np.arange(n) / SR
    env = np.exp(-6.91 * t / t60)
    sos = butter(2, 6000, "low", fs=SR, output="sos")
    irs = [sosfilt(sos, rng.standard_normal(n)) * env for _ in range(2)]
    pre = np.zeros(int(SR * pre_ms / 1000))
    out = np.zeros_like(stereo)
    mid = stereo.mean(1)
    for ch in range(2):
        ir = np.concatenate([pre, irs[ch]]); ir /= np.sqrt(np.sum(ir ** 2))
        out[:, ch] = fftconvolve(mid, ir)[: len(stereo)]
    return out

def early(stereo, taps=((-60, 7, .5), (60, 9, .5), (-120, 13, .35), (120, 15, .35), (180, 21, .25), (0, 25, .2))):
    mid = stereo.mean(1); out = np.zeros_like(stereo)
    for az, ms, g in taps:
        d = int(SR * ms / 1000); sig = np.concatenate([np.zeros(d), mid])[: len(mid)] * g
        out += place(sig, az)
    return out

def mix(*parts):
    return np.sum(parts, axis=0)

def db(x): return 10 ** (x / 20)

def anchor_C(x):  # 虚拟音箱 ±30° + 房间
    direct = place(x[:, 0], 30) + place(x[:, 1], -30)
    return mix(direct, early(x) * db(-3), reverb(x) * db(-6))

def anchor_D(x):  # 宽包围
    d = int(SR * 0.015)
    lag = lambda s: np.concatenate([np.zeros(d), s])[: len(s)]
    direct = place(x[:, 0], 70) + place(x[:, 1], -70)
    rear = place(lag(x[:, 0]), 120) * db(-5) + place(lag(x[:, 1]), -120) * db(-5)
    return mix(direct, rear, early(x) * db(-4), reverb(x, 1.2) * db(-5))

def anchor_E(x, period=8.0):  # 8D 环绕
    mono = x.mean(1); hop = 1024; win = np.hanning(2 * hop + 1)[:-1]
    out = np.zeros((len(mono) + 2 * hop + 400, 2))
    for s in range(0, len(mono), hop):
        seg = mono[s: s + 2 * hop]
        if len(seg) < 2 * hop: seg = np.pad(seg, (0, 2 * hop - len(seg)))
        az = (360.0 * (s / SR) / period) % 360
        h = hrir(az)
        for ch in range(2):
            y = fftconvolve(seg * win, h[ch]); out[s: s + len(y), ch] += y
    out = out[: len(mono)]
    return mix(out, reverb(x, 0.7) * db(-10))

def side_ambient(x):
    """简单的 M/S 上混：侧信号 (L−R)/2 作为环境成分，并用短延时让两路去相关。"""
    s = (x[:, 0] - x[:, 1]) / 2
    d1, d2 = int(SR * 0.011), int(SR * 0.017)
    a = np.concatenate([np.zeros(d1), s])[: len(s)]
    b = -np.concatenate([np.zeros(d2), s])[: len(s)]
    return a, b

def anchor_F(x, front=45):  # C 的头外感，更宽
    direct = place(x[:, 0], front) + place(x[:, 1], -front)
    a, b = side_ambient(x)
    wide = place(a, 90) * db(-3) + place(b, -90) * db(-3)
    return mix(direct, wide, early(x) * db(-3), reverb(x, 1.0) * db(-6))

def anchor_G(x, height_db=-6, el=45):  # F + 头顶音箱（4 个高度声道，仰角 45°）
    base = anchor_F(x)
    a, b = side_ambient(x)
    mid = x.mean(1)
    d = int(SR * 0.009); m2 = np.concatenate([np.zeros(d), mid])[: len(mid)]
    h = (place(a, 45, el) + place(b, -45, el) + place(b, 135, el) + place(a, -135, el)) * db(height_db)
    h += (place(m2, 45, el) + place(m2, -45, el)) * db(height_db - 9)   # 少量中置成分送到前上方，增强“头顶有音箱”的感觉
    ceiling = reverb(np.stack([mid, mid], 1), 1.0, pre_ms=6)                  # 天花板反射
    return mix(base, h, ceiling * db(height_db - 6))

def anchor_H(x):  # G 的加强版：高度更明显、包围更强
    return anchor_G(x, height_db=-2, el=55)

meter = pyln.Meter(SR)
def norm(y):
    y = y * db(-20 - meter.integrated_loudness(y))
    pk = np.max(np.abs(y)); return y * (db(-1) / pk) if pk > db(-1) else y

for cat in ("music", "voice", "ambience"):
    x, sr = sf.read(ROOT / f"data/assets/real_{cat}.wav", always_2d=True); assert sr == SR
    if x.shape[1] == 1: x = np.repeat(x, 2, 1)
    x = x[: SR * 20]
    for tag, fn in (("C_speakers_room", anchor_C), ("F_wider", anchor_F), ("G_wider_height", anchor_G), ("H_wider_height_strong", anchor_H)):
        sf.write(OUT / f"{cat}_{tag}.wav", norm(fn(x)), SR, subtype="PCM_24"); print(cat, tag, flush=True)
