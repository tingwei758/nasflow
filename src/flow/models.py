"""Frozen value objects passed between pipeline stages.

Each stage is a pure function from one of these to the next, which is what
lets the whole identification path be tested without a filesystem or network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ParsedName:
    """What a release name alone can tell us, before TMDB is consulted.

    `title_en is None` means the name carried no Latin-script title — common on
    Chinese PT sites, and the signal that there is nothing to cross-check a
    TMDB match against.
    """

    title_en: str | None
    title_zh: str | None
    year: int | None
    season: int | None
    source_name: str


@dataclass(frozen=True)
class VideoFile:
    """One video inside a download, with whatever episode markers its own name carries."""

    path: Path
    size: int
    season: int | None
    episode: int | None


@dataclass(frozen=True)
class Content:
    """What the files inside a download say about it.

    `kind` is decided by the files, never by the torrent name: episode markers
    in the filenames mean a show, a lone video means a movie. This is what makes
    multi-season packs and Chinese-only names tractable.
    """

    root: Path
    kind: Literal["movie", "tv", "unknown"]
    videos: list[VideoFile]
    subtitles: list[Path]
    reason: str = ""


@dataclass(frozen=True)
class Candidate:
    """One TMDB result, scored against what the release name claimed."""

    tmdb_id: int
    kind: str
    title_zh: str | None
    title_en: str
    year: int | None
    overview: str
    poster_path: str | None
    score: float
    reason: str
    # Title similarity on its own (0-1), so decide() can refuse a match whose
    # score rests mostly on the year. None when not measured.
    title_score: float | None = None


@dataclass(frozen=True)
class Identification:
    """A settled answer: this content is this TMDB entry."""

    candidate: Candidate
    kind: str
