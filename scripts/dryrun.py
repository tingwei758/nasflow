"""Show what nasflow would do with every entry in a downloads folder. Writes nothing.

Uses the very functions the pipeline uses (scan, parse, TMDB, decide, plan),
so its verdicts cannot drift from the real thing.

    python scripts/dryrun.py --config config.toml [--downloads DIR] [--limit N]
                             [--offline] [--tsv out.tsv]
"""

from __future__ import annotations

import argparse
import collections
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nasflow.config import Config, load_config  # noqa: E402
from nasflow.decide import decide  # noqa: E402
from nasflow.layout import plan_links  # noqa: E402
from nasflow.models import Identification  # noqa: E402
from nasflow.naming import parse_release_name  # noqa: E402
from nasflow.scan import scan_content  # noqa: E402
from nasflow.tmdb import TmdbClient, TmdbUnavailable  # noqa: E402


@dataclass
class Row:
    name: str
    kind: str
    videos: int
    parsed_title: str | None
    parsed_year: int | None
    action: str  # link | stop | offline
    reason: str
    top: str = ""
    score: float | None = None
    runner_up: float | None = None
    destinations: list[str] = field(default_factory=list)


def evaluate(path: Path, cfg: Config, tmdb) -> Row:
    """One entry's verdict. `tmdb=None` means offline: classify and parse only."""
    content = scan_content(path, cfg.media)
    parsed = parse_release_name(path.name)
    row = Row(
        name=path.name,
        kind=content.kind,
        videos=len(content.videos),
        parsed_title=parsed.title_en or parsed.title_zh,
        parsed_year=parsed.year,
        action="offline",
        reason=content.reason,
    )
    if tmdb is None:
        return row

    kind = content.kind if content.kind != "unknown" else "movie"
    try:
        candidates = tmdb.search(parsed, kind)
    except TmdbUnavailable as exc:
        row.action, row.reason = "stop", f"TMDB unavailable: {exc}"
        return row

    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    if ranked:
        best = ranked[0]
        row.top = f"{best.title_zh or best.title_en} ({best.year}) #{best.tmdb_id}"
        row.score = best.score
        row.runner_up = ranked[1].score if len(ranked) > 1 else None

    decision = decide(content, parsed, candidates, cfg)
    row.action, row.reason = decision.action, decision.reason
    if decision.action == "link":
        ident = Identification(decision.candidate, kind)
        links = plan_links(content, ident, cfg.staging_root, cfg.layout)
        row.destinations = [str(link.dest) for link in links]
    return row


def _fmt(score: float | None) -> str:
    return "" if score is None else f"{score:.2f}"


def _short(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, help="override downloads_root")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offline", action="store_true", help="skip TMDB")
    parser.add_argument("--tsv", type=Path, help="also write every row to this file")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    downloads = args.downloads or cfg.downloads_root
    tmdb = None if args.offline else TmdbClient(
        cfg.tmdb_api_key, cfg.tmdb_language, english_language=cfg.tmdb_english_language or None
    )

    entries = sorted(p for p in downloads.iterdir() if not p.name.startswith("."))
    if args.limit:
        entries = entries[: args.limit]

    rows = []
    for i, entry in enumerate(entries, 1):
        row = evaluate(entry, cfg, tmdb)
        rows.append(row)
        dest = row.destinations[0] if row.destinations else ""
        if dest:
            dest = str(Path(dest).relative_to(cfg.staging_root))
            if len(row.destinations) > 1:
                dest += f"  (+{len(row.destinations) - 1})"
        print(
            f"{i:3} {row.action:7} {row.kind:7} {_fmt(row.score):>4} {_fmt(row.runner_up):>4}  "
            f"{_short(row.name, 60):60}  {_short(row.top, 34):34}  {dest or row.reason}"
        )

    counts = collections.Counter(r.action for r in rows)
    print(f"\n{len(rows)} entries: " + ", ".join(f"{n} {a}" for a, n in counts.most_common()))
    stops = collections.Counter(
        r.reason.split(":")[0].split(" — ")[0][:50] for r in rows if r.action == "stop"
    )
    for reason, n in stops.most_common():
        print(f"  stop  {n:3}  {reason}")

    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8") as fh:
            fh.write("name\tkind\tvideos\tparsed_title\tparsed_year\taction\tscore\trunner_up\ttop\treason\tdestinations\n")
            for r in rows:
                fh.write("\t".join([
                    r.name, r.kind, str(r.videos), r.parsed_title or "", str(r.parsed_year or ""),
                    r.action, _fmt(r.score), _fmt(r.runner_up), r.top, r.reason,
                    " | ".join(r.destinations),
                ]) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
