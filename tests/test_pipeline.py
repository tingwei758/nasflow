import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from nasflow.config import Config
from nasflow.db import Store
from nasflow.models import Candidate
from nasflow.pipeline import NotWaiting, Pipeline
from nasflow.qbit import QbitUnavailable, Torrent
from nasflow.tmdb import TmdbUnavailable

PEGASUS = "飞驰人生.Pegasus.2019.BluRay.1080p.x265.10bit.MNHD-FRDS"
LONGMEN = "龙门飞甲.2011.HKG.1080p.国粤双语.简繁中字￡CMCT星辰"
BM_S06 = "黑镜.Black.Mirror.S06.2023.1080p.NF.WEB-DL.H264.DDP5.1.Atmos-OurTV"
JUNK = "CE教学实例应用-由浅入深学习游戏修改 [全十课]"


class FakeQbit:
    def __init__(self, torrents=()):
        self.torrents = list(torrents)
        self.fail = False

    def completed_torrents(self):
        if self.fail:
            raise QbitUnavailable("down")
        return list(self.torrents)


class FakeTmdb:
    def __init__(self, results=None):
        self.results = results or {}
        self.fail = False

    def search(self, parsed, kind):
        if self.fail:
            raise TmdbUnavailable("down")
        query = parsed.title_en or parsed.title_zh
        return list(self.results.get(query, []))

    def get(self, tmdb_id, kind):
        for cands in self.results.values():
            for c in cands:
                if c.tmdb_id == tmdb_id:
                    return replace(c, score=1.0, reason="chosen by you")
        return None

    def restore(self):
        self.fail = False


def cand(tmdb_id, title_zh, title_en, year, score, kind="movie"):
    return Candidate(
        tmdb_id=tmdb_id, kind=kind, title_zh=title_zh, title_en=title_en, year=year,
        overview="", poster_path=None, score=score, reason="title exact · year exact",
    )


def make_movie(downloads: Path, name: str) -> Torrent:
    folder = downloads / name
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{name}.mkv", "wb") as fh:
        fh.truncate(8_000_000_000)
    return Torrent(name, name, f"/downloads/{name}", "/downloads/", "seeding", 1.0, 0)


def make_season(downloads: Path, name: str, episodes: int = 3) -> Torrent:
    folder = downloads / name
    folder.mkdir(parents=True, exist_ok=True)
    for ep in range(1, episodes + 1):
        with open(folder / f"黑镜.Black.Mirror.S06E0{ep}.2023.mkv", "wb") as fh:
            fh.truncate(2_000_000_000)
    return Torrent(name, name, f"/downloads/{name}", "/downloads/", "seeding", 1.0, 0)


def make_junk(downloads: Path, name: str) -> Torrent:
    folder = downloads / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "lesson01.pdf").write_bytes(b"x")
    return Torrent(name, name, f"/downloads/{name}", "/downloads/", "seeding", 1.0, 0)


@pytest.fixture
def env(tmp_path):
    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    downloads.mkdir()
    staging.mkdir()
    cfg = Config(
        downloads_root=downloads, staging_root=staging, db_path=tmp_path / "nasflow.db",
        qb_url="http://qb", qb_username="u", qb_password="p", tmdb_api_key="k",
    )
    store = Store(cfg.db_path)
    qbit = FakeQbit()
    tmdb = FakeTmdb()
    return cfg, store, qbit, tmdb, downloads, staging


def build(cfg, store, qbit, tmdb):
    return Pipeline(cfg, store, qbit, tmdb)


def test_confident_movie_links_without_asking(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).state == "ready"
    assert (staging / "Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv").exists()


