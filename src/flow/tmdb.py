"""TMDB search and candidate scoring.

Two responsibilities, kept apart so the scoring can be tested without HTTP:
`TmdbClient` fetches, `score_candidate` judges.

A transport failure is `TmdbUnavailable` — deliberately distinct from "no
results", which is an empty list. Conflating them would let a network blip be
recorded as "this isn't media".
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import httpx

from .models import Candidate, ParsedName

_BASE = "https://api.themoviedb.org/3"
_TIMEOUT = httpx.Timeout(10.0)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


class TmdbUnavailable(RuntimeError):
    """TMDB could not be reached, or refused the request. Retryable."""


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", folded)).strip()


def _similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _year_of(raw: dict) -> int | None:
    date = raw.get("release_date") or raw.get("first_air_date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def score_candidate(
    raw: dict, parsed: ParsedName, kind: str, english_title: str | None = None
) -> Candidate:
    """Score one TMDB result against what the release name claimed.

    Title similarity is the best of every title TMDB offers (localized,
    original, and English when an English pass supplied it) compared against
    every title the name gave us. Without the English title, a film localized
    as 西线无战事 with a German original title has nothing to match the release's
    English name against, and a making-of documentary wins instead.
    """
    localized = raw.get("title") or raw.get("name") or ""
    original = raw.get("original_title") or raw.get("original_name") or ""
    candidate_titles = [t for t in (localized, original, english_title) if t]
    parsed_titles = [t for t in (parsed.title_en, parsed.title_zh) if t]

    title_score = max(
        (_similarity(p, c) for p in parsed_titles for c in candidate_titles), default=0.0
    )

    candidate_year = _year_of(raw)
    later_season = kind == "tv" and parsed.season is not None and parsed.season > 1
    if parsed.year is None or candidate_year is None:
        year_score, year_reason = 0.5, "year unknown"
    elif later_season:
        # Releases of later seasons carry either the show's premiere year
        # (`Dark.S02.2017`) or the season's own year (`Billions.2019.S04`).
        # Both are consistent with a show that premiered in or before it.
        if candidate_year <= parsed.year:
            year_score, year_reason = 1.0, f"year fits season {parsed.season}"
        else:
            year_score, year_reason = 0.0, "year mismatch"
    elif parsed.year == candidate_year:
        year_score, year_reason = 1.0, "year exact"
    elif abs(parsed.year - candidate_year) == 1:
        year_score, year_reason = 0.7, "year off by one"
    else:
        year_score, year_reason = 0.0, "year mismatch"

    score = round(0.65 * title_score + 0.35 * year_score, 4)

    if title_score >= 0.99:
        title_reason = "title exact"
    elif title_score >= 0.75:
        title_reason = "title close"
    else:
        title_reason = "similar title"

    return Candidate(
        tmdb_id=int(raw["id"]),
        kind=kind,
        title_zh=_pick_chinese(localized, original),
        title_en=english_title or localized or original,
        year=candidate_year,
        overview=raw.get("overview", "") or "",
        poster_path=raw.get("poster_path"),
        score=score,
        reason=f"{title_reason} · {year_reason}",
    )


_HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def _pick_chinese(*titles: str) -> str | None:
    for title in titles:
        if title and _HAN_RE.search(title):
            return title
    return None


class TmdbClient:
    """Thin TMDB search client. Synchronous — the poll loop runs it off-thread."""

    def __init__(
        self,
        api_key: str,
        language: str = "zh-CN",
        client: httpx.Client | None = None,
        english_language: str | None = None,
    ):
        self._api_key = api_key
        self._language = language
        self._english_language = english_language if english_language != language else None
        self._client = client or httpx.Client(timeout=_TIMEOUT)

    def _describe(self, exc: Exception) -> str:
        """An error summary that cannot contain the API key.

        TMDB v3 only accepts the key as a query parameter, and httpx puts the
        full request URL in its exception text. That text would otherwise reach
        an item's reason — stored in SQLite and shown on the board. Callers
        raise `from None` so a logged traceback cannot carry it either.
        """
        if isinstance(exc, httpx.HTTPStatusError):
            summary = f"HTTP {exc.response.status_code}"
        elif isinstance(exc, ValueError):
            summary = "response was not valid JSON"
        else:
            summary = f"{type(exc).__name__}: {exc}"
        return summary.replace(self._api_key, "***") if self._api_key else summary

    def search(self, parsed: ParsedName, kind: str) -> list[Candidate]:
        """Search TMDB for `parsed`, best match first.

        Prefers the English title when the release name carried one; falls back
        to the Chinese title, which is the only handle on releases that have no
        Latin-script title at all.
        """
        query = parsed.title_en or parsed.title_zh
        if not query:
            return []

        endpoint = f"{_BASE}/search/{'movie' if kind == 'movie' else 'tv'}"
        results = self._search_raw(endpoint, query, parsed, kind, self._language)

        # A second pass for English titles, merged by id: localized results hide
        # them whenever TMDB has a Chinese translation.
        english: dict[int, str] = {}
        if self._english_language:
            seen = {raw["id"] for raw in results}
            for raw in self._search_raw(endpoint, query, parsed, kind, self._english_language):
                title = raw.get("title") or raw.get("name")
                if title:
                    english[raw["id"]] = title
                if raw["id"] not in seen:
                    results.append(raw)
                    seen.add(raw["id"])

        scored = [score_candidate(raw, parsed, kind, english.get(raw["id"])) for raw in results]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def _search_raw(
        self, endpoint: str, query: str, parsed: ParsedName, kind: str, language: str
    ) -> list[dict]:
        params: dict[str, object] = {
            "api_key": self._api_key,
            "query": query,
            "language": language,
            "include_adult": "false",
        }
        # Movies only: a TV release's year is as often the season's as the
        # show's premiere, and TMDB's first_air_date_year filter would drop the
        # right show entirely (`Billions.2019.S04` premiered in 2016). Scoring
        # weighs the TV year instead.
        if parsed.year is not None and kind == "movie":
            params["year"] = parsed.year

        try:
            response = self._client.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TmdbUnavailable(f"TMDB search failed: {self._describe(exc)}") from None

        return [raw for raw in (payload.get("results") or []) if "id" in raw]

    def get(self, tmdb_id: int, kind: str) -> Candidate | None:
        """Fetch one entry by id, for when a human has chosen it explicitly."""
        endpoint = f"{_BASE}/{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}"
        params = {"api_key": self._api_key, "language": self._language}
        try:
            response = self._client.get(endpoint, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TmdbUnavailable(f"TMDB lookup failed: {self._describe(exc)}") from None

        chosen = ParsedName(
            title_en=raw.get("title") or raw.get("name"),
            title_zh=None,
            year=_year_of(raw),
            season=None,
            source_name="",
        )
        candidate = score_candidate(raw, chosen, kind)
        return Candidate(**{**candidate.__dict__, "score": 1.0, "reason": "chosen by you"})
