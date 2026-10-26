"""SQLite item store.

One table keyed by torrent hash. The important invariant is that a human
decision is durable: `upsert_discovered` must never clobber a chosen TMDB id
or an `ignored` state, because re-seeing a torrent on the next poll should
not re-ask a question the human already answered.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import Candidate
from .qbit import Torrent

# The persisted vocabulary. The board maps these to its own column names.
STATES = ("discovered", "identifying", "needs_you", "ready", "done", "ignored")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    hash            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    content_path    TEXT NOT NULL,
    state           TEXT NOT NULL,
    kind            TEXT,
    reason          TEXT NOT NULL DEFAULT '',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    chosen_tmdb_id  INTEGER,
    dest_root       TEXT,
    discovered_at   REAL NOT NULL,
    updated_at      REAL NOT NULL,
    retry           INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS flags (
    name TEXT PRIMARY KEY
);
"""

# Columns added after the first release, applied to existing databases on open.
_MIGRATIONS = {
    "retry": "ALTER TABLE items ADD COLUMN retry INTEGER NOT NULL DEFAULT 0",
}


def _locked(method):
    """Serialise access: the poll thread and the web threadpool share one
    connection, and sqlite3 connections are not safe for concurrent use."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


@dataclass(frozen=True)
class Item:
    hash: str
    name: str
    content_path: str
    state: str
    kind: str | None
    reason: str
    chosen_tmdb_id: int | None
    dest_root: str | None
    discovered_at: float
    updated_at: float
    # A stop that can clear by itself (TMDB down, destination occupied) and is
    # retried by the next poll. Judgement stops wait for the human.
    retry: bool = False


class Store:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        present = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        for column, statement in _MIGRATIONS.items():
            if column not in present:
                self._conn.execute(statement)
        self._conn.commit()

    @_locked
    def close(self) -> None:
        self._conn.close()

    @_locked
    def get_flag(self, name: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM flags WHERE name = ?", (name,)).fetchone()
        return row is not None

    @_locked
    def set_flag(self, name: str) -> None:
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO flags (name) VALUES (?)", (name,))

    @_locked
    def upsert_discovered(self, torrent: Torrent) -> None:
        """Record a torrent we have seen, without disturbing prior decisions."""
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO items (hash, name, content_path, state,
                                   discovered_at, updated_at)
                VALUES (?, ?, ?, 'discovered', ?, ?)
                ON CONFLICT(hash) DO UPDATE SET
                    name = excluded.name,
                    content_path = excluded.content_path
                """,
                (torrent.hash, torrent.name, torrent.content_path, now, now),
            )

    @_locked
    def set_state(
        self, hash_: str, state: str, reason: str | None = None, retry: bool = False
    ) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        with self._conn:
            self._conn.execute(
                "UPDATE items SET state = ?, reason = ?, retry = ?, updated_at = ? WHERE hash = ?",
                (state, reason or "", int(retry), time.time(), hash_),
            )

    @_locked
    def set_candidates(self, hash_: str, candidates: list[Candidate]) -> None:
        payload = json.dumps([asdict(c) for c in candidates], ensure_ascii=False)
        with self._conn:
            self._conn.execute(
                "UPDATE items SET candidates_json = ?, updated_at = ? WHERE hash = ?",
                (payload, time.time(), hash_),
            )

    @_locked
    def get_candidates(self, hash_: str) -> list[Candidate]:
        row = self._conn.execute(
            "SELECT candidates_json FROM items WHERE hash = ?", (hash_,)
        ).fetchone()
        if row is None:
            return []
        return [Candidate(**raw) for raw in json.loads(row["candidates_json"])]

    @_locked
    def set_resolution(self, hash_: str, tmdb_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE items SET chosen_tmdb_id = ?, updated_at = ? WHERE hash = ?",
                (tmdb_id, time.time(), hash_),
            )

    @_locked
    def set_kind(self, hash_: str, kind: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE items SET kind = ?, updated_at = ? WHERE hash = ?",
                (kind, time.time(), hash_),
            )

    @_locked
    def set_linked(self, hash_: str, dest_root: str) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE items SET dest_root = ?, state = 'ready', reason = '',
                   updated_at = ? WHERE hash = ?""",
                (dest_root, time.time(), hash_),
            )

    @_locked
    def get(self, hash_: str) -> Item | None:
        row = self._conn.execute("SELECT * FROM items WHERE hash = ?", (hash_,)).fetchone()
        return _to_item(row) if row else None

    @_locked
    def all_items(self) -> list[Item]:
        rows = self._conn.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()
        return [_to_item(r) for r in rows]

    @_locked
    def items_in(self, states: list[str]) -> list[Item]:
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        rows = self._conn.execute(
            f"SELECT * FROM items WHERE state IN ({placeholders}) ORDER BY updated_at DESC",
            states,
        ).fetchall()
        return [_to_item(r) for r in rows]


def _to_item(row: sqlite3.Row) -> Item:
    return Item(
        hash=row["hash"],
        name=row["name"],
        content_path=row["content_path"],
        state=row["state"],
        kind=row["kind"],
        reason=row["reason"],
        chosen_tmdb_id=row["chosen_tmdb_id"],
        dest_root=row["dest_root"],
        discovered_at=row["discovered_at"],
        updated_at=row["updated_at"],
        retry=bool(row["retry"]),
    )
