# syntax=docker/dockerfile:1
# Aurora Binaural — Linux Container Route (docs/plan.md Section 11 & P3-antigravity-01)
# Base image pinned to Python 3.12.15 + Debian 13 (Trixie) providing FFmpeg >= 7.1 for exact
# MP4/M4A edit-list ('elst' / 'stts') trailing AAC packet trimming.

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AURORA_DATA_DIR=/data/runtime \
    AURORA_HRTF_DIR=/app/data/hrtf

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
COPY --chown=aurora:aurora data/hrtf/ ./data/hrtf/

# Install aurora_binaural and its runtime dependencies (FastAPI, Uvicorn, Pydantic, NumPy)
RUN pip install --no-cache-dir .

USER aurora

ENTRYPOINT ["aurora"]
CMD ["presets", "--json"]
