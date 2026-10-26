"""Configuration loading.

This is the only module permitted to contain a path, directory name, filename
template, or media-extension literal. Every value below is a default that a
user's TOML file may override; nothing downstream may hardcode an equivalent.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

_MASK = "***"


@dataclass(frozen=True)
class LayoutConfig:
    """How the library on disk is named. All values are `str.format` templates."""

    movies_dirname: str = "Movies"
    tv_dirname: str = "TV Shows"
    season_dirname: str = "Season {season}"
    movie_dirname: str = "{title} ({year})"
    movie_filename: str = "{title} ({year})"
    show_dirname: str = "{title} ({year})"
    episode_filename: str = "{title} - S{season:02d}E{episode:02d}"
    specials_season: int = 0
    default_season: int = 1


@dataclass(frozen=True)
class MediaConfig:
    """Which files count as media, and which are ignored."""

    video_extensions: tuple[str, ...] = (".mkv", ".mp4", ".avi", ".rmvb", ".ts", ".m2ts")
    subtitle_extensions: tuple[str, ...] = (".srt", ".ass", ".ssa", ".sub", ".idx")
    companion_extensions: tuple[str, ...] = ()
    exclude_name_patterns: tuple[str, ...] = ("*sample*", "*trailer*")
    min_video_bytes: int = 50_000_000


@dataclass(frozen=True)
class Config:
    downloads_root: Path
    staging_root: Path
    db_path: Path
    qb_url: str
    qb_username: str
    qb_password: str
    tmdb_api_key: str
    container_downloads_prefix: str = "/downloads"
    tmdb_language: str = "zh-CN"
    tmdb_image_base: str = "https://image.tmdb.org/t/p/w154"
    poll_interval_s: int = 60
    score_accept: float = 0.80
    score_gap: float = 0.15
    require_english_title: bool = True
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    layout: LayoutConfig = LayoutConfig()
    media: MediaConfig = MediaConfig()

    def __repr__(self) -> str:
        shown = []
        for f in fields(self):
            value = _MASK if f.name in _SECRET_FIELDS else getattr(self, f.name)
            shown.append(f"{f.name}={value!r}")
        return f"Config({', '.join(shown)})"


_SECRET_FIELDS = {"tmdb_api_key", "qb_password"}

# TOML key -> environment variable that overrides it.
_ENV_OVERRIDES = {
    "tmdb_api_key": "FLOW_TMDB_API_KEY",
    "qb_password": "FLOW_QB_PASSWORD",
    "qb_username": "FLOW_QB_USERNAME",
    "qb_url": "FLOW_QB_URL",
}

_PATH_FIELDS = {"downloads_root", "staging_root", "db_path"}
_TUPLE_FIELDS = {
    "video_extensions",
    "subtitle_extensions",
    "companion_extensions",
    "exclude_name_patterns",
}


def _build(cls: type, raw: dict[str, Any], section: str) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown {section} field(s): {', '.join(sorted(unknown))}")
    coerced = {k: (tuple(v) if k in _TUPLE_FIELDS else v) for k, v in raw.items()}
    return cls(**coerced)


def load_config(path: Path) -> Config:
    """Read `path` as TOML and return a validated `Config`.

    `FLOW_*` environment variables take precedence over file values, so secrets
    can be supplied by the container runtime instead of written to disk.
    """
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    layout = _build(LayoutConfig, raw.pop("layout", {}), "layout")
    media = _build(MediaConfig, raw.pop("media", {}), "media")

    for key, env_var in _ENV_OVERRIDES.items():
        env_value = os.environ.get(env_var)
        if env_value is not None:
            raw[key] = env_value

    top_level = {f.name for f in fields(Config)}
    unknown = set(raw) - top_level
    if unknown:
        raise ValueError(f"unknown config field(s): {', '.join(sorted(unknown))}")

    missing = [name for name in _REQUIRED if name not in raw]
    if missing:
        raise ValueError(f"missing required config field(s): {', '.join(sorted(missing))}")

    for key in _PATH_FIELDS:
        raw[key] = Path(raw[key])

    return replace(Config(**raw), layout=layout, media=media)


_REQUIRED = (
    "downloads_root",
    "staging_root",
    "db_path",
    "qb_url",
    "qb_username",
    "qb_password",
    "tmdb_api_key",
)
