"""Response models for the board API.

Deliberately explicit rather than serializing internal dataclasses: the board
should only ever see what it needs, so a secret or an internal path cannot
leak by someone adding a field to `Config` or `Item`.
"""

from __future__ import annotations

from pydantic import BaseModel

# Internal state -> board column. `done` and `ignored` have no column: they
# have left the board.
STATE_TO_COLUMN = {
    "discovered": "downloaded",
    "identifying": "identifying",
    "needs_you": "needs_you",
    "ready": "ready",
}

COLUMNS = ("downloaded", "identifying", "needs_you", "ready")


class CandidateOut(BaseModel):
    tmdb_id: int
    title_zh: str | None
    title_en: str
    year: int | None
    overview: str
    poster_path: str | None
    score: float
    reason: str


class ItemOut(BaseModel):
    hash: str
    name: str
    state: str
    kind: str | None
    reason: str
    chosen_tmdb_id: int | None
    dest_root: str | None
    candidates: list[CandidateOut] = []
    planned_paths: list[str] = []
    video_count: int = 0
    total_bytes: int = 0
    classification_reason: str = ""


class ResolveIn(BaseModel):
    tmdb_id: int


class SearchOut(BaseModel):
    candidates: list[CandidateOut]
