import httpx
import pytest
import respx

from flow.models import ParsedName
from flow.tmdb import TmdbClient, TmdbUnavailable, score_candidate

BASE = "https://api.themoviedb.org/3"
SEARCH_MOVIE = f"{BASE}/search/movie"
SEARCH_TV = f"{BASE}/search/tv"

EL_CAMINO_MOVIE_ID = 559969


def parsed(title_en=None, title_zh=None, year=None, season=None):
    return ParsedName(
        title_en=title_en, title_zh=title_zh, year=year, season=season, source_name="x"
    )


def movie_result(tmdb_id, title, original_title="", year="2011", popularity=5.0):
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original_title or title,
        "release_date": f"{year}-01-01" if year else "",
        "overview": "",
        "poster_path": "/p.jpg",
        "popularity": popularity,
    }


def tv_result(tmdb_id, name, year="2011"):
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": name,
        "first_air_date": f"{year}-01-01" if year else "",
        "overview": "",
        "poster_path": "/p.jpg",
        "popularity": 5.0,
    }


@pytest.fixture
def client():
    return TmdbClient(api_key="k", language="zh-CN")


def test_searches_english_title_when_present(client):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(SEARCH_TV).respond(200, json={"results": [tv_result(1, "Black Mirror")]})
        client.search(parsed(title_en="Black Mirror", title_zh="黑镜", year=2011, season=1), "tv")
        assert "Black Mirror" in route.calls[0].request.url.params["query"]


def test_falls_back_to_chinese_with_zh_cn(client):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(SEARCH_MOVIE).respond(
            200, json={"results": [movie_result(1, "The Flying Swords of Dragon Gate")]}
        )
        client.search(parsed(title_zh="龙门飞甲", year=2011), "movie")
        req = route.calls[0].request
        assert req.url.params["query"] == "龙门飞甲"
        assert req.url.params["language"] == "zh-CN"


def test_exact_title_and_year_scores_above_accept():
    raw = movie_result(1, "The Flying Swords of Dragon Gate", "龙门飞甲", "2011")
    cand = score_candidate(raw, parsed(title_zh="龙门飞甲", year=2011), "movie")
    assert cand.score >= 0.80


def test_year_mismatch_scores_below_accept():
    raw = movie_result(2, "New Dragon Gate Inn", "新龙门客栈", "1992")
    cand = score_candidate(raw, parsed(title_zh="龙门飞甲", year=2011), "movie")
    assert cand.score < 0.50


def test_movie_named_after_a_series_resolves_to_the_movie(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).respond(
            200,
            json={
                "results": [
                    movie_result(EL_CAMINO_MOVIE_ID, "El Camino: A Breaking Bad Movie", "", "2019"),
                    movie_result(999, "Breaking Bad: The Movie", "", "2013"),
                ]
            },
        )
        cands = client.search(
            parsed(title_en="El Camino a Breaking Bad Movie", year=2019), "movie"
        )
        assert cands[0].tmdb_id == EL_CAMINO_MOVIE_ID


def test_chinese_title_fetched_even_when_matched_in_english(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_TV).respond(200, json={"results": [tv_result(1, "黑镜")]})
        cands = client.search(parsed(title_en="Black Mirror", title_zh="黑镜", year=2011), "tv")
        assert cands[0].title_zh == "黑镜"


def test_results_are_sorted_by_score(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).respond(
            200,
            json={
                "results": [
                    movie_result(2, "New Dragon Gate Inn", "新龙门客栈", "1992"),
                    movie_result(1, "Dragon Gate", "龙门飞甲", "2011"),
                ]
            },
        )
        cands = client.search(parsed(title_zh="龙门飞甲", year=2011), "movie")
        assert [c.tmdb_id for c in cands] == [1, 2]
        assert cands[0].score > cands[1].score


def test_candidate_carries_a_human_reason(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).respond(
            200, json={"results": [movie_result(1, "Dragon Gate", "龙门飞甲", "2011")]}
        )
        assert client.search(parsed(title_zh="龙门飞甲", year=2011), "movie")[0].reason


def test_network_error_raises_unavailable(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(TmdbUnavailable):
            client.search(parsed(title_en="Anything", year=2000), "movie")


def test_rate_limit_raises_unavailable(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).respond(429)
        with pytest.raises(TmdbUnavailable):
            client.search(parsed(title_en="Anything", year=2000), "movie")


def test_server_error_raises_unavailable(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).respond(500)
        with pytest.raises(TmdbUnavailable):
            client.search(parsed(title_en="Anything", year=2000), "movie")


def test_zero_results_returns_empty_not_error(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SEARCH_MOVIE).respond(200, json={"results": []})
        assert client.search(parsed(title_en="Anything", year=2000), "movie") == []


def test_api_key_is_sent_but_never_in_the_reason(client):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(SEARCH_MOVIE).respond(
            200, json={"results": [movie_result(1, "Dragon Gate", "龙门飞甲", "2011")]}
        )
        cands = client.search(parsed(title_zh="龙门飞甲", year=2011), "movie")
        assert route.calls[0].request.url.params["api_key"] == "k"
        assert "k" not in cands[0].reason.split()


def test_untitled_parse_yields_no_search(client):
    assert client.search(parsed(), "movie") == []
