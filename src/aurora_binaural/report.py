"""Structured report and probe models for Aurora Binaural (docs/plan.md Section 8, 9, 10)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aurora_binaural.params import AuroraParams, PresetSpec, TailPolicy, TimingMode


class AudioProbeInfo(BaseModel):
    """Audio file metadata and stream properties from ffprobe + decoded verification."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path inspected.")
    sha256: str = Field(description="SHA-256 hex digest of the encoded file on disk.")
    format: str = Field(description="Audio format label (e.g. 'wav', 'aac', 'mp3').")
    container: str = Field(
        description="Container format name from ffprobe (e.g. 'wav', 'mov,mp4,m4a,3gp,3g2,mj2', 'mp3')."
    )
    codec: str = Field(description="Codec name from ffprobe (e.g. 'pcm_s24le', 'aac', 'mp3').")
    sample_rate: int = Field(gt=0, description="Sample rate in Hz.")
    channels: int = Field(gt=0, description="Number of audio channels.")
    channel_layout: str | None = Field(default=None, description="Channel layout string if reported.")
    samples: int = Field(ge=0, description="Exact decoded PCM sample frames per channel.")
    duration_seconds: float = Field(ge=0.0, description="Exact duration in seconds (samples / sample_rate).")
    bit_depth: int | None = Field(
        default=None, description="Bits per sample for PCM formats, or None for lossy formats."
    )
    size_bytes: int = Field(ge=0, description="File size in bytes.")
    initial_padding: int = Field(
        default=0,
        ge=0,
        description="Encoder initial padding (priming) samples reported by stream headers.",
    )
    trailing_padding: int = Field(
        default=0, ge=0, description="Encoder trailing padding samples reported by stream headers."
    )


class LoudnessMetrics(BaseModel):
    """ITU-R BS.1770-4 loudness and true peak measurements via ffmpeg ebur128 filter."""

    model_config = ConfigDict(extra="forbid")

    integrated_lufs: float | None = Field(
        description="Integrated loudness in LUFS per ITU-R BS.1770-4 (None if silent)."
    )
    loudness_range_lu: float = Field(
        default=0.0,
        ge=0.0,
        description="Loudness range (LRA) in LU.",
    )
    true_peak_dbtp: float | None = Field(description="Maximum true peak in dBTP (None if pure digital silence / -inf).")
    is_silent: bool = Field(
        default=False,
        description="True if signal is below absolute gating threshold (-70 LUFS / -inf dBTP).",
    )
    method: Literal["ITU-R BS.1770-4 (ffmpeg ebur128)"] = "ITU-R BS.1770-4 (ffmpeg ebur128)"


class HrtfDataInfo(BaseModel):
    """HRTF dataset provenance metadata (docs/plan.md Section 5.3 & 10)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="none (P2 passthrough stub)", description="HRTF dataset identifier.")
    version: str = Field(default="0.0.0-stub", description="HRTF dataset version.")
    sha256: str | None = Field(default=None, description="SHA-256 hash of the .sofa file.")
    license: str | None = Field(default=None, description="Dataset license identifier.")


class EngineMetadata(BaseModel):
    """Core rendering engine provenance metadata (docs/plan.md Section 10)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="aurora-stub-core", description="Engine implementation name.")
    version: str = Field(default="0.2.0-p2-stub", description="Engine version string.")
    git_commit: str = Field(description="Git commit SHA of the repository.")
    is_stub: bool = Field(default=True, description="True when running passthrough stub core.")
    block_size_frames: int = Field(default=1024, gt=0, description="Block size in frames used during block processing.")
    internal_sample_rate: int = Field(default=48000, gt=0, description="Engine processing sample rate in Hz.")
    hrtf: HrtfDataInfo = Field(default_factory=HrtfDataInfo, description="HRTF dataset metadata.")


