"""Orchestration: wires the pure core to the I/O shells.

Deliberately thin. The stop-or-proceed rule lives in `decide.py` and is not
duplicated here — no threshold comparison appears in this file. What this
module owns is sequencing and the handling of things that can fail: TMDB being
down, a destination being occupied, a torrent vanishing mid-poll.
"""

from __future__ import annotations

import functools
import logging
import threading
from dataclasses import replace
from pathlib import Path

from .config import Config
from .db import Store
from .decide import decide
from .layout import plan_links
from .linker import LinkConflict, execute_links, prune_empty_dirs
from .models import Candidate, Identification
from .naming import parse_release_name
from .pathmap import to_host_path
from .qbit import QbitUnavailable
from .scan import scan_content
from .tmdb import TmdbUnavailable

log = logging.getLogger(__name__)

# States that mean "flow is finished with this, leave it alone".
_SETTLED = frozenset({"ready", "done", "ignored"})


def _serialized(method):
    """One item-changing operation at a time.

    The poll loop and the board's request threads both drive the pipeline. A
    poll that finished after a human's resolve would overwrite their choice.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class Pipeline:
    def __init__(self, config: Config, store: Store, qbit, tmdb):
        self.cfg = config
        self.store = store
        self.qbit = qbit
        self.tmdb = tmdb
        self._lock = threading.RLock()

    # ---- driving ---------------------------------------------------------

    def poll_once(self) -> None:
        """Ask qBittorrent what has finished, and process anything new."""
        try:
            torrents = self.qbit.completed_torrents()
        except QbitUnavailable as exc:
            log.warning("qBittorrent unavailable: %s", exc)
            return

        for torrent in torrents:
            self.store.upsert_discovered(torrent)

        for torrent in torrents:
            # Locked per item, not per poll: a human's click waits for at most
            # one item's TMDB lookup, and sees the state as it is after it.
            with self._lock:
                item = self.store.get(torrent.hash)
                if item is not None and item.state in _SETTLED:
                    continue
                if item is not None and item.state == "needs_you" and not item.retry:
                    continue  # waiting for a human; re-asking TMDB changes nothing
                try:
                    self.process(torrent.hash)
                except Exception as exc:
                    # One bad item becomes one stopped card, never a stuck
                    # pipeline that skips every torrent after it.
                    log.exception("processing %s failed", torrent.name)
                    self.store.set_state(
                        torrent.hash,
                        "needs_you",
                        reason=f"unexpected error: {type(exc).__name__}: {exc}",
                        retry=True,
                    )

    @_serialized
    def process(self, hash_: str) -> None:
        """Identify one item and either file it or stop for a human."""
        item = self.store.get(hash_)
        if item is None:
            return

        self.store.set_state(hash_, "identifying")

        try:
            source = to_host_path(
                item.content_path, self.cfg.container_downloads_prefix, self.cfg.downloads_root
            )
        except ValueError as exc:
            self.store.set_state(hash_, "needs_you", reason=str(exc), retry=True)
            return

        content = scan_content(source, self.cfg.media)
        if content.kind != "unknown":
            self.store.set_kind(hash_, content.kind)

        parsed = parse_release_name(item.name)

        # A human has already answered this one; honour it without re-asking.
        if item.chosen_tmdb_id is not None:
            self._link_chosen(hash_, content, item.chosen_tmdb_id)
            return

        try:
            kind = content.kind if content.kind != "unknown" else "movie"
            candidates = self.tmdb.search(parsed, kind)
        except TmdbUnavailable as exc:
            self.store.set_state(hash_, "needs_you", reason=f"TMDB unavailable: {exc}", retry=True)
            return

        self.store.set_candidates(hash_, candidates)

        decision = decide(content, parsed, candidates, self.cfg)
        if decision.action == "stop":
            self.store.set_state(hash_, "needs_you", reason=decision.reason)
            return

        self._link(hash_, content, Identification(decision.candidate, content.kind))

    # ---- human actions ---------------------------------------------------

    @_serialized
    def resolve(self, hash_: str, tmdb_id: int) -> None:
        """File this item as the entry the human picked."""
        item = self.store.get(hash_)
        if item is None:
            return
        self.store.set_resolution(hash_, tmdb_id)

        try:
            source = to_host_path(
                item.content_path, self.cfg.container_downloads_prefix, self.cfg.downloads_root
            )
        except ValueError as exc:
            self.store.set_state(hash_, "needs_you", reason=str(exc), retry=True)
            return

        self._link_chosen(hash_, scan_content(source, self.cfg.media), tmdb_id)

    @_serialized
    @_serialized
    def search(self, hash_: str, query: str) -> list[Candidate]:
        """A human's manual TMDB search; its results replace the candidates."""
        item = self.store.get(hash_)
        if item is None:
            return []
        parsed = replace(parse_release_name(item.name), title_en=query, title_zh=None)
        found = self.tmdb.search(parsed, item.kind or "movie")
        self.store.set_candidates(hash_, found)
        return found

    def ignore(self, hash_: str) -> None:
        self.store.set_state(hash_, "ignored", reason="marked not media")

    # ---- staging reconciliation -----------------------------------------

    @_serialized
    def reconcile_staging(self) -> None:
        """Notice what the human has moved out, without being told.

        Watching staging for *disappearance* is safe, unlike watching downloads
        for appearance: a folder only leaves staging because a human moved it.
        """
        # Prune first: moving only a season folder leaves its show folder empty,
        # and the show folder is what `dest_root` records.
        prune_empty_dirs(self.cfg.staging_root)
        for item in self.store.items_in(["ready"]):
            if item.dest_root and not Path(item.dest_root).exists():
                self.store.set_state(item.hash, "done", reason="moved to the library")

    # ---- internals -------------------------------------------------------

    def _link_chosen(self, hash_: str, content, tmdb_id: int) -> None:
        kind = content.kind if content.kind != "unknown" else "movie"
        candidates = [c for c in self.store.get_candidates(hash_) if c.tmdb_id == tmdb_id]
        candidate = candidates[0] if candidates else None

        if candidate is None:
            try:
                candidate = self.tmdb.get(tmdb_id, kind)
            except TmdbUnavailable as exc:
                self.store.set_state(
                    hash_, "needs_you", reason=f"TMDB unavailable: {exc}", retry=True
                )
                return

        if candidate is None:
            self.store.set_state(hash_, "needs_you", reason=f"TMDB id {tmdb_id} not found")
            return

        self._link(hash_, content, Identification(candidate, kind))

    def _link(self, hash_: str, content, ident: Identification) -> None:
        links = plan_links(content, ident, self.cfg.staging_root, self.cfg.layout)
        if not links:
            self.store.set_state(hash_, "needs_you", reason="nothing to link")
            return

        try:
            execute_links(links, self.cfg.staging_root)
        except LinkConflict as exc:
            self.store.set_state(
                hash_, "needs_you", reason=f"destination conflict: {exc}", retry=True
            )
            return

        self.store.set_linked(hash_, str(_title_root(links, self.cfg.staging_root)))


def _title_root(links, staging_root: Path) -> Path:
    """The folder a human will move: the show or movie directory, not a season.

    Every layout is `<kind dir>/<title dir>/...` under the staging root, so the
    title folder is the first two components of any planned destination.
    """
    relative = links[0].dest.relative_to(staging_root)
    return staging_root / relative.parts[0] / relative.parts[1]
