# Health Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A background checker probes every nasflow dependency on its own cadence, and the board shows a green light per dependency with the reason when one is red.

**Architecture:** A new `health.py` holds a thread-safe registry of the last result per check and an `asyncio` loop that runs each check's blocking probe in a worker thread when its interval has elapsed. The probes are cheap authenticated calls added to the existing clients. `GET /api/health` returns the last snapshot and never probes; the board polls it every 30s and draws a dot per check.

**Tech Stack:** Python 3.12, FastAPI, httpx, `asyncio.to_thread`, pytest + respx, vanilla ES modules.

**Spec:** `docs/superpowers/specs/2026-10-26-health-checks-design.md`

## Global Constraints

- **The light reports; it never steers.** No health result may change what the poll loop, the pipeline, or the linker does.
- **No probe may be semantically expensive.** No torrent is touched, no TMDB search is spent, nothing is written anywhere — in particular, the staging check compares `st_dev`, it does not call `os.link`.
- **Never leak a secret into a detail string.** Every detail passes through `redact(text, secrets)` from `redact.py` with `[cfg.tmdb_api_key, cfg.qb_password]` before it is stored.
- **No path, interval, filename, or threshold literal outside `config.py` and `health.py`'s named constants.** The JS may not hardcode a check label; labels come from the server, as the column labels already do.
- **`unknown` is not red.** `overall_ok` is false only when a check has `status == "fail"`.
- **A probe failure never ends the loop**, mirroring `poll_loop`'s "one bad pass never kills the loop".
- **Check intervals:** qBittorrent 300s, TMDB 900s, staging 300s, database 300s, polling 300s. Checker tick: `cfg.health_tick_s` (default 30s). Staging floor: `cfg.health_min_free_gb` (default 20).
- **Statuses are exactly** `"ok" | "fail" | "unknown"`.
- Python 3.12, `pytest`, format with `ruff` (line-length 100).

## Review Focus

Failure modes the spec implies but no happy path exercises. Each has a test in the owning task.

1. **A slow or hanging probe.** qB's client timeout is 15s; the tick must not lose or reorder other checks because one probe was slow, and must not run the slow check twice concurrently. Expected: remaining checks in that tick still run, the loop continues. (Task 1)
2. **A secret inside an exception's text.** `httpx` puts the full request URL in its message, and someone may put credentials in `qb_url`. Expected: the stored detail contains `***`, never the secret. (Task 1, Task 2)
3. **`/api/health` before the checker has ever run.** Expected: HTTP 200, `ok: true`, every check `unknown` with a `checked_at` of `null` — not a 500 from a `None` timestamp, and not a red board seconds after a restart. (Task 4)
4. **Staging missing or unreadable at runtime.** On first run `staging_root` does not exist yet (green, by its nearest existing parent); if the mount vanishes later, `disk_usage` raises `OSError`. Expected: green in the first case, `fail` with a readable detail in the second, never an unhandled exception. (Task 3)
5. **The board when `/api/health` fails or returns a check the JS has never heard of.** Expected: the last known lights stay, the board itself keeps rendering, and an unrecognised check name renders from its server-supplied label rather than crashing the strip. The repo has no JS test runner, so the pinnable part is a static assertion that `board.js` holds no check names of its own (Task 5); the failed-fetch behaviour is in the manual verification list.

---

### Task 1: The checker engine

