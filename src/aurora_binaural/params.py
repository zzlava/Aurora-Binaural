"""Unified Pydantic v2 parameter models for Aurora Binaural CLI, GUI, and HTTP API.

Implements docs/plan.md Sections 6.8, 6.10, 8, 9, and 10:
- preset: PresetSpec (TOML catalog, versioned; TOML default)
- strength: perceptual spatial intensity in [0.0, 1.0] (dry/wet mixing forbidden)
- seed: explicit PRNG seed for deterministic FDN reverb and decorrelation
- output: OutputSpec (wav / m4a / mp3, separated format & container, sample_rate, bit_depth, bitrate)
- timing: TimingSpec (mode=preserve, tail=trim|preserve)
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aurora_binaural.catalog import CATALOG, DEFAULT_PRESET_ID

# Compatibility constants and JSON Schema enum are generated from the same TOML
# catalog, including legacy explicit selections absent from the default cards.
PresetId = StrEnum("PresetId", {key.upper(): key for key in CATALOG})  # type: ignore[misc]


class OutputFormat(StrEnum):
    """Supported output audio formats (docs/plan.md Section 4.1 & 10)."""

    WAV = "wav"
    M4A = "m4a"
    MP3 = "mp3"
    FLAC = "flac"
    ALAC = "alac"
    AIFF = "aiff"


class OutputContainer(StrEnum):
    """Supported output audio container formats (docs/plan.md Section 4.1)."""

    WAV = "wav"
    M4A = "m4a"
    MP3 = "mp3"
    FLAC = "flac"
    AIFF = "aiff"


class TimingMode(StrEnum):
    """Timeline synchronization mode (docs/plan.md Section 8 & 10)."""

    PRESERVE = "preserve"


class TailPolicy(StrEnum):
    """Reverb tail handling policy (docs/plan.md Section 8 & 10)."""

    TRIM = "trim"
    PRESERVE = "preserve"


class RenderMode(StrEnum):
    """Job rendering mode (docs/plan.md Section 10)."""

    ENHANCE = "enhance"


SUPPORTED_SAMPLE_RATES: tuple[int, ...] = (44100, 48000, 96000)
SUPPORTED_WAV_BIT_DEPTHS: tuple[int, ...] = (16, 24, 32)


class PresetMetadata(BaseModel):
    """Versioned preset metadata returned by `aurora presets` and `GET /v1/presets`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PresetId
    name: str
    version: str
    default_strength: float = Field(ge=0.0, le=1.0)
    description: str
    algorithm_summary: str
    route: str
    is_default: bool = False


PRESET_CATALOG: dict[PresetId, PresetMetadata] = {
    PresetId(entry.id): PresetMetadata(
        id=PresetId(entry.id), name=entry.name, version=entry.version,
        default_strength=entry.default_strength, description=entry.description,
        algorithm_summary=f"Route {entry.route} / TOML", route=entry.route,
        is_default=entry.id == DEFAULT_PRESET_ID,
    )
    for entry in sorted(CATALOG.values(), key=lambda row: (row.route != "V", row.id)) if entry.public
}


class PresetSpec(BaseModel):
    """Preset selection and version specification (docs/plan.md Section 6.10 & 10)."""

    model_config = ConfigDict(extra="forbid")

    id: PresetId = Field(
        default=PresetId(DEFAULT_PRESET_ID),
        description="Preset identifier from presets/*.toml; default from route_v.toml. room is the classic route B.",
    )
    version: str = Field(
        default="1",
        min_length=1,
        max_length=32,
        pattern=r"^[0-9]+(\.[0-9]+)*$",
        description="Explicit preset version string for deterministic reproduction.",
    )


