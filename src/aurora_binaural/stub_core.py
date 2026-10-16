"""Backwards-compatibility re-exports for `aurora_binaural.stub_core`.

Canonical definitions live in `aurora_binaural.engine` per ADR 0002.
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

__all__ = [
    "DelayTestRenderer",
    "RenderConfig",
    "Renderer",
    "RendererStreamProcessor",
    "StubProcessor",
    "StubRenderer",
    "run_offline_block_pipeline",
]
