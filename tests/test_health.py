"""The background checker: a registry of last results, and the loop that fills it."""

import asyncio
import shutil
import threading
import time
from collections import Counter, namedtuple
from dataclasses import replace
from pathlib import Path

import pytest

from nasflow import health
from nasflow.config import Config
from nasflow.db import Store
from nasflow.health import (
    Check,
    CheckFailed,
    HealthRegistry,
    NotYetKnown,
    build_checks,
    health_loop,
)
from nasflow.pipeline import Pipeline

from .test_pipeline import FakeQbit, FakeTmdb

_StatVFS = namedtuple("_StatVFS", "f_bavail f_frsize")


def statvfs(free_gb):
    """Just the two fields `_free_gb` reads."""
    return _StatVFS(f_bavail=free_gb * 1024**3 // 4096, f_frsize=4096)


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "downloads").mkdir()
    (tmp_path / "staging").mkdir()
    return Config(
        downloads_root=tmp_path / "downloads",
        staging_root=tmp_path / "staging",
        db_path=tmp_path / "nasflow.db",
        qb_url="http://qb",
        qb_username="u",
        qb_password="p",
        tmdb_api_key="k",
    )


@pytest.fixture
def store(cfg):
    return Store(cfg.db_path)


@pytest.fixture
def pipeline(cfg, store):
    return Pipeline(cfg, store, FakeQbit(), FakeTmdb())


def _probe(checks, name):
    """Run one check's probe and return its detail."""
    return next(c for c in checks if c.name == name).probe()


async def _no_sleep(_seconds):
    return None


def probe_ok(detail="fine"):
    return lambda: detail


def check(name="qbittorrent", interval_s=300.0, probe=None):
    return Check(name=name, label=name.title(), interval_s=interval_s, probe=probe or probe_ok())


def _run(registry, iterations=1, now=time.time, probe_timeout_s=90.0):
    asyncio.run(
        health_loop(
            registry,
            tick_s=30.0,
            sleep=_no_sleep,
            now=now,
            probe_timeout_s=probe_timeout_s,
            max_iterations=iterations,
        )
    )


def _hung_probe(invocations, release, block_s=0.5):
    """A probe that does not answer. Blocks briefly so no thread outlives the test."""

    def probe():
        invocations.append(1)
        release.wait(timeout=block_s)
        return "answered too late"

    return probe


def _run_one(chk):
    registry = HealthRegistry([chk])
    _run(registry)
    return registry


def _two_checks():
    return HealthRegistry(
        [check(name="fast", interval_s=300.0), check(name="slow", interval_s=900.0)]
    )


def _mixed(first_probe, second_probe=None):
    return HealthRegistry(
        [
            check(name="first", probe=first_probe),
            check(name="second", probe=second_probe or probe_ok()),
        ]
    )


def _run_with_clock(iterations, step_s):
    """Run `iterations` ticks on a clock that each sleep advances by `step_s`."""
    runs = Counter()
    clock = [0.0]

    def tally(name):
        def probe():
            runs[name] += 1
            return "fine"

        return probe

    async def advance(_seconds):
        clock[0] += step_s

    registry = HealthRegistry(
        [
            check(name="fast", interval_s=300.0, probe=tally("fast")),
            check(name="slow", interval_s=900.0, probe=tally("slow")),
        ]
    )
    asyncio.run(
        health_loop(registry, sleep=advance, now=lambda: clock[0], max_iterations=iterations)
    )
    return dict(runs)


def test_every_check_starts_unknown_and_never_checked():
    registry = HealthRegistry([check()])
    result = registry.snapshot()[0]
    assert (result.status, result.checked_at) == ("unknown", None)
    assert result.detail == "not checked yet"


def test_snapshot_keeps_registration_order():
    names = ["a", "b", "c"]
    registry = HealthRegistry([check(name=n) for n in names])
    assert [r.name for r in registry.snapshot()] == names


def test_overall_ok_ignores_unknown_but_not_fail():
    registry = HealthRegistry([check(name="a"), check(name="b")])
    assert registry.overall_ok
    registry.record("a", "ok", "fine", 1.0)
    assert registry.overall_ok
    registry.record("b", "fail", "broken", 1.0)
    assert not registry.overall_ok


def test_recorded_detail_is_redacted():
    registry = HealthRegistry([check()], secrets=["super-secret"])
    registry.record("qbittorrent", "fail", "login as admin:super-secret failed", 1.0)
    detail = registry.snapshot()[0].detail
    assert "super-secret" not in detail and "***" in detail