class OutputSpec(BaseModel):
    """Output encoding and container specification (docs/plan.md Section 4.1, 8, 10).

    Explicitly separates audio format/codec from container per Section 4.1.
    Raw .aac is not permitted as an output container because ADTS lacks edit-list
    gapless metadata; AAC output always uses the M4A (MP4) container.
    """

    model_config = ConfigDict(extra="forbid")

    format: OutputFormat = Field(default=OutputFormat.WAV, description="wav, flac, alac (M4A), aiff, m4a (AAC), mp3")
    container: OutputContainer | None = Field(default=None, description="Derived from format; ALAC uses m4a.")
    sample_rate: Literal["source", 44100, 48000, 96000] = Field(
        default="source", description="source (default), 44100, 48000 or 96000 Hz. DSP runs at 48000 Hz.",
    )
    bit_depth: Literal[16, 24, 32, "32f"] | None = Field(
        default=24, description="Lossless output: 16/24; WAV also accepts 32f (legacy 32). 16 bit uses TPDF dither.",
    )
    bitrate_kbps: Annotated[int, Field(ge=64, le=512)] | None = Field(default=None)

    @property
    def is_lossless(self) -> bool:
        return self.format in (OutputFormat.WAV, OutputFormat.FLAC, OutputFormat.ALAC, OutputFormat.AIFF)

    @property
    def extension(self) -> str:
        return "m4a" if self.format == OutputFormat.ALAC else self.format.value

    @property
    def media_type(self) -> str:
        return {"wav": "audio/wav", "flac": "audio/flac", "alac": "audio/mp4", "aiff": "audio/aiff",
                "m4a": "audio/mp4", "mp3": "audio/mpeg"}[self.format.value]

    def resolve_sample_rate(self, source_rate: int) -> int:
        rate = source_rate if self.sample_rate == "source" else self.sample_rate
        if self.format == OutputFormat.MP3 and rate > 48000:
            raise ValueError("MP3 supports at most 48 kHz; cannot preserve higher source rates; select 44100/48000 or a lossless format.")
        if self.format == OutputFormat.M4A and rate > 96000:
            raise ValueError("AAC cannot preserve source rates above 96 kHz; select a lower rate or a lossless format.")
        return rate

    @model_validator(mode="after")
    def _validate_and_normalize_output(self) -> OutputSpec:
        expected = OutputContainer.M4A if self.format == OutputFormat.ALAC else OutputContainer(self.format.value)
        if self.container is None:
            self.container = expected
        elif self.container != expected:
            raise ValueError(f"Output container '{self.container}' is incompatible with format '{self.format}' (expected '{expected}').")
        if self.sample_rate != "source":
            self.resolve_sample_rate(self.sample_rate)
        if self.is_lossless:
            if self.bit_depth is None:
                self.bit_depth = 24
            if self.bit_depth in (32, "32f") and self.format != OutputFormat.WAV:
                raise ValueError("32f output is supported only in WAV; FLAC, ALAC and AIFF support 16/24 bit.")
            if self.bitrate_kbps is not None:
                raise ValueError("bitrate_kbps cannot be specified for lossless output.")
        else:
            self.bit_depth = None
            if self.bitrate_kbps is None:
                self.bitrate_kbps = 256
        return self

    @property
    def codec_name(self) -> str:
        if self.format == OutputFormat.WAV:
            return {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le", "32f": "pcm_f32le"}[self.bit_depth or 24]
        if self.format == OutputFormat.AIFF:
            return "pcm_s16be" if self.bit_depth == 16 else "pcm_s24be"
        return {OutputFormat.FLAC: "flac", OutputFormat.ALAC: "alac", OutputFormat.M4A: "aac",
                OutputFormat.MP3: "libmp3lame"}[self.format]

    @property
    def audio_format_label(self) -> str:
        if self.is_lossless:
            return f"{self.format.value.upper()} {self.bit_depth}-bit"
        return "AAC-LC" if self.format == OutputFormat.M4A else "MP3"


class TimingSpec(BaseModel):
    """Timeline synchronization and reverb tail policy (docs/plan.md Section 8 & 10)."""

    model_config = ConfigDict(extra="forbid")

    mode: TimingMode = Field(
        default=TimingMode.PRESERVE,
        description="Timeline synchronization mode. 'preserve' guarantees exact sample alignment.",
    )
    tail: TailPolicy = Field(
        default=TailPolicy.TRIM,
        description="Reverb tail strategy: 'trim' keeps exact input sample length with a short fade-out; 'preserve' appends tail_samples.",
    )
    fade_out_ms: Annotated[float, Field(ge=0.0, le=500.0)] = Field(
        default=10.0,
        description="Short fade-out duration in milliseconds applied at the end when tail='trim' to prevent clicks.",
    )


class InputSpec(BaseModel):
    """Input reference for HTTP API jobs or CLI invocations (docs/plan.md Section 10)."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Uploaded asset identifier for HTTP API jobs (e.g. 'asset_example').",
    )
    path: str | None = Field(
        default=None,
        min_length=1,
        description="Local filesystem path for CLI / local worker jobs.",
    )

    @model_validator(mode="after")
    def _check_at_least_one_source(self) -> InputSpec:
        if not self.asset_id and not self.path:
            raise ValueError("InputSpec requires at least one of 'asset_id' or 'path'.")
        return self


class PreviewSpec(BaseModel):
    """Source-timeline excerpt; duration is bounded before creating a queue entry."""

    model_config = ConfigDict(extra="forbid")
    start_seconds: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    duration_seconds: float = Field(default=10.0, gt=0.0, le=20.0, allow_inf_nan=False)


class PreviewRequest(PreviewSpec):
    """POST /v1/previews request. Always emits a matched stereo float32 WAV pair."""

    asset_id: str = Field(min_length=1, max_length=128)
    preset: PresetSpec = Field(default_factory=PresetSpec)
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    seed: int = Field(default=20261016, ge=0, le=4294967295)


class AuroraParams(BaseModel):
    """Unified parameter model shared by CLI, Web GUI, and HTTP API (docs/plan.md Sections 9 & 10).

    Supports both standalone render parameter validation and full `POST /v1/jobs` payloads:
    ```json
    {
      "api_version": "v1",
      "mode": "enhance",
      "input": {"asset_id": "asset_example"},
      "preset": {"id": "room", "version": "1"},
      "strength": 0.4,
      "seed": 20261016,
      "output": {"format": "wav", "sample_rate": 48000, "bit_depth": 24},
      "timing": {"mode": "preserve", "tail": "trim"}
    }
    ```
    """

    model_config = ConfigDict(extra="forbid")

    preview: PreviewSpec | None = None

    api_version: Literal["v1"] = Field(
        default="v1",
        description="API and schema contract version.",
    )
    mode: RenderMode = Field(
        default=RenderMode.ENHANCE,
        description="Processing mode ('enhance' for V1 stereo/mono binaural enhancement).",
    )
    input: InputSpec | None = Field(
        default=None,
        description="Optional input asset or file reference (required for POST /v1/jobs).",
    )
    preset: PresetSpec = Field(
        default_factory=PresetSpec,
        description="Spatial preset identifier and version.",
    )
    strength: Annotated[float, Field(ge=0.0, le=1.0)] | None = Field(
        default=None,
        description=(
            "Perceptual effect strength in [0.0, 1.0]. Mapped to upmix width, ambient ratio, "
            "early reflections, and DRR offset. Dry/wet mixing is forbidden. "
            "When 0.0, spatial DSP is bypassed. Defaults to the selected preset's default_strength."
        ),
    )
    seed: Annotated[int, Field(ge=0, le=4294967295)] = Field(
        default=20261016,
        description="Explicit 32-bit unsigned seed for deterministic FDN delay lines and decorrelation filters.",
    )
    output: OutputSpec = Field(
        default_factory=OutputSpec,
        description="Output format, container, sample rate, bit depth, and bitrate settings.",
    )
    timing: TimingSpec = Field(
        default_factory=TimingSpec,
        description="Timeline synchronization mode and reverb tail handling policy.",
    )

    @field_validator("preset", mode="before")
    @classmethod
    def _coerce_preset_shorthand(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"id": value, "version": "1"}
        if isinstance(value, PresetId):
            return {"id": value.value, "version": "1"}
        return value

    @model_validator(mode="after")
    def _apply_preset_default_strength(self) -> AuroraParams:
        if self.strength is None:
            self.strength = CATALOG[self.preset.id].default_strength
        return self

    @property
    def effective_strength(self) -> float:
        """Return validated non-null strength in [0.0, 1.0]."""
        if self.strength is None:
            return CATALOG[self.preset.id].default_strength
        return self.strength


def get_params_json_schema() -> dict[str, Any]:
    """Generate the canonical JSON Schema (Draft 2020-12) for AuroraParams."""
    schema = AuroraParams.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://aurora-binaural.local/schemas/v1/params.schema.json"
    return schema


def write_params_json_schema(target_path: Path) -> dict[str, Any]:
    """Export the unified parameter JSON Schema to `target_path`."""
    schema = get_params_json_schema()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return schema
