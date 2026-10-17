"""ADR 0005: TOML defaults, finite clamping, core readback and ignored macros."""

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from aurora_binaural.catalog import CATALOG, CONTROL_KEYS, load_catalog
from aurora_binaural.engine import RustRenderer, StubRenderer
from aurora_binaural.params import AuroraParams
from aurora_binaural.pipeline import convert_audio
from aurora_binaural.storage import compute_canonical_job_hash


@pytest.mark.parametrize("key", CONTROL_KEYS)
@pytest.mark.parametrize("raw,expected", [(-1.0, 0.0), (0.5, 0.5), (2.0, 1.0)])
def test_clamp_and_persist_events(key: str, raw: float, expected: float) -> None:
    params = AuroraParams.model_validate({"controls": {key: raw}})
    assert params.resolved_controls[key] == expected
    assert getattr(params, key) == expected
    assert len(params.control_clipping) == int(raw != expected)
    assert AuroraParams.model_validate_json(params.model_dump_json()) == params


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, []])
def test_nonfinite_or_invalid_controls_fail(value: object) -> None:
    with pytest.raises(ValidationError):
        AuroraParams.model_validate({"controls": {"width": value}})


def test_unknown_and_conflicting_controls_fail() -> None:
    with pytest.raises(ValidationError):
        AuroraParams.model_validate({"controls": {"widht": 0.5}})
    with pytest.raises(ValidationError, match="Conflicting width"):
        AuroraParams.model_validate({"width": 0.2, "controls": {"width": 0.3}})


def test_defaults_are_read_from_toml(tmp_path: Path) -> None:
    for key, entry in CATALOG.items():
        if entry.route == "V":
            document = tomllib.loads(entry.preset_path.read_text())
            assert AuroraParams.model_validate({"preset": key}).resolved_controls == document[key]["controls"]
    (tmp_path / "route_v.toml").write_text(
        'route="V"\n[V9]\nname="future"\ndefault_strength=0.1\n[V9.controls]\n'
        'strength=0.87\nwidth=0.13\nheight=0.91\nroom=0.44\nvoice_distance=0.08\n'
    )
    entry = load_catalog(tmp_path)["V9"]
    assert entry.default_strength == 0.87
    assert entry.default_controls == dict(strength=0.87, width=0.13, height=0.91, room=0.44, voice_distance=0.08)


def test_controls_participate_in_idempotency_hash() -> None:
    assert compute_canonical_job_hash(AuroraParams(width=0.2)) != compute_canonical_job_hash(AuroraParams(width=0.8))


@pytest.mark.parametrize("command", ["convert", "preview"])
def test_cli_controls_and_percentages(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str) -> None:
    from aurora_binaural.cli import run_cli
    from test_aurora_p6_defaults import source_audio

    source = tmp_path / "source.wav"
    source_audio(source)
    output = ["--output", str(tmp_path / "out.wav")] if command == "convert" else ["--output-dir", str(tmp_path / "excerpt"), "--duration", "0.5"]
    assert run_cli([command, str(source), *output, "--width", "25%", "--height", "0.75", "--room", "150%", "--voice-distance", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["params"]["controls"] == dict(strength=1, width=0.25, height=0.75, room=1, voice_distance=0)
    assert report["params"]["control_clipping"] == [dict(control="room", requested=1.5, applied=1)]


@pytest.mark.parametrize("engine,preset", [("rust", "V3"), ("rust", "room"), ("stub", "V3")])
def test_report_contains_actual_controls(tmp_path: Path, engine: str, preset: str) -> None:
    from test_aurora_p6_defaults import source_audio

    source = tmp_path / "source.wav"
    source_audio(source)
    params = AuroraParams.model_validate({"preset": preset, "controls": {"width": 1.3, "height": 0.5, "room": 0.0, "voice_distance": 1.0}})
    renderer = RustRenderer() if engine == "rust" else StubRenderer()
    report_path = tmp_path / "report.json"
    report = convert_audio(source, tmp_path / "out.wav", params, renderer=renderer, report_path=report_path)
    serialized = json.loads(report_path.read_text())
    assert serialized["params"]["control_clipping"] == [{"control": "width", "requested": 1.3, "applied": 1.0}]
    if engine == "rust" and preset == "V3":
        assert serialized["controls"] == params.resolved_controls
        assert serialized["effective_params"]["controls"] == params.resolved_controls
        assert "front_az" in serialized["effective_params"]
        assert not report.ignored_controls
    else:
        assert report.effective_params is None
        assert "width" in report.ignored_controls
        assert "忽略" in report.controls_note
        assert report.controls == ({"strength": params.effective_strength} if engine == "rust" else {})
