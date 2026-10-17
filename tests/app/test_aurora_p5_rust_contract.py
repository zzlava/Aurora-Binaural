"""Contract tests against the real optional aurora_core extension, never a mock."""

import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aurora_binaural.api import create_app
from aurora_binaural.config import ServiceConfig
from aurora_binaural.engine import (
    FloatBlock,
    RenderConfig,
    Renderer,
    RustRenderer,
    StubRenderer,
    run_offline_block_pipeline,
)
from aurora_binaural.exit_codes import ProcessingError
from aurora_binaural.params import AuroraParams
from aurora_binaural.pipeline import convert_audio
from aurora_binaural.worker import AuroraWorker

pytest.importorskip("aurora_core", reason="Build the Rust extension with bash scripts/build-core.sh")


def params(*, strength: float = 0.4, tail: str = "trim") -> AuroraParams:
    return AuroraParams.model_validate({
        "preset": "room", "strength": strength, "seed": 20261016,
        "timing": {"tail": tail, "fade_out_ms": 0},
    })


def prepared(max_block: int = 256, channels: int = 2, strength: float = 0.4) -> RustRenderer:
    renderer = RustRenderer()
    renderer.prepare(48000, max_block, RenderConfig(input_channels=channels))
    renderer.set_params(params(strength=strength))
    assert isinstance(renderer, Renderer)
    return renderer


def render(pcm: FloatBlock, block: int, config: AuroraParams, renderer: Renderer) -> FloatBlock:
    result, channels, frames, _, _ = run_offline_block_pipeline(
        pcm.tobytes(), sample_rate=48000, input_channels=pcm.shape[1],
        params=config, renderer=renderer, block_size=block,
    )
    assert channels == 2
    return np.frombuffer(result, dtype=np.float32).reshape(frames, 2)


@pytest.mark.parametrize("channels", [1, 2])
def test_native_block_boundaries_validation_flush_and_reset(channels: int) -> None:
    renderer = prepared(channels=channels)
    renderer.process(np.zeros((0, channels), dtype=np.float32))
    for frames in (1, 17, 255, 256):
        result = renderer.process(np.zeros((frames, channels), dtype=np.float32))
        assert result.shape == (frames, 2)
        assert result.dtype == np.float32
        assert np.all(np.isfinite(result))
    with pytest.raises(ProcessingError, match="exceeds"):
        renderer.process(np.zeros((257, channels), dtype=np.float32))
    with pytest.raises(ProcessingError, match="channel"):
        renderer.process(np.zeros((1, 3), dtype=np.float32))
    with pytest.raises(ProcessingError, match="non-finite"):
        renderer.process(np.full((1, channels), np.nan, dtype=np.float32))
    flushed = renderer.flush()
    assert len(flushed) == renderer.latency_samples() + renderer.tail_samples()
    assert not np.any(flushed)  # silence stays silent, including delayed/tail frames
    assert renderer.flush().shape == (0, 2)
    with pytest.raises(ProcessingError, match="reset"):
        renderer.process(np.zeros((1, channels), dtype=np.float32))
    renderer.reset()
    assert not np.any(renderer.process(np.zeros((1, channels), dtype=np.float32)))


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("frames", [1, 67, 1000, 8193])
def test_native_and_stub_bypass_share_exact_timeline(channels: int, frames: int) -> None:
    rng = np.random.default_rng(5)
    pcm = rng.uniform(-0.1, 0.1, (frames, channels)).astype(np.float32)
    actual = render(pcm, 127, params(strength=0), RustRenderer())
    stub = render(pcm, 127, params(strength=0), StubRenderer())
    np.testing.assert_array_equal(actual, stub)


@pytest.mark.parametrize("tail", ["trim", "preserve"])
@pytest.mark.parametrize("frames", [1, 67, 1000, 8193])
def test_native_latency_compensation_and_real_tail_match_raw_output(tail: str, frames: int) -> None:
    pcm = np.zeros((frames, 2), dtype=np.float32)
    pcm[-1, :] = 0.2
    raw_renderer = prepared(256)
    latency, tail_frames = raw_renderer.latency_samples(), raw_renderer.tail_samples()
    assert latency > 0 and tail_frames > 0
    chunks = [raw_renderer.process(pcm[i : i + 256]) for i in range(0, frames, 256)]
    chunks.append(raw_renderer.flush())
    raw = np.concatenate(chunks)
    output_frames = frames + (tail_frames if tail == "preserve" else 0)
    expected = raw[latency : latency + output_frames]
    actual = render(pcm, 256, params(tail=tail), RustRenderer())
    assert actual.shape == (output_frames, 2)
    np.testing.assert_array_equal(actual, expected)
    if tail == "preserve":
        assert np.max(np.abs(actual[frames:])) > 1e-6  # retained native tail, not padded silence


