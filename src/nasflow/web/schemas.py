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

# Display text lives with the column keys, so the front end renders whatever
# the server sends and never carries its own copy.
COLUMN_LABELS = {
    "downloaded": "Downloaded",
    "identifying": "Identifying",
    "needs_you": "Needs you",
    "ready": "Ready to move",
}


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
    parsed_title_en: str | None = None
    parsed_title_zh: str | None = None
    parsed_year: int | None = None


class ResolveIn(BaseModel):
    tmdb_id: int


class SearchOut(BaseModel):
    candidates: list[CandidateOut]


class ColumnOut(BaseModel):
    key: str
    label: str


class MetaOut(BaseModel):
    columns: list[ColumnOut]
    poster_base: str


class PreviewOut(BaseModel):
    paths: list[str]


class HealthCheckOut(BaseModel):
    name: str
    label: str
    status: str  # "ok" | "fail" | "unknown"
    detail: str
    checked_at: float | None


class HealthOut(BaseModel):
    # False only on an actual failure: a check nobody has run yet is not red.
    ok: bool
    checks: list[HealthCheckOut]