class TimelineVerification(BaseModel):
    """Timeline synchronization and codec priming/padding verification results (Section 8 & 10)."""

    model_config = ConfigDict(extra="forbid")

    mode: TimingMode = Field(description="Configured timing synchronization mode.")
    tail_policy: TailPolicy = Field(description="Configured reverb tail policy.")
    engine_latency_samples: int = Field(ge=0, description="Engine lookahead/processing latency compensated in samples.")
    engine_tail_samples: int = Field(ge=0, description="Engine reverb tail length in samples.")
    extra_tail_samples_kept: int = Field(
        ge=0, description="Extra reverb tail samples retained in output (0 when tail='trim')."
    )
    input_samples: int = Field(ge=0, description="Decoded input sample frames per channel.")
    expected_output_samples: int = Field(
        ge=0, description="Expected output sample frames per channel at output sample rate."
    )
    decoded_readback_samples: int = Field(
        ge=0, description="Actual sample frames per channel decoded on default readback."
    )
    raw_decoded_samples: int = Field(
        ge=0,
        description="Raw packet sample frames per channel when ignoring container edit-list / Xing tags.",
    )
    sample_diff: int = Field(
        description="decoded_readback_samples - expected_output_samples (0 means exact timeline match)."
    )
    raw_sample_diff: int = Field(
        description="raw_decoded_samples - expected_output_samples (includes raw priming + trailing padding)."
    )
    codec_priming_samples: int = Field(
        ge=0,
        description="Measured encoder/decoder start delay (priming) in raw stream before container compensation.",
    )
    codec_trailing_padding_samples: int = Field(
        ge=0,
        description="Measured frame-completion trailing padding in raw stream before container compensation.",
    )
    residual_priming_shift_samples: int = Field(
        description="Residual impulse/onset shift in samples after container gapless compensation."
    )
    compensation_strategy: str = Field(description="Applied timeline and codec delay compensation strategy.")
    tolerance_samples: int = Field(ge=0, default=0, description="Allowed sample count deviation tolerance.")
    sync_within_tolerance: bool = Field(
        description="True if abs(sample_diff) <= tolerance_samples and residual shift is within tolerance."
    )
    sync_warning: str | None = Field(
        default=None,
        description="Warning message advising WAV 48kHz/24bit if sync tolerance is exceeded or FFmpeg < 7.",
    )


class ConversionReport(BaseModel):
    """Complete conversion report returned by `aurora convert` and `GET /v1/jobs/{id}/report` (Section 10)."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    status: Literal["succeeded"] = "succeeded"
    input: AudioProbeInfo
    output: AudioProbeInfo
    engine: EngineMetadata
    params: AuroraParams
    timeline: TimelineVerification
    loudness_before: LoudnessMetrics
    loudness_after: LoudnessMetrics
    elapsed_ms: float = Field(ge=0.0, description="Total processing wall-clock time in milliseconds.")
    attempts: int = Field(default=1, ge=1, description="Execution attempt number.")
    created_at: str = Field(description="ISO-8601 UTC timestamp when report was finalized.")
    expires_at: str | None = Field(
        default=None, description="ISO-8601 UTC expiration timestamp for remote job artifacts."
    )


class CompareReport(BaseModel):
    """Report returned by `aurora compare` for BS.1770 equal-loudness A/B comparison (Section 9 & 12)."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    input: str = Field(description="Source audio file path.")
    output_dir: str = Field(description="Directory containing the loudness-matched dry and effect files.")
    dry_output: str = Field(description="Path to loudness-matched dry reference file.")
    effect_output: str = Field(description="Path to loudness-matched spatial effect file.")
    preset: PresetSpec = Field(description="Spatial preset used for the effect file.")
    strength: float = Field(ge=0.0, le=1.0, description="Effective spatial strength.")
    target_lufs: float | None = Field(description="Target integrated loudness in LUFS (None if silent).")
    dry_gain_db: float = Field(description="Gain in dB applied to the dry reference stream.")
    effect_gain_db: float = Field(description="Gain in dB applied to the spatial effect stream.")
    loudness_diff_lu: float = Field(
        ge=0.0,
        description="Absolute BS.1770 integrated loudness difference |effect - dry| in LU after matching.",
    )
    dry_loudness_before: LoudnessMetrics
    dry_loudness_after: LoudnessMetrics
    effect_loudness_before_match: LoudnessMetrics
    effect_loudness_after: LoudnessMetrics
    report: ConversionReport
