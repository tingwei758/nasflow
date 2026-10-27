"""Periodic liveness checks on everything nasflow depends on.

nasflow can be running perfectly and filing nothing: a wrong qBittorrent
password, an expired TMDB key, or a staging volume that came back from a
reboot without its mount all stop the pipeline dead. The board used to report
only on nasflow's own web server, so it looked healthy anyway.

Two responsibilities, kept apart so neither needs the network to be tested:
`HealthRegistry` remembers the last result per check, and `health_loop` runs
each check's probe when its interval has elapsed. The checks themselves live
in `build_checks`.

The light reports; it never steers. Nothing here may change what the poll
loop, the pipeline, or the linker does.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .pathmap import nearest_existing
from .qbit import QbitUnavailable
from .redact import redact
from .tmdb import TmdbUnavailable

# How often each kind of check is worth repeating. qBittorrent and the local
# checks are free; TMDB's is a request to someone else's API.
QB_INTERVAL_S = 300.0
TMDB_INTERVAL_S = 900.0
LOCAL_INTERVAL_S = 300.0

_NEVER_CHECKED = "not checked yet"

_BYTES_PER_GB = 1024**3

# How long the polling light tolerates silence: three cycles, and never less
# than this, so a short poll interval does not make the light twitchy.
_MIN_POLL_GRACE_S = 180.0


class CheckFailed(RuntimeError):
    """A probe's own "this is unhealthy, and here is the reason to show"."""


class NotYetKnown(Exception):
    """A probe that cannot answer yet. Recorded as `unknown`, which is not red."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    label: str
    status: str  # "ok" | "fail" | "unknown"
    detail: str
    checked_at: float | None


@dataclass(frozen=True)
class Check:
    name: str
    label: str
    interval_s: float
    probe: Callable[[], str]


class HealthRegistry:
    """The last result of every check, and which ones are due.

    Written by the checker's worker threads and read by the web threadpool, so
    every access takes the lock. Secrets are masked on the way in: a detail
    string is display text, and `httpx` puts the full request URL — API key
    and all — into its exception messages.
    """

    def __init__(self, checks: Sequence[Check], secrets: Sequence[str | None] = ()):
        self._checks = tuple(checks)
        self._secrets = tuple(secrets)
        self._lock = threading.Lock()
        self._results = {
            check.name: CheckResult(
                name=check.name,
                label=check.label,
                status="unknown",
                detail=_NEVER_CHECKED,
                checked_at=None,
            )
            for check in self._checks
        }

    def record(self, name: str, status: str, detail: str, checked_at: float) -> None:
        with self._lock:
            current = self._results.get(name)
            if current is None:
                return  # a check this registry does not know about
            self._results[name] = replace(
                current,
                status=status,
                detail=redact(detail, self._secrets),
                checked_at=checked_at,
            )

    def snapshot(self) -> list[CheckResult]:
        """Every result, in registration order."""
        with self._lock:
            return [self._results[check.name] for check in self._checks]

    @property
    def overall_ok(self) -> bool:
        """False only on an actual failure: `unknown` is not red."""
        return not any(result.status == "fail" for result in self.snapshot())

    def due(self, now: float) -> list[Check]:
        with self._lock:
            return [
                check
                for check in self._checks
                if self._results[check.name].checked_at is None
                or now - self._results[check.name].checked_at >= check.interval_s
            ]


def _describe(exc: BaseException) -> tuple[str, str]:
    """Map a probe's exception onto a status and the text to show for it."""
    if isinstance(exc, NotYetKnown):
        return "unknown", str(exc)
    if isinstance(exc, CheckFailed | QbitUnavailable | TmdbUnavailable):
        return "fail", str(exc)
    # Unexpected: the type is the most useful thing we can say about it.
    return "fail", f"{type(exc).__name__}: {exc}"


