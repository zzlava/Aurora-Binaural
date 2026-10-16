"""Entry point for running the Aurora Binaural FastAPI HTTP server (`python -m aurora_binaural.api`)."""

from __future__ import annotations

import sys

import uvicorn

from aurora_binaural.api import create_app
from aurora_binaural.config import ServiceConfig


def main() -> int:
    """Start the Aurora Binaural HTTP API server using Uvicorn."""
    config = ServiceConfig.from_env()
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
