"""Local GUI launcher: loopback-only API, worker lifecycle, and browser bootstrap."""
from __future__ import annotations

import os
import secrets
import socket
import threading
import time
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from aurora_binaural.api import create_app
from aurora_binaural.config import ServiceConfig
from aurora_binaural.exit_codes import ParameterValidationError
from aurora_binaural.media import log_stderr
from aurora_binaural.worker import AuroraWorker


def find_web_directory() -> Path:
    """Prefer an explicit deployment path, then packaged assets, then source checkout."""
    configured = os.environ.get('AURORA_WEB_DIR')
    candidates = ([Path(configured)] if configured else [
        Path(__file__).parent / 'web_dist', Path(__file__).resolve().parents[2] / 'web' / 'dist',
    ])
    for path in candidates:
        if (path / 'index.html').is_file():
            return path.resolve()
    raise ParameterValidationError(
        'Web GUI 尚未构建。请在 web/ 中运行 npm ci && npm run build，或设置 AURORA_WEB_DIR。',
        error_code='GUI_BUILD_MISSING',
    )


def run_gui(*, port: int = 0, open_browser: bool = True) -> int:
    """Run until Ctrl-C; bind before opening a browser so a busy port is never mistaken for ours."""
    web_dir = find_web_directory()
    cfg = ServiceConfig.from_env()
    if 'AURORA_DATA_DIR' not in os.environ:
        data_dir = Path.home() / 'Library' / 'Caches' / 'AuroraBinaural'
        cfg = replace(cfg, data_dir=data_dir, db_path=(cfg.db_path if 'AURORA_DB_PATH' in os.environ
                                                     else data_dir / 'aurora.db'))
    cfg = replace(cfg, api_key=secrets.token_urlsafe(32), host='127.0.0.1', port=port)
    app = create_app(cfg, web_dir=web_dir, local_gui=True)
    worker = AuroraWorker(cfg, store=app.state.store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker.start_background()
        try:
            yield
        finally:
            worker.stop()

    app.router.lifespan_context = lifespan
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
        listener.listen(128)
        bound_port = listener.getsockname()[1]
        url = f'http://127.0.0.1:{bound_port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=bound_port,
                                             log_level='warning', access_log=False))
        log_stderr(f'Aurora GUI: {url} （Ctrl-C 退出）')
        if open_browser:
            def launch_browser() -> None:
                deadline = time.monotonic() + 15.0
                while not server.started and not server.should_exit and time.monotonic() < deadline:
                    time.sleep(0.05)
                if server.started:
                    webbrowser.open(url)
            threading.Thread(target=launch_browser, name='gui-browser', daemon=True).start()
        server.run(sockets=[listener])
    return 0
