import asyncio
import logging
from pathlib import Path

import pytest

from nasflow import main as flow_main
from nasflow.config import DEFAULT_CONFIG_PATH, Config


class RecordingPipeline:
    def __init__(self, fail_first_poll=False):
        self.calls: list[str] = []
        self.fail_first_poll = fail_first_poll

    def poll_once(self):
        self.calls.append("poll")
        if self.fail_first_poll and self.calls.count("poll") == 1:
            raise RuntimeError("qBittorrent fell over")

    def reconcile_staging(self):
        self.calls.append("reconcile")


async def _no_sleep(_seconds):
    return None


def run_loop(pipeline, iterations):
    asyncio.run(
        flow_main.poll_loop(pipeline, 60, max_iterations=iterations, sleep=_no_sleep)
    )


def test_poll_loop_calls_poll_then_reconcile():
    p = RecordingPipeline()
    run_loop(p, 2)
    assert p.calls == ["poll", "reconcile", "poll", "reconcile"]


def test_poll_loop_survives_an_exception():
    p = RecordingPipeline(fail_first_poll=True)
    run_loop(p, 2)
    assert p.calls.count("poll") == 2


def test_failed_poll_still_reconciles_staging():
    p = RecordingPipeline(fail_first_poll=True)
    run_loop(p, 1)
    assert p.calls == ["poll", "reconcile"]


def test_poll_loop_sleeps_the_configured_interval():
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    asyncio.run(
        flow_main.poll_loop(RecordingPipeline(), 42, max_iterations=2, sleep=record)
    )
    assert slept == [42, 42]


def _cfg(tmp_path: Path) -> Config:
    return Config(
        downloads_root=tmp_path / "dl", staging_root=tmp_path / "st",
        db_path=tmp_path / "cfg/nasflow.db", qb_url="http://qb:8999", qb_username="u",
        qb_password="p", tmdb_api_key="k", bind_host="192.168.1.20", bind_port=9123,
    )


def test_app_binds_configured_host_and_port(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(flow_main, "load_config", lambda path: _cfg(tmp_path))
    monkeypatch.setattr(
        flow_main.uvicorn, "run", lambda app, **kw: seen.update(kw, app=app)
    )
    flow_main.main(["--config", "ignored.toml"])
    assert seen["host"] == "192.168.1.20" and seen["port"] == 9123


def test_config_path_defaults_from_config_module(monkeypatch):
    monkeypatch.delenv("NASFLOW_CONFIG", raising=False)
    assert flow_main.resolve_config_path([]) == DEFAULT_CONFIG_PATH


def test_config_path_from_env_then_flag(monkeypatch):
    monkeypatch.setenv("NASFLOW_CONFIG", "/env/config.toml")
    assert flow_main.resolve_config_path([]) == Path("/env/config.toml")
    assert flow_main.resolve_config_path(["--config", "/flag.toml"]) == Path("/flag.toml")


def test_http_client_loggers_are_quiet():
    """httpx logs request URLs at INFO, and TMDB's URL carries the API key."""
    flow_main.configure_logging()
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_built_app_serves_the_board(tmp_path):
    from fastapi.testclient import TestClient

    app = flow_main.build_app(_cfg(tmp_path), start_polling=False)
    with TestClient(app) as client:
        assert client.get("/api/board").status_code == 200
        assert client.get("/").status_code == 200


def test_built_app_starts_and_stops_the_poll_loop(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    started = []

    async def fake_loop(pipeline, interval_s, **_):
        started.append(interval_s)
        await asyncio.Event().wait()  # run until cancelled

    monkeypatch.setattr(flow_main, "poll_loop", fake_loop)
    app = flow_main.build_app(_cfg(tmp_path))
    with TestClient(app):
        pass
    assert started == [60]


def test_missing_config_file_exits_with_a_clear_message(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        flow_main.main(["--config", str(tmp_path / "nope.toml")])
    assert exc.value.code != 0
    assert "nope.toml" in capsys.readouterr().err


def test_startup_refuses_roots_on_different_filesystems(tmp_path, monkeypatch, capsys):
    """Hardlinks cannot cross filesystems; fail loudly instead of stopping every item."""
    cfg = _cfg(tmp_path)
    (tmp_path / "dl").mkdir()
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == tmp_path / "dl":
            return type("S", (), {"st_dev": result.st_dev + 1})()
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(flow_main, "load_config", lambda path: cfg)
    monkeypatch.setattr(flow_main.uvicorn, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        flow_main.main(["--config", "x.toml"])
    assert "different filesystems" in capsys.readouterr().err


def test_startup_checks_staging_by_its_nearest_existing_parent(tmp_path):
    """On first run staging does not exist yet; its parent decides."""
    (tmp_path / "dl").mkdir()
    flow_main.check_same_filesystem(_cfg(tmp_path))  # must not raise


def test_startup_fails_when_the_hardlink_probe_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(flow_main, "load_config", lambda path: _cfg(tmp_path))
    monkeypatch.setattr(flow_main, "probe_hardlink", lambda d, s: "Invalid cross-device link")
    monkeypatch.setattr(flow_main.uvicorn, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        flow_main.main(["--config", "x.toml"])
    assert "Invalid cross-device link" in capsys.readouterr().err


def test_log_output_never_carries_a_secret():
    import io
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("nasflow.test-redaction")
    logger.addHandler(handler)
    logger.propagate = False
    flow_main.redact_secrets(["tmdb-secret-123", "", "abc"], handlers=[handler])
    try:
        raise RuntimeError("url?api_key=tmdb-secret-123")
    except RuntimeError:
        logger.exception("lookup for %s failed", "tmdb-secret-123")
    out = stream.getvalue()
    assert "tmdb-secret-123" not in out and "RuntimeError" in out
