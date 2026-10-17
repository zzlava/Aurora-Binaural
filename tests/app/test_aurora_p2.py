"""Comprehensive P2 unit and end-to-end tests for Aurora Binaural CLI and streaming engine.

Covers docs/tasks/P2-antigravity-01.md items 1–6:
- `aurora_binaural.engine`: `Renderer` Protocol, numpy `StubRenderer`, and `DelayTestRenderer`
- Streaming block pipeline: latency compensation, `tail=trim` (10 ms fade-out, exact length), `tail=preserve` (`flush()` tail appended)
- Mono & stereo; 44.1 kHz, 48 kHz, and 96 kHz
- Silence, 10 ms ultra-short clip, corrupt file, non-ASCII paths, and paths with spaces
- `aurora compare` (BS.1770 equal-loudness matching), `aurora gui` (exit code 2), `aurora batch` (partial failure, overwrite protection, Ctrl-C 130 + temp file cleanup)
- `aurora convert --server` and `aurora job status` against a local Section 10 HTTP mock server
- F-AG-1 & F-AG-2: FFmpeg version detection (< 7 degradation warning vs `--strict-ffmpeg` exit code 4)
"""

from __future__ import annotations

import json
import math
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from aurora_binaural.cli import run_cli
from aurora_binaural.engine import (
    DelayTestRenderer,
    FloatBlock,
    RenderConfig,
    Renderer,
    StubRenderer,
)
from aurora_binaural.exit_codes import ExitCode
from aurora_binaural.media import decode_to_f32le, parse_ffmpeg_version_line
from aurora_binaural.params import (
    AuroraParams,
    OutputFormat,
    OutputSpec,
    PresetId,
    PresetSpec,
    TailPolicy,
    TimingMode,
    TimingSpec,
)
from aurora_binaural.pipeline import compare_audio, convert_audio


