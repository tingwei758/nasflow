"""Release name -> ParsedName.

Chinese PT releases follow a bilingual convention:

    中文名 . English.Title . [Sxx] . Year . quality . -GROUP

Strip the Chinese prefix and the site-specific noise, and what remains is a
near-standard scene name that `guessit` handles well. This module is that
pre-processor plus a thin call into guessit; the quirks it knows about are all
drawn from real names in the library (see tests/corpus.py).
"""

from __future__ import annotations

import re

from guessit import guessit

from .models import ParsedName

# CJK ideographs plus the full-width punctuation these releases use.
_CJK = r"\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
_CJK_RE = re.compile(f"[{_CJK}]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Everything from the pound/yen group marker onward is uploader branding:
#   ...AC3￡cXcY@FRDS   ...简繁中字￡CMCT星辰
_GROUP_MARKER_RE = re.compile(r"[￡£¥].*$")

_BRACKET_PREFIX_RE = re.compile(r"^\s*[\[【]([^\]】]+)[\]】]\s*[.\-_]?\s*")

# Qualifiers that ride along inside the Chinese title and are not part of it.
_ZH_PARENTHETICAL_RE = re.compile(r"[(（][^)）]*[)）]")
_ZH_SEASON_WORD_RE = re.compile(r"\s*第[0-9一二三四五六七八九十]+[季部]\s*$")
_ZH_SEASON_TAG_RE = re.compile(r"\s*S\d{1,2}(?:\s*-\s*S?\d{1,2})?\s*$", re.IGNORECASE)

# A year range (2011-2019) is a series' span. Keep the first year: that is the
# series year the library folders are named for.
_YEAR_RANGE_RE = re.compile(r"\b(19\d{2}|20\d{2})\s*-\s*(?:19\d{2}|20\d{2})\b")

# Tokens that look like scene tags to guessit but are region or encode noise.
_NOISE_TOKENS = {
    "chn", "hkg", "eur", "usa", "jpn", "kor", "tws", "cc",
    "v2", "v3", "60fps", "10bit", "8bit", "hq", "complete",
}
_NOISE_PATTERNS = (
    re.compile(r"^\d+audios?$", re.IGNORECASE),
    re.compile(r"^\d+audio$", re.IGNORECASE),
)

# "Version" only precedes an edition marker (Version.Director's.Cut); on its own
# it could be part of a real title, so it is dropped only in that context.
_EDITION_WORDS = {
    "director", "directors", "director's", "extended", "unrated",
    "theatrical", "uncut", "remastered", "special", "final",
}

_SPLIT_RE = re.compile(r"[.\s_]+")

# A trailing ".<2-4 alnum>" on a release name is a container extension. Matched
# generically rather than against a list, because the list of media extensions
# belongs to MediaConfig and this parser takes only a string.
_EXTENSION_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,3}$")


def _strip_extension(name: str) -> str:
    return _EXTENSION_RE.sub("", name)


def _clean_chinese_title(raw: str) -> str | None:
    cleaned = _ZH_PARENTHETICAL_RE.sub("", raw)
    cleaned = _ZH_SEASON_WORD_RE.sub("", cleaned)
    cleaned = _ZH_SEASON_TAG_RE.sub("", cleaned)
    cleaned = cleaned.strip(" ._-[]【】")
    return cleaned or None


def _split_chinese_prefix(name: str) -> tuple[str | None, str]:
    """Return (chinese_title, remainder) — remainder is the Latin-script part."""
    bracket = _BRACKET_PREFIX_RE.match(name)
    if bracket and _CJK_RE.search(bracket.group(1)):
        return _clean_chinese_title(bracket.group(1)), name[bracket.end():]

    head, sep, tail = name.partition(".")
    if _CJK_RE.search(head):
        return _clean_chinese_title(head), tail if sep else ""

    return None, name


def _drop_noise(remainder: str) -> str:
    tokens = [t for t in _SPLIT_RE.split(remainder) if t]
    kept = []
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if lowered in _NOISE_TOKENS:
            continue
        if lowered == "version":
            following = tokens[index + 1].lower() if index + 1 < len(tokens) else ""
            if following in _EDITION_WORDS:
                continue
        if any(p.match(token) for p in _NOISE_PATTERNS):
            continue
        if _CJK_RE.search(token):
            continue
        kept.append(token)
    return ".".join(kept)


def parse_release_name(name: str) -> ParsedName:
    """Best-effort parse of a torrent or file name. Never raises."""
    working = _strip_extension(name)
    working = _GROUP_MARKER_RE.sub("", working)

    title_zh, remainder = _split_chinese_prefix(working)
    remainder = _YEAR_RANGE_RE.sub(r"\1", remainder)
    remainder = _drop_noise(remainder)

    title_en: str | None = None
    year: int | None = None
    season: int | None = None

    if remainder:
        try:
            guess = guessit(remainder)
        except Exception:
            guess = {}
        raw_title = guess.get("title")
        if isinstance(raw_title, str) and _LATIN_RE.search(raw_title):
            title_en = raw_title.strip() or None
        year = _as_int(guess.get("year"))
        season = _as_int(guess.get("season"))

    return ParsedName(
        title_en=title_en,
        title_zh=title_zh,
        year=year,
        season=season,
        source_name=name,
    )


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list) and value:
        return _as_int(value[0])
    return None
