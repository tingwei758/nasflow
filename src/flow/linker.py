"""The only module in flow that creates anything on disk.

Three rules it must never break:

1. **Hardlinks only.** `os.link`, never a copy. A copy would silently consume
   another full-size allocation and take minutes instead of milliseconds.
2. **The source is read-only.** The files qBittorrent is seeding are never
   renamed, moved, modified, or deleted — only given a second name.
3. **Nothing is created outside the staging root.** Hardlinks cannot cross
   bind mounts, so downloads and staging share one read-write mount and the
   kernel no longer protects downloads. This module is that protection.

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


class OutsideStaging(LinkConflict):
    """A planned destination resolves outside the staging root. Never linked."""


def _check_confined(dest: Path, staging_root: Path) -> None:
    # realpath, not a lexical check: `..` and symlinks inside staging both resolve.
    root = os.path.realpath(staging_root)
    target = os.path.realpath(dest)
    if target == root or os.path.commonpath([root, target]) != root:
        raise OutsideStaging(dest, f"resolves outside the staging root {staging_root}")


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_ino == b.stat().st_ino and a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def execute_links(links: list[PlannedLink], staging_root: Path) -> None:
    """Create every link, or none of them.

    Every destination is confined to `staging_root` before anything is
    created. An existing destination that is already a hardlink to the same
    source is a no-op success, which makes re-processing a torrent safe. An
    existing destination that is a different file raises `LinkConflict` — flow
    never overwrites.
    """
    for link in links:
        _check_confined(link.dest, staging_root)

    created: list[Path] = []
    current: PlannedLink | None = None
    try:
        for link in links:
            current = link
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
    except (LinkConflict, OSError) as exc:
        # Any failure — including mkdir or stat being refused — undoes this
        # call's links, so a half-filed season never looks ready.
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        if isinstance(exc, LinkConflict):
            raise
        raise LinkConflict(current.dest, f"filesystem error: {exc}") from exc


# Created and removed at startup; never left behind.
_PROBE_NAME = ".flow-link-probe"


def _first_file(root: Path) -> Path | None:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "@eaDir"]
        for name in filenames:
            candidate = Path(dirpath) / name
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
    return None


def probe_hardlink(downloads_root: Path, staging_root: Path) -> str | None:
    """Try one real hardlink from downloads into staging, then remove it.

    Returns a description of the problem, or None. This is the only check
    that catches downloads and staging on separate bind mounts: both report
    the same device, yet link() between them fails with EXDEV. Nothing is
    written outside staging, and the source only gains and loses a name.
    """
    sample = _first_file(downloads_root)
    if sample is None:
        return None  # nothing downloaded yet; the first real link will tell
    probe = staging_root / _PROBE_NAME
    _check_confined(probe, staging_root)
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
        if probe.is_symlink() or probe.exists():
            probe.unlink()
        os.link(sample, probe)
    except OSError as exc:
        return f"cannot hardlink from {downloads_root} into {staging_root}: {exc}"
    finally:
        try:
            if probe.is_symlink() or probe.exists():
                probe.unlink()
        except OSError:
            pass
    return None


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
