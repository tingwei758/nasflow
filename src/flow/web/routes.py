"""HTTP handlers. No business logic — every action delegates to Pipeline."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from ..layout import plan_links
from ..models import Identification
from ..naming import parse_release_name
from ..pathmap import to_host_path
from ..scan import scan_content
from ..tmdb import TmdbUnavailable
from .schemas import COLUMNS, STATE_TO_COLUMN, CandidateOut, ItemOut, ResolveIn, SearchOut


def build_router(pipeline, store, cfg) -> APIRouter:
    router = APIRouter()

    def _require(hash_: str):
        item = store.get(hash_)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown item")
        return item

    def _content_for(item):
        try:
            source = to_host_path(
                item.content_path, cfg.container_downloads_prefix, cfg.downloads_root
            )
        except ValueError:
            return None
        return scan_content(source, cfg.media)

    def _preview_paths(item, content) -> list[str]:
        """What filing this item would create, shown before the human commits."""
        candidates = store.get_candidates(item.hash)
        if not candidates or content is None or content.kind == "unknown":
            return []
        kind = item.kind or content.kind
        links = plan_links(
            content, Identification(candidates[0], kind), cfg.staging_root, cfg.layout
        )
        return [str(link.dest) for link in links]

    def _to_out(item, *, detailed: bool) -> ItemOut:
        candidates = store.get_candidates(item.hash) if detailed else []
        content = _content_for(item) if detailed else None
        return ItemOut(
            hash=item.hash,
            name=item.name,
            state=item.state,
            kind=item.kind,
            reason=item.reason,
            chosen_tmdb_id=item.chosen_tmdb_id,
            dest_root=item.dest_root,
            candidates=[CandidateOut(**_candidate_fields(c)) for c in candidates],
            planned_paths=_preview_paths(item, content) if detailed else [],
            video_count=len(content.videos) if content else 0,
            total_bytes=sum(v.size for v in content.videos) if content else 0,
            classification_reason=content.reason if content else "",
        )

    @router.get("/api/board")
    def board() -> dict[str, list[ItemOut]]:
        grouped: dict[str, list[ItemOut]] = {column: [] for column in COLUMNS}
        for item in store.all_items():
            column = STATE_TO_COLUMN.get(item.state)
            if column is None:
                continue
            grouped[column].append(_to_out(item, detailed=column == "needs_you"))
        return grouped

    @router.post("/api/items/{hash_}/resolve")
    def resolve(hash_: str, body: ResolveIn) -> dict[str, str]:
        _require(hash_)
        pipeline.resolve(hash_, body.tmdb_id)
        return {"state": store.get(hash_).state}

    @router.post("/api/items/{hash_}/ignore")
    def ignore(hash_: str) -> dict[str, str]:
        _require(hash_)
        pipeline.ignore(hash_)
        return {"state": store.get(hash_).state}

    @router.get("/api/items/{hash_}/search")
    def search(hash_: str, q: str) -> SearchOut:
        item = _require(hash_)
        parsed = parse_release_name(item.name)
        manual = type(parsed)(
            title_en=q, title_zh=None, year=parsed.year,
            season=parsed.season, source_name=parsed.source_name,
        )
        try:
            found = pipeline.tmdb.search(manual, item.kind or "movie")
        except TmdbUnavailable as exc:
            raise HTTPException(status_code=503, detail=f"TMDB unavailable: {exc}") from exc
        store.set_candidates(hash_, found)
        return SearchOut(candidates=[CandidateOut(**_candidate_fields(c)) for c in found])

    return router


def _candidate_fields(candidate) -> dict:
    fields = asdict(candidate)
    fields.pop("kind", None)
    return fields
