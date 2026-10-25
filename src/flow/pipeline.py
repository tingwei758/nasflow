"""Orchestration: wires the pure core to the I/O shells.

Deliberately thin. The stop-or-proceed rule lives in `decide.py` and is not
duplicated here — no threshold comparison appears in this file. What this
module owns is sequencing and the handling of things that can fail: TMDB being
down, a destination being occupied, a torrent vanishing mid-poll.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import Config
from .db import Store
from .decide import decide
from .layout import plan_links
from .linker import LinkConflict, execute_links, prune_empty_dirs
from .models import Identification
from .naming import parse_release_name
from .pathmap import to_host_path
from .qbit import QbitUnavailable
from .scan import scan_content
from .tmdb import TmdbUnavailable

log = logging.getLogger(__name__)

# States that mean "flow is finished with this, leave it alone".
_SETTLED = frozenset({"ready", "done", "ignored"})


class Pipeline:
    def __init__(self, config: Config, store: Store, qbit, tmdb):
        self.cfg = config
        self.store = store
        self.qbit = qbit
        self.tmdb = tmdb

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
            item = self.store.get(torrent.hash)
            if item is not None and item.state in _SETTLED:
                continue
            self.process(torrent.hash)

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
            self.store.set_state(hash_, "needs_you", reason=str(exc))
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
            self.store.set_state(hash_, "needs_you", reason=f"TMDB unavailable: {exc}")
            return

        self.store.set_candidates(hash_, candidates)

        decision = decide(content, parsed, candidates, self.cfg)
        if decision.action == "stop":
            self.store.set_state(hash_, "needs_you", reason=decision.reason)
            return

        self._link(hash_, content, Identification(decision.candidate, content.kind))

    # ---- human actions ---------------------------------------------------

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
            self.store.set_state(hash_, "needs_you", reason=str(exc))
            return

        self._link_chosen(hash_, scan_content(source, self.cfg.media), tmdb_id)

    def ignore(self, hash_: str) -> None:
        self.store.set_state(hash_, "ignored", reason="marked not media")

    # ---- staging reconciliation -----------------------------------------

    def reconcile_staging(self) -> None:
        """Notice what the human has moved out, without being told.

        Watching staging for *disappearance* is safe, unlike watching downloads
        for appearance: a folder only leaves staging because a human moved it.
        """
        for item in self.store.items_in(["ready"]):
            if item.dest_root and not Path(item.dest_root).exists():
                self.store.set_state(item.hash, "done", reason="moved to the library")
        prune_empty_dirs(self.cfg.staging_root)

    # ---- internals -------------------------------------------------------

    def _link_chosen(self, hash_: str, content, tmdb_id: int) -> None:
        kind = content.kind if content.kind != "unknown" else "movie"
        candidates = [c for c in self.store.get_candidates(hash_) if c.tmdb_id == tmdb_id]
        candidate = candidates[0] if candidates else None

        if candidate is None:
            try:
                candidate = self.tmdb.get(tmdb_id, kind)
            except TmdbUnavailable as exc:
                self.store.set_state(hash_, "needs_you", reason=f"TMDB unavailable: {exc}")
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
            execute_links(links)
        except LinkConflict as exc:
            self.store.set_state(hash_, "needs_you", reason=f"destination conflict: {exc}")
            return

        self.store.set_linked(hash_, str(_common_root(links)))


def _common_root(links) -> Path:
    """The folder a human will move: the show or movie directory, not a season."""
    return Path(os.path.commonpath([str(link.dest.parent) for link in links]))
