"""Reproduce the 20-minute default CLI run without committing large runtime WAVs."""

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QA = ROOT / "qa/p6/codex"
RUNTIME = QA / ".runtime/long"
RUNTIME.mkdir(parents=True, exist_ok=True)
source, target = RUNTIME / "stereo-20min.wav", RUNTIME / "stereo-20min.V2.wav"
subprocess.run([
    "ffmpeg", "-v", "error", "-stream_loop", "59", "-i", str(ROOT / "data/assets/real_music.wav"),
    "-t", "1200", "-c:a", "pcm_s16le", "-y", str(source),
], check=True)
env = dict(os.environ)
env.pop("AURORA_ENGINE", None)
argv = [str(ROOT / ".venv/bin/aurora"), "convert", str(source), "--output", str(target), "--overwrite"]
started = time.perf_counter()
with (QA / "long-conversion.json").open("w") as stdout, (QA / "long-time.txt").open("w") as stderr:
    completed = subprocess.run(["/usr/bin/time", "-l", *argv], env=env, stdout=stdout, stderr=stderr, check=True)
wall = time.perf_counter() - started
report = json.loads((QA / "long-conversion.json").read_text())
assert report["engine"]["route"] == "V" and not report["engine"]["is_stub"]
assert report["params"]["preset"]["id"] == "V2"
assert report["input"]["channels"] == report["output"]["channels"] == 2
assert report["output"]["samples"] == 57_600_000 and report["timeline"]["sample_diff"] == 0
rss = int(re.search(r"(\d+)\s+maximum resident set size", (QA / "long-time.txt").read_text())[1])
summary = {
    "platform": platform.platform(), "machine": platform.machine(),
    "source_recipe": "real_music.wav (20 s), looped 60 times; FFmpeg PCM s16le stereo / 48 kHz / 1200 s",
    "command": argv, "engine_override": None, "preset_override": None,
    "exit_code": completed.returncode, "wall_seconds": wall,
    "conversion_seconds": report["elapsed_ms"] / 1000,
    "realtime_speed_x": 1200 / wall, "realtime_factor": wall / 1200,
    "max_rss_bytes": rss, "max_rss_mib": rss / 1024**2,
    "rss_method": "macOS /usr/bin/time -l maximum resident set size (bytes)",
    "samples": report["output"]["samples"], "true_peak_dbtp": report["loudness_after"]["true_peak_dbtp"],
    "output_sha256": report["output"]["sha256"],
}
(QA / "long-benchmark.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
