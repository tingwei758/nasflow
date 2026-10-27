from pathlib import Path

from .test_pipeline import cand
from .test_web_api import seed_needs_you

STATIC = Path(__file__).resolve().parents[1] / "src/nasflow/web/static"
MODULES = ("api.js", "board.js", "resolve.js", "dom.js")


def test_index_served_at_root(api):
    assert api[0].get("/").status_code == 200


def test_static_assets_served(api):
    client = api[0]
    for name in (*MODULES, "style.css"):
        response = client.get(f"/{name}")
        assert response.status_code == 200, name


def test_index_references_board_endpoint(api):
    assert "/api/board" in api[0].get("/").text


def test_index_loads_board_as_module(api):
    assert 'type="module"' in api[0].get("/").text


def test_only_api_js_calls_fetch():
    for name in ("board.js", "resolve.js", "dom.js"):
        assert "fetch(" not in (STATIC / name).read_text(encoding="utf-8"), name


def test_column_labels_are_not_hardcoded_in_js():
    for name in MODULES:
        text = (STATIC / name).read_text(encoding="utf-8")
        for label in ("Downloaded", "Identifying", "Needs you", "Ready to move"):
            assert label not in text, f"{name} hardcodes {label!r}"


def test_no_mark_complete_control_anywhere():
    for path in STATIC.iterdir():
        text = path.read_text(encoding="utf-8").lower()
        assert "mark complete" not in text and "mark done" not in text, path.name


def test_meta_lists_columns_in_board_order_with_labels(api):
    meta = api[0].get("/api/meta").json()
    assert [c["key"] for c in meta["columns"]] == [
        "downloaded", "identifying", "needs_you", "ready"
    ]
    assert meta["columns"][3]["label"] == "Ready to move"


def test_meta_exposes_poster_base_and_no_secrets(api):
    response = api[0].get("/api/meta")
    assert response.json()["poster_base"].startswith("https://")
    assert "super-secret" not in response.text and "key-123" not in response.text


def test_preview_follows_the_selected_candidate(api):
    client, _, _, _, tmdb, _, _ = api
    t = seed_needs_you(api)
    tmdb.results["Dragon Inn"] = [cand(5, "龙门客栈", "Dragon Inn", 1967, 0.5)]
    client.get(f"/api/items/{t.hash}/search", params={"q": "Dragon Inn"})
    paths = client.get(f"/api/items/{t.hash}/preview", params={"tmdb_id": 5}).json()["paths"]
    assert len(paths) == 1 and "龙门客栈 (1967)" in paths[0]


def test_preview_does_not_create_anything(api):
    client, _, _, _, _, _, staging = api
    t = seed_needs_you(api)
    client.get(f"/api/items/{t.hash}/preview", params={"tmdb_id": 72113})
    assert not any(staging.rglob("*"))


def test_preview_unknown_candidate_returns_404(api):
    client = api[0]
    t = seed_needs_you(api)
    assert client.get(f"/api/items/{t.hash}/preview", params={"tmdb_id": 999}).status_code == 404


def test_needs_you_item_carries_what_flow_parsed(api):
    client = api[0]
    seed_needs_you(api)
    item = client.get("/api/board").json()["needs_you"][0]
    assert item["parsed_title_zh"] == "龙门飞甲"
    assert item["parsed_year"] == 2011
    assert item["video_count"] == 1
    assert item["classification_reason"] == "single video, no episode markers"


def test_health_strip_is_in_the_page(api):
    assert 'id="health"' in api[0].get("/").text


def test_only_api_js_calls_health(api):
    assert "getHealth" in (STATIC / "api.js").read_text(encoding="utf-8")
    assert "/api/health" not in (STATIC / "board.js").read_text(encoding="utf-8")


def test_check_labels_are_not_hardcoded_in_js():
    """The strip renders whatever /api/health labels its checks.

    resolve.js says "TMDB" for its own manual-search controls, which has
    nothing to do with a check label; that one pair is the only exemption.
    """
    exempt = {("resolve.js", "TMDB")}
    for name in MODULES:
        text = (STATIC / name).read_text(encoding="utf-8")
        for label in ("qBittorrent", "TMDB", "Staging folder", "Database", "Polling"):
            if (name, label) in exempt:
                continue
            assert label not in text, f"{name} hardcodes {label!r}"


def test_health_strip_is_grouped_not_announced(api):
    """A dot changing colour every few minutes is not worth interrupting for."""
    tag = next(line for line in api[0].get("/").text.splitlines() if 'id="health"' in line)
    assert 'role="group"' in tag and "aria-live" not in tag and 'role="status"' not in tag
