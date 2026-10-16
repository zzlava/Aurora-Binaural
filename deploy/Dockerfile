# syntax=docker/dockerfile:1
# Aurora Binaural — P0 Linux Container Route Draft (docs/plan.md Section 11 & P0-antigravity-01 item 6)
# Base image pinned to an explicit Python 3.12 + Debian 12 (Bookworm) release.

FROM python:3.12.9-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system ffmpeg / ffprobe for float32 decode, BS.1770 ebur128 loudness, and WAV/M4A/MP3 encoding
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged runtime user and workspace directories
RUN groupadd --gid 10001 aurora \
    && useradd --uid 10001 --gid aurora --create-home --shell /bin/bash aurora \
    && mkdir -p /app /data/runtime \
    && chown -R aurora:aurora /app /data

WORKDIR /app

# Copy project metadata and application layer packages
COPY --chown=aurora:aurora pyproject.toml uv.lock README.md ./
COPY --chown=aurora:aurora cli/ ./cli/
COPY --chown=aurora:aurora api/ ./api/
COPY --chown=aurora:aurora worker/ ./worker/

# Install aurora_binaural and its locked runtime dependencies
RUN pip install --no-cache-dir .

USER aurora

ENTRYPOINT ["aurora"]
CMD ["presets", "--json"]
