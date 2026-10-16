"""Passthrough stub core implementing the real-time safe block Processor contract (docs/plan.md Section 7 & 8).

Do NOT implement DSP algorithms here (Cursor owns `core/`). This module provides:
1. The Python protocol/stub matching Section 7 (`prepare`, `set_params`, `process`, `latency_samples`, `tail_samples`, `reset`).
2. Offline block-by-block runner with engine latency compensation and reverb tail policy (`trim` with short fade-out vs `preserve`).
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Callable
from dataclasses import dataclass

from aurora_binaural.exit_codes import OperationCanceledError, ProcessingError
from aurora_binaural.params import AuroraParams, TailPolicy


@dataclass(frozen=True)
class RenderConfig:
    """Configuration passed to Processor.prepare() (docs/plan.md Section 7)."""

    input_channels: int = 2
    output_channels: int = 2
    simulated_latency_samples: int = 0
    simulated_tail_samples: int = 0


class StubProcessor:
    """P0 passthrough block processor matching the Rust `Processor` trait in docs/plan.md Section 7."""

    def __init__(self) -> None:
        self._sample_rate: int = 48000
        self._max_block: int = 256
        self._config: RenderConfig = RenderConfig()
        self._params: AuroraParams | None = None
        self._prepared: bool = False
        self._delay_lines: list[list[float]] = [[], []]
        self._delay_read_idx: int = 0

    def prepare(self, sample_rate: int, max_block: int, config: RenderConfig) -> None:
        """Prepare processor state before block streaming."""
        if sample_rate <= 0:
            raise ProcessingError(f"Invalid sample_rate for prepare(): {sample_rate}")
        if max_block <= 0:
            raise ProcessingError(f"Invalid max_block for prepare(): {max_block}")
        if config.input_channels not in (1, 2):
            raise ProcessingError(f"Unsupported input_channels: {config.input_channels} (must be 1 or 2)")
        if config.output_channels != 2:
            raise ProcessingError(f"Output must be 2-channel binaural stereo, got {config.output_channels}")

        self._sample_rate = sample_rate
        self._max_block = max_block
        self._config = config
        self._prepared = True
        self.reset()

    def set_params(self, params: AuroraParams) -> None:
        """Update render parameters (lock-free in Rust; stored for block execution in stub)."""
        self._params = params

    def latency_samples(self) -> int:
        """Report internal lookahead / filter latency in sample frames."""
        return self._config.simulated_latency_samples

    def tail_samples(self) -> int:
        """Report reverb tail length in sample frames."""
        return self._config.simulated_tail_samples

    def reset(self) -> None:
        """Reset delay lines and internal state."""
        lat = self._config.simulated_latency_samples
        self._delay_lines = [[0.0] * lat for _ in range(self._config.output_channels)]
        self._delay_read_idx = 0

    def process(
        self,
        input_channels: list[list[float]],
        output_channels: list[list[float]],
        n: int,
    ) -> None:
        """Process `n` frames of planar float32 audio without allocations or I/O.

        If input is mono (1 channel), routes identically to both L and R channels in the stub.
        If simulated_latency_samples > 0, shifts samples through a fixed circular delay buffer.
        """
        if not self._prepared:
            raise ProcessingError("StubProcessor.process() called before prepare().")
        if n > self._max_block or n < 0:
            raise ProcessingError(f"Block size n={n} exceeds max_block={self._max_block}.")

        in_ch = len(input_channels)
        left_in = input_channels[0]
        right_in = input_channels[1] if in_ch > 1 else input_channels[0]
        left_out = output_channels[0]
        right_out = output_channels[1]

        lat = self._config.simulated_latency_samples
        if lat == 0:
            for i in range(n):
                left_out[i] = left_in[i]
                right_out[i] = right_in[i]
        else:
            dl_l = self._delay_lines[0]
            dl_r = self._delay_lines[1]
            idx = self._delay_read_idx
            for i in range(n):
                left_out[i] = dl_l[idx]
                right_out[i] = dl_r[idx]
                dl_l[idx] = left_in[i]
                dl_r[idx] = right_in[i]
                idx += 1
                if idx >= lat:
                    idx = 0
            self._delay_read_idx = idx


def run_offline_block_pipeline(
    pcm_f32le: bytes,
    *,
    sample_rate: int,
    input_channels: int,
    params: AuroraParams,
    block_size: int = 256,
    simulated_latency_samples: int = 0,
    simulated_tail_samples: int = 0,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[bytes, int, int, int, int]:
    """Feed interleaved float32le PCM into `StubProcessor` block-by-block.

    Implements docs/plan.md Section 7 & 8:
    - Converts mono/stereo input to 2-channel binaural output (or preserves channel count if requested).
    - Compensates `processor.latency_samples()` so output timeline aligns with input.
    - Applies `params.timing.tail`:
      - `trim`: output sample frames == input sample frames, with a short cosine/linear fade-out at the end.
      - `preserve`: output sample frames == input sample frames + `processor.tail_samples()`.
    - Verifies no NaN or Inf values exist in input or output.

    Returns:
        (output_pcm_f32le, output_channels, output_sample_frames, latency_compensated, extra_tail_kept)
    """
    if input_channels not in (1, 2):
        raise ProcessingError(f"Only mono (1) or stereo (2) inputs are supported, got {input_channels}.")

    in_floats = array("f")
    in_floats.frombytes(pcm_f32le)
    total_float_count = len(in_floats)
    if total_float_count % input_channels != 0:
        raise ProcessingError(
            f"Corrupt float32 PCM buffer: {total_float_count} floats not divisible by {input_channels} channels."
        )

    for v in in_floats:
        if not math.isfinite(v):
            raise ProcessingError("Input PCM contains NaN or Inf values.")

    input_frames = total_float_count // input_channels
    output_channels = 2  # Binaural rendering always outputs 2 channels (L/R), or passthrough stereo

    processor = StubProcessor()
    config = RenderConfig(
        input_channels=input_channels,
        output_channels=output_channels,
        simulated_latency_samples=simulated_latency_samples,
        simulated_tail_samples=simulated_tail_samples,
    )
    processor.prepare(sample_rate=sample_rate, max_block=block_size, config=config)
    processor.set_params(params)

    latency = processor.latency_samples()
    tail = processor.tail_samples()
    extra_tail = tail if params.timing.tail == TailPolicy.PRESERVE else 0

    # Total frames we need to clock through the processor to flush latency + desired tail
    total_frames_to_clock = input_frames + latency + extra_tail
    target_output_frames = input_frames + extra_tail

    in_buf_l = [0.0] * block_size
    in_buf_r = [0.0] * block_size
    out_buf_l = [0.0] * block_size
    out_buf_r = [0.0] * block_size

    out_interleaved = array("f", [0.0] * (target_output_frames * output_channels))

    clocked_frames = 0
    written_frames = 0

    while clocked_frames < total_frames_to_clock:
        if cancel_check is not None and cancel_check():
            raise OperationCanceledError("Processing canceled at block boundary.")

        n = min(block_size, total_frames_to_clock - clocked_frames)
        for i in range(n):
            frame_idx = clocked_frames + i
            if frame_idx < input_frames:
                if input_channels == 1:
                    val = in_floats[frame_idx]
                    in_buf_l[i] = val
                    in_buf_r[i] = val
                else:
                    base = frame_idx * 2
                    in_buf_l[i] = in_floats[base]
                    in_buf_r[i] = in_floats[base + 1]
            else:
                in_buf_l[i] = 0.0
                in_buf_r[i] = 0.0

        processor.process([in_buf_l, in_buf_r], [out_buf_l, out_buf_r], n)

        # Discard the first `latency` output samples to compensate engine lookahead/delay
        for i in range(n):
            out_frame_idx = clocked_frames + i - latency
            if 0 <= out_frame_idx < target_output_frames:
                base_out = out_frame_idx * 2
                out_interleaved[base_out] = out_buf_l[i]
                out_interleaved[base_out + 1] = out_buf_r[i]
                written_frames += 1

        clocked_frames += n

    # When tail == "trim" and tail_samples > 0 (or fade_out_ms > 0 when trimming a reverb tail),
    # apply a short fade-out at the end if there was an active tail being truncated.
    if params.timing.tail == TailPolicy.TRIM and tail > 0 and params.timing.fade_out_ms > 0.0:
        fade_frames = min(
            target_output_frames,
            max(1, int(round(params.timing.fade_out_ms * sample_rate / 1000.0))),
        )
        start_fade = target_output_frames - fade_frames
        for k in range(fade_frames):
            gain = 0.5 * (1.0 + math.cos(math.pi * (k + 1) / fade_frames))
            idx = (start_fade + k) * 2
            out_interleaved[idx] *= gain
            out_interleaved[idx + 1] *= gain

    for v in out_interleaved:
        if not math.isfinite(v):
            raise ProcessingError("Rendered output PCM contains NaN or Inf values.")

    return (
        out_interleaved.tobytes(),
        output_channels,
        written_frames,
        latency,
        extra_tail,
    )
