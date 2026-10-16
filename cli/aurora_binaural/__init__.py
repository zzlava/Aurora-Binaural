"""Aurora Binaural Python package (`aurora_binaural`).

Provides unified Pydantic v2 parameter models, passthrough stub processor,
FFmpeg/FFprobe media pipeline, codec timeline verification, and the `aurora` CLI.
"""

from __future__ import annotations

from aurora_binaural.exit_codes import (
    AuroraError,
    ExitCode,
    InputAudioError,
    OperationCanceledError,
    ParameterValidationError,
    ProcessingError,
    RemoteServiceError,
)
from aurora_binaural.params import (
    PRESET_CATALOG,
    AuroraParams,
    InputSpec,
    OutputContainer,
    OutputFormat,
    OutputSpec,
    PresetId,
    PresetMetadata,
    PresetSpec,
    RenderMode,
    TailPolicy,
    TimingMode,
    TimingSpec,
    get_params_json_schema,
    write_params_json_schema,
)
from aurora_binaural.pipeline import convert_audio
from aurora_binaural.report import (
    AudioProbeInfo,
    ConversionReport,
    EngineMetadata,
    HrtfDataInfo,
    LoudnessMetrics,
    TimelineVerification,
)
from aurora_binaural.stub_core import RenderConfig, StubProcessor, run_offline_block_pipeline

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AudioProbeInfo",
    "AuroraError",
    "AuroraParams",
    "ConversionReport",
    "EngineMetadata",
    "ExitCode",
    "HrtfDataInfo",
    "InputAudioError",
    "InputSpec",
    "LoudnessMetrics",
    "OperationCanceledError",
    "OutputContainer",
    "OutputFormat",
    "OutputSpec",
    "PRESET_CATALOG",
    "ParameterValidationError",
    "PresetId",
    "PresetMetadata",
    "PresetSpec",
    "ProcessingError",
    "RemoteServiceError",
    "RenderConfig",
    "RenderMode",
    "StubProcessor",
    "TailPolicy",
    "TimelineVerification",
    "TimingMode",
    "TimingSpec",
    "convert_audio",
    "get_params_json_schema",
    "run_offline_block_pipeline",
    "write_params_json_schema",
]
