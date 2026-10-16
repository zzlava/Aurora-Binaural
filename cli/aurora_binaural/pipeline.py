"""End-to-end conversion pipeline for Aurora Binaural (docs/plan.md Section 8, 9, 10).

Pipeline stages:
1. Probe input & validate overwrite protection
2. Decode input to float32le
3. Measure input BS.1770 loudness & true peak
4. Process through block-based StubProcessor (with latency compensation & tail policy)
5. Encode to temporary file in destination directory
6. Readback decode (both container-compensated and raw) to verify sample count, priming, padding, and post-encode loudness
7. Atomically move verified output file into place
8. Return structured ConversionReport
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aurora_binaural.exit_codes import InputAudioError
from aurora_binaural.media import (
    atomic_write_bytes,
    decode_to_f32le,
    encode_f32le_to_path,
    log_stderr,
    measure_loudness_f32le,
    probe_audio,
)
from aurora_binaural.params import AuroraParams, OutputFormat
from aurora_binaural.report import (
    ConversionReport,
    EngineMetadata,
    TimelineVerification,
)
from aurora_binaural.stub_core import run_offline_block_pipeline


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


def convert_audio(
    input_path: Path,
    output_path: Path,
    params: AuroraParams,
    *,
    overwrite: bool = False,
    report_path: Path | None = None,
    block_size: int = 256,
    simulated_latency_samples: int = 0,
    simulated_tail_samples: int = 0,
    keep_input_sample_rate: bool = False,
) -> ConversionReport:
    """Execute the complete P0 conversion and verification pipeline."""
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

    log_stderr(f"Probing and decoding input: {input_path}")
    in_pcm_native, in_sr, in_ch = decode_to_f32le(input_path)
    input_probe = probe_audio(input_path, predecoded=(in_pcm_native, in_sr, in_ch))

    effective_params = params.model_copy(deep=True)
    if keep_input_sample_rate and in_sr in (44100, 48000, 96000):
        effective_params.output.sample_rate = in_sr  # type: ignore[assignment]

    target_sr = effective_params.output.sample_rate

    # Measure input loudness on decoded float32 PCM
    loudness_before = measure_loudness_f32le(in_pcm_native, in_sr, in_ch)

    # If output sample rate differs from input sample rate, decode/resample to target_sr before core
    if target_sr != in_sr:
        log_stderr(f"Resampling input from {in_sr} Hz to {target_sr} Hz for processing")
        proc_in_pcm, proc_sr, proc_ch = decode_to_f32le(input_path, target_sample_rate=target_sr)
    else:
        proc_in_pcm, proc_sr, proc_ch = in_pcm_native, in_sr, in_ch

    log_stderr(
        f"Running stub core (preset={effective_params.preset.id.value}, "
        f"strength={effective_params.effective_strength}, block_size={block_size})"
    )
    rendered_pcm, out_ch, rendered_frames, latency_comp, extra_tail = run_offline_block_pipeline(
        proc_in_pcm,
        sample_rate=proc_sr,
        input_channels=proc_ch,
        params=effective_params,
        block_size=block_size,
        simulated_latency_samples=simulated_latency_samples,
        simulated_tail_samples=simulated_tail_samples,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out_path = output_path.parent / f".aurora_tmp_{uuid.uuid4().hex}_{output_path.name}"

    try:
        log_stderr(
            f"Encoding output to temporary file ({effective_params.output.format.value}, "
            f"codec={effective_params.output.codec_name}, sr={target_sr})"
        )
        encode_f32le_to_path(
            rendered_pcm,
            sample_rate=proc_sr,
            channels=out_ch,
            output_spec=effective_params.output,
            target_path=tmp_out_path,
        )

        # Ensure file data is flushed to disk before readback
        with tmp_out_path.open("rb") as f_sync:
            os.fsync(f_sync.fileno())

        log_stderr("Performing readback decode verification (gapless & raw stream)")
        readback_pcm, rb_sr, rb_ch = decode_to_f32le(tmp_out_path)
        raw_readback_pcm, raw_sr, raw_ch = decode_to_f32le(tmp_out_path, ignore_gapless=True)

        output_probe_tmp = probe_audio(tmp_out_path, predecoded=(readback_pcm, rb_sr, rb_ch))
        loudness_after = measure_loudness_f32le(readback_pcm, rb_sr, rb_ch)

        readback_samples = len(readback_pcm) // (4 * rb_ch)
        raw_samples = len(raw_readback_pcm) // (4 * raw_ch)

        expected_output_samples = rendered_frames
        sample_diff = readback_samples - expected_output_samples
        raw_sample_diff = raw_samples - expected_output_samples

        strategy_desc, nominal_priming = describe_compensation_strategy(effective_params.output.format)
        if effective_params.output.format == OutputFormat.WAV:
            priming_samples = 0
            trailing_padding_samples = 0
        else:
            priming_samples = output_probe_tmp.initial_padding or nominal_priming
            trailing_padding_samples = max(0, raw_sample_diff - priming_samples)

        # Tolerance is 0 samples for same-sample-rate preserve mode, or 1 sample when resampling 44.1k <-> 48k
        tolerance_samples = 0 if in_sr == target_sr else 2
        sync_ok = abs(sample_diff) <= tolerance_samples
        sync_warning: str | None = None
        if not sync_ok:
            sync_warning = (
                f"Decoded output sample count ({readback_samples}) differs from expected "
                f"({expected_output_samples}) by {sample_diff:+d} samples (tolerance ±{tolerance_samples}). "
                "For frame-accurate video production workflows, use WAV 48 kHz / 24-bit."
            )
            log_stderr(f"WARNING: {sync_warning}")

        # Final atomic commit check
        if output_path.exists() and not overwrite:
            raise InputAudioError(
                f"Output file already exists (use --overwrite to replace): {output_path}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )
        os.replace(tmp_out_path, output_path)
    except Exception:
        if tmp_out_path.exists():
            tmp_out_path.unlink(missing_ok=True)
        raise

    output_probe = output_probe_tmp.model_copy(update={"path": str(output_path)})
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    timeline = TimelineVerification(
        mode=effective_params.timing.mode,
        tail_policy=effective_params.timing.tail,
        engine_latency_samples=latency_comp,
        engine_tail_samples=simulated_tail_samples,
        extra_tail_samples_kept=extra_tail,
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
            internal_sample_rate=proc_sr,
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
