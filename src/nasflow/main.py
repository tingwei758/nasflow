"""Entrypoint: build everything from config, poll in the background, serve the board."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .db import Store
from .linker import probe_hardlink
from .pipeline import Pipeline
from .qbit import QbitClient
from .redact import RedactingFormatter
from .tmdb import TmdbClient
from .web.app import create_app

log = logging.getLogger("nasflow")


async def poll_loop(pipeline, interval_s: float, *, max_iterations=None, sleep=asyncio.sleep):
    """Poll, reconcile, sleep — forever. One bad pass never kills the loop.

    The pipeline is synchronous (blocking HTTP, filesystem), so each step runs
    in a worker thread and the board stays responsive meanwhile.
    """
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        for step in (pipeline.poll_once, pipeline.reconcile_staging):
            try:
                await asyncio.to_thread(step)
            except Exception:
                log.exception("%s failed; retrying next cycle", step.__name__)
        await sleep(interval_s)


def build_app(cfg: Config, *, start_polling: bool = True) -> FastAPI:
    store = Store(cfg.db_path)
    pipeline = Pipeline(
        cfg,
        store,
        QbitClient(cfg.qb_url, cfg.qb_username, cfg.qb_password),
        TmdbClient(
            cfg.tmdb_api_key,
            cfg.tmdb_language,
            english_language=cfg.tmdb_english_language or None,
        ),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        task = None
        if start_polling:
            task = asyncio.create_task(poll_loop(pipeline, cfg.poll_interval_s))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            store.close()

    return create_app(pipeline, store, cfg, lifespan=lifespan)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx logs every request URL at INFO, and TMDB's URL carries the API key.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def redact_secrets(secrets, handlers=None) -> None:
    """Mask secrets in everything the given (default: root) log handlers write."""
    for handler in handlers if handlers is not None else logging.getLogger().handlers:
        inner = handler.formatter or logging.Formatter()
        handler.setFormatter(RedactingFormatter(inner, secrets))


def resolve_config_path(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(prog="nasflow")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.config is not None:
        return args.config
    env = os.environ.get("NASFLOW_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def check_same_filesystem(cfg: Config) -> None:
    """Hardlinks need downloads and staging on one filesystem. Staging may not
    exist yet on first run; its parent decides. (Separate bind mounts of one
    filesystem share a device, so probe_hardlink covers that case.)"""
    downloads = _nearest_existing(cfg.downloads_root)
    staging = _nearest_existing(cfg.staging_root)
    if downloads.stat().st_dev != staging.stat().st_dev:
        raise ValueError(
            f"downloads_root ({cfg.downloads_root}) and staging_root ({cfg.staging_root}) "
            "are on different filesystems, so no hardlink can be made"
        )


def _fail(message: str) -> None:
    print(f"nasflow: {message}", file=sys.stderr)
    sys.exit(2)


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    path = resolve_config_path(sys.argv[1:] if argv is None else argv)
    try:
        cfg = load_config(path)
    except FileNotFoundError:
        _fail(f"config file not found: {path}")
    except ValueError as exc:
        _fail(f"invalid config {path}: {exc}")
    redact_secrets([cfg.tmdb_api_key, cfg.qb_password])
    try:
        check_same_filesystem(cfg)
    except ValueError as exc:
        _fail(str(exc))
    problem = probe_hardlink(cfg.downloads_root, cfg.staging_root)
    if problem:
        _fail(f"{problem}. downloads and staging must share one container mount (see README).")

    log.info("starting with %r", cfg)  # repr masks the TMDB key and qB password
    uvicorn.run(build_app(cfg), host=cfg.bind_host, port=cfg.bind_port, log_level="info")


if __name__ == "__main__":
    main()
