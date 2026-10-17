#!/usr/bin/env bash
# AI audio -> 48 kHz WAV -> Aurora -> video + PCM MOV -> decoded sync check.
# Demo: bash examples/ai_video_pipeline.sh --demo outputs/video-demo
# Real: bash examples/ai_video_pipeline.sh video.mp4 ai-voice.wav outputs/video
# Add --server http://127.0.0.1:8000 for the API path (AURORA_API_KEY required).
set -euo pipefail
aurora_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$aurora_root" "$@" <<'PY'
import argparse
import array
import json
import math
import os
import shutil
import subprocess
import sys
import wave
from fractions import Fraction
from pathlib import Path

root = Path(sys.argv[1])
parser = argparse.ArgumentParser(description="Spatialize AI audio and verify the final video's timeline.")
parser.add_argument("video", nargs="?", type=Path)
parser.add_argument("audio", nargs="?", type=Path)
parser.add_argument("directory", nargs="?", type=Path)
parser.add_argument("--demo", type=Path, metavar="DIRECTORY", help="generate synthetic flash/click fixtures")
parser.add_argument("--server", help="use examples/python_client.py instead of the local Rust core")
args = parser.parse_args(sys.argv[2:])
if args.demo:
    if any((args.video, args.audio, args.directory)):
        parser.error("--demo cannot be combined with VIDEO AUDIO DIRECTORY")
    directory = args.demo.resolve()
else:
    if not all((args.video, args.audio, args.directory)):
        parser.error("supply VIDEO AUDIO DIRECTORY or --demo DIRECTORY")
    directory = args.directory.resolve()
    for source in (args.video, args.audio):
        if not source.is_file():
            parser.error(f"input does not exist: {source}")
if directory.exists() and any(directory.iterdir()):
    parser.error("use an empty/new output directory; this example never overwrites an earlier run")
directory.mkdir(parents=True, exist_ok=True)
env = {**os.environ, "AURORA_ENGINE": "rust"}
rate = 48000


def command(argv, *, capture=False):
    print("running " + str(argv[0]), file=sys.stderr)
    return subprocess.run([str(x) for x in argv], check=True, env=env,
                          stdout=subprocess.PIPE if capture else subprocess.DEVNULL).stdout


def probe(path):
    return json.loads(command(["ffprobe", "-v", "error", "-count_frames", "-show_streams",
                               "-of", "json", path], capture=True))["streams"]


def pcm(path):
    raw = command(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-f", "f32le",
                   "-acodec", "pcm_f32le", "-ar", str(rate), "-ac", "2", "pipe:1"], capture=True)
    values = array.array("f")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def first_event(values, start=0, end=None):
    # A fixed -80 dBFS threshold detects the test click's leading edge.
    end = len(values) // 2 if end is None else min(end, len(values) // 2)
    return next((i for i in range(start, end)
                 if max(abs(values[2*i]), abs(values[2*i+1])) > 0.0001), None)


if args.demo:
    video, audio = directory / "source.mp4", directory / "ai-audio-fixture.wav"
    # A deterministic stand-in for AI-generated speech/music, not a model output.
    values = array.array("h", [0]) * (6 * rate)
    for second in (1, 3, 5):
        for i in range(480):
            values[second * rate + i] = round(0.2 * 32767 * math.sin(2 * math.pi * 1000 * i / rate))
    if sys.byteorder != "little":
        values.byteswap()
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(values.tobytes())
    command(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=6",
             "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,25)+eq(n,75)+eq(n,125)'",
             "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-n", video])
else:
    video, audio = args.video.resolve(), args.audio.resolve()
video_stream = next(s for s in probe(video) if s["codec_type"] == "video")
if Fraction(video_stream.get("start_time", "0")) != 0:
    raise SystemExit("video must start at time zero; normalize its timeline explicitly before using this example")
video_duration = int(video_stream["duration_ts"]) * Fraction(video_stream["time_base"])
normalized, spatial, final = (directory / name for name in ("input-48k.wav", "spatial.wav", "final.mov"))
report_path = directory / "aurora-report.json"
command(["ffmpeg", "-v", "error", "-i", audio, "-map", "0:a:0", "-vn", "-ar", str(rate),
         "-c:a", "pcm_s24le", "-n", normalized])
