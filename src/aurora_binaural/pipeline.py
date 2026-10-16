"""Streaming conversion and equal-loudness comparison pipeline for Aurora Binaural (docs/plan.md Sections 7, 8, 9, 10).

Pipeline stages:
1. Probe input stream metadata, validate overwrite protection, and verify FFmpeg version (F-AG-1)
2. Measure input BS.1770 loudness & true peak via streaming FFmpeg ebur128
3. Stream-decode input in blocks (default 1024 frames) -> `Renderer` (`process` + `flush`) with
   latency compensation and tail policy (`trim` with 10 ms fade-out vs `preserve`) -> stream-encode
   to temporary file in destination directory (peak RAM O(block_size))
4. Stream readback decode (both container-compensated and raw) to verify sample count, priming,
   padding, and post-encode loudness
5. Atomically move verified output file into place; clean up temporary files on any error or SIGINT
6. Return structured `ConversionReport` (or `CompareReport` for `aurora compare`)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from aurora_binaural.engine import Renderer, RendererStreamProcessor, StubRenderer
from aurora_binaural.exit_codes import InputAudioError
from aurora_binaural.media import (
    StreamingF32LeEncoder,
    atomic_write_bytes,
    check_m4a_ffmpeg_support,
    compute_sha256,
    count_decoded_samples_streaming,
    log_stderr,
    measure_loudness_file,
    probe_audio,
    probe_stream_metadata,
    stream_decode_f32le,
)
from aurora_binaural.params import AuroraParams, OutputFormat
from aurora_binaural.report import (
    AudioProbeInfo,
    CompareReport,
    ConversionReport,
    EngineMetadata,
    TimelineVerification,
)


def get_git_commit_sha() -> str:
    """Retrieve current git HEAD short SHA, or 'unknown' if unavailable."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def describe_compensation_strategy(fmt: OutputFormat) -> tuple[str, int]:
    """Return the documented codec priming/padding compensation strategy and nominal raw priming."""
    if fmt == OutputFormat.WAV:
        return (
            "Uncompressed PCM in RIFF WAV container: zero encoder priming (0 samples) and "
            "zero frame padding (0 samples). Direct 1:1 sample frame preservation.",
            0,
        )
    if fmt == OutputFormat.M4A:
        return (
            "AAC-LC in MP4/M4A container using native FFmpeg 'aac' encoder (1024-sample MDCT window): "
            "encoder writes 1024 priming samples into MP4 'elst' (edit list) + 'sgpd'/'sbgp' roll group "
            "and exact final packet duration in 'stts'. Standard edit-list-aware decoders strip all 1024 "
            "priming samples and trailing frame padding automatically; fallback raw stream pipeline trims "
            "[1024 : 1024 + expected_output_samples]. Note: macOS 'aac_at' (2112 priming, un-trimmed trailing "
            "padding) is explicitly avoided for cross-platform determinism.",
            1024,
        )
    return (
        "MP3 via libmp3lame (1152-sample MPEG-1 Layer III granule frame): encoder delay is 576 samples "
        "plus 529-sample synthesis filterbank decoder delay (total round-trip priming = 1105 samples), "
        "written with trailing padding into the LAME/Xing Info header (-write_xing 1). Gapless-aware "
        "decoders strip 1105 leading samples and trailing padding to recover exact sample count; "
        "fallback raw decoder trims [1105 : 1105 + expected_output_samples].",
        1105,
    )


