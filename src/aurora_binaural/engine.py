"""Core engine Protocol (`Renderer`), numpy-based `StubRenderer`, and `DelayTestRenderer`.

Implements ADR 0002 & docs/plan.md Sections 7 & 8:
- `Renderer` Protocol:
  - `prepare(sample_rate, max_block, config)`
  - `set_params(params)`
  - `process(block) -> block`
  - `latency_samples()`
  - `tail_samples()`
  - `flush() -> block`
  - `reset()`
- `StubRenderer`: pure numpy block passthrough renderer with configurable fixed latency (default 0).
- `DelayTestRenderer`: test-dedicated renderer with known latency and known deterministic tail.
- `RendererStreamProcessor`: streaming block pipeline with latency compensation, 10 ms trim fade-out,
  and preserve-tail flush handling with O(block_size) memory footprint.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from aurora_binaural.catalog import CATALOG
from aurora_binaural.exit_codes import OperationCanceledError, ProcessingError
from aurora_binaural.params import AuroraParams, TailPolicy
from aurora_binaural.resources import asset_directory

FloatBlock = NDArray[np.float32]


def selected_engine() -> str:
    engine = os.environ.get("AURORA_ENGINE", "rust").strip().lower()
    if engine not in ("rust", "stub"):
        raise ProcessingError("AURORA_ENGINE must be rust or stub.", error_code="INVALID_ENGINE")
    return engine


def engine_status(engine: str | None = None) -> dict[str, Any]:
    """Describe the configured engine without rendering or silently changing it."""
    choice = engine or selected_engine()
    if choice == "stub":
        return {"name": "stub", "is_stub": True, "available": True, "message": "效果引擎：桩（直通）"}
    if choice != "rust":
        raise ProcessingError("AURORA_ENGINE must be rust or stub.", error_code="INVALID_ENGINE")
    try:
        importlib.import_module("aurora_core")
    except ImportError:
        return {"name": "rust", "is_stub": False, "available": False,
                "message": "Rust 核心未安装。请运行 bash scripts/build-core.sh；测试流程可显式设置 AURORA_ENGINE=stub（桩）。"}
    return {"name": "rust", "is_stub": False, "available": True, "message": "效果引擎：Rust"}


@dataclass(frozen=True)
class RenderConfig:
    """Configuration passed to `Renderer.prepare()` (docs/plan.md Section 7)."""

    input_channels: int = 2
    output_channels: int = 2
    simulated_latency_samples: int = 0
    simulated_tail_samples: int = 0


@runtime_checkable
class Renderer(Protocol):
    """Protocol for Aurora Binaural block-based spatial audio renderers (ADR 0002 & plan Section 7)."""

    def prepare(self, sample_rate: int, max_block: int, config: RenderConfig) -> None:
        """Prepare internal buffers and state for block processing."""
        ...

    def set_params(self, params: AuroraParams) -> None:
        """Update rendering parameters for subsequent blocks."""
        ...

    def process(self, block: FloatBlock) -> FloatBlock:
        """Process an input float32 block of shape `(frames, in_channels)` and return `(frames, 2)`."""
        ...

    def latency_samples(self) -> int:
        """Return internal lookahead / filter latency in sample frames."""
        ...

    def tail_samples(self) -> int:
        """Return reverb / filter tail length in sample frames."""
        ...

    def flush(self) -> FloatBlock:
        """Flush remaining delayed samples (`latency_samples()`) and reverb tail (`tail_samples()`)."""
        ...

    def reset(self) -> None:
        """Reset delay lines and internal state without reallocating configuration."""
        ...


def _normalize_input_block(block: FloatBlock, expected_in_channels: int, max_block: int) -> FloatBlock:
    """Validate an input numpy block and normalize to stereo `(n_frames, 2)` float32 without loops."""
    arr = np.asarray(block, dtype=np.float32)
    if arr.ndim == 1:
        if expected_in_channels != 1:
            raise ProcessingError(
                f"Expected {expected_in_channels}-channel 2D block, got 1D array of shape {arr.shape}."
            )
        arr = arr[:, np.newaxis]
    elif arr.ndim != 2:
        raise ProcessingError(f"Expected 2D audio block (frames, channels), got shape {arr.shape}.")

    n_frames, in_ch = arr.shape
    if in_ch != expected_in_channels:
        raise ProcessingError(
            f"Block channel count {in_ch} does not match prepared input_channels={expected_in_channels}."
        )
    if n_frames > max_block:
        raise ProcessingError(f"Block size {n_frames} exceeds prepared max_block={max_block}.")
    if n_frames > 0 and not np.all(np.isfinite(arr)):
        raise ProcessingError("Input block contains non-finite (NaN/Inf) samples.", error_code="NON_FINITE_SAMPLES")

    if in_ch == 1:
        return np.repeat(arr, 2, axis=1)
    return np.ascontiguousarray(arr, dtype=np.float32)


class RustRenderer:
    """Validated application adapter for the optional local ``aurora_core`` extension.

    Native construction is deferred until set_params so prepare sees the selected
    route, strength and seed. Both the legacy route B binding and the upcoming
    route= binding are supported; route V is never substituted with route B.
    """

    def __init__(self, *, hrtf_dir: Path | None = None) -> None:
        try:
            self._module = importlib.import_module("aurora_core")
        except ImportError as exc:
            raise ProcessingError(engine_status("rust")["message"], error_code="CORE_NOT_INSTALLED") from exc
        self._hrtf_dir = hrtf_dir or asset_directory("hrtf")
        self._native: Any = None
        self._config = RenderConfig()
        self._sample_rate = 48000
        self._max_block = 1024
        self._prepared = False
        self._flushed = False
        self.hrtf_source = self._hrtf_dir / "sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa"
        self.hrtf_pack = self._hrtf_dir / "sadie_d1_frontal.ahrp"

    def prepare(self, sample_rate: int, max_block: int, config: RenderConfig) -> None:
        if sample_rate <= 0 or max_block <= 0:
            raise ProcessingError("sample_rate and max_block must be positive.")
        if config.input_channels not in (1, 2) or config.output_channels != 2:
            raise ProcessingError("Rust renderer requires mono/stereo input and stereo output.")
        if config.simulated_latency_samples or config.simulated_tail_samples:
            raise ProcessingError("Simulated latency/tail require AURORA_ENGINE=stub.")
        self._sample_rate, self._max_block, self._config = sample_rate, max_block, config
        self._native = None
        self._prepared, self._flushed = True, False

    def set_params(self, params: AuroraParams) -> None:
        if not self._prepared:
            raise ProcessingError("prepare before set_params.")
        if self._native is not None:
            try:
                self._native.set_params(params)
            except Exception as exc:
                raise ProcessingError(f"Rust set_params failed: {exc}", error_code="CORE_ERROR") from exc
            return
        preset_id = str(params.preset.id)
        entry = CATALOG[preset_id]
        route, preset_path = entry.route, entry.preset_path
        kwargs: dict[str, Any] = {
            "strength": params.effective_strength, "seed": params.seed, "preset": preset_id,
        }
        supports_route = "route" in inspect.signature(self._module.Renderer).parameters
        if supports_route:
            kwargs["route"] = route
        elif route != "B":
            raise ProcessingError(
                "已安装的 aurora_core 仅支持路线 B。请构建支持 route='V' 的新版核心，或显式选择经典 room。",
                error_code="CORE_ROUTE_UNSUPPORTED",
            )
        try:
            self._native = self._module.Renderer(str(self.hrtf_pack), str(self.hrtf_source), str(preset_path), **kwargs)
            self._native.prepare(self._sample_rate, self._max_block, self._config)
            self._native.set_params(params)
        except Exception as exc:
            self._native = None
            raise ProcessingError(f"Rust prepare failed: {exc}", error_code="CORE_ERROR") from exc

    def _require_native(self) -> Any:
        if self._native is None:
            raise ProcessingError("prepare and set_params before processing.")
        return self._native

    def _validate_output(self, output: Any, frames: int) -> FloatBlock:
        arr = np.asarray(output)
        if arr.dtype != np.float32 or arr.shape != (frames, 2) or not np.all(np.isfinite(arr)):
            raise ProcessingError("Rust core violated float32 (frames, 2) output contract.", error_code="CORE_CONTRACT_ERROR")
        return np.ascontiguousarray(arr)

    def process(self, block: FloatBlock) -> FloatBlock:
        native = self._require_native()
        if self._flushed:
            raise ProcessingError("reset before process after flush.")
        stereo = _normalize_input_block(block, self._config.input_channels, self._max_block)
        normalized = stereo[:, :1].copy() if self._config.input_channels == 1 else stereo
        try:
            output = native.process(normalized)
        except Exception as exc:
            raise ProcessingError(f"Rust process failed: {exc}", error_code="CORE_ERROR") from exc
        return self._validate_output(output, len(stereo))

    def latency_samples(self) -> int:
        return int(self._require_native().latency_samples())

    def tail_samples(self) -> int:
        return int(self._require_native().tail_samples())

    def flush(self) -> FloatBlock:
        native = self._require_native()
        if self._flushed:
            return np.zeros((0, 2), dtype=np.float32)
        expected = self.latency_samples() + self.tail_samples()
        try:
            output = native.flush()
        except Exception as exc:
            raise ProcessingError(f"Rust flush failed: {exc}", error_code="CORE_ERROR") from exc
        result = self._validate_output(output, expected)
        self._flushed = True
        return result

    def reset(self) -> None:
        self._require_native().reset()
        self._flushed = False


def create_renderer(
    *, engine: str | None = None, hrtf_dir: Path | None = None,
    latency_samples: int = 0, tail_samples: int = 0,
) -> Renderer:
    choice = engine or selected_engine()
    if choice == "stub":
        return StubRenderer(latency_samples=latency_samples, tail_samples=tail_samples)
    if choice != "rust":
        raise ProcessingError("AURORA_ENGINE must be rust or stub.", error_code="INVALID_ENGINE")
    if latency_samples or tail_samples:
        raise ProcessingError("Simulated latency/tail require AURORA_ENGINE=stub.")
    return RustRenderer(hrtf_dir=hrtf_dir)


def renderer_metadata(renderer: Renderer, *, git_commit: str, block_size: int, sample_rate: int) -> Any:
    """Report the renderer actually used, including verified native HRTF provenance."""
    from aurora_binaural.report import EngineMetadata, HrtfDataInfo

    common = {"git_commit": git_commit, "block_size_frames": block_size, "internal_sample_rate": sample_rate}
    if isinstance(renderer, StubRenderer):
        return EngineMetadata(**common)
    if isinstance(renderer, RustRenderer):
        with renderer.hrtf_source.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return EngineMetadata(
            **common, name="aurora_core", version=str(getattr(renderer._module, "__version__", "unversioned")),
            is_stub=False, hrtf=HrtfDataInfo(name="SADIE II D1", version="D1-48K-256tap",
                                           sha256=digest, license="Apache-2.0"),
        )
    return EngineMetadata(**common, name=type(renderer).__name__, version="custom", is_stub=False,
                          hrtf=HrtfDataInfo(name="custom renderer; unspecified HRTF", version="unknown"))


class StubRenderer:
    """Numpy-based passthrough stub renderer implementing `Renderer` (ADR 0002 & P2 Task 2).

    Uses vectorized numpy array slicing and concatenation (no Python per-sample loops).
    Supports a configurable fixed latency (default 0) for testing latency compensation.
    """

    def __init__(self, latency_samples: int = 0, tail_samples: int = 0) -> None:
        if latency_samples < 0 or tail_samples < 0:
            raise ProcessingError("latency_samples and tail_samples must be non-negative.")
        self._init_latency = latency_samples
        self._init_tail = tail_samples
        self._sample_rate: int = 48000
        self._max_block: int = 1024
        self._config: RenderConfig = RenderConfig(
            simulated_latency_samples=latency_samples,
            simulated_tail_samples=tail_samples,
        )
        self._params: AuroraParams | None = None
        self._prepared: bool = False
        self._flushed: bool = False
        self._delay_buf: FloatBlock = np.zeros((latency_samples, 2), dtype=np.float32)

    def prepare(self, sample_rate: int, max_block: int, config: RenderConfig | None = None) -> None:
        """Prepare renderer state before block streaming."""
        if sample_rate <= 0:
            raise ProcessingError(f"Invalid sample_rate for prepare(): {sample_rate}")
        if max_block <= 0:
            raise ProcessingError(f"Invalid max_block for prepare(): {max_block}")

        cfg = config or RenderConfig(
            simulated_latency_samples=self._init_latency,
            simulated_tail_samples=self._init_tail,
        )
        if cfg.input_channels not in (1, 2):
            raise ProcessingError(f"Unsupported input_channels: {cfg.input_channels} (must be 1 or 2)")
        if cfg.output_channels != 2:
            raise ProcessingError(f"Output must be 2-channel binaural stereo, got {cfg.output_channels}")

        eff_lat = cfg.simulated_latency_samples if cfg.simulated_latency_samples > 0 else self._init_latency
        eff_tail = cfg.simulated_tail_samples if cfg.simulated_tail_samples > 0 else self._init_tail
        if eff_lat < 0 or eff_tail < 0:
            raise ProcessingError("Latency and tail samples must be non-negative.")

        self._sample_rate = sample_rate
        self._max_block = max_block
        self._config = RenderConfig(
            input_channels=cfg.input_channels,
            output_channels=cfg.output_channels,
            simulated_latency_samples=eff_lat,
            simulated_tail_samples=eff_tail,
        )
        self._prepared = True
        self.reset()

    def set_params(self, params: AuroraParams) -> None:
        """Store parameters for block processing."""
        self._params = params

    def latency_samples(self) -> int:
        """Report fixed lookahead latency in sample frames."""
        return self._config.simulated_latency_samples

    def tail_samples(self) -> int:
        """Report tail length in sample frames."""
        return self._config.simulated_tail_samples

    def reset(self) -> None:
        """Reset delay line buffer to zeros."""
        lat = self._config.simulated_latency_samples
        self._delay_buf = np.zeros((lat, 2), dtype=np.float32)
        self._flushed = False

    def process(self, block: FloatBlock) -> FloatBlock:
        """Process a block of audio `(n, in_ch)` and return stereo `(n, 2)` using numpy."""
        if not self._prepared:
            raise ProcessingError("StubRenderer.process() called before prepare().")
        self._flushed = False

        stereo = _normalize_input_block(block, self._config.input_channels, self._max_block)
        n = stereo.shape[0]
        if n == 0:
            return np.zeros((0, 2), dtype=np.float32)

        lat = self._config.simulated_latency_samples
        if lat == 0:
            return stereo.copy()

        if n <= lat:
            out = self._delay_buf[:n].copy()
            if n < lat:
                self._delay_buf[: lat - n] = self._delay_buf[n:]
            self._delay_buf[lat - n :] = stereo
            return out

        out = np.concatenate([self._delay_buf, stereo[: n - lat]], axis=0)
        self._delay_buf[:] = stereo[n - lat :]
        return out

    def flush(self) -> FloatBlock:
        """Flush remaining samples in the fixed delay buffer plus any configured tail frames."""
        if not self._prepared:
            raise ProcessingError("StubRenderer.flush() called before prepare().")
        if self._flushed:
            return np.zeros((0, 2), dtype=np.float32)

        self._flushed = True
        lat = self._config.simulated_latency_samples
        tail = self._config.simulated_tail_samples

        parts: list[FloatBlock] = []
        if lat > 0:
            parts.append(self._delay_buf.copy())
            self._delay_buf.fill(0.0)
        if tail > 0:
            parts.append(np.zeros((tail, 2), dtype=np.float32))

        if not parts:
            return np.zeros((0, 2), dtype=np.float32)
        return np.concatenate(parts, axis=0)


class DelayTestRenderer(StubRenderer):
    """Test-only renderer with known fixed latency and known non-zero reverb tail (P2 Task 2).

    Used to verify:
    1. Exact latency compensation (`latency_samples()`).
    2. `timing.tail=trim`: output length strictly matches input length, with a 10 ms fade-out at the end.
    3. `timing.tail=preserve`: output appends `flush()` tail of length `tail_samples()`, matching `expected_tail()`.
    """

    def __init__(
        self,
        latency_samples: int = 128,
        tail_samples: int = 240,
        *,
        tail_amplitude: float = 0.25,
    ) -> None:
        super().__init__(latency_samples=latency_samples, tail_samples=tail_samples)
        self._tail_amplitude = float(tail_amplitude)

    def expected_tail(self) -> FloatBlock:
        """Return the deterministic `(tail_samples, 2)` float32 tail waveform."""
        tail_len = self.tail_samples()
        if tail_len <= 0:
            return np.zeros((0, 2), dtype=np.float32)

        k = np.arange(tail_len, dtype=np.float32)
        envelope = (self._tail_amplitude * np.exp(-2.5 * k / float(max(1, tail_len)))).astype(np.float32)
        phase = (2.0 * np.pi * 250.0 * k / float(max(1, self._sample_rate))).astype(np.float32)
        left = (envelope * np.cos(phase)).astype(np.float32)
        right = (-envelope * np.cos(phase)).astype(np.float32)
        return np.column_stack((left, right))

    def flush(self) -> FloatBlock:
        """Flush the remaining `latency_samples()` delayed input frames followed by `expected_tail()`."""
        if not self._prepared:
            raise ProcessingError("DelayTestRenderer.flush() called before prepare().")
        if self._flushed:
            return np.zeros((0, 2), dtype=np.float32)

        self._flushed = True
        lat = self.latency_samples()
        tail_arr = self.expected_tail()

        parts: list[FloatBlock] = []
        if lat > 0:
            parts.append(self._delay_buf.copy())
            self._delay_buf.fill(0.0)
        if tail_arr.shape[0] > 0:
            parts.append(tail_arr)

        if not parts:
            return np.zeros((0, 2), dtype=np.float32)
        return np.concatenate(parts, axis=0)


# Backwards-compatible alias for P0 imports
StubProcessor = StubRenderer


def apply_cosine_fade_out_inplace(block: FloatBlock, fade_frames: int) -> None:
    """Apply a smooth half-cosine fade-out over the last `min(len(block), fade_frames)` frames in-place."""
    total = block.shape[0]
    if total == 0 or fade_frames <= 0:
        return
    actual_fade = min(total, fade_frames)
    k = np.arange(1, actual_fade + 1, dtype=np.float32)
    gains = (0.5 * (1.0 + np.cos(np.pi * k / float(actual_fade)))).astype(np.float32)[:, np.newaxis]
    block[total - actual_fade :] *= gains


class RendererStreamProcessor:
    """Streaming block adapter around `Renderer` with O(block_size) memory footprint.

    Handles:
    - Chunking arbitrarily sized input blocks into `<= block_size` calls to `renderer.process(chunk)`
    - Discarding the first `renderer.latency_samples()` output frames
    - Calling `renderer.flush()` at stream completion
    - When `tail=trim`: keeping strictly `input_frames` frames and applying a `fade_out_ms` (default 10 ms)
      half-cosine fade-out to the final frames via a small rolling window buffer
    - When `tail=preserve`: appending `tail_samples()` frames from `flush()`
    """

    def __init__(
        self,
        renderer: Renderer,
        *,
        sample_rate: int,
        input_channels: int,
        params: AuroraParams,
        block_size: int = 1024,
        simulated_latency_samples: int = 0,
        simulated_tail_samples: int = 0,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        if block_size <= 0:
            raise ProcessingError(f"block_size must be positive, got {block_size}.")
        if input_channels not in (1, 2):
            raise ProcessingError(f"Only mono (1) or stereo (2) inputs are supported, got {input_channels}.")

        self.renderer = renderer
        self.sample_rate = sample_rate
        self.input_channels = input_channels
        self.params = params
        self.block_size = block_size
        self.cancel_check = cancel_check

        config = RenderConfig(
            input_channels=input_channels,
            output_channels=2,
            simulated_latency_samples=simulated_latency_samples,
            simulated_tail_samples=simulated_tail_samples,
        )
        self.renderer.prepare(sample_rate=sample_rate, max_block=block_size, config=config)
        self.renderer.set_params(params)

        self.latency: int = self.renderer.latency_samples()
        self.tail: int = self.renderer.tail_samples()
        self._latency_to_drop: int = self.latency

        self.input_frames: int = 0
        self.output_frames: int = 0
        self.extra_tail_kept: int = 0

        self._is_trim = params.timing.tail == TailPolicy.TRIM
        self._fade_frames: int = (
            max(1, int(round(params.timing.fade_out_ms * sample_rate / 1000.0)))
            if (self._is_trim and params.timing.fade_out_ms > 0.0)
            else 0
        )
        self._in_Staging: FloatBlock = np.zeros((0, input_channels), dtype=np.float32)
        self._tail_hold_buf: FloatBlock = np.zeros((0, 2), dtype=np.float32)

    def _consume_post_latency_chunk(self, raw_out: FloatBlock, *, max_total_frames: int | None = None) -> FloatBlock:
        """Drop initial latency samples, optionally cap total frames, and manage fade-out holdback window."""
        if raw_out.shape[0] == 0:
            return np.zeros((0, 2), dtype=np.float32)

        if not np.all(np.isfinite(raw_out)):
            raise ProcessingError("Rendered output block contains NaN or Inf values.", error_code="NON_FINITE_SAMPLES")

        if self._latency_to_drop > 0:
            drop = min(raw_out.shape[0], self._latency_to_drop)
            raw_out = raw_out[drop:]
            self._latency_to_drop -= drop
            if raw_out.shape[0] == 0:
                return np.zeros((0, 2), dtype=np.float32)

        if max_total_frames is not None:
            already_produced = self.output_frames + self._tail_hold_buf.shape[0]
            remaining_allowed = max(0, max_total_frames - already_produced)
            if raw_out.shape[0] > remaining_allowed:
                raw_out = raw_out[:remaining_allowed]
            if raw_out.shape[0] == 0:
                return np.zeros((0, 2), dtype=np.float32)

        if self._fade_frames <= 0:
            self.output_frames += raw_out.shape[0]
            return raw_out

        combined = (
            np.concatenate([self._tail_hold_buf, raw_out], axis=0)
            if self._tail_hold_buf.shape[0] > 0
            else raw_out
        )
        if combined.shape[0] <= self._fade_frames:
            self._tail_hold_buf = combined.copy()
            return np.zeros((0, 2), dtype=np.float32)

        emit_count = combined.shape[0] - self._fade_frames
        emit_chunk = combined[:emit_count]
        self._tail_hold_buf = combined[emit_count:].copy()
        self.output_frames += emit_chunk.shape[0]
        return emit_chunk

    def push_block(self, block: FloatBlock) -> Iterator[FloatBlock]:
        """Push an input float32 block of shape `(frames, input_channels)` and yield ready stereo output chunks."""
        if self.cancel_check is not None and self.cancel_check():
            raise OperationCanceledError("Processing canceled at block boundary.")

        arr = np.asarray(block, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr[:, np.newaxis]
        if arr.shape[0] == 0:
            return

        if not np.all(np.isfinite(arr)):
            raise ProcessingError("Input PCM contains NaN or Inf values.", error_code="NON_FINITE_SAMPLES")

        self.input_frames += arr.shape[0]
        staged = (
            np.concatenate([self._in_Staging, arr], axis=0)
            if self._in_Staging.shape[0] > 0
            else arr
        )

        offset = 0
        total = staged.shape[0]
        while total - offset >= self.block_size:
            if self.cancel_check is not None and self.cancel_check():
                raise OperationCanceledError("Processing canceled at block boundary.")
            sub = staged[offset : offset + self.block_size]
            out = self.renderer.process(sub)
            ready = self._consume_post_latency_chunk(out)
            if ready.shape[0] > 0:
                yield ready
            offset += self.block_size

        self._in_Staging = staged[offset:].copy()

    def finish(self) -> Iterator[FloatBlock]:
        """Process any remaining staged input frames, call `renderer.flush()`, and apply tail policy."""
        if self.cancel_check is not None and self.cancel_check():
            raise OperationCanceledError("Processing canceled at block boundary.")

        if self._in_Staging.shape[0] > 0:
            out = self.renderer.process(self._in_Staging)
            self._in_Staging = np.zeros((0, self.input_channels), dtype=np.float32)
            ready = self._consume_post_latency_chunk(out)
            if ready.shape[0] > 0:
                yield ready

        target_total_frames = self.input_frames if self._is_trim else (self.input_frames + self.tail)
        flushed = self.renderer.flush()
        if flushed.shape[0] > 0:
            ready = self._consume_post_latency_chunk(flushed, max_total_frames=target_total_frames)
            if ready.shape[0] > 0:
                yield ready

        # If a custom renderer's flush() returned fewer than latency_samples + expected frames, pad with zeros
        already_queued = self.output_frames + self._tail_hold_buf.shape[0]
        if already_queued < target_total_frames:
            shortfall = target_total_frames - already_queued
            pad = np.zeros((shortfall, 2), dtype=np.float32)
            ready = self._consume_post_latency_chunk(pad, max_total_frames=target_total_frames)
            if ready.shape[0] > 0:
                yield ready

        if self._tail_hold_buf.shape[0] > 0:
            final_chunk = self._tail_hold_buf.copy()
            self._tail_hold_buf = np.zeros((0, 2), dtype=np.float32)
            if self._is_trim and self._fade_frames > 0:
                apply_cosine_fade_out_inplace(final_chunk, self._fade_frames)
            self.output_frames += final_chunk.shape[0]
            yield final_chunk

        self.extra_tail_kept = max(0, self.output_frames - self.input_frames)


def run_offline_block_pipeline(
    pcm_f32le: bytes,
    *,
    sample_rate: int,
    input_channels: int,
    params: AuroraParams,
    block_size: int = 1024,
    simulated_latency_samples: int = 0,
    simulated_tail_samples: int = 0,
    cancel_check: Callable[[], bool] | None = None,
    renderer: Renderer | None = None,
) -> tuple[bytes, int, int, int, int]:
    """Run an in-memory float32le buffer block-by-block through a `Renderer` (default `StubRenderer`).

    Returns:
        (output_pcm_f32le, output_channels, output_sample_frames, latency_compensated, extra_tail_kept)
    """
    if input_channels not in (1, 2):
        raise ProcessingError(f"Only mono (1) or stereo (2) inputs are supported, got {input_channels}.")
    if len(pcm_f32le) % (4 * input_channels) != 0:
        raise ProcessingError(
            f"Corrupt float32 PCM buffer: {len(pcm_f32le)} bytes not divisible by {4 * input_channels}."
        )

    in_arr = np.frombuffer(pcm_f32le, dtype=np.float32).reshape(-1, input_channels)
    if not np.all(np.isfinite(in_arr)):
        raise ProcessingError("Input PCM contains NaN or Inf values.", error_code="NON_FINITE_SAMPLES")

    active_renderer: Renderer = renderer or create_renderer(
        latency_samples=simulated_latency_samples,
        tail_samples=simulated_tail_samples,
    )
    stream_proc = RendererStreamProcessor(
        active_renderer,
        sample_rate=sample_rate,
        input_channels=input_channels,
        params=params,
        block_size=block_size,
        simulated_latency_samples=simulated_latency_samples,
        simulated_tail_samples=simulated_tail_samples,
        cancel_check=cancel_check,
    )

    chunks: list[FloatBlock] = []
    total_frames = in_arr.shape[0]
    offset = 0
    while offset < total_frames:
        end = min(offset + block_size, total_frames)
        chunks.extend(stream_proc.push_block(in_arr[offset:end]))
        offset = end
    chunks.extend(stream_proc.finish())

    if chunks:
        out_arr = np.ascontiguousarray(np.concatenate(chunks, axis=0), dtype=np.float32)
    else:
        out_arr = np.zeros((0, 2), dtype=np.float32)

    return (
        out_arr.tobytes(),
        2,
        stream_proc.output_frames,
        stream_proc.latency,
        stream_proc.extra_tail_kept,
    )
