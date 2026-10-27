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

from .pathmap import check_same_filesystem, nearest_existing
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

# A probe that has not answered in this long is treated as a failure. Well above
# every client's own timeout: this is here to catch an *unbounded* hang — a
# wedged NFS mount makes `stat` block forever — not a merely slow server.
PROBE_TIMEOUT_S = 90.0

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

    Staging is measured by its nearest existing parent, because on first run it
    does not exist yet and a path that is gone says nothing about the device it
    used to be on. Downloads gets no such grace: if it is missing, the volume
    is unmounted, and walking up to a parent would compare that parent against
    itself and report a cheerful green.

    Deliberately not a real `os.link` probe — that writes files, and a periodic
    writer in staging is worse than comparing devices.
    """
    try:
        # Only a path that is really absent means unmounted: is_dir() would also
        # say False on a permission error, and blame the mount for it.
        cfg.downloads_root.stat()
    except FileNotFoundError:
        raise CheckFailed(
            f"{cfg.downloads_root} is missing — is the downloads volume mounted?"
        ) from None
    except OSError as exc:
        raise CheckFailed(f"cannot read downloads: {exc}") from None
    try:
        check_same_filesystem(cfg.downloads_root, cfg.staging_root)
        staging = nearest_existing(cfg.staging_root)
    except ValueError as exc:
        raise CheckFailed(str(exc)) from None
    except OSError as exc:
        raise CheckFailed(f"cannot read staging: {exc}") from None

    free_gb = _free_gb(staging)
    if free_gb < cfg.health_min_free_gb:
        raise CheckFailed(
            f"only {free_gb:.0f} GB free in staging (floor {cfg.health_min_free_gb} GB)"
        )
    return f"linkable, {free_gb:.0f} GB free"


def _polling_probe(cfg, pipeline, now: Callable[[], float], started_at: float) -> str:
    """Whether the poll loop is still ticking — the one thing no other check sees.

    Before the first poll the light is `unknown`, but only for as long as a
    poll would be allowed to take: a first poll that never finishes (a mount
    wedged since boot) is the same stall as a later one.
    """
    limit = max(3 * cfg.poll_interval_s, _MIN_POLL_GRACE_S)
    last = pipeline.last_poll_at
    if last is None:
        waited = now() - started_at
        if waited > limit:
            raise CheckFailed(
                f"no poll has finished since startup {waited:.0f}s ago "
                f"(expected within {limit:.0f}s)"
            )
        raise NotYetKnown("waiting for the first poll")
    gap = now() - last
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

    started_at = now()

    return [
        Check("qbittorrent", "qBittorrent", QB_INTERVAL_S, qbittorrent),
        Check("tmdb", "TMDB", TMDB_INTERVAL_S, tmdb),
        Check("staging", "Staging folder", LOCAL_INTERVAL_S, lambda: _staging_probe(cfg)),
        Check("database", "Database", LOCAL_INTERVAL_S, database),
        Check(
            "polling",
            "Polling",
            LOCAL_INTERVAL_S,
            lambda: _polling_probe(cfg, pipeline, now, started_at),
        ),
    ]


def _run_probe(check: Check) -> tuple[str, str]:
    """Run one probe in a worker thread. Never raises, so a hung thread that
    finishes after we stopped waiting leaves no unretrieved exception."""
    try:
        return "ok", str(check.probe())
    except BaseException as exc:  # noqa: BLE001 - a probe may not kill the checker
        return _describe(exc)


def _settle(future: asyncio.Future, result: tuple[str, str]) -> None:
    if not future.done():  # the loop may have been cancelled while we ran
        future.set_result(result)


def _start_probe(loop: asyncio.AbstractEventLoop, check: Check) -> asyncio.Future:
    """Run one probe on a thread of its own, not on the loop's default executor.

    The default executor is shared with the poll loop's `asyncio.to_thread`, so
    probes stuck on a wedged mount would take the pipeline's workers; and its
    threads are joined at exit, so one would hold up shutdown until Docker
    kills the container. A daemon thread does neither. They stay bounded: the
    loop never starts a check again while its last thread is still running.
    """
    future = loop.create_future()

    def work() -> None:
        result = _run_probe(check)
        try:
            loop.call_soon_threadsafe(_settle, future, result)
        except RuntimeError:
            pass  # the loop closed while we were blocked; nobody is waiting

    threading.Thread(target=work, name=f"health-{check.name}", daemon=True).start()
    return future


async def health_loop(
    registry: HealthRegistry,
    *,
    tick_s: float = 30.0,
    sleep=asyncio.sleep,
    now=time.time,
    probe_timeout_s: float = PROBE_TIMEOUT_S,
    max_iterations: int | None = None,
) -> None:
    """Probe what is due, record it, sleep — forever.

    Work comes before the sleep, so the first pass happens at startup rather
    than a tick later. Probes are synchronous (blocking HTTP, filesystem), so
    each runs in a worker thread and the board stays responsive meanwhile. One
    bad probe never ends the loop, and never stops the checks after it.

    A probe that never answers is recorded as a failure and left running: a
    thread blocked in the kernel cannot be cancelled, so the check is skipped
    on later ticks until its thread finishes rather than piling up copies of
    it. Without the timeout, one wedged mount would freeze every other light
    at its last value — green, and indistinguishable from healthy.
    """
    loop = asyncio.get_running_loop()
    in_flight: dict[str, asyncio.Future] = {}
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        for check in registry.due(now()):
            pending = in_flight.get(check.name)
            if pending is not None and not pending.done():
                continue  # still running from an earlier tick
            future = _start_probe(loop, check)
            in_flight[check.name] = future
            try:
                status, detail = await asyncio.wait_for(
                    asyncio.shield(future), probe_timeout_s
                )
            except TimeoutError:
                status, detail = "fail", f"no answer within {probe_timeout_s:.0f}s"
            # Stamped when this probe answered: an earlier slow one in the same
            # tick must not make this result look older than it is.
            registry.record(check.name, status, detail, now())
        await sleep(tick_s)