def _free_gb(path: Path) -> float:
    """Free space in GB, by the same measure as `shutil.disk_usage().free`.

    `os.statvfs` rather than `shutil.disk_usage` because `tests/test_confinement.py`
    refuses a `shutil` import outside the linker: shutil is a write library, and
    this module must never be able to touch a file.
    """
    try:
        stats = os.statvfs(path)
    except OSError as exc:
        raise CheckFailed(f"cannot read staging: {exc}") from None
    return stats.f_bavail * stats.f_frsize / _BYTES_PER_GB


def _staging_probe(cfg) -> str:
    """Hardlinks still possible, and room to make them.

    Measured against each root's nearest existing parent: on first run staging
    does not exist yet, and a path that is gone says nothing about the device
    it used to be on. Deliberately not a real `os.link` probe — that writes
    files, and a periodic writer in staging is worse than comparing devices.
    """
    downloads = nearest_existing(cfg.downloads_root)
    staging = nearest_existing(cfg.staging_root)
    try:
        same_device = downloads.stat().st_dev == staging.stat().st_dev
    except OSError as exc:
        raise CheckFailed(f"cannot read staging: {exc}") from None
    if not same_device:
        raise CheckFailed(
            f"{cfg.downloads_root} and {cfg.staging_root} are on different filesystems, "
            "so no hardlink can be made"
        )

    free_gb = _free_gb(staging)
    if free_gb < cfg.health_min_free_gb:
        raise CheckFailed(
            f"only {free_gb:.0f} GB free in staging (floor {cfg.health_min_free_gb} GB)"
        )
    return f"linkable, {free_gb:.0f} GB free"


def _polling_probe(cfg, pipeline, now: Callable[[], float]) -> str:
    """Whether the poll loop is still ticking — the one thing no other check sees."""
    last = pipeline.last_poll_at
    if last is None:
        raise NotYetKnown("waiting for the first poll")
    gap = now() - last
    limit = max(3 * cfg.poll_interval_s, _MIN_POLL_GRACE_S)
    if gap > limit:
        raise CheckFailed(f"last poll was {gap:.0f}s ago (expected within {limit:.0f}s)")
    return f"last poll {gap:.0f}s ago"


def build_checks(cfg, store, pipeline, *, now=time.time) -> list[Check]:
    """The five things that have to work for nasflow to file anything.

    The clients come off the pipeline, so the checker probes the very same
    instances the pipeline uses, session state included. Sharing them across
    threads is safe: `httpx.Client` is thread-safe, and a race on qB's
    authenticated flag costs at worst one redundant login.
    """

    def qbittorrent() -> str:
        return f"version {pipeline.qbit.app_version()}"

    def tmdb() -> str:
        pipeline.tmdb.ping()
        return "API key accepted"

    def database() -> str:
        return f"ok, {store.ping()} items"

    return [
        Check("qbittorrent", "qBittorrent", QB_INTERVAL_S, qbittorrent),
        Check("tmdb", "TMDB", TMDB_INTERVAL_S, tmdb),
        Check("staging", "Staging folder", LOCAL_INTERVAL_S, lambda: _staging_probe(cfg)),
        Check("database", "Database", LOCAL_INTERVAL_S, database),
        Check("polling", "Polling", LOCAL_INTERVAL_S, lambda: _polling_probe(cfg, pipeline, now)),
    ]


async def health_loop(
    registry: HealthRegistry,
    *,
    tick_s: float = 30.0,
    sleep=asyncio.sleep,
    now=time.time,
    max_iterations: int | None = None,
) -> None:
    """Probe what is due, record it, sleep — forever.

    Work comes before the sleep, so the first pass happens at startup rather
    than a tick later. Probes are synchronous (blocking HTTP, filesystem), so
    each runs in a worker thread and the board stays responsive meanwhile. One
    bad probe never ends the loop, and never stops the checks after it.
    """
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        checked_at = now()
        for check in registry.due(checked_at):
            try:
                detail = await asyncio.to_thread(check.probe)
                status = "ok"
            except Exception as exc:
                status, detail = _describe(exc)
            registry.record(check.name, status, detail, checked_at)
        await sleep(tick_s)