def _build_probe_info_from_metadata(
    path: Path,
    stream: dict[str, object],
    fmt_info: dict[str, object],
    *,
    samples: int,
    sample_rate: int,
    channels: int,
) -> AudioProbeInfo:
    duration = samples / sample_rate if sample_rate > 0 else 0.0
    codec = str(stream.get("codec_name", "unknown"))
    container = str(fmt_info.get("format_name", "unknown"))

    if codec.startswith("pcm_"):
        fmt_label = "wav"
    elif codec == "aac":
        fmt_label = "aac"
    elif codec == "mp3":
        fmt_label = "mp3"
    else:
        fmt_label = codec

    raw_bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    bit_depth: int | None = None
    if raw_bits is not None:
        try:
            parsed_bits = int(str(raw_bits))
            if parsed_bits > 0:
                bit_depth = parsed_bits
        except (TypeError, ValueError):
            bit_depth = None

    init_pad = int(str(stream.get("initial_padding") or 0))
    trail_pad = int(str(stream.get("trailing_padding") or 0))
    ch_layout = stream.get("channel_layout")

    return AudioProbeInfo(
        path=str(path),
        sha256=compute_sha256(path),
        format=fmt_label,
        container=container,
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        channel_layout=str(ch_layout) if ch_layout is not None else None,
        samples=samples,
        duration_seconds=round(duration, 9),
        bit_depth=bit_depth,
        size_bytes=path.stat().st_size,
        initial_padding=init_pad,
        trailing_padding=trail_pad,
    )