def test_loop_runs_every_check_on_the_first_tick():
    registry = _two_checks()
    _run(registry, iterations=1)
    assert [r.status for r in registry.snapshot()] == ["ok", "ok"]


def test_loop_reruns_only_what_is_due():
    # The 300s check comes due every tick; the 900s one on the third (900s elapsed).
    runs = _run_with_clock(iterations=3, step_s=450.0)
    assert runs == {"fast": 3, "slow": 2}


def test_a_raising_probe_becomes_fail_with_its_message():
    def boom():
        raise CheckFailed("only 2 GB free in staging")

    result = _run_one(check(probe=boom)).snapshot()[0]
    assert result.status == "fail" and result.detail == "only 2 GB free in staging"


def test_an_unexpected_exception_names_its_type():
    def boom():
        raise KeyError("images")

    result = _run_one(check(probe=boom)).snapshot()[0]
    assert result.status == "fail" and result.detail.startswith("KeyError")


def test_not_yet_known_is_unknown_not_fail():
    def waiting():
        raise NotYetKnown("waiting for the first poll")

    registry = _run_one(check(probe=waiting))
    result = registry.snapshot()[0]
    assert result.status == "unknown" and result.detail == "waiting for the first poll"
    assert registry.overall_ok


def test_a_probe_failure_does_not_stop_later_checks_or_the_loop():
    def boom():
        raise RuntimeError("down")

    registry = _mixed(boom)
    _run(registry, iterations=2)
    assert [r.status for r in registry.snapshot()] == ["fail", "ok"]


def test_a_slow_probe_does_not_lose_the_rest_of_the_tick():
    calls = []

    def slow():
        time.sleep(0.05)
        calls.append("slow")
        return "slow but fine"

    registry = _mixed(slow, second_probe=lambda: calls.append("fast") or "fine")
    _run(registry, iterations=1)
    assert calls == ["slow", "fast"]
    assert all(r.status == "ok" for r in registry.snapshot())


def test_probe_failure_text_is_redacted():
    def boom():
        raise RuntimeError("GET https://api.themoviedb.org/3/configuration?api_key=key-123")

    registry = HealthRegistry([check(probe=boom)], secrets=["key-123"])
    _run(registry, iterations=1)
    assert "key-123" not in registry.snapshot()[0].detail


def test_loop_sleeps_the_configured_tick():
    slept = []

    async def record(seconds):
        slept.append(seconds)

    asyncio.run(health_loop(_two_checks(), tick_s=7.0, sleep=record, max_iterations=2))
    assert slept == [7.0, 7.0]


# ---- the five checks ---------------------------------------------------


def test_build_checks_lists_the_five_in_display_order(cfg, store, pipeline):
    names = [c.name for c in build_checks(cfg, store, pipeline)]
    assert names == ["qbittorrent", "tmdb", "staging", "database", "polling"]


def test_qbittorrent_check_reports_the_version(cfg, store, pipeline):
    pipeline.qbit.version = "v4.6.5"
    assert "4.6.5" in _probe(build_checks(cfg, store, pipeline), "qbittorrent")


def test_tmdb_check_reports_the_key_was_accepted(cfg, store, pipeline):
    assert _probe(build_checks(cfg, store, pipeline), "tmdb") == "API key accepted"


def test_staging_check_is_ok_before_staging_exists(cfg, store, pipeline):
    shutil.rmtree(cfg.staging_root)  # first run: not created yet
    assert "free" in _probe(build_checks(cfg, store, pipeline), "staging")


def test_staging_check_fails_across_filesystems(cfg, store, pipeline, monkeypatch):
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == cfg.downloads_root:
            # Everything the real stat answers, on a different device.
            return type(
                "S", (), {"st_dev": result.st_dev + 1, "st_mode": result.st_mode}
            )()
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    with pytest.raises(CheckFailed, match="different filesystems"):
        _probe(build_checks(cfg, store, pipeline), "staging")


def test_staging_check_fails_below_the_free_space_floor(cfg, store, pipeline, monkeypatch):
    monkeypatch.setattr(health.os, "statvfs", lambda p: statvfs(free_gb=3))
    with pytest.raises(CheckFailed, match="3 GB free"):
        _probe(build_checks(cfg, store, pipeline), "staging")  # floor is 20


def test_staging_check_fails_when_the_mount_has_vanished(cfg, store, pipeline, monkeypatch):
    def vanished(_path):
        raise OSError("Stale file handle")

    monkeypatch.setattr(health.os, "statvfs", vanished)
    with pytest.raises(CheckFailed, match="Stale file handle"):
        _probe(build_checks(cfg, store, pipeline), "staging")


