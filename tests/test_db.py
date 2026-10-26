import time

from nasflow.db import Store
from nasflow.models import Candidate
from nasflow.qbit import Torrent


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
    return Store(tmp_path / "nasflow.db")


def test_schema_created_on_first_open(tmp_path):
    s = store(tmp_path)
    assert s.all_items() == []
    assert (tmp_path / "nasflow.db").exists()


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
    reopened = Store(tmp_path / "nasflow.db")
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


def test_store_survives_concurrent_use_from_several_threads(tmp_path):
    """The poll loop and the web threadpool share one Store."""
    import threading

    s = store(tmp_path)
    errors: list[BaseException] = []

    def worker(n):
        try:
            for i in range(150):
                h = f"t{n}-{i % 5}"
                s.upsert_discovered(torrent(h, h))
                s.set_state(h, "needs_you", reason="x")
                s.set_candidates(h, [candidate(i)])
                s.get_candidates(h)
                s.all_items()
        except BaseException as exc:  # noqa: BLE001 - surface anything
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(s.all_items()) == 40


def test_retry_flag_round_trips(tmp_path):
    s = store(tmp_path)
    s.upsert_discovered(torrent())
    s.set_state("h1", "needs_you", reason="TMDB unavailable", retry=True)
    assert s.get("h1").retry is True
    s.set_state("h1", "needs_you", reason="2 close matches")
    assert s.get("h1").retry is False


def test_opens_a_database_created_before_the_retry_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE items (hash TEXT PRIMARY KEY, name TEXT NOT NULL,
           content_path TEXT NOT NULL, state TEXT NOT NULL, kind TEXT,
           reason TEXT NOT NULL DEFAULT '', candidates_json TEXT NOT NULL DEFAULT '[]',
           chosen_tmdb_id INTEGER, dest_root TEXT, discovered_at REAL NOT NULL,
           updated_at REAL NOT NULL)"""
    )
    conn.execute("INSERT INTO items (hash, name, content_path, state, discovered_at, updated_at)"
                 " VALUES ('h1', 'n', '/downloads/n', 'needs_you', 0, 0)")
    conn.commit()
    conn.close()
    assert Store(path).get("h1").retry is False


def test_flags_default_to_unset_and_persist(tmp_path):
    s = store(tmp_path)
    assert s.get_flag("baseline_taken") is False
    s.set_flag("baseline_taken")
    s.close()
    assert Store(tmp_path / "nasflow.db").get_flag("baseline_taken") is True
