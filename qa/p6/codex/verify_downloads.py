"""Decode the actual browser downloads and compare them with downloaded reports."""

import hashlib
import json
import subprocess
from pathlib import Path

from aurora_binaural.media import measure_loudness_file

QA = Path(__file__).resolve().parent
rows = []
for name in ("real_music", "holdout_electronic"):
    path = QA / f"downloads/{name}.V2.wav"
    report_path = QA / f"gui-reports/{name}.export.json"
    report = json.loads(report_path.read_text())
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path),
    ]))["streams"][0]
    decoder = subprocess.Popen([
        "ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1",
    ], stdout=subprocess.PIPE)
    assert decoder.stdout is not None
    count = 0
    while block := decoder.stdout.read(1024 * 1024):
        count += len(block)
    decoder.stdout.close()
    assert decoder.wait() == 0
    samples = count // (4 * probe["channels"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    loudness = measure_loudness_file(path)
    assert digest == report["output"]["sha256"]
    assert samples == report["output"]["samples"] == report["input"]["samples"]
    assert loudness.true_peak_dbtp == report["loudness_after"]["true_peak_dbtp"]
    rows.append({
        "file": str(path.relative_to(QA)), "samples": samples,
        "channels": probe["channels"], "sample_rate": int(probe["sample_rate"]),
        "codec": probe["codec_name"], "sha256": digest,
        "true_peak_dbtp": loudness.true_peak_dbtp, "peak_method": loudness.method,
        "conversion_elapsed_ms": report["elapsed_ms"],
    })
    for kind in ("preview", "export"):
        saved = QA / f"gui-reports/{name}.{kind}.json"
        saved.write_text(json.dumps(json.loads(saved.read_text()), ensure_ascii=False, indent=2) + "\n")
(QA / "download-readback.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(rows, ensure_ascii=False, indent=2))
