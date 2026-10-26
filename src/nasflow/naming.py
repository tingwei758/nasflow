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

# A single season riding on the Chinese title: 冰血暴S04, 一拳超人第二季. A range
# (黑镜S01-S05) is a pack and names no one season.
_ZH_ONE_SEASON_TAG_RE = re.compile(r"S(\d{1,2})\s*$", re.IGNORECASE)
_ZH_ONE_SEASON_WORD_RE = re.compile(r"第([0-9]+|[一二三四五六七八九十]+)季\s*$")
_ZH_NUMERALS = {c: i for i, c in enumerate("一二三四五六七八九十", start=1)}

# Pinyin or English glued straight onto the Chinese title (长安三万里Chang.an...):
# the Chinese title is the CJK run; the Latin rest belongs to the remainder.
# Latin inside the Chinese title (脱口秀和Ta的朋友们) stays: the rest must contain
# no CJK. A season tag (S04) is not split off this way; it is read above.
_ZH_GLUED_LATIN_RE = re.compile(f"^([{_CJK}]+)((?!S\\d)[A-Za-z]{{2}}[^{_CJK}]*)$")

# guessit reads "Part II" as a part number and drops it from the title.
_PART_RE = re.compile(r"(?:^|[.\s_])Part[.\s_]+([IVX]+|\d+)(?=[.\s_]|$)", re.IGNORECASE)

# guessit occasionally reads a trailing year as a season number.
_MAX_SEASON = 99

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


def _zh_season(raw: str) -> int | None:
    """The one season a Chinese title names, if it names exactly one."""
    cleaned = _ZH_PARENTHETICAL_RE.sub("", raw).strip(" ._-[]【】")
    if tag := _ZH_ONE_SEASON_TAG_RE.search(cleaned):
        if not re.search(r"S\d{1,2}\s*-\s*S?\d{1,2}\s*$", cleaned, re.IGNORECASE):
            return int(tag.group(1))
    if word := _ZH_ONE_SEASON_WORD_RE.search(cleaned):
        digits = word.group(1)
        if digits.isdigit():
            return int(digits)
        if len(digits) == 1:
            return _ZH_NUMERALS.get(digits)
    return None


def _split_chinese_prefix(name: str) -> tuple[str | None, str, int | None]:
    """Return (chinese_title, remainder, season named by the Chinese title).

    The remainder is the Latin-script part.
    """
    bracket = _BRACKET_PREFIX_RE.match(name)
    if bracket and _CJK_RE.search(bracket.group(1)):
        raw = bracket.group(1)
        return _clean_chinese_title(raw), name[bracket.end():], _zh_season(raw)

    head, sep, tail = name.partition(".")
    if _CJK_RE.search(head):
        rest = tail if sep else ""
        glued = _ZH_GLUED_LATIN_RE.match(head)
        if glued:
            head, rest = glued.group(1), ".".join(p for p in (glued.group(2), rest) if p)
        return _clean_chinese_title(head), rest, _zh_season(head)

    return None, name, None


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

    title_zh, remainder, zh_season = _split_chinese_prefix(working)
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
        year = _as_int(guess.get("year"))
        season = _as_int(guess.get("season"))
        if season is not None and season > _MAX_SEASON:
            season = None

        raw_title = guess.get("title")
        if not isinstance(raw_title, str) or not raw_title.strip():
            # guessit sometimes consumes a short title as metadata (a lone "X2"
            # becomes a bonus-feature marker), leaving no title at all. Recover
            # the tokens that precede the first thing it did recognise.
            raw_title = _leading_tokens(remainder, year, season)
        if isinstance(raw_title, str) and _LATIN_RE.search(raw_title):
            title_en = raw_title.strip() or None
        if title_en and guess.get("part") is not None:
            part = _PART_RE.search(remainder)
            if part:
                title_en = f"{title_en} Part {part.group(1).upper()}"

    if season is None:
        season = zh_season

    return ParsedName(
        title_en=title_en,
        title_zh=title_zh,
        year=year,
        season=season,
        source_name=name,
    )


_METADATA_TOKEN_RE = re.compile(
    r"^(?:\d{3,4}[pi]|S\d{1,2}(?:-S?\d{1,2})?|E\d{1,3}|19\d{2}|20\d{2})$", re.IGNORECASE
)


def _leading_tokens(remainder: str, year: int | None, season: int | None) -> str:
    """Tokens before the first recognisable metadata token, as a title."""
    words = []
    for token in _SPLIT_RE.split(remainder):
        if not token:
            continue
        if _METADATA_TOKEN_RE.match(token):
            break
        if year is not None and token == str(year):
            break
        if season is not None and token.lower() == f"s{season:02d}":
            break
        words.append(token)
    return " ".join(words)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list) and value:
        return _as_int(value[0])
    return None
