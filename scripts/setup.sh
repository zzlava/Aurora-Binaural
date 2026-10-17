#!/usr/bin/env bash
# Run from any directory; all generated artifacts stay outside the core source.
set -euo pipefail
aurora_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$aurora_root"

for aurora_tool in cargo rustup; do
    if ! command -v "$aurora_tool" >/dev/null 2>&1; then
        for aurora_bin_dir in /opt/homebrew/opt/rustup/bin "$HOME/.cargo/bin"; do
            if [[ -x "$aurora_bin_dir/$aurora_tool" ]]; then
                export PATH="$aurora_bin_dir:$PATH"
                break
            fi
        done
    fi
    if ! command -v "$aurora_tool" >/dev/null 2>&1; then
        printf 'Missing %s. Install Rust with rustup (https://rustup.rs), then rerun this script.\n' "$aurora_tool" >&2
        exit 1
    fi
done
for aurora_tool in uv node npm; do
    if ! command -v "$aurora_tool" >/dev/null 2>&1; then
        printf 'Missing %s. Install uv (https://docs.astral.sh/uv/) and Node.js/npm (https://nodejs.org), then rerun.\n' "$aurora_tool" >&2
        exit 1
    fi
done
cargo --version
rustup --version
uv --version
node --version
npm --version

# PyO3 0.25 supports the project's Python 3.12 baseline, not arbitrary future
# interpreters selected by uv on a machine without Python already installed.
uv sync --locked --python 3.12
uv run python -c 'from aurora_binaural.ffmpeg_discovery import configure_local_ffmpeg; configure_local_ffmpeg()'
bash scripts/build-core.sh
(
    cd web
    npm ci
    npm run build
)
printf '下一步运行 `uv run aurora gui`\n'