input_pcm = pcm(normalized)
if abs(Fraction(len(input_pcm) // 2, rate) - video_duration) > Fraction(1, rate):
    raise SystemExit("audio/video durations differ by more than one sample; pad leading/trailing silence "
                     "explicitly to match the intended timeline before running this example")
if args.server:
    command([sys.executable, root / "examples/python_client.py", normalized, "--server", args.server,
             "--output", spatial, "--report", report_path, "--state", directory / "client-state.json",
             "--preset", "room", "--require-rust"])
else:
    aurora = root / ".venv/bin/aurora"
    if not aurora.is_file():
        aurora = shutil.which("aurora")
    if not aurora:
        raise SystemExit("build/install Aurora first: bash scripts/build-core.sh")
    command([aurora, "convert", normalized, "--preset", "room", "--strength", "0.4", "--output", spatial,
             "--sample-rate", str(rate), "--bit-depth", "24", "--timing-mode", "preserve", "--tail", "trim",
             "--report", report_path])
report = json.loads(report_path.read_text())
if report["engine"]["is_stub"] or report["engine"]["name"] != "aurora_core":
    raise SystemExit("expected a real Rust engine")
if not report["timeline"]["sync_within_tolerance"] or report["timeline"]["sample_diff"] != 0:
    raise SystemExit("Aurora readback did not preserve the sample timeline")
# PCM in MOV avoids adding a second lossy codec's delay. No -shortest, timestamp reset,
# silence removal, tempo correction or unmeasured AAC padding is used to mask a mismatch.
command(["ffmpeg", "-v", "error", "-i", video, "-i", spatial, "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "pcm_s24le", "-n", final])
effect_pcm, final_pcm = pcm(spatial), pcm(final)
streams = probe(final)
final_video = next(s for s in streams if s["codec_type"] == "video")
final_audio = next(s for s in streams if s["codec_type"] == "audio")
expected, effect_count, final_count = (len(v) // 2 for v in (input_pcm, effect_pcm, final_pcm))
max_mux_error = max((abs(a-b) for a, b in zip(effect_pcm, final_pcm)), default=0)
input_onset, effect_onset, final_onset = (first_event(v) for v in (input_pcm, effect_pcm, final_pcm))
event_deltas = []
fps = Fraction(video_stream["avg_frame_rate"])
event_tolerance = math.ceil(rate / fps)  # Frame-level A/V tolerance, not sample-exact DSP onset.
if args.demo:
    for second in (1, 3, 5):
        start, end = second * rate - event_tolerance, second * rate + event_tolerance
        before, after = first_event(input_pcm, start, end), first_event(final_pcm, start, end)
        event_deltas.append(None if before is None or after is None else after - before)
onset_delta = None if input_onset is None or final_onset is None else final_onset - input_onset
frame_count = int(video_stream["nb_read_frames"])
final_frames = int(final_video["nb_read_frames"])
audio_start = Fraction(final_audio.get("start_time", "0"))
video_start = Fraction(final_video.get("start_time", "0"))
checks = {"sample_count_exact": expected == effect_count == final_count,
          "mux_pcm_exact": max_mux_error == 0 and effect_count == final_count,
          "video_frames_exact": frame_count == final_frames,
          "start_timestamps_zero": audio_start == video_start == 0,
          "demo_events_within_one_video_frame": not args.demo or all(
              d is not None and abs(d) <= event_tolerance for d in event_deltas)}
result = {"ok": all(checks.values()), "fixture": "synthetic flash/click" if args.demo else "user AI audio/video",
          "engine": report["engine"], "sample_rate": rate, "input_samples": expected,
          "spatial_samples": effect_count, "final_decoded_samples": final_count,
          "sample_diff": final_count - expected, "video_frames_before": frame_count,
          "video_frames_after": final_frames, "video_fps": str(fps),
          "audio_start_seconds": str(audio_start), "video_start_seconds": str(video_start),
          "input_onset_sample": input_onset, "spatial_onset_sample": effect_onset,
          "final_onset_sample": final_onset, "onset_shift_samples": onset_delta,
          "onset_shift_video_frames": None if onset_delta is None else float(onset_delta * fps / rate),
          "demo_event_shifts_samples": event_deltas, "mux_max_abs_pcm_error": max_mux_error,
          "event_tolerance_samples": event_tolerance,
          "event_tolerance_basis": "one video frame; DSP onset may shift even when sample counts match",
          "loudness_after": report["loudness_after"], "checks": checks,
          "final": str(final), "report": str(report_path)}
(directory / "sync.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
print(json.dumps(result, ensure_ascii=False))
if not result["ok"]:
    raise SystemExit("final video synchronization check failed; inspect sync.json")
PY
