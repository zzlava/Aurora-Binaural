"""Comprehensive unit and integration tests for Aurora Binaural P0 application layer."""

from __future__ import annotations

import json
import math
import subprocess
from array import array
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from aurora_binaural.cli import run_cli
from aurora_binaural.exit_codes import ExitCode, OperationCanceledError, ProcessingError
from aurora_binaural.params import (
    AuroraParams,
    OutputContainer,
    OutputFormat,
    OutputSpec,
    PresetId,
    TailPolicy,
    TimingMode,
    TimingSpec,
    get_params_json_schema,
)
from aurora_binaural.stub_core import run_offline_block_pipeline


def _create_test_wav(path: Path, sample_rate: int = 48000, samples: int = 24000, channels: int = 2) -> bytes:
    """Create a synthetic float32 WAV file in a temporary directory for testing."""
    buf = array("f", [0.0] * (samples * channels))
    amp = 0.25
    for i in range(samples):
        val = amp * math.sin(2.0 * math.pi * 440.0 * i / sample_rate)
        for c in range(channels):
            buf[i * channels + c] = val if c == 0 else -val
    if samples > 500:
        buf[500 * channels] = 0.8
    pcm_bytes = buf.tobytes()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
            "-c:a",
            "pcm_s24le",
            str(path),
        ],
        input=pcm_bytes,
        check=True,
    )
    return pcm_bytes


class TestUnifiedParameterModel:
    """Tests for docs/plan.md Sections 9 & 10 unified Pydantic v2 parameter model."""

    def test_section_10_job_payload_validation(self) -> None:
        payload = {
            "api_version": "v1",
            "mode": "enhance",
            "input": {"asset_id": "asset_example"},
            "preset": {"id": "room", "version": "1"},
            "strength": 0.4,
            "seed": 20261016,
            "output": {"format": "wav", "sample_rate": 48000, "bit_depth": 24},
            "timing": {"mode": "preserve", "tail": "trim"},
        }
        params = AuroraParams.model_validate(payload)
        assert params.api_version == "v1"
        assert params.preset.id == PresetId.ROOM
        assert params.preset.version == "1"
        assert params.strength == pytest.approx(0.4)
        assert params.seed == 20261016
        assert params.output.format == OutputFormat.WAV
        assert params.output.container == OutputContainer.WAV
        assert params.output.sample_rate == 48000
        assert params.output.bit_depth == 24
        assert params.timing.mode == TimingMode.PRESERVE
        assert params.timing.tail == TailPolicy.TRIM

    def test_preset_default_strengths(self) -> None:
        assert AuroraParams.model_validate({"preset": "room"}).effective_strength == pytest.approx(0.4)
        assert AuroraParams.model_validate({"preset": "surround"}).effective_strength == pytest.approx(0.5)
        assert AuroraParams.model_validate({"preset": "orbit"}).effective_strength == pytest.approx(0.3)

    def test_invalid_strength_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuroraParams(strength=-0.1)
        with pytest.raises(ValidationError):
            AuroraParams(strength=1.01)

    def test_output_container_and_codec_mapping(self) -> None:
        m4a_spec = OutputSpec(format=OutputFormat.M4A, sample_rate=48000)
        assert m4a_spec.container == OutputContainer.M4A
        assert m4a_spec.codec_name == "aac"
        assert m4a_spec.bitrate_kbps == 256

        mp3_spec = OutputSpec(format=OutputFormat.MP3, sample_rate=44100, bitrate_kbps=192)
        assert mp3_spec.container == OutputContainer.MP3
        assert mp3_spec.codec_name == "libmp3lame"
        assert mp3_spec.bitrate_kbps == 192

        with pytest.raises(ValidationError):
            OutputSpec(format=OutputFormat.WAV, container=OutputContainer.M4A)

    def test_schema_generation_and_qa_artifact_parity(self) -> None:
        schema = get_params_json_schema()
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        for required_key in ("preset", "strength", "seed", "output", "timing"):
            assert required_key in schema["properties"]

        qa_schema_path = Path(__file__).resolve().parents[2] / "qa" / "p0" / "antigravity" / "params.schema.json"
        if qa_schema_path.exists():
            on_disk = json.loads(qa_schema_path.read_text(encoding="utf-8"))
            assert on_disk == schema


