"""The only module in flow that creates anything on disk.

Two rules it must never break:

1. **Hardlinks only.** `os.link`, never a copy. A copy would silently consume
   another full-size allocation and take minutes instead of milliseconds.
2. **The source is read-only.** The files qBittorrent is seeding are never
   renamed, moved, modified, or deleted — only given a second name.

A batch is all-or-nothing: if any link conflicts, the links this call already
made are removed, so a half-filed season never reaches the board as "ready".
"""

from __future__ import annotations

import os
from pathlib import Path

from .layout import PlannedLink


class LinkConflict(RuntimeError):
    """A destination exists and is not already a link to our source."""

    def __init__(self, dest: Path, reason: str):
        super().__init__(f"{dest}: {reason}")
        self.dest = dest
        self.reason = reason


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_ino == b.stat().st_ino and a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def execute_links(links: list[PlannedLink]) -> None:
    """Create every link, or none of them.

    An existing destination that is already a hardlink to the same source is a
    no-op success, which makes re-processing a torrent safe. An existing
    destination that is a different file raises `LinkConflict` — flow never
    overwrites.
    """
    created: list[Path] = []
    try:
        for link in links:
            if link.dest.exists():
                if _same_file(link.source, link.dest):
                    continue
                raise LinkConflict(link.dest, "destination exists and is a different file")

            if not link.source.exists():
                raise LinkConflict(link.dest, f"source is gone: {link.source}")

            link.dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(link.source, link.dest)
            except FileExistsError:
                # Raced with something else between exists() and link().
                if _same_file(link.source, link.dest):
                    continue
                raise LinkConflict(link.dest, "destination appeared during linking") from None
            except OSError as exc:
                raise LinkConflict(link.dest, f"could not hardlink: {exc}") from exc
            created.append(link.dest)
    except LinkConflict:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        raise


def prune_empty_dirs(root: Path) -> list[Path]:
    """Remove empty directories under `root`, bottom-up.

    Files are never touched and `root` itself always survives. Called after a
    human moves a finished folder out of staging, which leaves the parent
    directories behind.
    """
    if not root.is_dir():
        return []

    removed: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        current = Path(dirpath)
        if current == root:
            continue
        if filenames:
            continue
        try:
            next(current.iterdir())
        except StopIteration:
            try:
                current.rmdir()
                removed.append(current)
            except OSError:
                pass
        except OSError:
            pass
    return removed
