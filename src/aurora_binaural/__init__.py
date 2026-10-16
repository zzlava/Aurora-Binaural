"""Aurora Binaural Python package (`aurora_binaural`).

Provides unified Pydantic v2 parameter models, `Renderer` protocol & numpy `StubRenderer`,
streaming FFmpeg/FFprobe media pipeline, codec timeline verification, and the `aurora` CLI.
"""

from __future__ import annotations

from aurora_binaural.engine import (
    DelayTestRenderer,
    RenderConfig,
    Renderer,
    RendererStreamProcessor,
    StubProcessor,
    StubRenderer,
    run_offline_block_pipeline,
)
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
from aurora_binaural.pipeline import compare_audio, convert_audio
from aurora_binaural.report import (
    AudioProbeInfo,
    CompareReport,
    ConversionReport,
    EngineMetadata,
    HrtfDataInfo,
    LoudnessMetrics,
    TimelineVerification,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "AudioProbeInfo",
    "AuroraError",
    "AuroraParams",
    "CompareReport",
    "ConversionReport",
    "DelayTestRenderer",
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
    "Renderer",
    "RendererStreamProcessor",
    "StubProcessor",
    "StubRenderer",
    "TailPolicy",
    "TimelineVerification",
    "TimingMode",
    "TimingSpec",
    "compare_audio",
    "convert_audio",
    "get_params_json_schema",
    "run_offline_block_pipeline",
    "write_params_json_schema",
]