class TestStubCoreAndBlockInvariance:
    """Tests for docs/plan.md Section 7 & 8 block processor stub and timeline handling."""

    def test_block_size_invariance(self) -> None:
        sr = 48000
        frames = 2000
        in_floats = array("f", [math.sin(2.0 * math.pi * 330.0 * (i // 2) / sr) * 0.5 for i in range(frames * 2)])
        pcm_in = in_floats.tobytes()
        params = AuroraParams()

        outputs: list[bytes] = []
        for blk in (64, 256, 1024, frames):
            out_pcm, out_ch, out_frames, _, _ = run_offline_block_pipeline(
                pcm_in,
                sample_rate=sr,
                input_channels=2,
                params=params,
                block_size=blk,
                simulated_latency_samples=128,
            )
            assert out_ch == 2
            assert out_frames == frames
            outputs.append(out_pcm)

        for other in outputs[1:]:
            assert other == outputs[0]

    def test_latency_compensation_and_tail_policies(self) -> None:
        sr = 48000
        frames = 1024
        in_floats = array("f", [0.0] * (frames * 2))
        in_floats[100 * 2] = 0.75
        in_floats[100 * 2 + 1] = -0.75
        pcm_in = in_floats.tobytes()

        # Trim mode: output length == input length, impulse remains at index 100 despite 64-sample latency
        params_trim = AuroraParams(timing=TimingSpec(mode=TimingMode.PRESERVE, tail=TailPolicy.TRIM))
        out_trim, _, n_trim, lat, extra_trim = run_offline_block_pipeline(
            pcm_in,
            sample_rate=sr,
            input_channels=2,
            params=params_trim,
            block_size=256,
            simulated_latency_samples=64,
            simulated_tail_samples=200,
        )
        assert n_trim == frames
        assert lat == 64
        assert extra_trim == 0
        out_arr = array("f")
        out_arr.frombytes(out_trim)
        assert out_arr[100 * 2] == pytest.approx(0.75)
        assert out_arr[100 * 2 + 1] == pytest.approx(-0.75)

        # Preserve tail mode: output length == input length + simulated_tail_samples
        params_preserve = AuroraParams(timing=TimingSpec(mode=TimingMode.PRESERVE, tail=TailPolicy.PRESERVE))
        _, _, n_pres, _, extra_pres = run_offline_block_pipeline(
            pcm_in,
            sample_rate=sr,
            input_channels=2,
            params=params_preserve,
            block_size=256,
            simulated_latency_samples=64,
            simulated_tail_samples=200,
        )
        assert extra_pres == 200
        assert n_pres == frames + 200

    def test_nan_rejected_and_cancel_supported(self) -> None:
        bad_floats = array("f", [0.0, float("nan"), 0.0, 0.0])
        with pytest.raises(ProcessingError):
            run_offline_block_pipeline(
                bad_floats.tobytes(),
                sample_rate=48000,
                input_channels=2,
                params=AuroraParams(),
            )

        good_floats = array("f", [0.1] * 1024)
        with pytest.raises(OperationCanceledError):
            run_offline_block_pipeline(
                good_floats.tobytes(),
                sample_rate=48000,
                input_channels=2,
                params=AuroraParams(),
                cancel_check=lambda: True,
            )


class TestCliPipelineAndExitCodes:
    """Tests for `aurora` CLI commands, stdout/stderr separation, overwrite protection, and exit codes."""

    @pytest.mark.parametrize("sr", [44100, 48000])
    @pytest.mark.parametrize("fmt", ["wav", "m4a", "mp3"])
    def test_convert_roundtrip_sample_accuracy(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        sr: int,
        fmt: str,
    ) -> None:
        in_wav = tmp_path / f"in_{sr}.wav"
        n_samples = sr // 2 + 77  # Non-frame-aligned sample count
        _create_test_wav(in_wav, sample_rate=sr, samples=n_samples)

        out_file = tmp_path / f"out_{sr}.{fmt}"
        report_file = tmp_path / f"report_{sr}_{fmt}.json"

        code = run_cli(
            [
                "convert",
                str(in_wav),
                "--output",
                str(out_file),
                "--preset",
                "room",
                "--strength",
                "0.4",
                "--sample-rate",
                str(sr),
                "--report",
                str(report_file),
            ]
        )
        captured = capsys.readouterr()
        assert code == int(ExitCode.OK)
        assert out_file.exists()
        assert report_file.exists()
        assert "[aurora]" in captured.err

        stdout_json = json.loads(captured.out)
        assert stdout_json["ok"] is True
        assert stdout_json["status"] == "succeeded"
        assert stdout_json["input"]["samples"] == n_samples
        assert stdout_json["output"]["samples"] == n_samples
        assert stdout_json["timeline"]["sample_diff"] == 0
        assert stdout_json["timeline"]["sync_within_tolerance"] is True

    def test_overwrite_protection_and_exit_code_3(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        in_wav = tmp_path / "in.wav"
        out_wav = tmp_path / "out.wav"
        _create_test_wav(in_wav, sample_rate=48000, samples=4800)
        out_wav.write_bytes(b"existing-content")

        # Without --overwrite: must fail with ExitCode.INPUT_ERROR (3) and preserve existing file
        code = run_cli(["convert", str(in_wav), "--output", str(out_wav)])
        captured = capsys.readouterr()
        assert code == int(ExitCode.INPUT_ERROR)
        assert out_wav.read_bytes() == b"existing-content"
        err_json = json.loads(captured.out)
        assert err_json["ok"] is False
        assert err_json["error"]["exit_code"] == 3

        # With --overwrite: succeeds with ExitCode.OK (0)
        code_ok = run_cli(["convert", str(in_wav), "--output", str(out_wav), "--overwrite"])
        captured_ok = capsys.readouterr()
        assert code_ok == int(ExitCode.OK)
        ok_json = json.loads(captured_ok.out)
        assert ok_json["ok"] is True

    def test_all_exit_codes_and_json_stdout(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        in_wav = tmp_path / "in.wav"
        _create_test_wav(in_wav, sample_rate=48000, samples=4800)

        # Exit code 0: inspect & presets
        assert run_cli(["inspect", str(in_wav), "--json"]) == int(ExitCode.OK)
        inspect_data = json.loads(capsys.readouterr().out)
        assert inspect_data["ok"] is True
        assert inspect_data["audio"]["samples"] == 4800

        # Exit code 2: Parameter error (invalid strength > 1.0)
        assert run_cli(["convert", str(in_wav), "--output", str(tmp_path / "bad.wav"), "--strength", "1.5"]) == int(
            ExitCode.PARAM_ERROR
        )
        err2 = json.loads(capsys.readouterr().out)
        assert err2["error"]["exit_code"] == 2

        # Exit code 2: Raw .aac output rejected
        assert run_cli(["convert", str(in_wav), "--output", str(tmp_path / "bad.aac")]) == int(ExitCode.PARAM_ERROR)
        _ = json.loads(capsys.readouterr().out)

        # Exit code 3: Missing input file
        assert run_cli(["inspect", str(tmp_path / "missing.wav")]) == int(ExitCode.INPUT_ERROR)
        err3 = json.loads(capsys.readouterr().out)
        assert err3["error"]["exit_code"] == 3

        # Exit code 4: Processing failure
        with patch(
            "aurora_binaural.cli.convert_audio",
            side_effect=ProcessingError("Simulated encoder crash"),
        ):
            assert run_cli(["convert", str(in_wav), "--output", str(tmp_path / "fail.wav")]) == int(
                ExitCode.PROCESSING_ERROR
            )
            err4 = json.loads(capsys.readouterr().out)
            assert err4["error"]["exit_code"] == 4

        # Exit code 5: Remote server unreachable
        assert run_cli(
            [
                "convert",
                str(in_wav),
                "--output",
                str(tmp_path / "remote.wav"),
                "--server",
                "http://127.0.0.1:1",
            ]
        ) == int(ExitCode.REMOTE_ERROR)
        err5 = json.loads(capsys.readouterr().out)
        assert err5["error"]["exit_code"] == 5

        # Exit code 130: Canceled (KeyboardInterrupt)
        with patch("aurora_binaural.cli.convert_audio", side_effect=KeyboardInterrupt()):
            assert run_cli(["convert", str(in_wav), "--output", str(tmp_path / "cancel.wav")]) == int(ExitCode.CANCELED)
            err130 = json.loads(capsys.readouterr().out)
            assert err130["error"]["exit_code"] == 130
