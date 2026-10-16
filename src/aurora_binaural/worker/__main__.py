"""Entry point for running the Aurora Binaural background Worker (`python -m aurora_binaural.worker`)."""

from __future__ import annotations

import signal
import sys
from types import FrameType

from aurora_binaural.config import ServiceConfig
from aurora_binaural.worker import AuroraWorker


def main() -> int:
    """Run the Aurora Binaural Worker until SIGINT or SIGTERM."""
    config = ServiceConfig.from_env()
    worker = AuroraWorker(config)

    def _handle_sig(_signum: int, _frame: FrameType | None) -> None:
        worker.stop(timeout=2.0)

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)
    worker.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
