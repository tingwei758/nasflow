import importlib.util
import sys
from pathlib import Path

from flow.config import Config

from .test_pipeline import (
    BM_S06,
    JUNK,
    LONGMEN,
    PEGASUS,
    FakeTmdb,
    cand,
    make_junk,
    make_movie,
    make_season,
)

_SPEC = importlib.util.spec_from_file_location(
    "dryrun", Path(__file__).resolve().parents[1] / "scripts" / "dryrun.py"
)
dryrun = importlib.util.module_from_spec(_SPEC)
sys.modules["dryrun"] = dryrun  # @dataclass resolves its module through sys.modules
_SPEC.loader.exec_module(dryrun)


def _setup(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    cfg = Config(
        downloads_root=downloads, staging_root=tmp_path / "staging", db_path=tmp_path / "x.db",
        qb_url="x", qb_username="u", qb_password="p", tmdb_api_key="k",
    )
    tmdb = FakeTmdb({
        "Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)],
        "Black Mirror": [cand(2, "黑镜", "Black Mirror", 2011, 0.97, "tv")],
        "龙门飞甲": [cand(3, "龙门飞甲", "Dragon Gate", 2011, 0.94)],
    })
    return cfg, tmdb, downloads


def test_confident_movie_row_links_with_its_destination(tmp_path):
    cfg, tmdb, downloads = _setup(tmp_path)
    make_movie(downloads, PEGASUS)
    row = dryrun.evaluate(downloads / PEGASUS, cfg, tmdb)
    assert row.action == "link" and row.kind == "movie"
    assert row.destinations == [str(cfg.staging_root / "Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv")]


def test_season_row_lists_every_episode(tmp_path):
    cfg, tmdb, downloads = _setup(tmp_path)
    make_season(downloads, BM_S06, 3)
    row = dryrun.evaluate(downloads / BM_S06, cfg, tmdb)
    assert row.action == "link" and len(row.destinations) == 3


def test_stops_carry_their_reason(tmp_path):
    cfg, tmdb, downloads = _setup(tmp_path)
    make_movie(downloads, LONGMEN)
    make_junk(downloads, JUNK)
    assert "no English title" in dryrun.evaluate(downloads / LONGMEN, cfg, tmdb).reason
    junk = dryrun.evaluate(downloads / JUNK, cfg, tmdb)
    assert junk.action == "stop" and "no video files" in junk.reason


def test_offline_mode_classifies_without_calling_tmdb(tmp_path):
    cfg, tmdb, downloads = _setup(tmp_path)
    make_movie(downloads, PEGASUS)
    tmdb.fail = True
    row = dryrun.evaluate(downloads / PEGASUS, cfg, None)
    assert row.kind == "movie" and row.action == "offline" and row.parsed_title == "Pegasus"


def test_dry_run_creates_nothing(tmp_path):
    cfg, tmdb, downloads = _setup(tmp_path)
    make_movie(downloads, PEGASUS)
    before = sorted(p for p in tmp_path.rglob("*"))
    dryrun.evaluate(downloads / PEGASUS, cfg, tmdb)
    assert sorted(p for p in tmp_path.rglob("*")) == before
    assert not cfg.staging_root.exists()