**Files:**
- Create: `src/nasflow/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `redact(text, secrets)` from `redact.py`.
- Produces:
  - `CheckResult` — frozen dataclass: `name: str`, `label: str`, `status: str`, `detail: str`, `checked_at: float | None`.
  - `Check` — frozen dataclass: `name: str`, `label: str`, `interval_s: float`, `probe: Callable[[], str]`.
  - `CheckFailed(RuntimeError)` — a probe's own "this is unhealthy, here is why".
  - `NotYetKnown(Exception)` — a probe's "cannot say yet"; recorded as `unknown`.
  - `HealthRegistry(checks: Sequence[Check], secrets: Sequence[str | None] = ())` with `record(name, status, detail, checked_at)`, `snapshot() -> list[CheckResult]`, `overall_ok -> bool`, `due(now) -> list[Check]`.
  - `async def health_loop(registry, checks, *, tick_s=30.0, sleep=asyncio.sleep, now=time.time, max_iterations=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def probe_ok(detail="fine"):
    return lambda: detail

def check(name="qbittorrent", interval_s=300.0, probe=None):
    return Check(name=name, label=name.title(), interval_s=interval_s, probe=probe or probe_ok())

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
    registry, checks = _two_checks()          # intervals 300 and 900
    _run(registry, checks, iterations=1)
    assert [r.status for r in registry.snapshot()] == ["ok", "ok"]

def test_loop_reruns_only_what_is_due():
    # clock advances 300s per tick: the 300s check runs every tick, the 900s one every third
    runs = _run_with_clock(iterations=3, step_s=300.0)
    assert runs == {"fast": 3, "slow": 2}      # slow: tick 1 (never run) and tick 3 (900s elapsed)

def test_a_raising_probe_becomes_fail_with_its_message():
    def boom():
        raise CheckFailed("only 2 GB free in staging")
    registry, _ = _run_one(check(probe=boom))
    result = registry.snapshot()[0]
    assert result.status == "fail" and result.detail == "only 2 GB free in staging"

def test_an_unexpected_exception_names_its_type():
    def boom():
        raise KeyError("images")
    result = _run_one(check(probe=boom))[0].snapshot()[0]
    assert result.status == "fail" and result.detail.startswith("KeyError")

def test_not_yet_known_is_unknown_not_fail():
    def waiting():
        raise NotYetKnown("waiting for the first poll")
    registry = _run_one(check(probe=waiting))[0]
    result = registry.snapshot()[0]
    assert result.status == "unknown" and result.detail == "waiting for the first poll"
    assert registry.overall_ok

def test_a_probe_failure_does_not_stop_later_checks_or_the_loop():
    def boom():
        raise RuntimeError("down")
    registry, checks = _mixed(boom)            # failing check first, healthy one second
    _run(registry, checks, iterations=2)
    assert [r.status for r in registry.snapshot()] == ["fail", "ok"]

def test_a_slow_probe_does_not_lose_the_rest_of_the_tick():
    calls = []
    def slow():
        time.sleep(0.05)
        calls.append("slow")
        return "slow but fine"
    registry, checks = _mixed(slow, second_probe=lambda: calls.append("fast") or "fine")
    _run(registry, checks, iterations=1)
    assert calls == ["slow", "fast"]
    assert all(r.status == "ok" for r in registry.snapshot())

def test_probe_failure_text_is_redacted():
    def boom():
        raise RuntimeError("GET https://api.themoviedb.org/3/configuration?api_key=key-123")
    checks = [check(probe=boom)]
    registry = HealthRegistry(checks, secrets=["key-123"])
    _run(registry, checks, iterations=1)
    assert "key-123" not in registry.snapshot()[0].detail

def test_loop_sleeps_the_configured_tick():
    slept = []
    async def record(seconds):
        slept.append(seconds)
    registry, checks = _two_checks()
    asyncio.run(health_loop(registry, checks, tick_s=7.0, sleep=record, max_iterations=2))
    assert slept == [7.0, 7.0]
```

Helpers (`_run`, `_run_one`, `_two_checks`, `_mixed`, `_run_with_clock`) are local to the test module, built on `asyncio.run(health_loop(..., sleep=_no_sleep, now=fake_clock, max_iterations=n))` in the style of `tests/test_main.py:25-33`.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nasflow.health'`

- [ ] **Step 3: Implement `health.py`**

- `HealthRegistry.__init__` builds one `unknown` / `"not checked yet"` / `checked_at=None` result per check, in order, and keeps a `threading.Lock` (written by worker threads, read by the web threadpool).
- `record` redacts `detail` before storing.
- `due(now)` returns every check whose stored `checked_at` is `None` or older than `interval_s`.
- `health_loop` runs `registry.due(now())` sequentially via `await asyncio.to_thread(check.probe)`, records `("ok", detail)` on return, then sleeps `tick_s` — work first, so the first tick happens immediately. Exception mapping: `NotYetKnown` → `unknown` with `str(exc)`; `CheckFailed`, `QbitUnavailable`, `TmdbUnavailable` → `fail` with `str(exc)`; anything else → `fail` with `f"{type(exc).__name__}: {exc}"`.
- Module constants: `QB_INTERVAL_S = 300.0`, `TMDB_INTERVAL_S = 900.0`, `LOCAL_INTERVAL_S = 300.0`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_health.py -v && ruff check src/nasflow/health.py tests/test_health.py`
Expected: PASS, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/nasflow/health.py tests/test_health.py
git commit -m "feat: health check registry and background checker loop"
```

---

### Task 2: Cheap probes on the existing clients

**Files:**
- Modify: `src/nasflow/qbit.py`, `src/nasflow/tmdb.py`, `src/nasflow/db.py`, `src/nasflow/pathmap.py`, `src/nasflow/main.py:125-137`
- Test: `tests/test_qbit.py`, `tests/test_tmdb.py`, `tests/test_db.py`, `tests/test_pathmap.py`, `tests/test_main.py`

**Interfaces:**
- Produces:
  - `QbitClient.app_version() -> str` — `GET /api/v2/app/version`, through the same login-and-retry-once path as `_list`, raising `QbitUnavailable`.
  - `TmdbClient.ping() -> None` — `GET /configuration` with the API key, raising `TmdbUnavailable` built through the existing `_describe`.
  - `Store.ping() -> int` — row count of `items`, under the existing lock.
  - `nearest_existing(path: Path) -> Path` in `pathmap.py`, replacing `main.py:_nearest_existing`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_qbit.py — VERSION = f"{BASE}/api/v2/app/version"
def test_app_version_logs_in_and_returns_the_version(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Ok.")
        mock.get(VERSION).respond(200, text="v4.6.5")
        assert client.app_version() == "v4.6.5"

def test_app_version_relogins_once_on_403(client):
    # first GET 403, second 200 — mirrors test for `_list`'s retry
    ...
    assert client.app_version() == "v4.6.5"

def test_app_version_reports_rejected_credentials(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).respond(200, text="Fails.")
        with pytest.raises(QbitUnavailable, match="qb_password"):
            client.app_version()

# tests/test_tmdb.py — CONFIGURATION = f"{BASE}/configuration"
def test_ping_accepts_a_good_key():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(CONFIGURATION).respond(200, json={"images": {}})
        TmdbClient("key-123").ping()      # must not raise

def test_ping_on_401_does_not_leak_the_key():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(CONFIGURATION).respond(401, json={"status_message": "Invalid API key"})
        with pytest.raises(TmdbUnavailable) as exc:
            TmdbClient("key-123").ping()
        assert "key-123" not in str(exc.value) and "401" in str(exc.value)

# tests/test_db.py
def test_ping_returns_the_item_count(tmp_path):
    store = Store(tmp_path / "f.db")
    assert store.ping() == 0

# tests/test_pathmap.py
def test_nearest_existing_walks_up_to_a_folder_that_exists(tmp_path):
    assert nearest_existing(tmp_path / "a" / "b" / "c") == tmp_path
```

`tests/test_main.py:153` (`test_startup_checks_staging_by_its_nearest_existing_parent`) must still pass against the moved helper.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_qbit.py tests/test_tmdb.py tests/test_db.py tests/test_pathmap.py -v`
Expected: FAIL with `AttributeError: 'QbitClient' object has no attribute 'app_version'` and friends.

- [ ] **Step 3: Implement the four probes**

- `qbit.py`: factor the "login if needed, send, retry once on 403" dance out of `_list` into a private `_authenticated_get(path)` and have both `_list` and `app_version` use it. `app_version` returns `response.text.strip()`.
- `tmdb.py`: `ping` requests `f"{_BASE}/configuration"` with `{"api_key": self._api_key}`, raising `TmdbUnavailable(f"TMDB configuration failed: {self._describe(exc)}") from None` on `httpx.HTTPError | ValueError`.
- `db.py`: `@_locked def ping(self) -> int` returning `SELECT count(*) FROM items`.
- `pathmap.py`: move `_nearest_existing` in as `nearest_existing` with its docstring; `main.py` imports it and drops its copy.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q && ruff check src tests`
Expected: PASS — including the existing qB, TMDB, and startup tests.

- [ ] **Step 5: Commit**

```bash
git add src/nasflow tests
git commit -m "feat: cheap liveness probes on the qB, TMDB, and store clients"
```

---

### Task 3: The five checks, config, and poll freshness

**Files:**
- Modify: `src/nasflow/health.py`, `src/nasflow/config.py`, `src/nasflow/pipeline.py:68-106`, `config.example.toml`
- Test: `tests/test_health.py`, `tests/test_config.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1's `Check` / `CheckFailed` / `NotYetKnown` / interval constants; Task 2's four probes; `nearest_existing`.
- Produces:
  - `build_checks(cfg, store, pipeline, *, now=time.time) -> list[Check]` — the five checks, in display order: `qbittorrent`, `tmdb`, `staging`, `database`, `polling`, with labels `qBittorrent`, `TMDB`, `Staging folder`, `Database`, `Polling`.
  - `Config.health_min_free_gb: int = 20`, `Config.health_tick_s: int = 30`.
  - `Pipeline.last_poll_at: float | None` — `None` until the first `poll_once` returns without raising.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_health.py
def test_build_checks_lists_the_five_in_display_order(cfg, store, pipeline):
    names = [c.name for c in build_checks(cfg, store, pipeline)]
    assert names == ["qbittorrent", "tmdb", "staging", "database", "polling"]

def test_qbittorrent_check_reports_the_version(cfg, store, pipeline):
    pipeline.qbit.version = "v4.6.5"
    assert "4.6.5" in _probe(build_checks(cfg, store, pipeline), "qbittorrent")

def test_staging_check_is_ok_before_staging_exists(cfg, store, pipeline):
    shutil.rmtree(cfg.staging_root)                       # first run: not created yet
    assert "free" in _probe(build_checks(cfg, store, pipeline), "staging")

def test_staging_check_fails_across_filesystems(cfg, store, pipeline, monkeypatch):
    # monkeypatch Path.stat so downloads and staging report different st_dev
    with pytest.raises(CheckFailed, match="different filesystems"):
        _probe(build_checks(cfg, store, pipeline), "staging")

def test_staging_check_fails_below_the_free_space_floor(cfg, store, pipeline, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: _usage(free=3 * 1024**3))
    with pytest.raises(CheckFailed, match="3 GB free"):
        _probe(build_checks(cfg, store, pipeline), "staging")   # floor is 20

def test_staging_check_fails_when_the_mount_has_vanished(cfg, store, pipeline, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", _raise(OSError("Stale file handle")))
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
    pipeline.last_poll_at = 1000.0            # poll_interval_s is 60 -> limit 180
    checks = build_checks(cfg, store, pipeline, now=lambda: 1000.0 + 3600)
    with pytest.raises(CheckFailed, match="3600s ago"):
        _probe(checks, "polling")

def test_intervals_match_the_spec(cfg, store, pipeline):
    by_name = {c.name: c.interval_s for c in build_checks(cfg, store, pipeline)}
    assert by_name == {"qbittorrent": 300.0, "tmdb": 900.0,
                       "staging": 300.0, "database": 300.0, "polling": 300.0}

# tests/test_config.py — written in this module's existing style for writing a config.toml
def test_health_settings_default_when_absent(tmp_path):
    cfg = load_config(_write_minimal_config(tmp_path))
    assert (cfg.health_min_free_gb, cfg.health_tick_s) == (20, 30)

def test_health_settings_are_overridable(tmp_path):
    cfg = load_config(_write_minimal_config(tmp_path, health_min_free_gb=50, health_tick_s=5))
    assert (cfg.health_min_free_gb, cfg.health_tick_s) == (50, 5)

# tests/test_pipeline.py
def test_last_poll_at_is_none_until_the_first_poll(pipeline):
    assert pipeline.last_poll_at is None

def test_last_poll_at_is_set_after_a_poll(pipeline):
    pipeline.poll_once()
    assert pipeline.last_poll_at is not None

def test_last_poll_at_is_set_even_when_qbittorrent_is_down(pipeline, qbit):
    qbit.fail = True
    pipeline.poll_once()
    assert pipeline.last_poll_at is not None     # the loop ran; qB has its own light
```

`FakeQbit` in `tests/test_pipeline.py` gains `version = "v4.6.5"` and `app_version()`; `FakeTmdb` gains a no-op `ping()`. `_probe(checks, name)` calls that check's `probe()` and returns its detail.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_health.py tests/test_config.py tests/test_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_checks'`, and `AttributeError: last_poll_at`.

- [ ] **Step 3: Implement the checks, the config fields, and `last_poll_at`**

- `build_checks` assembles five `Check`s whose probes return the detail strings pinned above: `f"version {v}"`, `"API key accepted"`, `f"linkable, {free_gb:.0f} GB free"`, `f"ok, {n} items"`, `f"last poll {gap:.0f}s ago"`.
- Staging probe: compare `nearest_existing(cfg.downloads_root).stat().st_dev` with `nearest_existing(cfg.staging_root).stat().st_dev`, raising `CheckFailed` naming "different filesystems" when they differ; `shutil.disk_usage(nearest_existing(cfg.staging_root))` for free space, `OSError` → `CheckFailed(f"cannot read staging: {exc}")`; below `cfg.health_min_free_gb` → `CheckFailed`.
- Polling probe: `NotYetKnown("waiting for the first poll")` while `last_poll_at is None`; limit is `max(3 * cfg.poll_interval_s, 180)`.
- `pipeline.py`: rename today's `poll_once` body to `_poll_once`; the new `poll_once` calls it and then sets `self.last_poll_at = time.time()`, so an exception propagates as it does today and leaves the timestamp stale.
- `config.py`: the two defaulted fields, each with a one-line comment; `config.example.toml` gains both with the spec's values and a line saying what red means.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -q && ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/nasflow tests config.example.toml
git commit -m "feat: the five dependency checks and poll-loop freshness"
```

---

### Task 4: `GET /api/health`

**Files:**
- Modify: `src/nasflow/web/schemas.py`, `src/nasflow/web/routes.py`, `src/nasflow/web/app.py`, `tests/conftest.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 1's `HealthRegistry.snapshot()` / `overall_ok`, Task 3's `build_checks`.
- Produces:
  - `HealthCheckOut(name: str, label: str, status: str, detail: str, checked_at: float | None)` and `HealthOut(ok: bool, checks: list[HealthCheckOut])` in `schemas.py`.
  - `create_app(pipeline, store, cfg, lifespan=None, health=None)` and `build_router(pipeline, store, cfg, health=None)`; `health=None` becomes an empty `HealthRegistry(())`.
  - The `api` fixture's tuple gains the registry as its final element.

- [ ] **Step 1: Write the failing tests**

```python
def test_health_before_any_check_has_run_is_ok_and_unknown(api):
    body = api[0].get("/api/health").json()
    assert body["ok"] is True
    assert {c["status"] for c in body["checks"]} == {"unknown"}
    assert all(c["checked_at"] is None for c in body["checks"])

def test_health_reflects_recorded_results(api):
    registry = api[-1]
    registry.record("qbittorrent", "fail", "login rejected: check qb_password", 1761500000.0)
    body = api[0].get("/api/health").json()
    assert body["ok"] is False
    qb = next(c for c in body["checks"] if c["name"] == "qbittorrent")
    assert qb == {"name": "qbittorrent", "label": "qBittorrent", "status": "fail",
                  "detail": "login rejected: check qb_password", "checked_at": 1761500000.0}

def test_health_endpoint_runs_no_probe(api):
    _, _, _, qbit, tmdb, _, _, _ = api
    api[0].get("/api/health")
    assert qbit.calls == [] and tmdb.calls == []

def test_health_exposes_no_secret(api):
    response = api[0].get("/api/health")
    assert "super-secret" not in response.text and "key-123" not in response.text

def test_health_labels_come_from_the_server(api):
    labels = [c["label"] for c in api[0].get("/api/health").json()["checks"]]
    assert "qBittorrent" in labels and "TMDB" in labels
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_web_api.py -k health -v`
Expected: FAIL — 404 on `/api/health`

- [ ] **Step 3: Implement the endpoint**

`@router.get("/api/health") def health() -> HealthOut` returns `HealthOut(ok=registry.overall_ok, checks=[HealthCheckOut(**asdict(r)) for r in registry.snapshot()])` and nothing else — it must never probe. `conftest.py` builds `HealthRegistry(build_checks(cfg, store, pipeline), secrets=[cfg.tmdb_api_key, cfg.qb_password])`, passes it to `create_app`, and appends it to the fixture tuple; `FakeQbit`/`FakeTmdb` record calls so the no-probe test can assert emptiness.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -q && ruff check src tests`
Expected: PASS — existing `api`-fixture tests still pass with the widened tuple.

- [ ] **Step 5: Commit**

```bash
git add src/nasflow/web tests
git commit -m "feat: GET /api/health returns the last recorded snapshot"
```

---

### Task 5: The lights on the board

**Files:**
- Modify: `src/nasflow/web/static/index.html`, `api.js`, `board.js`, `style.css`, `README.md`
- Test: `tests/test_web_static.py`

**Interfaces:**
- Consumes: Task 4's `/api/health` shape.
- Produces: `getHealth()` in `api.js`; `#health` container in the header.

- [ ] **Step 1: Write the failing tests**

```python
def test_health_strip_is_in_the_page(api):
    assert 'id="health"' in api[0].get("/").text

def test_only_api_js_calls_health(api):
    assert "getHealth" in (STATIC / "api.js").read_text(encoding="utf-8")
    assert "/api/health" not in (STATIC / "board.js").read_text(encoding="utf-8")

def test_check_labels_are_not_hardcoded_in_js():
    for name in MODULES:
        text = (STATIC / name).read_text(encoding="utf-8")
        for label in ("qBittorrent", "TMDB", "Staging folder", "Database", "Polling"):
            assert label not in text, f"{name} hardcodes {label!r}"
```

The existing `test_only_api_js_calls_fetch` covers the new code too.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_web_static.py -v`
Expected: FAIL — no `id="health"` in `index.html`

- [ ] **Step 3: Implement the strip**

- `index.html`: `<div id="health" class="health" role="status"></div>` in `.top`, after the existing `#status` span.
- `api.js`: `export const getHealth = () => request("GET", "/api/health");`
- `board.js`: `HEALTH_POLL_MS = 30000`; a `refreshHealth()` that renders `health.checks` as one dot plus label each — class `dot-ok` / `dot-fail` / `dot-unknown` from `status`, `title` and `aria-label` both `` `${label}: ${status} — ${detail}` ``, label text straight from the server so an unknown check name still renders. When `ok` is false, append the failing checks' `label: detail` as visible text. On a fetch error, keep the last rendered strip and leave `#status` to the board poll. Started from `start()` alongside the board poll.
- `style.css`: `.health`, `.dot`, `.dot-ok` (`var(--ok)`), `.dot-fail` (`var(--err)`), `.dot-unknown` (grey), `.health-detail` (`var(--err)`), in the file's existing compact idiom.
- `README.md`: a short paragraph under "The board" — what the lights mean, that grey means not checked yet, that qB and the local checks refresh every 5 minutes and TMDB every 15.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -q && ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/nasflow/web/static README.md tests/test_web_static.py
git commit -m "feat: show dependency lights on the board"
```

---

### Task 6: Start the checker with the app

**Files:**
- Modify: `src/nasflow/main.py:47-73`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `HealthRegistry`, `build_checks`, `health_loop`, `create_app(..., health=...)`.
- Produces: nothing new; `build_app` wires the checker into the lifespan.

- [ ] **Step 1: Write the failing tests**

```python
def test_built_app_starts_and_stops_the_health_loop(tmp_path, monkeypatch):
    started = []
    async def fake_loop(registry, checks, **kwargs):
        started.append(kwargs.get("tick_s"))
        await asyncio.sleep(3600)
    monkeypatch.setattr(flow_main, "health_loop", fake_loop)
    with TestClient(flow_main.build_app(_cfg(tmp_path))):
        pass
    assert started == [30]        # cfg.health_tick_s; cancelled cleanly on exit

def test_health_loop_is_off_when_polling_is_off(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(flow_main, "health_loop", lambda *a, **k: started.append(1))
    with TestClient(flow_main.build_app(_cfg(tmp_path), start_polling=False)):
        pass
    assert started == []

def test_built_app_serves_health(tmp_path):
    with TestClient(flow_main.build_app(_cfg(tmp_path), start_polling=False)) as client:
        body = client.get("/api/health").json()
    assert [c["name"] for c in body["checks"]][0] == "qbittorrent"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — `AttributeError: module 'nasflow.main' has no attribute 'health_loop'`

- [ ] **Step 3: Implement the wiring**

`build_app` creates `registry = HealthRegistry(build_checks(cfg, store, pipeline), secrets=[cfg.tmdb_api_key, cfg.qb_password])` and passes it to `create_app` as `health=`. The lifespan starts `health_loop(registry, checks, tick_s=cfg.health_tick_s)` as a second task when `start_polling` is true, and cancels both tasks on shutdown with the existing `suppress(CancelledError)` pattern — refactor the single-task teardown into a loop over a list of tasks.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q && ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/nasflow/main.py tests/test_main.py
git commit -m "feat: run the health checker alongside the poll loop"
```

---

## Manual verification (after Task 6)

Not a task; the check a human does once on the NAS.

- [ ] Open the board: five lights, green within 30 seconds of start.
- [ ] Put a wrong `qb_password` in `config.toml`, restart: the qBittorrent light goes red within ~5 minutes reading "login rejected: check qb_username / qb_password", the rest stay green, and the board still shows its cards.
- [ ] Stop nasflow with the board open: the strip keeps its last lights and the cards stay on screen (only `#status` turns red), rather than blanking.