def test_tv_season_links_every_episode(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_season(downloads, BM_S06)
    qbit.torrents = [t]
    tmdb.results = {"Black Mirror": [cand(2, "黑镜", "Black Mirror", 2011, 0.96, "tv")]}
    build(cfg, store, qbit, tmdb).poll_once()
    season = staging / "TV Shows/黑镜 (2011)/Season 6"
    assert {p.name for p in season.iterdir()} == {
        "黑镜 - S06E01.mkv", "黑镜 - S06E02.mkv", "黑镜 - S06E03.mkv"
    }


def test_links_are_hardlinks_to_the_original(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    build(cfg, store, qbit, tmdb).poll_once()
    src = downloads / PEGASUS / f"{PEGASUS}.mkv"
    dest = staging / "Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv"
    assert src.stat().st_ino == dest.stat().st_ino


def test_source_files_never_modified_end_to_end(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    src = downloads / PEGASUS / f"{PEGASUS}.mkv"
    before = src.stat().st_mtime
    build(cfg, store, qbit, tmdb).poll_once()
    assert src.exists() and src.stat().st_mtime == before


def test_low_score_stops_in_needs_you(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.40)]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).state == "needs_you"
    assert not any(staging.rglob("*.mkv"))


def test_close_top_two_stops(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [
        cand(1, "飞驰人生", "Pegasus", 2019, 0.91),
        cand(2, "飞驰人生2", "Pegasus 2", 2024, 0.88),
    ]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).state == "needs_you"


def test_chinese_only_name_stops_despite_high_score(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(3, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).state == "needs_you"


def test_no_video_files_stops_with_reason(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_junk(downloads, JUNK)
    qbit.torrents = [t]
    build(cfg, store, qbit, tmdb).poll_once()
    item = store.get(t.hash)
    assert item.state == "needs_you" and "no video files" in item.reason


def test_tmdb_unavailable_stops_and_is_retried(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    tmdb.fail = True
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    assert store.get(t.hash).state == "needs_you"
    assert "unavailable" in store.get(t.hash).reason.lower()
    tmdb.restore()
    p.process(t.hash)
    assert store.get(t.hash).state == "ready"


def test_qbit_unavailable_does_not_raise_out_of_poll(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    qbit.fail = True
    build(cfg, store, qbit, tmdb).poll_once()  # must not raise
    assert store.all_items() == []


def test_resolve_links_using_human_choice(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(72113, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    assert store.get(t.hash).state == "needs_you"
    p.resolve(t.hash, 72113)
    assert store.get(t.hash).state == "ready"
    assert (staging / "Movies/龙门飞甲 (2011)/龙门飞甲 (2011).mkv").exists()


def test_already_resolved_item_does_not_ask_again(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(72113, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    p.resolve(t.hash, 72113)
    p.process(t.hash)
    assert store.get(t.hash).state == "ready"


def test_ignore_marks_item_and_links_nothing(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_junk(downloads, JUNK)
    qbit.torrents = [t]
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    p.ignore(t.hash)
    assert store.get(t.hash).state == "ignored"
    assert not any(staging.rglob("*"))


def test_ignored_item_is_not_reprocessed(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    store.set_state(t.hash, "ignored")
    p.poll_once()
    assert store.get(t.hash).state == "ignored"


def test_ready_item_is_not_relinked_on_next_poll(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    p.poll_once()
    assert store.get(t.hash).state == "ready"


def test_reconcile_marks_done_when_folder_disappears(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    shutil.rmtree(staging / "Movies/飞驰人生 (2019)")
    p.reconcile_staging()
    assert store.get(t.hash).state == "done"


def test_reconcile_leaves_untouched_items_ready(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    p.reconcile_staging()
    assert store.get(t.hash).state == "ready"


def test_reconcile_prunes_empty_dirs(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    (staging / "Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv").unlink()
    p.reconcile_staging()
    assert not (staging / "Movies/飞驰人生 (2019)").exists()


def test_link_conflict_stops_rather_than_overwriting(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    blocker = staging / "Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv"
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"someone elses file")
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).state == "needs_you"
    assert blocker.read_bytes() == b"someone elses file"


def test_candidates_are_stored_for_the_resolve_panel(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(3, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert [c.tmdb_id for c in store.get_candidates(t.hash)] == [3]


def test_tv_dest_root_is_the_show_folder_not_the_season(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_season(downloads, BM_S06)
    qbit.torrents = [t]
    tmdb.results = {"Black Mirror": [cand(2, "黑镜", "Black Mirror", 2011, 0.96, "tv")]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).dest_root == str(staging / "TV Shows/黑镜 (2011)")


def test_movie_dest_root_is_the_movie_folder(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(t.hash).dest_root == str(staging / "Movies/飞驰人生 (2019)")


def test_moving_only_the_season_folder_still_reaches_done(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_season(downloads, BM_S06)
    qbit.torrents = [t]
    tmdb.results = {"Black Mirror": [cand(2, "黑镜", "Black Mirror", 2011, 0.96, "tv")]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    shutil.rmtree(staging / "TV Shows/黑镜 (2011)/Season 6")
    p.reconcile_staging()
    assert store.get(t.hash).state == "done"


def test_human_resolve_is_not_overwritten_by_a_concurrent_poll(env):
    """A poll mid-way through an item must not clobber a choice made meanwhile."""
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(72113, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()  # -> needs_you, candidates stored
    store.set_state(t.hash, "discovered")  # make the next poll pick it up again

    entered, release = threading.Event(), threading.Event()
    real_search = tmdb.search

    def slow_search(parsed, kind):
        entered.set()
        release.wait(5)
        return real_search(parsed, kind)

    tmdb.search = slow_search
    poller = threading.Thread(target=p.poll_once)
    poller.start()
    assert entered.wait(5)

    resolver = threading.Thread(target=p.resolve, args=(t.hash, 72113))
    resolver.start()
    resolver.join(0.3)
    release.set()
    poller.join(5)
    resolver.join(5)

    assert store.get(t.hash).state == "ready"
    assert store.get(t.hash).chosen_tmdb_id == 72113


def test_one_item_crashing_does_not_block_the_rest_of_the_poll(env, monkeypatch):
    cfg, store, qbit, tmdb, downloads, staging = env
    bad = make_movie(downloads, LONGMEN)
    good = make_movie(downloads, PEGASUS)
    qbit.torrents = [bad, good]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    real_search = tmdb.search

    def explode_on_longmen(parsed, kind):
        if parsed.title_zh == "龙门飞甲":
            raise KeyError("boom")
        return real_search(parsed, kind)

    tmdb.search = explode_on_longmen
    build(cfg, store, qbit, tmdb).poll_once()
    assert store.get(bad.hash).state == "needs_you"
    assert "unexpected error" in store.get(bad.hash).reason
    assert store.get(good.hash).state == "ready"


# ---- review: only stops that can clear by themselves are retried -------------


def test_judgement_stop_is_not_reprocessed_every_poll(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(3, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    calls = []
    real = tmdb.search
    tmdb.search = lambda parsed, kind: calls.append(1) or real(parsed, kind)
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    p.poll_once()
    p.poll_once()
    assert len(calls) == 1 and store.get(t.hash).state == "needs_you"


def test_tmdb_outage_is_retried_by_the_next_poll(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    tmdb.fail = True
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    assert store.get(t.hash).state == "needs_you"
    tmdb.restore()
    p.poll_once()
    assert store.get(t.hash).state == "ready"


def test_destination_conflict_is_retried_once_the_old_folder_is_gone(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    blocker = staging / "Movies/飞驰人生 (2019)/飞驰人生 (2019).mkv"
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"older release, not yet moved")
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    assert store.get(t.hash).state == "needs_you"
    blocker.unlink()  # the human moved the older version to the library
    p.poll_once()
    assert store.get(t.hash).state == "ready"


def test_manual_search_replaces_candidates_under_the_pipeline(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {
        "龙门飞甲": [cand(3, "龙门飞甲", "Dragon Gate", 2011, 0.94)],
        "Dragon Inn": [cand(5, "龙门客栈", "Dragon Inn", 1967, 0.5)],
    }
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    found = p.search(t.hash, "Dragon Inn")
    assert [c.tmdb_id for c in found] == [5]
    p.poll_once()
    assert [c.tmdb_id for c in store.get_candidates(t.hash)] == [5]


# ---- review 2: human actions re-check the state under the pipeline lock -----

def _retryable_stop_then_slow_poll(env):
    """An item stopped for a TMDB outage, and a poll now re-processing it slowly."""
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    tmdb.fail = True
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    assert store.get(t.hash).state == "needs_you"
    tmdb.restore()

    entered, release = threading.Event(), threading.Event()
    real_search = tmdb.search

    def slow_search(parsed, kind):
        entered.set()
        release.wait(5)
        return real_search(parsed, kind)

    tmdb.search = slow_search
    poller = threading.Thread(target=p.poll_once)
    poller.start()
    assert entered.wait(5)
    return p, t, poller, release


def _run_blocked(action, release, poller):
    errors = []

    def call():
        try:
            action()
        except Exception as exc:  # collected for the assertion
            errors.append(exc)

    worker = threading.Thread(target=call)
    worker.start()
    worker.join(0.3)
    blocked = worker.is_alive()
    release.set()
    poller.join(5)
    worker.join(5)
    return blocked, errors


def test_ignore_waits_for_an_in_flight_poll(env):
    cfg, store, *_ , staging = env
    p, t, poller, release = _retryable_stop_then_slow_poll(env)
    blocked, errors = _run_blocked(lambda: p.ignore(t.hash), release, poller)
    assert blocked, "ignore must not change the item while a poll is filing it"
    # The poll filed it first; ignoring a filed item is refused, not half-applied.
    assert store.get(t.hash).state == "ready"
    assert [type(e) for e in errors] == [NotWaiting]


def test_resolve_after_the_poll_filed_it_is_refused_not_filed_twice(env):
    cfg, store, *_ , staging = env
    p, t, poller, release = _retryable_stop_then_slow_poll(env)
    _, errors = _run_blocked(lambda: p.resolve(t.hash, 1), release, poller)
    assert [type(e) for e in errors] == [NotWaiting]
    assert store.get(t.hash).chosen_tmdb_id is None
    assert len(list((staging / "Movies").iterdir())) == 1


def test_ignore_of_a_waiting_item_still_works(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    p.ignore(t.hash)
    assert store.get(t.hash).state == "ignored"


# ---- review 2: stops that can never clear, yearless picks, secrets -----------

def test_a_link_escaping_staging_stops_without_retrying(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    elsewhere = downloads.parent / "elsewhere"
    elsewhere.mkdir()
    (staging / "Movies").symlink_to(elsewhere)
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    item = store.get(t.hash)
    assert item.state == "needs_you" and "outside" in item.reason and item.retry is False
    assert not any(elsewhere.iterdir())


def test_resolving_to_a_yearless_entry_stops_instead_of_naming_a_none_folder(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", None, 0.95)]}
    p = build(cfg, store, qbit, tmdb)
    p.poll_once()
    assert store.get(t.hash).state == "needs_you"
    p.resolve(t.hash, 1)
    item = store.get(t.hash)
    assert item.state == "needs_you" and "year" in item.reason
    assert not any(staging.rglob("*.mkv"))


def test_unexpected_error_text_never_carries_a_secret(env):
    cfg, store, qbit, tmdb, downloads, staging = env
    cfg = replace(cfg, tmdb_api_key="tmdb-secret-123", qb_password="qb-secret-456")
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]

    def leaky(parsed, kind):
        raise RuntimeError("GET https://api/search?api_key=tmdb-secret-123 pw=qb-secret-456")

    tmdb.search = leaky
    build(cfg, store, qbit, tmdb).poll_once()
    reason = store.get(t.hash).reason
    assert "tmdb-secret-123" not in reason and "qb-secret-456" not in reason
    assert "RuntimeError" in reason