def test_database_check_reports_the_item_count(cfg, store, pipeline):
    assert _probe(build_checks(cfg, store, pipeline), "database") == "ok, 0 items"


def test_polling_check_is_unknown_before_the_first_poll(cfg, store, pipeline):
    with pytest.raises(NotYetKnown):
        _probe(build_checks(cfg, store, pipeline), "polling")


def test_polling_check_is_ok_within_three_intervals(cfg, store, pipeline):
    pipeline.last_poll_at = 1000.0
    checks = build_checks(cfg, store, pipeline, now=lambda: 1042.0)
    assert _probe(checks, "polling") == "last poll 42s ago"


def test_polling_check_fails_when_the_loop_has_stalled(cfg, store, pipeline):
    pipeline.last_poll_at = 1000.0  # poll_interval_s is 60 -> limit 180
    checks = build_checks(cfg, store, pipeline, now=lambda: 1000.0 + 3600)
    with pytest.raises(CheckFailed, match="3600s ago"):
        _probe(checks, "polling")


def test_intervals_match_the_spec(cfg, store, pipeline):
    by_name = {c.name: c.interval_s for c in build_checks(cfg, store, pipeline)}
    assert by_name == {
        "qbittorrent": 300.0,
        "tmdb": 900.0,
        "staging": 300.0,
        "database": 300.0,
        "polling": 300.0,
    }


def test_a_hung_probe_fails_with_a_timeout_and_the_rest_of_the_tick_still_runs():
    release = threading.Event()
    invocations = []
    registry = _mixed(_hung_probe(invocations, release))
    try:
        _run(registry, iterations=1, probe_timeout_s=0.05)
        first, second = registry.snapshot()
        assert first.status == "fail" and "no answer" in first.detail
        assert second.status == "ok", "a hung probe must not swallow the checks after it"
    finally:
        release.set()


def test_a_hung_check_is_not_started_again_while_it_is_still_running():
    release = threading.Event()
    invocations = []
    registry = HealthRegistry([check(interval_s=0.0, probe=_hung_probe(invocations, release))])
    try:
        _run(registry, iterations=3, probe_timeout_s=0.02)
        assert len(invocations) == 1
    finally:
        release.set()


def test_staging_check_fails_when_the_downloads_volume_is_missing(cfg, store, pipeline):
    shutil.rmtree(cfg.downloads_root)
    with pytest.raises(CheckFailed, match="mounted"):
        _probe(build_checks(cfg, store, pipeline), "staging")


def test_a_password_embedded_in_qb_url_is_masked_in_a_detail(cfg):
    cfg = replace(cfg, qb_url="http://u:proxy-pass@qb:8999")
    registry = HealthRegistry([check()], secrets=cfg.secrets())
    registry.record(
        "qbittorrent", "fail", "POST http://u:proxy-pass@qb:8999/api/v2/auth/login 401", 1.0
    )
    assert "proxy-pass" not in registry.snapshot()[0].detail


def test_polling_check_fails_when_the_first_poll_never_finishes(cfg, store, pipeline):
    clock = [1000.0]
    checks = build_checks(cfg, store, pipeline, now=lambda: clock[0])
    clock[0] += 3600  # last_poll_at is still None
    with pytest.raises(CheckFailed, match="since startup"):
        _probe(checks, "polling")


def test_staging_check_blames_permissions_not_the_mount(cfg, store, pipeline, monkeypatch):
    real_stat = Path.stat

    def denied(self, *args, **kwargs):
        if self == cfg.downloads_root:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(CheckFailed, match="Permission denied") as caught:
        _probe(build_checks(cfg, store, pipeline), "staging")
    assert "mounted" not in str(caught.value)


def test_probes_run_on_daemon_threads_off_the_default_executor():
    seen = []
    registry = _run_one(check(probe=lambda: seen.append(threading.current_thread()) or "ok"))
    assert registry.snapshot()[0].status == "ok"
    assert seen[0].daemon and seen[0].name == "health-qbittorrent"


def test_each_result_is_stamped_when_its_own_probe_answered():
    clock = [0.0]

    def slow():
        clock[0] += 90.0  # stands in for a probe that took 90s
        return "slow"

    registry = _mixed(slow)
    _run(registry, iterations=1, now=lambda: clock[0])
    first, second = registry.snapshot()
    assert (first.checked_at, second.checked_at) == (90.0, 90.0)
