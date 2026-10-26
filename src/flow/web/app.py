"""App factory: wiring and static mounting only.

No authentication, by design — the board binds to the LAN and must not be
reachable from the internet. See the spec's Exposure section.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import build_router

_STATIC = Path(__file__).parent / "static"


def create_app(pipeline, store, cfg) -> FastAPI:
    app = FastAPI(title="flow", docs_url=None, redoc_url=None)
    app.include_router(build_router(pipeline, store, cfg))
    _STATIC.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
    return app
