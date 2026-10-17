"""Bounded A/B excerpts with source-origin renderer state and BS.1770 matching."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from aurora_binaural.engine import FloatBlock, Renderer, RendererStreamProcessor, StubRenderer
from aurora_binaural.exit_codes import OperationCanceledError, ProcessingError
from aurora_binaural.media import (
    StreamingF32LeEncoder,
    measure_loudness_file,
    probe_audio,
    probe_stream_metadata,
    stream_decode_f32le,
)
from aurora_binaural.params import AuroraParams, OutputSpec
from aurora_binaural.pipeline import get_git_commit_sha
from aurora_binaural.report import ConversionReport, EngineMetadata, PreviewReport, TimelineVerification


def render_preview(
    input_path: Path,
    effect_path: Path,
    original_path: Path,
    params: AuroraParams,
    *,
    renderer: Renderer | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[float], None] | None = None,
) -> ConversionReport:
    """Advance the engine from t=0, retaining at most 20 seconds per A/B side.

    Prefix processing is deliberate: seeking a fresh core to the excerpt would
    reset orbit phase, seeded decorrelation and reverb state. No prefix is kept
    in memory or on disk. Both files are always stereo 48 kHz float32 WAV.
    """
    started = time.monotonic()
    assert params.preview is not None
    spec = params.preview
    sr = 48000
    start = round(spec.start_seconds * sr)
    end = start + max(1, round(spec.duration_seconds * sr))
    metadata, _ = probe_stream_metadata(input_path)
    channels = int(metadata["channels"])
    active_renderer = renderer or StubRenderer()
    processor = RendererStreamProcessor(
        active_renderer, sample_rate=sr, input_channels=channels, params=params, cancel_check=cancel_check
    )
    dry_parts: list[FloatBlock] = []
    wet_parts: list[FloatBlock] = []
    in_offset = 0
    out_offset = 0

    def check_canceled() -> None:
        if cancel_check and cancel_check():
            raise OperationCanceledError("Preview canceled.")

    def collect_wet(block: FloatBlock) -> None:
        nonlocal out_offset
        lo, hi = max(0, start - out_offset), min(len(block), end - out_offset)
        if hi > lo:
            wet_parts.append(block[lo:hi].copy())
        out_offset += len(block)

    decoder = stream_decode_f32le(input_path, target_sample_rate=sr)
    with contextlib.closing(decoder):
        for block, _, _ in decoder:
            check_canceled()
            block = block[: max(0, end - in_offset)]
            lo, hi = max(0, start - in_offset), min(len(block), end - in_offset)
            if hi > lo:
                dry = block[lo:hi]
                dry_parts.append(np.repeat(dry, 2, axis=1) if channels == 1 else dry.copy())
            for rendered in processor.push_block(block):
                collect_wet(rendered)
            in_offset += len(block)
            if progress:
                progress(0.8 * min(1.0, in_offset / end))
            if in_offset >= end:
                break
    for rendered in processor.finish():
        collect_wet(rendered)
    if not dry_parts or not wet_parts:
        raise ProcessingError("试听起点超出音频长度。", error_code="PREVIEW_OUT_OF_RANGE")
    dry_pcm = np.ascontiguousarray(np.concatenate(dry_parts), dtype=np.float32)
    wet_pcm = np.ascontiguousarray(np.concatenate(wet_parts), dtype=np.float32)
    if len(dry_pcm) != len(wet_pcm):
        raise ProcessingError("Preview A/B lengths differ.", error_code="PREVIEW_LENGTH_MISMATCH")
    output_spec = OutputSpec(bit_depth=32)

    def encode(path: Path, pcm: FloatBlock, gain_db: float = 0.0) -> None:
        check_canceled()
        with StreamingF32LeEncoder(
            sample_rate=sr, channels=2, output_spec=output_spec, target_path=path, gain_db=gain_db
        ) as encoder:
            for offset in range(0, len(pcm), 4096):
                check_canceled()
                encoder.write_block(pcm[offset : offset + 4096])

    encode(original_path, dry_pcm)
    encode(effect_path, wet_pcm)
    dry_before = measure_loudness_file(original_path)
    wet_before = measure_loudness_file(effect_path)
    target: float | None = None
    dry_gain = wet_gain = 0.0
    match_status: str
    if dry_before.is_silent and wet_before.is_silent:
        match_status = "both_silent"
    elif dry_before.integrated_lufs is None or wet_before.integrated_lufs is None:
        raise ProcessingError("原声与效果仅一侧为静音，无法进行等响度匹配。", error_code="PREVIEW_SILENCE_MISMATCH")
    else:
        match_status = "matched"
        target = min(dry_before.integrated_lufs, wet_before.integrated_lufs)
        ceiling_excess = max(
            0.0,
            (dry_before.true_peak_dbtp if dry_before.true_peak_dbtp is not None else -120.0)
            + target
            - dry_before.integrated_lufs
            + 1.0,
            (wet_before.true_peak_dbtp if wet_before.true_peak_dbtp is not None else -120.0)
            + target
            - wet_before.integrated_lufs
            + 1.0,
        )
        target -= ceiling_excess
        dry_gain = target - dry_before.integrated_lufs
        wet_gain = target - wet_before.integrated_lufs
        encode(original_path, dry_pcm, dry_gain)
        encode(effect_path, wet_pcm, wet_gain)
    dry_after = measure_loudness_file(original_path)
    wet_after = measure_loudness_file(effect_path)
    diff = (
        abs(dry_after.integrated_lufs - wet_after.integrated_lufs)
        if dry_after.integrated_lufs is not None and wet_after.integrated_lufs is not None
        else None
    )
    if diff is not None and diff > 0.2:
        raise ProcessingError("BS.1770 loudness match exceeds 0.2 LU.", error_code="PREVIEW_LOUDNESS_MISMATCH")
    original = probe_audio(original_path)
    effect = probe_audio(effect_path)
    if original.samples != effect.samples or original.samples != len(dry_pcm):
        raise ProcessingError("Preview readback sample mismatch.", error_code="PREVIEW_LENGTH_MISMATCH")
    if progress:
        progress(0.95)
    return ConversionReport(
        input=original,
        output=effect,
        engine=EngineMetadata(git_commit=get_git_commit_sha()),
        params=params,
        timeline=TimelineVerification(
            mode=params.timing.mode,
            tail_policy=params.timing.tail,
            engine_latency_samples=processor.latency,
            engine_tail_samples=processor.tail,
            extra_tail_samples_kept=0,
            input_samples=original.samples,
            expected_output_samples=original.samples,
            decoded_readback_samples=effect.samples,
            raw_decoded_samples=effect.samples,
            sample_diff=0,
            raw_sample_diff=0,
            codec_priming_samples=0,
            codec_trailing_padding_samples=0,
            residual_priming_shift_samples=0,
            compensation_strategy="Source-origin render, latency compensated; matched float32 WAV excerpt.",
            sync_within_tolerance=True,
        ),
        preview=PreviewReport(
            start_seconds=start / sr,
            requested_duration_seconds=spec.duration_seconds,
            actual_duration_seconds=len(dry_pcm) / sr,
            start_sample=start,
            samples=len(dry_pcm),
            sample_rate=sr,
            original=original,
            original_loudness=dry_after,
            target_lufs=target,
            original_gain_db=dry_gain,
            effect_gain_db=wet_gain,
            loudness_diff_lu=diff,
            match_status=match_status,
        ),
        loudness_before=dry_before,
        loudness_after=wet_after,
        elapsed_ms=(time.monotonic() - started) * 1000,
        created_at=datetime.now(UTC).isoformat(),
    )
