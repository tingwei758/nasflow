"""Frozen value objects passed between pipeline stages.

Each stage is a pure function from one of these to the next, which is what
lets the whole identification path be tested without a filesystem or network.
"""

from __future__ import annotations

from dataclasses import dataclass


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
