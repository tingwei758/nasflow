import time

from flow.db import Store
from flow.models import Candidate
from flow.qbit import Torrent


def torrent(hash_="h1", name="Some.Release.2020"):
    return Torrent(
        hash=hash_, name=name, content_path=f"/downloads/{name}", save_path="/downloads/",
        state="seeding", progress=1.0, completed_on=1700000000,
    )


def candidate(tmdb_id=1, score=0.9):
    return Candidate(
        tmdb_id=tmdb_id, kind="movie", title_zh="龙门飞甲", title_en="Dragon Gate",
        year=2011, overview="", poster_path="/p.jpg", score=score, reason="title exact",
    )


def store(tmp_path):
    return Store(tmp_path / "flow.db")


def test_schema_created_on_first_open(tmp_path):
    s = store(tmp_path)
    assert s.all_items() == []
    assert (tmp_path / "flow.db").exists()


def test_upsert_is_idempotent_on_hash(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.upsert_discovered(torrent())
    assert len(s.all_items()) == 1


def test_discovered_item_starts_in_discovered(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    assert s.get("h1").state == "discovered"


def test_state_transition_persists(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.set_state("h1", "needs_you", reason="2 close matches")
    reopened = Store(tmp_path / "flow.db")
    assert reopened.get("h1").state == "needs_you"
    assert reopened.get("h1").reason == "2 close matches"


def test_candidates_round_trip(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.set_candidates("h1", [candidate(1, 0.94), candidate(2, 0.41)])
    got = s.get_candidates("h1")
    assert [c.tmdb_id for c in got] == [1, 2]
    assert got[0].title_zh == "龙门飞甲" and got[0].score == 0.94


def test_resolution_survives_reprocessing(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.set_resolution("h1", 72113)
    s.set_state("h1", "discovered")
    s.upsert_discovered(torrent())
    assert s.get("h1").chosen_tmdb_id == 72113


def test_ignored_item_stays_ignored_across_rediscovery(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.set_state("h1", "ignored")
    s.upsert_discovered(torrent())
    assert s.get("h1").state == "ignored"


def test_set_linked_records_dest_root_and_ready(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.set_linked("h1", "/staging/Movies/龙门飞甲 (2011)")
    item = s.get("h1")
    assert item.state == "ready"
    assert item.dest_root == "/staging/Movies/龙门飞甲 (2011)"


def test_items_in_filters_by_state(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent("a", "A"))
    s.upsert_discovered(torrent("b", "B"))
    s.set_state("b", "needs_you")
    assert [i.hash for i in s.items_in(["needs_you"])] == ["b"]


def test_all_items_orders_newest_first(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent("a", "A"))
    time.sleep(0.01)
    s.upsert_discovered(torrent("b", "B"))
    s.set_state("b", "ready")
    assert [i.hash for i in s.all_items()][0] == "b"


def test_get_unknown_hash_returns_none(tmp_path):
    assert store(tmp_path).get("nope") is None


def test_name_and_content_path_are_stored(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent("h1", "黑镜.Black.Mirror.S06"))
    item = s.get("h1")
    assert item.name == "黑镜.Black.Mirror.S06"
    assert item.content_path == "/downloads/黑镜.Black.Mirror.S06"
