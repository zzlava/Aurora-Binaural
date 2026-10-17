"""A real second-origin API/worker for browser tests; credentials are test fixtures only."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from aurora_binaural.api import create_app
from aurora_binaural.config import ServiceConfig
from aurora_binaural.worker import AuroraWorker

cfg = ServiceConfig.from_env()
app = create_app(cfg)
worker = AuroraWorker(cfg, store=app.state.store)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    worker.start_background()
    try:
        yield
    finally:
        worker.stop()


app.router.lifespan_context = lifespan
uvicorn.run(app, host='127.0.0.1', port=8768, access_log=False, log_level='warning')
