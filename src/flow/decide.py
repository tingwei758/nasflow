"""Stop or proceed — the one rule that keeps flow trustworthy.

A wrong guess that lands in the library is worse than stopping, because the
human may not notice it for months. A stop costs one click. So this errs
toward stopping, and every stop carries a reason the board can show.

Pure by design: no filesystem, no network, no database. The whole safety
property is readable in one screen and testable without any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import Config
from .models import Candidate, Content, ParsedName


# Scores are rounded to 4 places when computed; compare at the same precision,
# or 1.00 - 0.90 (= 0.0999...) fails a 0.10 gap and boundaries turn arbitrary.
_PRECISION = 4


@dataclass(frozen=True)
class Decision:
    action: Literal["link", "stop"]
    candidate: Candidate | None
    reason: str


def _stop(reason: str) -> Decision:
    return Decision(action="stop", candidate=None, reason=reason)


def decide(
    content: Content,
    parsed: ParsedName,
    candidates: list[Candidate],
    cfg: Config,
) -> Decision:
    """Decide whether flow may file this content on its own."""
    if content.kind == "unknown":
        return _stop(content.reason or "could not tell what this is")

    if content.kind == "tv":
        unmarked = [v for v in content.videos if v.episode is None]
        if unmarked:
            many = len(unmarked) > 1
            return _stop(
                f"{len(unmarked)} video{'s' if many else ''} without "
                f"{'' if many else 'an '}episode marker{'s' if many else ''}, which will "
                f"not be filed (e.g. {unmarked[0].path.name})"
            )

    if not candidates:
        return _stop("no TMDB match")

    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    top = ranked[0]

    if round(top.score, _PRECISION) < round(cfg.score_accept, _PRECISION):
        return _stop(f"best match only scored {top.score:.2f}")

    if len(ranked) > 1:
        gap = round(top.score - ranked[1].score, _PRECISION)
        if gap < round(cfg.score_gap, _PRECISION):
            return _stop(
                f"ambiguous: top two matches are {gap:.2f} apart "
                f"({top.title_en} vs {ranked[1].title_en})"
            )

    if top.title_score is not None and round(top.title_score, _PRECISION) < cfg.title_accept:
        return _stop(
            f"title only loosely matches {top.title_en} ({top.title_score:.2f}); "
            "the score rests on the year"
        )

    if top.year is None:
        return _stop(f"TMDB has no release year for {top.title_en}, so the folder can't be named")

    if cfg.require_english_title and parsed.title_en is None:
        # Confident, but there is nothing to cross-check the match against:
        # the release name carried no Latin-script title at all.
        return _stop(
            f"no English title in the release name to confirm {top.title_en} "
            f"({top.year}) — please check"
        )

    return Decision(action="link", candidate=top, reason=top.reason)
