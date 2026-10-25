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

    if not candidates:
        return _stop("no TMDB match")

    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    top = ranked[0]

    if top.score < cfg.score_accept:
        return _stop(f"best match only scored {top.score:.2f}")

    if len(ranked) > 1:
        gap = top.score - ranked[1].score
        if gap < cfg.score_gap:
            return _stop(
                f"ambiguous: top two matches are {gap:.2f} apart "
                f"({top.title_en} vs {ranked[1].title_en})"
            )

    if cfg.require_english_title and parsed.title_en is None:
        # Confident, but there is nothing to cross-check the match against:
        # the release name carried no Latin-script title at all.
        return _stop(
            f"no English title in the release name to confirm {top.title_en} "
            f"({top.year}) — please check"
        )

    return Decision(action="link", candidate=top, reason=top.reason)
