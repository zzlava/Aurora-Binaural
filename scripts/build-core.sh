#!/usr/bin/env bash
# Build the read-only Cargo workspace without writing artifacts into core/.
set -euo pipefail
aurora_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$aurora_root"
uv sync --group core-build
aurora_build_dir="$(mktemp -d "${TMPDIR:-/tmp}/aurora-core.XXXXXX")"
trap 'rm -rf -- "$aurora_build_dir"' EXIT
uv run python scripts/stage-core.py "$aurora_root" "$aurora_build_dir"
CARGO_TARGET_DIR="$aurora_build_dir/target" uv run --group core-build maturin build \
    --manifest-path "$aurora_build_dir/core/aurora-py/Cargo.toml" --release --locked \
    --interpreter "$aurora_root/.venv/bin/python" --out "$aurora_build_dir/wheels"
uv pip install --python "$aurora_root/.venv/bin/python" "$aurora_build_dir"/wheels/*.whl
uv run python -c 'import aurora_core; print("aurora_core loaded:", aurora_core.__file__)'
