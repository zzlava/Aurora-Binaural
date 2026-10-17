"""Generate real Rust reports at 0/0.5/1 for each ADR 0005 macro.

Run from the checkout: uv run python qa/p6/codex/verify_controls_04.py
Large audio outputs stay in the ignored .runtime directory.
"""

import hashlib
import json
import platform
import subprocess
from pathlib import Path

from aurora_binaural.catalog import CATALOG, CONTROL_KEYS, DEFAULT_PRESET_ID
from aurora_binaural.engine import RustRenderer
from aurora_binaural.media import ensure_ffmpeg_available
from aurora_binaural.params import AuroraParams
from aurora_binaural.pipeline import convert_audio

ROOT = Path(__file__).resolve().parents[3]
QA = ROOT / "qa/p6/codex"
RUNTIME = QA / ".runtime/controls-matrix-04"
REPORTS = QA / "controls-04/matrix"


def main() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    source = RUNTIME / "source.wav"
    ffmpeg, _ = ensure_ffmpeg_available()
    subprocess.run([ffmpeg, "-v", "error", "-i", str(ROOT / "data/assets/real_music.wav"),
                    "-t", "1", "-c:a", "pcm_s16le", "-y", str(source)], check=True)
    rows = []
    for control in CONTROL_KEYS:
        for value in (0.0, 0.5, 1.0):
            params = AuroraParams.model_validate({"controls": {control: value}})
            label = f"{control}-{value:g}"
            report = convert_audio(source, RUNTIME / f"{label}.wav", params,
                                   renderer=RustRenderer(), overwrite=True)
            assert report.controls == params.resolved_controls
            assert report.effective_params and report.effective_params["controls"] == report.controls
            assert not report.engine.is_stub and report.engine.route == "V"
            assert report.timeline.sample_diff == 0
            assert not report.ignored_controls
            (REPORTS / f"{label}.json").write_text(report.model_dump_json(indent=2) + "\n")
            rows.append({"varied_control": control, "value": value, "controls": report.controls,
                         "effective_params": report.effective_params, "output_sha256": report.output.sha256,
                         "sample_diff": report.timeline.sample_diff, "report": f"matrix/{label}.json"})
    evidence = {"platform": platform.platform(), "python": platform.python_version(),
                "default_preset": DEFAULT_PRESET_ID,
                "default_controls": CATALOG[DEFAULT_PRESET_ID].default_controls,
                "source_recipe": "first 1 s of data/assets/real_music.wav -> stereo 48 kHz PCM16",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "rows": rows}
    (QA / "controls-04/matrix.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    print(f"Verified {len(rows)} real Rust renders; all controls read back exactly and sample_diff=0")


if __name__ == "__main__":
    main()