def test_native_block_size_invariance_and_reset_determinism() -> None:
    pcm = np.random.default_rng(23).normal(0, 0.03, (8193, 2)).astype(np.float32)
    reference = render(pcm, 256, params(tail="preserve"), RustRenderer())
    for size in (1, 63, 1024, 1537):
        actual = render(pcm, size, params(tail="preserve"), RustRenderer())
        assert np.max(np.abs(actual - reference)) <= 1e-5  # <= -100 dBFS
    renderer = prepared()
    a = renderer.process(pcm[:256]).copy()
    renderer.process(pcm[256:512])
    renderer.flush()
    renderer.reset()
    np.testing.assert_array_equal(renderer.process(pcm[:256]), a)


def test_legacy_core_rejects_route_v_without_substitution() -> None:
    import importlib
    import inspect

    aurora_core = importlib.import_module("aurora_core")

    if "route" in inspect.signature(aurora_core.Renderer).parameters:
        pytest.skip("New route V binding installed; legacy compatibility check does not apply")
    renderer = RustRenderer()
    renderer.prepare(48000, 256, RenderConfig())
    with pytest.raises(ProcessingError) as failure:
        renderer.set_params(AuroraParams())
    assert failure.value.error_code == "CORE_ROUTE_UNSUPPORTED"


def test_native_hrtf_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    from aurora_binaural.resources import asset_directory

    source = tmp_path / "sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
    source.parent.mkdir()
    source.write_bytes(b"invalid SOFA hash")
    (tmp_path / "sadie_d1_frontal.ahrp").symlink_to(asset_directory("hrtf") / "sadie_d1_frontal.ahrp")
    renderer = RustRenderer(hrtf_dir=tmp_path)
    renderer.prepare(48000, 256, RenderConfig())
    with pytest.raises(ProcessingError, match="[Hh]ash"):
        renderer.set_params(params())


@pytest.mark.parametrize("sample_rate", [44100, 48000, 96000])
@pytest.mark.parametrize("fmt", ["wav", "m4a", "mp3"])
def test_native_end_to_end_rate_and_codec_timeline(tmp_path: Path, sample_rate: int, fmt: str) -> None:
    source = tmp_path / "input.wav"
    t = np.arange(12937, dtype=np.float64) / 48000
    mono = (3000 * np.sin(2 * np.pi * 440 * t)).astype("<i2")
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(mono.tobytes())
    payload = {
        "preset": "room", "strength": 0.4,
        "output": {"format": fmt, "sample_rate": sample_rate},
    }
    if fmt == "mp3" and sample_rate == 96000:
        with pytest.raises(ValidationError, match="MP3 supports"):
            AuroraParams.model_validate(payload)
        return
    config = AuroraParams.model_validate(payload)
    report = convert_audio(source, tmp_path / f"output.{fmt}", config, renderer=RustRenderer())
    assert not report.engine.is_stub
    assert report.engine.internal_sample_rate == 48000
    assert report.engine.hrtf.license == "Apache-2.0"
    assert report.engine.hrtf.sha256 == "e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31"
    assert report.output.sample_rate == sample_rate
    assert report.timeline.sync_within_tolerance
    assert abs(report.output.samples - round(12937 * sample_rate / 48000)) <= (0 if sample_rate == 48000 else 2)


@pytest.mark.parametrize("preview", [False, True])
def test_api_worker_uses_configured_native_engine_for_export_and_preview(tmp_path: Path, preview: bool) -> None:
    source = tmp_path / "input.wav"
    t = np.arange(144000, dtype=np.float64) / 48000
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes((3000 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes())
    config = ServiceConfig(engine="rust", api_key="native-fixture", data_dir=tmp_path / "runtime",
                           db_path=tmp_path / "runtime/db")
    headers = {"Authorization": "Bearer native-fixture"}
    app = create_app(config)
    with TestClient(app) as client:
        uploaded = client.post("/v1/assets", headers=headers, files={"file": ("input.wav", source.read_bytes())})
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["asset_id"]
        if preview:
            created = client.post("/v1/previews", headers=headers, json={
                "asset_id": asset_id, "preset": {"id": "room"}, "start_seconds": 1, "duration_seconds": 1,
            })
        else:
            created = client.post("/v1/jobs", headers=headers, json={
                "input": {"asset_id": asset_id}, "preset": {"id": "room"},
            })
        assert created.status_code == 202
        completed = AuroraWorker(config, store=app.state.store).run_once()
        assert completed is not None and completed.status == "succeeded"
        job_id = created.json()["job_id"]
        report = client.get(f"/v1/jobs/{job_id}/report", headers=headers).json()
        assert report["engine"]["is_stub"] is False
        assert report["engine"]["name"] == "aurora_core"
        assert report["timeline"]["sample_diff"] == 0
        assert report["output"]["samples"] == (48000 if preview else 144000)
        if preview:
            assert report["preview"]["start_sample"] == 48000
            assert report["preview"]["loudness_diff_lu"] <= 0.2
            assert client.get(f"/v1/jobs/{job_id}/original", headers=headers).status_code == 200
        assert client.get(f"/v1/jobs/{job_id}/result", headers=headers).status_code == 200
