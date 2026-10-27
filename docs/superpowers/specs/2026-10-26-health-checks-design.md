# health checks — design

_2026-10-26_

## Intent and why

nasflow can be running perfectly and filing nothing. A wrong `qb_password`, an
expired TMDB key, a volume that came back from a reboot without the bind mount
— each of these stops the pipeline dead, and today the only trace is a
`log.warning` inside the container (`pipeline.py:73`). The board keeps saying
"updated 14:22" because the board is reporting on nasflow's own web server, not
on the things nasflow depends on.

This adds a background checker that probes each dependency on its own cadence
and a light on the board showing the last recorded result. The point is that a
glance at the board answers "is nasflow actually able to work right now?".

## Goals

- Each dependency probed periodically whether or not anyone is watching: qB and
  the local checks every 5 minutes, TMDB every 15.
- A green light on the board, per dependency, from the last recorded result.
- When a light is red, the reason is on the board, specific enough to act on
  ("login rejected: check qb_password"), without opening the container log.
- Probes cost nothing semantic: no torrent is touched, no TMDB search is spent,
  nothing is written to staging.

## Non-goals

- Alerting: no email, no push, no webhook. The light is passive.
- History or uptime tracking. The board shows now, not a timeline.
- Health-gated behaviour: the poll loop keeps running exactly as it does today
  regardless of what the checker found. The light reports; it does not steer.
- Authentication on the endpoint. nasflow has none by design (LAN only), and
  `/api/health` follows the rest of the API.
- A readiness/liveness probe for Docker. If that's wanted later, this endpoint
  is the obvious foundation, but nothing here is shaped for it.

## Anti-goals

These would make the feature a failure even if it worked:

- **A green light while nasflow is unable to file.** A probe that is cheaper
  than the thing it stands in for, and so passes when the real call would fail,
  is worse than no light. This is why the qB probe is authenticated and the
  TMDB probe sends the API key.
- **Leaking a secret onto the board.** Probe failures become display text.
  `httpx` exception text carries the full request URL, and TMDB v3 takes the API
  key as a query parameter. Every detail string is redacted before it leaves
  the probe.
- **The checker breaking the app.** A probe that hangs or raises must not stall
  the poll loop, the web server, or the next check.

## Constraints

- The clients are synchronous (`httpx.Client`), so probes run off the event
  loop via `asyncio.to_thread`, the same way `poll_loop` runs the pipeline.
- `Store` serialises on one connection and one lock; a DB probe must be a
  trivial query so it never holds the lock behind a real decision.
- `Config` is the only source of settings, and every setting it reads lives in
  `config.example.toml`.
- Checks must be testable without network or sleeping: intervals and the clock
  are injectable, as `poll_loop` already does with `sleep`.

## Approaches

**A. Background checker task, in-memory snapshot** *(chosen)*. A second
`asyncio` task alongside `poll_loop` in `main.py`'s lifespan. Each check keeps
its own interval; results live in a small thread-safe registry.
`GET /api/health` returns the last snapshot. Nothing survives a restart, so the
board shows "checking…" for the few seconds until the first pass lands.

**B. Same, persisted in SQLite.** Survives restarts, and would allow "red since
19:40". Rejected: a schema migration and a write path for a value that
re-derives itself seconds after boot, and history is a non-goal.

**C. On-demand with a TTL cache.** `/api/health` probes anything stale; no
background task. Rejected: checks only happen while a browser is open, which
misses exactly the "nothing moved all evening" case this is for.

## What gets built

### `src/nasflow/health.py` (new)

The whole feature except the endpoint and the rendering.

- `CheckResult` — frozen dataclass: `name`, `label`, `status`
  (`"ok" | "fail" | "unknown"`), `detail`, `checked_at` (epoch seconds, or
  `None` when never run).
