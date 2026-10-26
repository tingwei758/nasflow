"""Library layout: where a file should end up, by name.

Renders the templates in `LayoutConfig` into destination paths. Deliberately
knows nothing about the filesystem — it neither reads nor creates anything, so
the whole naming convention is testable as pure string work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import LayoutConfig
from .models import Content, Identification, VideoFile


@dataclass(frozen=True)
class PlannedLink:
    source: Path
    dest: Path


def _sanitize(title: str) -> str:
    """Make a title safe as a single path component.

    Only the path separators are replaced. Full-width characters stay as they
    are, because the existing library already uses them (`007：幽灵党 (2015)`).
    """
    cleaned = title.replace("/", "-")
    if os.sep != "/":
        cleaned = cleaned.replace(os.sep, "-")
    return cleaned.strip().strip(".") or "Unknown"


def _title_of(ident: Identification) -> str:
    candidate = ident.candidate
    return _sanitize(candidate.title_zh or candidate.title_en or "Unknown")


def _subtitles_for(video: VideoFile, content: Content) -> list[tuple[Path, str]]:
    """Subtitles belonging to `video`, with the suffix to carry over.

    A subtitle belongs to the video whose stem is the longest prefix of its
    own name, which keeps `Movie.2020.zh.srt` attached to `Movie.2020.mkv`
    while preserving the `.zh` language tag.
    """
    attached = []
    for subtitle in content.subtitles:
        best = max(
            (v for v in content.videos if subtitle.stem.startswith(v.path.stem)),
            key=lambda v: len(v.path.stem),
            default=None,
        )
        if best is not None and best.path == video.path:
            extra = subtitle.name[len(video.path.stem):]
            attached.append((subtitle, extra))
    return attached


def plan_links(
    content: Content,
    ident: Identification,
    staging_root: Path,
    layout: LayoutConfig,
) -> list[PlannedLink]:
    """Every link that should exist for this content, as source/dest pairs."""
    if content.kind == "unknown":
        return []

    title = _title_of(ident)
    year = ident.candidate.year

    if ident.kind == "movie":
        folder = staging_root / layout.movies_dirname / layout.movie_dirname.format(
            title=title, year=year
        )
        stem = layout.movie_filename.format(title=title, year=year)
    else:
        folder = staging_root / layout.tv_dirname / layout.show_dirname.format(
            title=title, year=year
        )
        stem = None

    links: list[PlannedLink] = []
    for video in content.videos:
        if ident.kind != "movie" and video.episode is None:
            # A film or extra riding in a TV download (the Downton Abbey films
            # in a series pack) is not an episode; decide() stops for it.
            continue
        if ident.kind == "movie":
            target_dir = folder
            file_stem = stem
        else:
            season = video.season if video.season is not None else layout.default_season
            if video.episode == 0:
                season = layout.specials_season
            target_dir = folder / layout.season_dirname.format(season=season)
            file_stem = layout.episode_filename.format(
                title=title, season=season, episode=video.episode or 0
            )

        links.append(
            PlannedLink(source=video.path, dest=target_dir / f"{file_stem}{video.path.suffix}")
        )
        for subtitle, extra in _subtitles_for(video, content):
            links.append(PlannedLink(source=subtitle, dest=target_dir / f"{file_stem}{extra}"))

    return links
