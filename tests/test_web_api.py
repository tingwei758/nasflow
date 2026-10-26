from nasflow.qbit import Torrent

from .test_pipeline import cand, make_junk, make_movie

LONGMEN = "龙门飞甲.2011.HKG.1080p.国粤双语.简繁中字￡CMCT星辰"
PEGASUS = "飞驰人生.Pegasus.2019.BluRay.1080p.x265.10bit.MNHD-FRDS"
JUNK = "CE教学实例应用-由浅入深学习游戏修改 [全十课]"


def seed_needs_you(api) -> Torrent:
    client, pipeline, store, qbit, tmdb, downloads, staging = api
    t = make_movie(downloads, LONGMEN)
    qbit.torrents = [t]
    tmdb.results = {"龙门飞甲": [cand(72113, "龙门飞甲", "Dragon Gate", 2011, 0.94)]}
    pipeline.poll_once()
    return t


def test_board_groups_items_by_column(api):
    client = api[0]
    body = client.get("/api/board").json()
    assert set(body) == {"downloaded", "identifying", "needs_you", "ready"}


def test_needs_you_item_includes_candidates_and_reason(api):
    client = api[0]
    t = seed_needs_you(api)
    item = client.get("/api/board").json()["needs_you"][0]
    assert item["hash"] == t.hash
    assert item["reason"]
    assert item["candidates"][0]["reason"]
    assert item["candidates"][0]["tmdb_id"] == 72113


def test_needs_you_item_includes_planned_paths(api):
    client = api[0]
    seed_needs_you(api)
    item = client.get("/api/board").json()["needs_you"][0]
    assert item["planned_paths"]
    assert "龙门飞甲 (2011)" in item["planned_paths"][0]


def test_resolve_links_and_moves_card_to_ready(api):
    client, _, store, _, _, _, staging = api
    t = seed_needs_you(api)
    assert client.post(f"/api/items/{t.hash}/resolve", json={"tmdb_id": 72113}).status_code == 200
    assert client.get("/api/board").json()["ready"]
    assert (staging / "Movies/龙门飞甲 (2011)/龙门飞甲 (2011).mkv").exists()


def test_ignore_removes_from_needs_you(api):
    client = api[0]
    t = seed_needs_you(api)
    assert client.post(f"/api/items/{t.hash}/ignore").status_code == 200
    assert client.get("/api/board").json()["needs_you"] == []


def test_resolve_unknown_hash_returns_404(api):
    client = api[0]
    assert client.post("/api/items/nope/resolve", json={"tmdb_id": 1}).status_code == 404


def test_ignore_unknown_hash_returns_404(api):
    client = api[0]
    assert client.post("/api/items/nope/ignore").status_code == 404


def test_manual_search_proxies_tmdb(api):
    client, _, _, _, tmdb, _, _ = api
    t = seed_needs_you(api)
    tmdb.results["Dragon Inn"] = [cand(5, "龙门客栈", "Dragon Inn", 1967, 0.5)]
    body = client.get(f"/api/items/{t.hash}/search", params={"q": "Dragon Inn"}).json()
    assert [c["tmdb_id"] for c in body["candidates"]] == [5]


def test_manual_search_unknown_hash_returns_404(api):
    client = api[0]
    assert client.get("/api/items/nope/search", params={"q": "x"}).status_code == 404


def test_board_never_exposes_secrets(api):
    client = api[0]
    seed_needs_you(api)
    text = client.get("/api/board").text
    assert "super-secret" not in text and "key-123" not in text


def test_discovered_items_appear_under_downloaded(api):
    client, pipeline, store, qbit, tmdb, downloads, staging = api
    t = make_junk(downloads, JUNK)
    store.upsert_discovered(t)
    assert client.get("/api/board").json()["downloaded"][0]["hash"] == t.hash


def test_done_and_ignored_do_not_appear_on_the_board(api):
    client, pipeline, store, qbit, tmdb, downloads, staging = api
    t = seed_needs_you(api)
    pipeline.ignore(t.hash)
    board = client.get("/api/board").json()
    assert all(not column for column in board.values())


def test_board_item_carries_its_release_name(api):
    client = api[0]
    t = seed_needs_you(api)
    assert client.get("/api/board").json()["needs_you"][0]["name"] == t.name


def test_resolve_refuses_an_item_that_is_not_waiting_for_you(api):
    client, pipeline, store, qbit, tmdb, downloads, staging = api
    t = make_movie(downloads, PEGASUS)
    qbit.torrents = [t]
    tmdb.results = {"Pegasus": [cand(1, "飞驰人生", "Pegasus", 2019, 0.95)]}
    pipeline.poll_once()
    assert store.get(t.hash).state == "ready"
    assert client.post(f"/api/items/{t.hash}/resolve", json={"tmdb_id": 1}).status_code == 409


def test_manual_search_survives_the_next_poll(api):
    client, pipeline, store, qbit, tmdb, downloads, staging = api
    t = seed_needs_you(api)
    tmdb.results["Dragon Inn"] = [cand(5, "龙门客栈", "Dragon Inn", 1967, 0.5)]
    client.get(f"/api/items/{t.hash}/search", params={"q": "Dragon Inn"})
    pipeline.poll_once()
    assert client.get(f"/api/items/{t.hash}/preview", params={"tmdb_id": 5}).status_code == 200


def test_ignore_refuses_an_item_that_is_not_waiting_for_you(api):
    client, pipeline, store, qbit, tmdb, downloads, staging = api
    t = seed_needs_you(api)
    client.post(f"/api/items/{t.hash}/resolve", json={"tmdb_id": 72113})
    assert client.post(f"/api/items/{t.hash}/ignore").status_code == 409
    assert store.get(t.hash).state == "ready"
