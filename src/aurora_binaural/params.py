"""Unified Pydantic v2 parameter models for Aurora Binaural CLI, GUI, and HTTP API.

Implements docs/plan.md Sections 6.8, 6.10, 8, 9, and 10:
- preset: PresetSpec (room / surround / orbit, versioned)
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


class PresetId(StrEnum):
    """Built-in spatial preset identifiers (docs/plan.md Section 6.10)."""

    ROOM = "room"
    SURROUND = "surround"
    ORBIT = "orbit"


class OutputFormat(StrEnum):
    """Supported output audio formats (docs/plan.md Section 4.1 & 10)."""

    WAV = "wav"
    M4A = "m4a"
    MP3 = "mp3"


class OutputContainer(StrEnum):
    """Supported output audio container formats (docs/plan.md Section 4.1)."""

    WAV = "wav"
    M4A = "m4a"
    MP3 = "mp3"


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
    max_orbit_deg_per_sec: float | None = None


PRESET_CATALOG: dict[PresetId, PresetMetadata] = {
    PresetId.ROOM: PresetMetadata(
        id=PresetId.ROOM,
        name="自然空间",
        version="1",
        default_strength=0.4,
        description="上混（窄）+ 小房间早期反射 + 短 FDN + 音色保护；中置稳定",
        algorithm_summary="STFT upmix (±30°..±45°) + shoebox early reflections + short FDN + diffuse-field EQ",
    ),
    PresetId.SURROUND: PresetMetadata(
        id=PresetId.SURROUND,
        name="包围感",
        version="1",
        default_strength=0.5,
        description="上混（宽）+ 环境成分推向侧后方 + 中等房间",
        algorithm_summary="STFT upmix (±30°..±60°) + lateral/rear 3OA bed + medium room FDN",
    ),
    PresetId.ORBIT: PresetMetadata(
        id=PresetId.ORBIT,
        name="缓慢环绕",
        version="1",
        default_strength=0.3,
        description="整个声场或单一对象做 Ambisonics 旋转；转速上限约 10°/s",
        algorithm_summary="3rd-order Ambisonics spherical harmonic rotation + MagLS binaural decode",
        max_orbit_deg_per_sec=10.0,
    ),
}


class PresetSpec(BaseModel):
    """Preset selection and version specification (docs/plan.md Section 6.10 & 10)."""

    model_config = ConfigDict(extra="forbid")

    id: PresetId = Field(
        default=PresetId.ROOM,
        description="Preset identifier: 'room' (自然空间), 'surround' (包围感), or 'orbit' (缓慢环绕).",
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

    format: OutputFormat = Field(
        default=OutputFormat.WAV,
        description="Output format: 'wav' (PCM), 'm4a' (AAC-LC in M4A container), or 'mp3' (MPEG-1 Layer III).",
    )
    container: OutputContainer | None = Field(
        default=None,
        description="Output container ('wav', 'm4a', 'mp3'). Automatically derived from format if omitted.",
    )
    sample_rate: Literal[44100, 48000, 96000] = Field(
        default=48000,
        description="Output sample rate in Hz (44100, 48000, or 96000). Internal engine runs at 48000 Hz.",
    )
    bit_depth: Literal[16, 24, 32] | None = Field(
        default=24,
        description="PCM bit depth for WAV output (16, 24, or 32). Ignored/null for lossy M4A/MP3 outputs.",
    )
    bitrate_kbps: Annotated[int, Field(ge=64, le=512)] | None = Field(
        default=None,
        description="Target bitrate in kbps for lossy codecs (M4A/MP3). Defaults to 256 kbps when encoding lossy formats.",
    )

    @model_validator(mode="after")
    def _validate_and_normalize_output(self) -> OutputSpec:
        expected_container = OutputContainer(self.format.value)
        if self.container is None:
            self.container = expected_container
        elif self.container != expected_container:
            raise ValueError(
                f"Output container '{self.container}' is incompatible with format '{self.format}' "
                f"(expected '{expected_container}')."
            )

        if self.format == OutputFormat.WAV:
            if self.bit_depth is None:
                self.bit_depth = 24
            if self.bitrate_kbps is not None:
                raise ValueError("bitrate_kbps cannot be specified for uncompressed WAV output.")
        else:
            # Lossy formats (m4a / mp3): bit_depth does not apply; default bitrate is 256 kbps.
            if self.bitrate_kbps is None:
                self.bitrate_kbps = 256

        return self

    @property
    def codec_name(self) -> str:
        """Return the canonical FFmpeg audio encoder name for this output specification."""
        if self.format == OutputFormat.WAV:
            if self.bit_depth == 16:
                return "pcm_s16le"
            if self.bit_depth == 32:
                return "pcm_f32le"
            return "pcm_s24le"
        if self.format == OutputFormat.M4A:
            # Explicitly use FFmpeg native 'aac' encoder rather than macOS 'aac_at'
            # so priming (1024) and MP4 edit-list trimming behave identically on macOS and Linux.
            return "aac"
        return "libmp3lame"

    @property
    def audio_format_label(self) -> str:
        """Human-readable codec/format label separated from container (Section 4.1)."""
        if self.format == OutputFormat.WAV:
            return f"PCM {self.bit_depth or 24}-bit"
        if self.format == OutputFormat.M4A:
            return "AAC-LC"
        return "MP3"


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
            self.strength = PRESET_CATALOG[self.preset.id].default_strength
        return self

    @property
    def effective_strength(self) -> float:
        """Return validated non-null strength in [0.0, 1.0]."""
        if self.strength is None:
            return PRESET_CATALOG[self.preset.id].default_strength
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
