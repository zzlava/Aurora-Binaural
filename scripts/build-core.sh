#!/usr/bin/env bash
# Build the read-only Cargo workspace without writing artifacts into core/.
set -euo pipefail
aurora_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$aurora_root"
uv sync --locked --group core-build
aurora_build_dir="$(mktemp -d "${TMPDIR:-/tmp}/aurora-core.XXXXXX")"
trap 'rm -rf -- "$aurora_build_dir"' EXIT
uv run python scripts/stage-core.py "$aurora_root" "$aurora_build_dir"
CARGO_TARGET_DIR="$aurora_build_dir/target" uv run --group core-build maturin build \
    --manifest-path "$aurora_build_dir/core/aurora-py/Cargo.toml" --release --locked \
    --interpreter "$aurora_root/.venv/bin/python" --out "$aurora_build_dir/wheels"
uv pip install --python "$aurora_root/.venv/bin/python" "$aurora_build_dir"/wheels/*.whl
uv run python -c 'import aurora_core; print("aurora_core loaded:", aurora_core.__file__)'
uv run python - <<'PY'
import inspect
import importlib.metadata

import aurora_core
import numpy as np

from aurora_binaural.catalog import CATALOG, DEFAULT_PRESET_ID
from aurora_binaural.engine import RenderConfig, RustRenderer
from aurora_binaural.params import AuroraParams

assert "route" in inspect.signature(aurora_core.Renderer).parameters, "Core must support route V"
params = AuroraParams()
assert params.preset.id == DEFAULT_PRESET_ID and CATALOG[params.preset.id].route == "V"
renderer = RustRenderer()
renderer.prepare(48000, 1024, RenderConfig())
renderer.set_params(params)
block = np.zeros((1024, 2), dtype=np.float32)
assert renderer.process(block).shape == block.shape
assert np.isfinite(renderer.flush()).all()
print(f"Default core smoke passed: Rust {importlib.metadata.version('aurora_core')} / V / {DEFAULT_PRESET_ID} / SADIE II D1")
PY