def convert_audio(
    input_path: Path,
    output_path: Path,
    params: AuroraParams,
    *,
    overwrite: bool = False,
    report_path: Path | None = None,
    block_size: int = 1024,
    simulated_latency_samples: int = 0,
    simulated_tail_samples: int = 0,
    keep_input_sample_rate: bool = False,
    renderer: Renderer | None = None,
    strict_ffmpeg: bool = False,
    post_gain_db: float = 0.0,
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[float], None] | None = None,
) -> ConversionReport:
    """Execute the streaming conversion and timeline verification pipeline."""
    t0 = time.perf_counter()

    if not input_path.exists():
        raise InputAudioError(f"Input file does not exist: {input_path}", error_code="INPUT_NOT_FOUND")
    if not input_path.is_file():
        raise InputAudioError(f"Input path is not a file: {input_path}", error_code="INPUT_NOT_A_FILE")

    if output_path.exists() and not overwrite:
        raise InputAudioError(
            f"Output file already exists (use --overwrite to replace): {output_path}",
            error_code="OUTPUT_ALREADY_EXISTS",
        )

    if report_path is not None and report_path.exists() and not overwrite:
        raise InputAudioError(
            f"Report file already exists (use --overwrite to replace): {report_path}",
            error_code="REPORT_ALREADY_EXISTS",
        )

    log_stderr(f"Probing input metadata: {input_path}")
    in_stream_meta, in_fmt_meta = probe_stream_metadata(input_path)
    in_sr = int(in_stream_meta["sample_rate"])
    in_ch = int(in_stream_meta["channels"])
    in_codec = str(in_stream_meta.get("codec_name", "")).lower()

    effective_params = params.model_copy(deep=True)
    if keep_input_sample_rate and in_sr in (44100, 48000, 96000):
        effective_params.output.sample_rate = in_sr  # type: ignore[assignment]

    target_sr = effective_params.output.sample_rate

    # F-AG-1: Check FFmpeg version when handling M4A/AAC input or output
    involves_m4a = (
        effective_params.output.format == OutputFormat.M4A
        or input_path.suffix.lower() in (".m4a", ".aac", ".mp4")
        or in_codec == "aac"
    )
    m4a_version_warning = check_m4a_ffmpeg_support(involves_m4a=involves_m4a, strict=strict_ffmpeg)

    log_stderr(f"Measuring input BS.1770 loudness: {input_path}")
    loudness_before = measure_loudness_file(input_path)

    active_renderer: Renderer = renderer or StubRenderer(
        latency_samples=simulated_latency_samples,
        tail_samples=simulated_tail_samples,
    )
    stream_proc = RendererStreamProcessor(
        active_renderer,
        sample_rate=target_sr,
        input_channels=in_ch,
        params=effective_params,
        block_size=block_size,
        simulated_latency_samples=simulated_latency_samples,
        simulated_tail_samples=simulated_tail_samples,
        cancel_check=cancel_check,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out_path = output_path.parent / f".aurora_tmp_{uuid.uuid4().hex}_{output_path.name}"

    try:
        log_stderr(
            f"Streaming decode -> renderer (preset={effective_params.preset.id.value}, "
            f"strength={effective_params.effective_strength}, block_size={block_size}) -> "
            f"encode ({effective_params.output.format.value}, sr={target_sr})"
        )
        input_duration = float(in_fmt_meta.get("duration", 0) or 0)
        expected_frames = max(1.0, input_duration * target_sr)
        with StreamingF32LeEncoder(
            sample_rate=target_sr,
            channels=2,
            output_spec=effective_params.output,
            target_path=tmp_out_path,
            gain_db=post_gain_db,
        ) as encoder:
            for in_block, _, _ in stream_decode_f32le(
                input_path,
                target_sample_rate=target_sr,
                block_frames=block_size,
            ):
                for out_chunk in stream_proc.push_block(in_block):
                    encoder.write_block(out_chunk)
                if progress:
                    progress(min(0.8, 0.8 * stream_proc.input_frames / expected_frames))

            for out_chunk in stream_proc.finish():
                encoder.write_block(out_chunk)

        if in_sr == target_sr:
            native_input_samples = stream_proc.input_frames
        else:
            native_input_samples, _, _ = count_decoded_samples_streaming(input_path)

        input_probe = _build_probe_info_from_metadata(
            input_path,
            in_stream_meta,
            in_fmt_meta,
            samples=native_input_samples,
            sample_rate=in_sr,
            channels=in_ch,
        )

        with tmp_out_path.open("rb") as f_sync:
            os.fsync(f_sync.fileno())

        if progress:
            progress(0.9)
        log_stderr("Performing streaming readback decode verification (gapless & raw stream)")
        out_stream_meta, out_fmt_meta = probe_stream_metadata(tmp_out_path)
        readback_samples, rb_sr, rb_ch = count_decoded_samples_streaming(tmp_out_path, ignore_gapless=False)
        if effective_params.output.format == OutputFormat.WAV:
            raw_samples = readback_samples
        else:
            raw_samples, _, _ = count_decoded_samples_streaming(tmp_out_path, ignore_gapless=True)

        output_probe_tmp = _build_probe_info_from_metadata(
            tmp_out_path,
            out_stream_meta,
            out_fmt_meta,
            samples=readback_samples,
            sample_rate=rb_sr,
            channels=rb_ch,
        )
        loudness_after = measure_loudness_file(tmp_out_path)

        expected_output_samples = stream_proc.output_frames
        sample_diff = readback_samples - expected_output_samples
        raw_sample_diff = raw_samples - expected_output_samples

        strategy_desc, nominal_priming = describe_compensation_strategy(effective_params.output.format)
        if effective_params.output.format == OutputFormat.WAV:
            priming_samples = 0
            trailing_padding_samples = 0
        else:
            priming_samples = output_probe_tmp.initial_padding or nominal_priming
            trailing_padding_samples = max(0, raw_sample_diff - priming_samples)

        tolerance_samples = 0 if in_sr == target_sr else 2
        sync_ok = abs(sample_diff) <= tolerance_samples
        sync_warning: str | None = None
        if not sync_ok:
            sync_warning = (
                f"Decoded output sample count ({readback_samples}) differs from expected "
                f"({expected_output_samples}) by {sample_diff:+d} samples (tolerance ±{tolerance_samples}). "
                "For frame-accurate video production workflows, use WAV 48 kHz / 24-bit."
            )
            if m4a_version_warning:
                sync_warning = f"{sync_warning} {m4a_version_warning}"
            log_stderr(f"WARNING: {sync_warning}")
        elif m4a_version_warning is not None:
            sync_warning = m4a_version_warning

        if output_path.exists() and not overwrite:
            raise InputAudioError(
                f"Output file already exists (use --overwrite to replace): {output_path}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )
        os.replace(tmp_out_path, output_path)
    finally:
        if tmp_out_path.exists():
            tmp_out_path.unlink(missing_ok=True)

    output_probe = output_probe_tmp.model_copy(update={"path": str(output_path)})
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    timeline = TimelineVerification(
        mode=effective_params.timing.mode,
        tail_policy=effective_params.timing.tail,
        engine_latency_samples=stream_proc.latency,
        engine_tail_samples=stream_proc.tail,
        extra_tail_samples_kept=stream_proc.extra_tail_kept,
        input_samples=input_probe.samples,
        expected_output_samples=expected_output_samples,
        decoded_readback_samples=readback_samples,
        raw_decoded_samples=raw_samples,
        sample_diff=sample_diff,
        raw_sample_diff=raw_sample_diff,
        codec_priming_samples=priming_samples,
        codec_trailing_padding_samples=trailing_padding_samples,
        residual_priming_shift_samples=0 if sync_ok else sample_diff,
        compensation_strategy=strategy_desc,
        tolerance_samples=tolerance_samples,
        sync_within_tolerance=sync_ok,
        sync_warning=sync_warning,
    )

    report = ConversionReport(
        input=input_probe,
        output=output_probe,
        engine=EngineMetadata(
            git_commit=get_git_commit_sha(),
            block_size_frames=block_size,
            internal_sample_rate=target_sr,
        ),
        params=effective_params,
        timeline=timeline,
        loudness_before=loudness_before,
        loudness_after=loudness_after,
        elapsed_ms=elapsed_ms,
        attempts=1,
        created_at=datetime.now(UTC).isoformat(),
    )

    if report_path is not None:
        report_bytes = (report.model_dump_json(indent=2) + "\n").encode("utf-8")
        atomic_write_bytes(report_path, report_bytes, overwrite=overwrite)
        log_stderr(f"Wrote conversion report to {report_path}")

    log_stderr(f"Completed conversion in {elapsed_ms:.1f} ms -> {output_path}")
    return report


def _encode_dry_reference_streaming(
    input_path: Path,
    dry_target_path: Path,
    params: AuroraParams,
    *,
    block_size: int = 1024,
    gain_db: float = 0.0,
    overwrite: bool = False,
) -> None:
    """Stream-decode `input_path` to 2-channel stereo at `params.output.sample_rate` and atomically write `dry_target_path`."""
    if dry_target_path.exists() and not overwrite:
        raise InputAudioError(
            f"Output file already exists (use --overwrite to replace): {dry_target_path}",
            error_code="OUTPUT_ALREADY_EXISTS",
        )
    dry_target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dry_target_path.parent / f".aurora_tmp_{uuid.uuid4().hex}_{dry_target_path.name}"
    target_sr = params.output.sample_rate
    try:
        with StreamingF32LeEncoder(
            sample_rate=target_sr,
            channels=2,
            output_spec=params.output,
            target_path=tmp_path,
            gain_db=gain_db,
        ) as encoder:
            for block, _, in_ch in stream_decode_f32le(
                input_path,
                target_sample_rate=target_sr,
                block_frames=block_size,
            ):
                stereo = np.repeat(block, 2, axis=1) if in_ch == 1 else block
                encoder.write_block(stereo)

        with tmp_path.open("rb") as f_sync:
            os.fsync(f_sync.fileno())
        if dry_target_path.exists() and not overwrite:
            raise InputAudioError(
                f"Output file already exists (use --overwrite to replace): {dry_target_path}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )
        os.replace(tmp_path, dry_target_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def compare_audio(
    input_path: Path,
    output_dir: Path,
    params: AuroraParams,
    *,
    overwrite: bool = False,
    block_size: int = 1024,
    renderer: Renderer | None = None,
    strict_ffmpeg: bool = False,
) -> CompareReport:
    """Generate BS.1770 equal-loudness matched dry reference and spatial effect audio files (`aurora compare`)."""
    if not input_path.exists():
        raise InputAudioError(f"Input file does not exist: {input_path}", error_code="INPUT_NOT_FOUND")
    if not input_path.is_file():
        raise InputAudioError(f"Input path is not a file: {input_path}", error_code="INPUT_NOT_A_FILE")

    fmt_ext = params.output.format.value
    preset_id = params.preset.id.value
    dry_path = output_dir / f"{input_path.stem}_dry.{fmt_ext}"
    effect_path = output_dir / f"{input_path.stem}_{preset_id}.{fmt_ext}"

    for target in (dry_path, effect_path):
        if target.exists() and not overwrite:
            raise InputAudioError(
                f"Compare output file already exists (use --overwrite to replace): {target}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )

    output_dir.mkdir(parents=True, exist_ok=True)

    # First pass in a temporary workspace to measure exact pre-match dry (2ch) and effect BS.1770 loudness & true peak
    with tempfile.TemporaryDirectory(prefix="aurora_compare_", dir=str(output_dir)) as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        pre_dry_wav = tmp_dir / "pre_dry.wav"
        pre_wet_wav = tmp_dir / "pre_wet.wav"

        probe_wav_params = params.model_copy(deep=True)
        probe_wav_params.output.format = OutputFormat.WAV
        probe_wav_params.output.container = probe_wav_params.output.container.__class__.WAV if probe_wav_params.output.container else None
        probe_wav_params.output.bit_depth = 32
        probe_wav_params.output.bitrate_kbps = None

        _encode_dry_reference_streaming(
            input_path,
            pre_dry_wav,
            probe_wav_params,
            block_size=block_size,
            gain_db=0.0,
            overwrite=True,
        )
        dry_before = measure_loudness_file(pre_dry_wav)

        _ = convert_audio(
            input_path=input_path,
            output_path=pre_wet_wav,
            params=probe_wav_params,
            overwrite=True,
            block_size=block_size,
            renderer=renderer,
            strict_ffmpeg=strict_ffmpeg,
        )
        wet_before = measure_loudness_file(pre_wet_wav)

        dry_gain_db = 0.0
        effect_gain_db = 0.0
        target_lufs: float | None = None

        if (
            not dry_before.is_silent
            and not wet_before.is_silent
            and dry_before.integrated_lufs is not None
            and wet_before.integrated_lufs is not None
        ):
            # Match to dry integrated LUFS unless True Peak ceiling (-1.0 dBTP) requires lowering both equally
            desired_lufs = dry_before.integrated_lufs
            dry_tp = dry_before.true_peak_dbtp if dry_before.true_peak_dbtp is not None else -120.0
            wet_tp = wet_before.true_peak_dbtp if wet_before.true_peak_dbtp is not None else -120.0

            dry_headroom_over = max(0.0, (dry_tp + (desired_lufs - dry_before.integrated_lufs)) - (-1.0))
            wet_headroom_over = max(0.0, (wet_tp + (desired_lufs - wet_before.integrated_lufs)) - (-1.0))
            attenuation = max(dry_headroom_over, wet_headroom_over)

            target_lufs = round(desired_lufs - attenuation, 2)
            dry_gain_db = round(target_lufs - dry_before.integrated_lufs, 4)
            effect_gain_db = round(target_lufs - wet_before.integrated_lufs, 4)

    # Second pass: write loudness-matched dry and effect files to destination
    _encode_dry_reference_streaming(
        input_path,
        dry_path,
        params,
        block_size=block_size,
        gain_db=dry_gain_db,
        overwrite=overwrite,
    )
    dry_after = measure_loudness_file(dry_path)

    if renderer is not None:
        renderer.reset()
    effect_report = convert_audio(
        input_path=input_path,
        output_path=effect_path,
        params=params,
        overwrite=overwrite,
        block_size=block_size,
        renderer=renderer,
        strict_ffmpeg=strict_ffmpeg,
        post_gain_db=effect_gain_db,
    )
    wet_after = effect_report.loudness_after

    if dry_after.integrated_lufs is not None and wet_after.integrated_lufs is not None:
        loudness_diff = round(abs(wet_after.integrated_lufs - dry_after.integrated_lufs), 2)
    else:
        loudness_diff = 0.0

    # Ensure probe_audio is used on dry_path to verify its integrity as well
    _ = probe_audio(dry_path)

    return CompareReport(
        input=str(input_path),
        output_dir=str(output_dir),
        dry_output=str(dry_path),
        effect_output=str(effect_path),
        preset=params.preset,
        strength=params.effective_strength,
        target_lufs=target_lufs,
        dry_gain_db=dry_gain_db,
        effect_gain_db=effect_gain_db,
        loudness_diff_lu=loudness_diff,
        dry_loudness_before=dry_before,
        dry_loudness_after=dry_after,
        effect_loudness_before_match=wet_before,
        effect_loudness_after=wet_after,
        report=effect_report,
    )
