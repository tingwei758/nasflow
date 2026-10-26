"""Content path -> Content.

The central design decision lives here: **what a download is gets decided by
the files inside it, not by the torrent name.**

Every TV episode in this library carries its own `SxxExx`, however messy the
enclosing folder name is, and movie folders hold a single large video with no
episode markers. Classifying on that dissolves the cases that would otherwise
be hardest — multi-season packs, year-after-season names, and releases with no
English title at all.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatch
from pathlib import Path

from .config import MediaConfig
from .models import Content, VideoFile

# Preferred form: season and episode together.
_SXXEXX_RE = re.compile(r"(?:^|[^A-Za-z0-9])S(\d{1,2})[._\- ]?E(\d{1,3})(?![0-9])", re.IGNORECASE)
# Fallback for releases that number episodes on their own: E01-E05, EP15.
_EXX_RE = re.compile(r"(?:^|[^A-Za-z0-9])EP?(\d{1,3})(?![0-9])", re.IGNORECASE)
# A season tag standing apart from the episode: `...S02.2024.Chinese.EP15...`.
_SEASON_ONLY_RE = re.compile(r"(?:^|[^A-Za-z0-9])S(\d{1,2})(?![0-9A-Za-z])", re.IGNORECASE)
# A folder spanning seasons (Show.S01-S03) names no single season.
_SEASON_RANGE_RE = re.compile(r"(?:^|[^A-Za-z0-9])S\d{1,2}\s*-\s*S?\d{1,2}(?![0-9])", re.IGNORECASE)


def _is_excluded(name: str, media: MediaConfig) -> bool:
    lowered = name.lower()
    return any(fnmatch(lowered, pattern.lower()) for pattern in media.exclude_name_patterns)


def _walk(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != "@eaDir"]
        found.extend(Path(dirpath) / name for name in filenames)
    return found


def scan_content(path: Path, media: MediaConfig) -> Content:
    """Inspect `path` and decide what it holds. Never raises."""
    try:
        exists = path.exists()
    except OSError:
        exists = False
    if not exists:
        return Content(root=path, kind="unknown", videos=[], subtitles=[], reason="path not found")

    videos: list[VideoFile] = []
    subtitles: list[Path] = []

    for item in _walk(path):
        if _is_excluded(item.name, media):
            continue
        suffix = item.suffix.lower()
        if suffix in media.subtitle_extensions:
            subtitles.append(item)
            continue
        if suffix not in media.video_extensions:
            continue
        try:
            size = item.stat().st_size
        except OSError:
            continue
        if size < media.min_video_bytes:
            continue
        videos.append(VideoFile(path=item, size=size, season=None, episode=None))

    if not videos:
        return Content(
            root=path, kind="unknown", videos=[], subtitles=subtitles,
            reason="no video files found",
        )

    videos = _mark_episodes(videos, path)

    if any(v.episode is not None for v in videos):
        return Content(root=path, kind="tv", videos=videos, subtitles=subtitles,
                       reason="episode markers in filenames")
    if len(videos) == 1:
        return Content(root=path, kind="movie", videos=videos, subtitles=subtitles,
                       reason="single video, no episode markers")
    return Content(
        root=path, kind="unknown", videos=videos, subtitles=subtitles,
        reason=f"ambiguous: {len(videos)} videos with no episode markers",
    )


def _season_from_folders(video: Path, root: Path) -> int | None:
    """The single season named by the nearest enclosing folder, up to `root`.

    Only folders inside the content count: the folder qBittorrent saved it in
    is not part of the release. A folder naming several seasons (S01-S03,
    S01.S02) answers "unknown" and stops the search: going further up could
    only find a broader, less trustworthy name.
    """
    for folder in video.parents:
        if folder != root and root not in folder.parents:
            return None
        if _SEASON_RANGE_RE.search(folder.name):
            return None
        seasons = {int(n) for n in _SEASON_ONLY_RE.findall(folder.name)}
        if len(seasons) > 1:
            return None
        if seasons:
            return seasons.pop()
        if folder == root:
            return None
    return None


def _mark_episodes(videos: list[VideoFile], root: Path) -> list[VideoFile]:
    """Attach season/episode from each file's own name.

    Two passes: `SxxExx` first, and only if no file in the whole content matches
    it, fall back to a bare `Exx`/`EPxx`. The fallback is content-wide so that a
    stray `E` token in one filename cannot turn a movie into a show. In the
    fallback the season comes from a separate `Sxx` in the same filename, else
    from the nearest enclosing folder (`Show.S02.../Show.EP15.mkv`), and is
    otherwise unknown — never assumed to be 1 here, since a Season 2 episode
    filed as Season 1 would be silently wrong.
    """
    marked = []
    for video in videos:
        match = _SXXEXX_RE.search(video.path.name)
        if match:
            marked.append(
                VideoFile(video.path, video.size, int(match.group(1)), int(match.group(2)))
            )
        else:
            marked.append(video)

    if any(v.episode is not None for v in marked):
        return marked

    fallback = []
    for video in videos:
        match = _EXX_RE.search(video.path.name)
        if match:
            tag = _SEASON_ONLY_RE.search(video.path.name)
            season = int(tag.group(1)) if tag else _season_from_folders(video.path, root)
            fallback.append(VideoFile(video.path, video.size, season, int(match.group(1))))
        else:
            fallback.append(video)
    return fallback