- `HealthRegistry` — holds one `CheckResult` per check name behind a `Lock`
  (written by the checker's worker threads, read by the web threadpool).
  `snapshot()` returns results in registration order; every check starts as
  `unknown` with detail `"not checked yet"`. `overall_ok` is false only when a
  check has `status == "fail"`: `unknown` is not red, so a fresh start is not
  alarming.
- `Check` — frozen dataclass: `name`, `label`, `interval_s`, and `probe`, a
  zero-argument callable returning a detail string on success and raising on
  failure.
- `build_checks(cfg, store, pipeline) -> list[Check]` — assembles the five
  checks below from the already-wired objects (the clients come off
  `pipeline.qbit` / `pipeline.tmdb`, so the checker probes the very same client
  instances the pipeline uses, session state included). Sharing those clients
  across the poll thread and the checker's worker thread is safe:
  `httpx.Client` is thread-safe, and the only shared mutable state is
  `QbitClient._authenticated`, where a race costs at worst one redundant
  login.
- `async def health_loop(registry, checks, *, tick_s=30, sleep=asyncio.sleep,
  now=time.time)` — every tick, runs each check whose interval has elapsed, in
  `asyncio.to_thread`, and records the result. Every check runs on the first
  tick, which happens immediately (sleep after work, not before). A probe
  raising anything at all becomes `fail` with a redacted detail; the loop
  itself never exits on a probe failure, mirroring `poll_loop`'s "one bad pass
  never kills the loop".
- Redaction: every detail string passes through `redact(text, secrets)` from
  `redact.py` with `[cfg.tmdb_api_key, cfg.qb_password]`, belt and braces over
  the clients' own masking.

### The five checks

| name | label | interval | probe |
|---|---|---|---|
| `qbittorrent` | qBittorrent | 300s | `QbitClient.app_version()` → `"version 4.6.5"` |
| `tmdb` | TMDB | 900s | `TmdbClient.ping()` → `"API key accepted"` |
| `staging` | Staging folder | 300s | hardlink-capable and has room → `"linkable, 412 GB free"` |
| `database` | Database | 300s | `Store.ping()` → `"ok, 37 items"` |
| `polling` | Polling | 300s | last completed poll is recent → `"last poll 42s ago"` |

- **qBittorrent** — new `QbitClient.app_version()`: `GET /api/v2/app/version`
  through the same login-and-retry-once path as `_list`, raising
  `QbitUnavailable`. Authenticated on purpose: it proves reachability *and*
  credentials, which is the failure actually worth catching. The existing
  "login rejected: check qb_username / qb_password" message is what the board
  will show.
- **TMDB** — new `TmdbClient.ping()`: `GET /configuration` with the API key,
  raising `TmdbUnavailable` via the existing `_describe` (so the key cannot
  reach the detail string). `/configuration` costs no search and returns a
  stable payload; a 401 there means the key is bad.
- **Staging folder** — re-checks at runtime what `main.py` checks once at
  startup: that `downloads_root` and `staging_root` still resolve to one
  device, and reports free space. Both the `st_dev` comparison and
  `shutil.disk_usage` run against `nearest_existing(path)`, not the path
  itself — on a genuine first run `staging_root` does not exist yet, and a
  not-yet-created staging folder must show green, not red. Fails when the
  devices differ, when `downloads_root` itself has vanished, or when free
  space is under `health_min_free_gb`. `_nearest_existing` moves from `main.py` to `pathmap.py` as
  `nearest_existing` and both callers use it; `main.py`'s startup check is
  otherwise untouched, because failing fast at boot is still right.
  Deliberately *not* a real `os.link` probe: `probe_hardlink` writes files, and
  a periodic writer in staging is a worse idea than an `st_dev` comparison.
- **Database** — new `Store.ping()` under the existing lock, returning the row
  count (`SELECT count(*) FROM items`). Proves the file is open, readable, and
  not corrupt.
- **Polling** — `Pipeline` gains `last_poll_at: float | None`, set at the end
  of a `poll_once` that completed without raising (including the early return
  when qB is unavailable — that condition is the qB light's job, not this
  one's). The probe fails when the gap exceeds `max(3 × poll_interval_s, 180)`
  seconds, and reports `unknown` while it is still `None`. This is the only
  check that catches a wedged or dead loop, which none of the other four would
  show.

### Wiring — `main.py`

`build_app` creates the registry and the checks, passes the registry to
`create_app`, and the lifespan starts `health_loop` as a second task next to
`poll_loop`, cancelled the same way on shutdown. `start_polling=False` (used by
tests) also leaves the checker off.

`create_app` gains `health=None` as a trailing optional parameter, defaulting
to an empty registry (every check `unknown`). Existing positional callers,
including `conftest.py`, keep working unchanged, and a board served by an app
built without a checker shows grey lights rather than failing.

### API — `GET /api/health`

```json
{
  "ok": true,
  "checks": [
    {"name": "qbittorrent", "label": "qBittorrent", "status": "ok",
     "detail": "version 4.6.5", "checked_at": 1761500000.0}
  ]
}
```

`HealthOut` / `HealthCheckOut` go in `web/schemas.py` as explicit models, like
every other response — the board must never see a config value or a raw
exception that wasn't put there on purpose. Handler in `web/routes.py`:
`registry.snapshot()`, nothing else. It never probes, so hammering the endpoint
costs nothing and cannot be turned into a way to hit TMDB.

### Board — `static/`

- `api.js`: `getHealth()`.
- `board.js`: a 30s health poll, separate from the 4s board poll. The value
  changes at most every 5 minutes, but a 30s read keeps the light fresh within
  half a minute of a real change and costs one in-memory read.
- Rendering, in the header next to the existing status text: one small dot per
  check with its label, green / red / grey for ok / fail / unknown, each
  carrying its detail as a `title` tooltip. When any check is failing, its
  label and detail also render as visible text (`qBittorrent: login rejected…`)
  so the reason needs no hover — this is my call, not something discussed, and
  the cheapest thing to change if it's too loud.
- Labels come from the server, like the column labels, and
  `test_column_labels_are_not_hardcoded_in_js` has a sibling asserting the same
  for check labels. Colours use the existing `--ok` / `--err` custom properties.
- `aria-live` is deliberately absent on the light: a dot changing colour every
  few minutes is not worth announcing over the board's own updates. The
  container gets `role="status"` and each dot an `aria-label` of
  `"<label>: <status> — <detail>"`.

### Config

Two new settings in `config.example.toml`, both with defaults so an existing
`config.toml` keeps working untouched:

```toml
health_min_free_gb = 20      # staging light turns red below this
health_tick_s = 30           # how often the checker looks for due checks
```

Both become defaulted fields on the `Config` dataclass, so an existing
`config.toml` that omits them loads unchanged (`load_config` rejects unknown
keys, never missing optional ones). `20` is my call, not a discussed number:
it is a few hours of downloads' headroom, and it is the one value here worth
a second look.

Check intervals (300s / 900s) stay in code: they're the shape of the feature,
not a knob worth exposing, and the one that matters (TMDB's) is set by
politeness to someone else's API.

## Tests

Following the existing style — fakes over mocks, no network, no sleeping.

- `tests/test_health.py`: registry starts all-unknown; `overall_ok` ignores
  unknown but not fail; `health_loop` with a fake clock and fake sleep runs
  every check on the first tick, re-runs only what is due, turns a raising
  probe into `fail` and keeps going, and redacts a secret that appears in a
  probe's exception text.
- Each probe against its fake or `tmp_path`: qB version success, qB 403 →
  re-login → success, qB login rejected; TMDB ping ok and TMDB 401 with the key
  absent from the detail; staging on two devices (monkeypatched `st_dev`), a
  missing staging path, and free space under the floor; `Store.ping` on a real
  temp DB; polling fresh, stale, and never-polled.
- `tests/test_web_api.py`: `/api/health` shape, that it reflects the registry
  it was given, that it runs no probe, and that no secret appears in the body
  (the existing `"super-secret"` / `"key-123"` assertion pattern).
- `tests/test_web_static.py`: `getHealth` exists in `api.js` and only `api.js`
  calls `fetch`; check labels are not hardcoded in the JS.

The `api` fixture in `conftest.py` gains a registry so `create_app`'s signature
stays satisfied; it is seeded with fixed results so endpoint tests are
deterministic.

## Left to the builder

- Exact dot markup and CSS (a `<span>` with a background colour, inline SVG, a
  text glyph) — whatever fits `style.css`'s existing idiom.
- Whether `build_checks` takes `pipeline` or the four pieces it needs
  separately.
- Where `last_poll_at` is stored on `Pipeline` (plain attribute vs. behind the
  existing lock), given one writer and readers that tolerate a stale read.
