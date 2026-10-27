"""The background checker: a registry of last results, and the loop that fills it."""

import asyncio
import time
from collections import Counter

from nasflow.health import Check, CheckFailed, HealthRegistry, NotYetKnown, health_loop


async def _no_sleep(_seconds):
    return None


def probe_ok(detail="fine"):
    return lambda: detail


def check(name="qbittorrent", interval_s=300.0, probe=None):
    return Check(name=name, label=name.title(), interval_s=interval_s, probe=probe or probe_ok())


def _run(registry, iterations=1, now=time.time):
    asyncio.run(
        health_loop(registry, tick_s=30.0, sleep=_no_sleep, now=now, max_iterations=iterations)
    )


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
    """Run `iterations` ticks on a clock that advances `step_s` per tick."""
    runs = Counter()

    def tally(name):
        def probe():
            runs[name] += 1
            return "fine"

        return probe

    registry = HealthRegistry(
        [
            check(name="fast", interval_s=300.0, probe=tally("fast")),
            check(name="slow", interval_s=900.0, probe=tally("slow")),
        ]
    )
    ticks = iter([i * step_s for i in range(iterations)])
    _run(registry, iterations=iterations, now=lambda: next(ticks))
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
