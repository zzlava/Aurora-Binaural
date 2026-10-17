# syntax=docker/dockerfile:1
# Aurora Binaural — Linux Container Route (docs/plan.md Section 11 & P3-antigravity-01)
# Base image pinned to Python 3.12.15 + Debian 13 (Trixie) providing FFmpeg >= 7.1 for exact
# MP4/M4A edit-list ('elst' / 'stts') trailing AAC packet trimming.

FROM node:24.12.0-bookworm-slim@sha256:7326fb2dbdce998edd72140946851be64ef4a643e8715e138ca467e8e9d92c99 AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/tsconfig.json ./
COPY web/src/ ./src/
COPY web/public/ ./public/
RUN npm run build

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS core-build
RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup.sh \
    && sh /tmp/rustup.sh -y --profile minimal --default-toolchain 1.99.1 \
    && rm /tmp/rustup.sh
RUN pip install 'uv==0.12.13'
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --only-group core-build --no-install-project
COPY core/ /source/core/
COPY scripts/stage-core.py /source/scripts/stage-core.py
RUN python /source/scripts/stage-core.py /source /build/staged \
    && .venv/bin/maturin build --manifest-path staged/core/aurora-py/Cargo.toml \
    --release --locked --interpreter /usr/local/bin/python --out /wheels

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AURORA_DATA_DIR=/data/runtime \
    AURORA_HRTF_DIR=/app/data/hrtf \
    AURORA_PRESETS_DIR=/app/presets \
    AURORA_ENGINE=rust \
    AURORA_WEB_DIR=/app/web/dist

# Install system ffmpeg / ffprobe (>= 7.1 in Debian Trixie) for float32 decode, BS.1770 ebur128 loudness,
# and WAV / M4A / MP3 encoding
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg=7:7.1.* \
        ca-certificates \
    && ffmpeg -version | head -n 1 | grep -E "ffmpeg version (n|N-)?([7-9]|[1-9][0-9])\\." \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged runtime user and workspace directories
RUN groupadd --gid 10001 aurora \
    && useradd --uid 10001 --gid aurora --create-home --shell /bin/bash aurora \
    && mkdir -p /app /app/data/hrtf /data/runtime \
    && chown -R aurora:aurora /app /data

WORKDIR /app

# Copy project metadata, fixed-version HRTF datasets (plan Section 11), and src/aurora_binaural package
COPY --chown=aurora:aurora pyproject.toml uv.lock README.md ./
COPY --chown=aurora:aurora src/ ./src/
COPY --chown=aurora:aurora data/hrtf/sadie2_d1/ ./data/hrtf/sadie2_d1/
COPY --chown=aurora:aurora data/hrtf/sadie_d1_frontal.ahrp ./data/hrtf/sadie_d1_frontal.ahrp
COPY --chown=aurora:aurora presets/ ./presets/
COPY --chown=aurora:aurora deploy/licenses/ ./licenses/
COPY --chown=aurora:aurora deploy/THIRD_PARTY_NOTICES.md ./THIRD_PARTY_NOTICES.md

COPY --from=web-build --chown=aurora:aurora /web/dist/ ./web/dist/

# Install aurora_binaural and its runtime dependencies (FastAPI, Uvicorn, Pydantic, NumPy)
COPY --from=core-build /wheels/ /wheels/
RUN pip install --no-cache-dir . /wheels/*.whl && rm -rf /wheels \
    && python -c 'import aurora_core'

USER aurora

ENTRYPOINT ["aurora"]
CMD ["presets", "--json"]