def _write_synthetic_wav(
    path: Path,
    *,
    sample_rate: int = 48000,
    samples: int = 24000,
    channels: int = 2,
    amplitude: float = 0.25,
    impulse_at: int | None = 500,
) -> FloatBlock:
    """Create a deterministic float32 WAV file using numpy and ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(samples, dtype=np.float32)
    wave = (amplitude * np.sin(2.0 * math.pi * 440.0 * t / float(sample_rate))).astype(np.float32)
    arr = wave[:, np.newaxis] if channels == 1 else np.column_stack((wave, (-0.8 * wave).astype(np.float32)))

    if impulse_at is not None and 0 <= impulse_at < samples and amplitude > 0.0:
        arr[impulse_at, 0] = 0.85
        if channels > 1:
            arr[impulse_at, 1] = -0.85

    pcm_bytes = np.ascontiguousarray(arr, dtype=np.float32).tobytes()
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
            "pcm_f32le",
            str(path),
        ],
        input=pcm_bytes,
        check=True,
    )
    return arr


class _GainDelayTestRenderer(DelayTestRenderer):
    """Test renderer that applies a fixed linear gain in addition to delay + tail for testing `compare`."""

    def __init__(self, gain_db: float = -6.0, latency_samples: int = 64, tail_samples: int = 120) -> None:
        super().__init__(latency_samples=latency_samples, tail_samples=tail_samples, tail_amplitude=0.1)
        self._linear_gain = float(10.0 ** (gain_db / 20.0))

    def process(self, block: FloatBlock) -> FloatBlock:
        out = super().process(block)
        return (out * self._linear_gain).astype(np.float32)


class TestEngineProtocolAndRenderers:
    """Tests for Task 2 (`aurora_binaural.engine.Renderer`, `StubRenderer`, `DelayTestRenderer`) and Task 3."""

    def test_renderer_protocol_conformance(self) -> None:
        stub = StubRenderer(latency_samples=32)
        delay_test = DelayTestRenderer(latency_samples=128, tail_samples=240)
        assert isinstance(stub, Renderer)
        assert isinstance(delay_test, Renderer)

    def test_stub_renderer_numpy_fixed_latency_and_flush(self) -> None:
        sr = 48000
        lat = 150
        n_frames = 1000
        renderer = StubRenderer(latency_samples=lat)
        renderer.prepare(sample_rate=sr, max_block=256, config=RenderConfig(input_channels=1, output_channels=2))
        renderer.set_params(AuroraParams())
        assert renderer.latency_samples() == lat
        assert renderer.tail_samples() == 0

        # Feed mono ramp signal in chunks smaller and equal to latency
        mono_in = np.linspace(0.01, 0.9, n_frames, dtype=np.float32)[:, np.newaxis]
        out_chunks: list[FloatBlock] = []
        for start in range(0, n_frames, 200):
            out_chunks.append(renderer.process(mono_in[start : start + 200]))
        flushed = renderer.flush()
        assert flushed.shape == (lat, 2)
        # Second flush without new process() calls must return empty block
        assert renderer.flush().shape == (0, 2)

        full_out = np.concatenate([*out_chunks, flushed], axis=0)
        assert full_out.shape == (n_frames + lat, 2)
        # First `lat` frames are zero pre-delay; frames [lat : lat + n_frames] equal mono_in repeated to 2 channels
        np.testing.assert_allclose(full_out[:lat], 0.0, atol=1e-7)
        np.testing.assert_allclose(full_out[lat:, 0], mono_in[:, 0], atol=1e-7)
        np.testing.assert_allclose(full_out[lat:, 1], mono_in[:, 0], atol=1e-7)

    def test_delay_test_renderer_trim_vs_preserve_and_10ms_fade_out(self, tmp_path: Path) -> None:
        sr = 48000
        n_frames = 4800  # 100 ms
        lat = 128
        tail_len = 360
        in_wav = tmp_path / "delay_in.wav"

        # Constant DC-like + impulse signal so 10ms fade-out at the end is directly measurable
        arr = np.full((n_frames, 2), 0.5, dtype=np.float32)
        arr[200, 0] = 0.9
        arr[200, 1] = -0.9
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
                str(sr),
                "-ac",
                "2",
                "-i",
                "pipe:0",
                "-c:a",
                "pcm_f32le",
                str(in_wav),
            ],
            input=arr.tobytes(),
            check=True,
        )

        # 1. timing.tail = trim: output samples strictly equal input, 10 ms fade-out (480 samples) at end
        renderer_trim = DelayTestRenderer(latency_samples=lat, tail_samples=tail_len, tail_amplitude=0.3)
        out_trim_wav = tmp_path / "out_trim.wav"
        params_trim = AuroraParams(
            output=OutputSpec(format=OutputFormat.WAV, sample_rate=sr, bit_depth=32),
            timing=TimingSpec(mode=TimingMode.PRESERVE, tail=TailPolicy.TRIM, fade_out_ms=10.0),
        )
        rep_trim = convert_audio(
            in_wav,
            out_trim_wav,
            params_trim,
            block_size=1024,
            renderer=renderer_trim,
        )
        assert rep_trim.output.samples == n_frames
        assert rep_trim.timeline.engine_latency_samples == lat
        assert rep_trim.timeline.engine_tail_samples == tail_len
        assert rep_trim.timeline.extra_tail_samples_kept == 0

        trim_bytes, _, _ = decode_to_f32le(out_trim_wav)
        trim_pcm = np.frombuffer(trim_bytes, dtype=np.float32).reshape(-1, 2)
        assert trim_pcm.shape == (n_frames, 2)
        # Impulse at index 200 is latency-compensated back to exact index 200
        assert trim_pcm[200, 0] == pytest.approx(0.9, abs=1e-5)
        assert trim_pcm[200, 1] == pytest.approx(-0.9, abs=1e-5)
        # Before the last 480 samples (10 ms), signal is untouched at 0.5
        assert trim_pcm[n_frames - 481, 0] == pytest.approx(0.5, abs=1e-5)
        # Halfway through the 10 ms fade-out, gain is ~0.5 (value ~0.25); final sample is faded to 0.0
        assert trim_pcm[n_frames - 240, 0] == pytest.approx(0.25, abs=0.01)
        assert trim_pcm[-1, 0] == pytest.approx(0.0, abs=1e-6)
        assert trim_pcm[-1, 1] == pytest.approx(0.0, abs=1e-6)

        # 2. timing.tail = preserve: appends flush() tail, extra_tail_samples_kept == tail_len
        renderer_pres = DelayTestRenderer(latency_samples=lat, tail_samples=tail_len, tail_amplitude=0.3)
        out_pres_wav = tmp_path / "out_preserve.wav"
        params_pres = AuroraParams(
            output=OutputSpec(format=OutputFormat.WAV, sample_rate=sr, bit_depth=32),
            timing=TimingSpec(mode=TimingMode.PRESERVE, tail=TailPolicy.PRESERVE),
        )
        rep_pres = convert_audio(
            in_wav,
            out_pres_wav,
            params_pres,
            block_size=1024,
            renderer=renderer_pres,
        )
        assert rep_pres.output.samples == n_frames + tail_len
        assert rep_pres.timeline.extra_tail_samples_kept == tail_len
        assert rep_pres.timeline.expected_output_samples == n_frames + tail_len

        pres_bytes, _, _ = decode_to_f32le(out_pres_wav)
        pres_pcm = np.frombuffer(pres_bytes, dtype=np.float32).reshape(-1, 2)
        assert pres_pcm.shape == (n_frames + tail_len, 2)
        # Body [0 : n_frames] is NOT faded out in preserve mode, and tail [n_frames :] matches expected_tail()
        assert pres_pcm[n_frames - 1, 0] == pytest.approx(0.5, abs=1e-5)
        np.testing.assert_allclose(pres_pcm[n_frames:], renderer_pres.expected_tail(), atol=1e-5)


class TestFormatsSampleRatesAndEdgeCases:
    """Tests for Task 6: mono/stereo, 44.1/48/96 kHz, silence, 10 ms clip, corrupt file, Unicode/space paths."""

    @pytest.mark.parametrize("channels", [1, 2])
    @pytest.mark.parametrize("sr", [44100, 48000, 96000])
    def test_mono_stereo_and_sample_rates(self, tmp_path: Path, channels: int, sr: int) -> None:
        in_wav = tmp_path / f"in_{channels}ch_{sr}.wav"
        out_wav = tmp_path / f"out_{channels}ch_{sr}.wav"
        n_samples = sr // 10 + 37
        _write_synthetic_wav(in_wav, sample_rate=sr, samples=n_samples, channels=channels)

        params = AuroraParams(
            output=OutputSpec(format=OutputFormat.WAV, sample_rate=sr, bit_depth=24),
        )
        rep = convert_audio(in_wav, out_wav, params, block_size=1024)
        assert rep.input.channels == channels
        assert rep.input.sample_rate == sr
        assert rep.output.channels == 2
        assert rep.output.sample_rate == sr
        assert rep.output.samples == n_samples
        assert rep.timeline.sync_within_tolerance is True

    def test_digital_silence_and_10ms_extreme_short_clip(self, tmp_path: Path) -> None:
        sr = 48000
        # 1. Pure silence
        silent_in = tmp_path / "silent.wav"
        silent_out = tmp_path / "silent_out.wav"
        _write_synthetic_wav(silent_in, sample_rate=sr, samples=4800, channels=2, amplitude=0.0, impulse_at=None)
        rep_silent = convert_audio(silent_in, silent_out, AuroraParams())
        assert rep_silent.loudness_before.is_silent is True
        assert rep_silent.loudness_before.integrated_lufs is None
        assert rep_silent.loudness_after.is_silent is True
        assert rep_silent.output.samples == 4800

        # 2. 10 ms ultra-short clip (480 samples at 48 kHz), even when latency_samples (600) > clip length (480)!
        short_in = tmp_path / "short_10ms.wav"
        short_out = tmp_path / "short_10ms_out.wav"
        _write_synthetic_wav(short_in, sample_rate=sr, samples=480, channels=2, amplitude=0.4, impulse_at=100)
        rep_short = convert_audio(
            short_in,
            short_out,
            AuroraParams(output=OutputSpec(format=OutputFormat.WAV, sample_rate=sr, bit_depth=32)),
            block_size=1024,
            simulated_latency_samples=600,
        )
        assert rep_short.input.samples == 480
        assert rep_short.output.samples == 480
        assert rep_short.timeline.engine_latency_samples == 600
        assert rep_short.timeline.sample_diff == 0

    def test_non_ascii_and_spaced_paths_and_corrupt_input(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        special_dir = tmp_path / "极光 空间音频 (Aurora Binaural) 测试目录"
        in_wav = special_dir / "输入 音频 样片 01.wav"
        out_wav = special_dir / "输出 双耳 结果 01.wav"
        _write_synthetic_wav(in_wav, sample_rate=48000, samples=9600, channels=2)

        code = run_cli(["convert", str(in_wav), "--output", str(out_wav), "--preset", "surround"])
        captured = capsys.readouterr()
        assert code == int(ExitCode.OK)
        assert out_wav.exists()
        payload = json.loads(captured.out)
        assert payload["ok"] is True
        assert payload["output"]["samples"] == 9600

        # Corrupt audio file -> ExitCode.INPUT_ERROR (3)
        corrupt_file = special_dir / "损坏文件 corrupt.wav"
        corrupt_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt garbage-not-valid-pcm")
        code_bad = run_cli(["convert", str(corrupt_file), "--output", str(special_dir / "never.wav")])
        bad_out = json.loads(capsys.readouterr().out)
        assert code_bad == int(ExitCode.INPUT_ERROR)
        assert bad_out["ok"] is False
        assert bad_out["error"]["exit_code"] == 3


class TestCliCommandsCompareGuiBatchAndRemote:
    """Tests for Task 4: `compare`, `gui`, `batch` (partial failure & Ctrl-C cleanup), and `--server` / `job status`."""

    def test_gui_missing_build_exit_code_2(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AURORA_WEB_DIR", str(tmp_path / "missing"))
        code = run_cli(["gui"])
        captured = capsys.readouterr()
        assert code == int(ExitCode.PARAM_ERROR)
        data = json.loads(captured.out)
        assert data["ok"] is False
        assert data["error"]["exit_code"] == 2
        assert data["error"]["code"] == "GUI_BUILD_MISSING"

    def test_compare_bs1770_equal_loudness_matching(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        in_wav = tmp_path / "music_clip.wav"
        ab_dir = tmp_path / "ab_compare"
        # Create 1.5s stereo signal at 48 kHz (BS.1770 gating uses 400ms blocks)
        _write_synthetic_wav(in_wav, sample_rate=48000, samples=72000, channels=2, amplitude=0.35)

        # Test via compare_audio with a renderer that attenuates by -6 dB to prove loudness matching compensates it
        attenuating_renderer = _GainDelayTestRenderer(gain_db=-6.0, latency_samples=64, tail_samples=0)
        comp_rep = compare_audio(
            in_wav,
            ab_dir,
            AuroraParams(preset=PresetSpec(id=PresetId("room"), version="1")),
            renderer=attenuating_renderer,
        )
        assert Path(comp_rep.dry_output).exists()
        assert Path(comp_rep.effect_output).exists()
        assert comp_rep.dry_loudness_after.integrated_lufs is not None
        assert comp_rep.effect_loudness_after.integrated_lufs is not None
        assert comp_rep.loudness_diff_lu <= 0.2
        assert (comp_rep.effect_loudness_after.true_peak_dbtp or -99.0) <= -0.9

        # Also test CLI `aurora compare` entrypoint and overwrite protection
        code_exists = run_cli(["compare", str(in_wav), "--preset", "room", "--output-dir", str(ab_dir)])
        _ = capsys.readouterr()
        assert code_exists == int(ExitCode.INPUT_ERROR)

        code_ok = run_cli(
            ["compare", str(in_wav), "--preset", "room", "--output-dir", str(ab_dir), "--overwrite"]
        )
        out_json = json.loads(capsys.readouterr().out)
        assert code_ok == int(ExitCode.OK)
        assert out_json["ok"] is True
        assert out_json["loudness_diff_lu"] <= 0.2

    def test_batch_partial_failure_and_ctrl_c_temp_cleanup(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        in_dir = tmp_path / "batch_in"
        out_dir = tmp_path / "batch_out"
        in_dir.mkdir()
        out_dir.mkdir()

        _write_synthetic_wav(in_dir / "01_good.wav", sample_rate=48000, samples=4800)
        (in_dir / "02_corrupt.wav").write_bytes(b"NOT_A_WAV_FILE")
        _write_synthetic_wav(in_dir / "03_good.wav", sample_rate=48000, samples=4800)

        # Pre-create output for 03_good.wav to verify overwrite protection per item
        existing_out = out_dir / "03_good.wav"
        existing_out.write_bytes(b"DO_NOT_OVERWRITE")

        code = run_cli(["batch", str(in_dir), "--output-dir", str(out_dir), "--preset", "room", "--format", "wav"])
        batch_json = json.loads(capsys.readouterr().out)
        assert code == int(ExitCode.PROCESSING_ERROR)
        assert batch_json["ok"] is False
        assert batch_json["total"] == 3
        assert batch_json["succeeded"] == 1
        assert batch_json["failed"] == 2
        assert (out_dir / "01_good.wav").exists()
        assert existing_out.read_bytes() == b"DO_NOT_OVERWRITE"

        # Simulate Ctrl-C during encoding and verify temp files `.aurora_tmp_*` are cleaned up and exit code is 130
        leftover_tmp = out_dir / ".aurora_tmp_simulated_leftover.wav"
        leftover_tmp.write_bytes(b"temp")
        with patch("aurora_binaural.cli.convert_audio", side_effect=KeyboardInterrupt()):
            code_cancel = run_cli(
                ["batch", str(in_dir), "--output-dir", str(out_dir), "--overwrite"]
            )
            cancel_json = json.loads(capsys.readouterr().out)
            assert code_cancel == int(ExitCode.CANCELED)
            assert cancel_json["error"]["exit_code"] == 130
            assert list(out_dir.glob(".aurora_tmp_*")) == []

    def test_remote_convert_and_job_status_with_mock_server(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        in_wav = tmp_path / "local_input.wav"
        _write_synthetic_wav(in_wav, sample_rate=48000, samples=4800)
        wav_bytes = in_wav.read_bytes()

        state: dict[str, Any] = {
            "uploaded_bytes": b"",
            "auth_headers": [],
            "idempotency_keys": [],
            "job_payload": None,
            "poll_count": 0,
            "canceled": False,
        }

        class _MockAuroraApiHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                auth = self.headers.get("Authorization")
                if auth:
                    state["auth_headers"].append(auth)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length > 0 else b""

                if self.path == "/v1/assets":
                    state["uploaded_bytes"] = body
                    resp = {"ok": True, "asset_id": "asset_mock_001", "size_bytes": len(body)}
                    self._send_json(201, resp)
                elif self.path == "/v1/jobs":
                    state["idempotency_keys"].append(self.headers.get("Idempotency-Key"))
                    state["job_payload"] = json.loads(body.decode("utf-8"))
                    self._send_json(202, {"ok": True, "job_id": "job_mock_001", "status": "queued"})
                elif self.path == "/v1/jobs/job_mock_001/cancel":
                    state["canceled"] = True
                    self._send_json(200, {"ok": True, "job_id": "job_mock_001", "status": "canceled"})
                else:
                    self._send_json(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Not found"}})

            def do_GET(self) -> None:
                if self.path == "/v1/jobs/job_mock_001":
                    state["poll_count"] += 1
                    status = "running" if state["poll_count"] == 1 else "succeeded"
                    self._send_json(
                        200,
                        {"ok": True, "job_id": "job_mock_001", "status": status, "progress": 0.5 if status == "running" else 1.0},
                    )
                elif self.path == "/v1/jobs/job_mock_001/result":
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(wav_bytes)))
                    self.end_headers()
                    self.wfile.write(wav_bytes)
                elif self.path == "/v1/jobs/job_mock_001/report":
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "status": "succeeded",
                            "job_id": "job_mock_001",
                            "preset": state["job_payload"]["preset"],
                            "output": {"format": "wav", "samples": 4800},
                        },
                    )
                else:
                    self._send_json(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown job"}})

            def _send_json(self, code: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _MockAuroraApiHandler)
        port = server.server_address[1]
        server_url = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            monkeypatch.setenv("AURORA_API_KEY", "secret_test_token")
            remote_out = tmp_path / "remote_out.wav"
            remote_rep = tmp_path / "remote_report.json"

            # 1. aurora convert --server
            code_conv = run_cli(
                [
                    "convert",
                    str(in_wav),
                    "--server",
                    server_url,
                    "--preset",
                    "room",
                    "--strength",
                    "0.4",
                    "--output",
                    str(remote_out),
                    "--report",
                    str(remote_rep),
                ]
            )
            conv_json = json.loads(capsys.readouterr().out)
            assert code_conv == int(ExitCode.OK)
            assert conv_json["ok"] is True
            assert conv_json["job_id"] == "job_mock_001"
            assert remote_out.exists()
            assert remote_out.read_bytes() == wav_bytes
            assert remote_rep.exists()
            assert state["uploaded_bytes"] == wav_bytes
            assert "Bearer secret_test_token" in state["auth_headers"]
            assert state["idempotency_keys"][0] is not None
            assert state["job_payload"]["input"] == {"asset_id": "asset_mock_001"}

            # 2. aurora job status
            code_status = run_cli(["job", "status", "job_mock_001", "--server", server_url, "--json"])
            status_json = json.loads(capsys.readouterr().out)
            assert code_status == int(ExitCode.OK)
            assert status_json["job_id"] == "job_mock_001"
            assert status_json["status"] == "succeeded"

            # 3. Unknown remote job -> HTTP 404 -> ExitCode.REMOTE_ERROR (5)
            code_404 = run_cli(["job", "status", "missing_job", "--server", server_url, "--json"])
            err_404 = json.loads(capsys.readouterr().out)
            assert code_404 == int(ExitCode.REMOTE_ERROR)
            assert err_404["error"]["exit_code"] == 5
        finally:
            server.shutdown()
            server.server_close()


class TestFfmpegVersionChecksFAg1AndFAg2:
    """Tests for Task 5 (F-AG-1 & F-AG-2): FFmpeg < 7 detection, degradation warning, and strict rejection."""

    def test_parse_ffmpeg_version_variants(self) -> None:
        v9 = parse_ffmpeg_version_line("ffmpeg version 9.0.2 Copyright (c) 2000-2026 the FFmpeg developers")
        assert (v9.major, v9.minor, v9.patch) == (9, 0, 2)
        assert v9.meets_m4a_requirement is True

        v7_deb = parse_ffmpeg_version_line("ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2025")
        assert (v7_deb.major, v7_deb.minor, v7_deb.patch) == (7, 1, 5)
        assert v7_deb.meets_m4a_requirement is True

        v4_ubuntu = parse_ffmpeg_version_line("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021")
        assert (v4_ubuntu.major, v4_ubuntu.minor, v4_ubuntu.patch) == (4, 4, 2)
        assert v4_ubuntu.meets_m4a_requirement is False

    def test_ffmpeg_under_7_m4a_degradation_warning_and_strict_mode(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        in_wav = tmp_path / "in.wav"
        _write_synthetic_wav(in_wav, sample_rate=48000, samples=12000)

        monkeypatch.setenv("AURORA_SIMULATE_FFMPEG_VERSION", "4.4.2-0ubuntu0.22.04.1")

        # 1. Default mode with FFmpeg < 7 on M4A output: succeeds with explicit degradation warning in sync_warning & stderr
        out_m4a = tmp_path / "degraded.m4a"
        code_warn = run_cli(["convert", str(in_wav), "--output", str(out_m4a)])
        captured_warn = capsys.readouterr()
        assert code_warn == int(ExitCode.OK)
        assert "FFMPEG_VERSION_DEGRADED" in captured_warn.err
        warn_json = json.loads(captured_warn.out)
        assert warn_json["timeline"]["sync_warning"] is not None
        assert "4.4.2" in warn_json["timeline"]["sync_warning"]

        # 2. Strict mode (--strict-ffmpeg) with FFmpeg < 7 on M4A output: fails with ExitCode.PROCESSING_ERROR (4)
        out_strict = tmp_path / "strict.m4a"
        code_strict = run_cli(["convert", str(in_wav), "--output", str(out_strict), "--strict-ffmpeg"])
        captured_strict = capsys.readouterr()
        assert code_strict == int(ExitCode.PROCESSING_ERROR)
        strict_json = json.loads(captured_strict.out)
        assert strict_json["ok"] is False
        assert strict_json["error"]["code"] == "FFMPEG_VERSION_TOO_OLD"
        assert strict_json["error"]["exit_code"] == 4
        assert not out_strict.exists()
