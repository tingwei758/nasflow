"""Container <-> host path translation.

qBittorrent reports paths as its own container sees them (`/downloads/...`);
nasflow sees the same files at a host path. Nothing else lives in this module.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def _normalized_prefix(prefix: str) -> PurePosixPath:
    return PurePosixPath(prefix.rstrip("/") or "/")


def to_host_path(container_path: str, prefix: str, host_root: Path) -> Path:
    """Map a container-side path onto the host.

    Raises `ValueError` when the path does not live under `prefix`, rather than
    silently producing a path that points nowhere.
    """
    candidate = PurePosixPath(container_path)
    base = _normalized_prefix(prefix)
    if candidate == base:
        return host_root
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"{container_path!r} is not under container prefix {prefix!r}"
        ) from exc
    return host_root.joinpath(*relative.parts)


def to_container_path(host_path: Path, prefix: str, host_root: Path) -> str:
    """Inverse of `to_host_path`."""
    try:
        relative = host_path.relative_to(host_root)
    except ValueError as exc:
        raise ValueError(f"{host_path!r} is not under host root {host_root!r}") from exc
    base = _normalized_prefix(prefix)
    return str(base.joinpath(*relative.parts))
