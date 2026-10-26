import httpx
import pytest
import respx

from nasflow.models import ParsedName
from nasflow.tmdb import TmdbClient, TmdbUnavailable, score_candidate

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


# ---- the key rides in the query string, so every error path must scrub it

SECRET = "sekrit-tmdb-key-0123456789"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_search_http_error_never_carries_the_key(status):
    client = TmdbClient(SECRET)
    with respx.mock:
        respx.get(SEARCH_MOVIE).respond(status)
        with pytest.raises(TmdbUnavailable) as exc:
            client.search(parsed(title_en="Pegasus", year=2019), "movie")
    assert SECRET not in str(exc.value)
    assert str(status) in str(exc.value)


def test_get_http_error_never_carries_the_key():
    client = TmdbClient(SECRET)
    with respx.mock:
        respx.get(f"{BASE}/movie/1").respond(401)
        with pytest.raises(TmdbUnavailable) as exc:
            client.get(1, "movie")
    assert SECRET not in str(exc.value)


def test_transport_error_never_carries_the_key():
    client = TmdbClient(SECRET)
    with respx.mock:
        respx.get(SEARCH_MOVIE).mock(
            side_effect=httpx.ConnectError(f"boom for {SEARCH_MOVIE}?api_key={SECRET}")
        )
        with pytest.raises(TmdbUnavailable) as exc:
            client.search(parsed(title_en="Pegasus"), "movie")
    assert SECRET not in str(exc.value)


def test_chained_cause_is_dropped_so_tracebacks_cannot_leak_the_key():
    client = TmdbClient(SECRET)
    with respx.mock:
        respx.get(SEARCH_MOVIE).respond(401)
        with pytest.raises(TmdbUnavailable) as exc:
            client.search(parsed(title_en="Pegasus"), "movie")
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


# ---- regressions from the real-NAS dry run (Task 14) -------------------------
#
# Both were confident, *wrong* auto-links. The bar is that they never link to
# the wrong entry again; stopping for a human is an acceptable outcome.

from pathlib import Path  # noqa: E402

from nasflow.config import Config  # noqa: E402
from nasflow.decide import decide  # noqa: E402
from nasflow.models import Content, VideoFile  # noqa: E402

_CFG = Config(
    downloads_root=Path("/d"), staging_root=Path("/s"), db_path=Path("/db"),
    qb_url="x", qb_username="u", qb_password="p", tmdb_api_key="k",
)


def _content(kind):
    return Content(root=Path("/d"), kind=kind,
                   videos=[VideoFile(Path("/d/x.mkv"), 9_000_000_000, None, None)], subtitles=[])


def _by_language(zh_results, en_results):
    def handler(request):
        lang = request.url.params["language"]
        return httpx.Response(200, json={"results": en_results if lang == "en-US" else zh_results})
    return handler


ALL_QUIET_FILM_ZH = movie_result(49046, "西线无战事", "Im Westen nichts Neues", "2022")
ALL_QUIET_FILM_EN = movie_result(49046, "All Quiet on the Western Front", "Im Westen nichts Neues", "2022")
ALL_QUIET_MAKING_OF = movie_result(1076364, "Making All Quiet on the Western Front", "", "2022")


def test_english_pass_supplies_the_title_a_chinese_localized_search_hides():
    client = TmdbClient("k", "zh-CN", english_language="en-US")
    with respx.mock:
        respx.get(SEARCH_MOVIE).mock(side_effect=_by_language(
            [ALL_QUIET_FILM_ZH, ALL_QUIET_MAKING_OF], [ALL_QUIET_FILM_EN, ALL_QUIET_MAKING_OF]
        ))
        cands = client.search(parsed(title_en="All Quiet on the Western Front", year=2022), "movie")
    film = next(c for c in cands if c.tmdb_id == 49046)
    assert film.title_zh == "西线无战事"
    assert film.title_en == "All Quiet on the Western Front"
    assert cands[0].tmdb_id == 49046


def test_all_quiet_never_auto_links_the_making_of():
    client = TmdbClient("k", "zh-CN", english_language="en-US")
    p = parsed(title_en="All Quiet on the Western Front", year=2022)
    with respx.mock:
        respx.get(SEARCH_MOVIE).mock(side_effect=_by_language(
            [ALL_QUIET_FILM_ZH, ALL_QUIET_MAKING_OF], [ALL_QUIET_FILM_EN, ALL_QUIET_MAKING_OF]
        ))
        d = decide(_content("movie"), p, client.search(p, "movie"), _CFG)
    assert d.action == "stop" or d.candidate.tmdb_id == 49046


def test_english_pass_is_off_when_not_configured():
    with respx.mock:
        route = respx.get(SEARCH_MOVIE).respond(200, json={"results": []})
        TmdbClient("k", "zh-CN").search(parsed(title_en="X"), "movie")
    assert route.call_count == 1


def test_tv_search_never_filters_by_first_air_year():
    """`Billions.2019.S04`: 2019 is the season's year; the show premiered in 2016."""
    with respx.mock:
        route = respx.get(SEARCH_TV).respond(200, json={"results": []})
        TmdbClient("k").search(parsed(title_en="Billions", year=2019, season=4), "tv")
    assert "first_air_date_year" not in route.calls[0].request.url.params


def test_later_season_release_year_after_premiere_is_consistent():
    raw = tv_result(62852, "Billions", year="2016")
    cand = score_candidate(raw, parsed(title_en="Billions", year=2019, season=4), "tv")
    assert cand.score >= 0.99 and "season" in cand.reason


def test_later_season_show_premiering_after_release_year_is_a_mismatch():
    raw = tv_result(1, "Billions", year="2021")
    cand = score_candidate(raw, parsed(title_en="Billions", year=2019, season=4), "tv")
    assert "year mismatch" in cand.reason


def test_first_season_still_wants_the_premiere_year():
    raw = tv_result(1, "Dark", year="2015")
    assert "year mismatch" in score_candidate(raw, parsed(title_en="Dark", year=2017, season=1), "tv").reason


def test_billions_s04_never_auto_links_d_billions():
    client = TmdbClient("k", "zh-CN", english_language="en-US")
    p = parsed(title_en="Billions", title_zh="亿万", year=2019, season=4)
    billions_zh, billions_en = tv_result(62852, "亿万", "2016"), tv_result(62852, "Billions", "2016")
    d_billions = tv_result(119513, "D Billions", "2019")
    with respx.mock:
        respx.get(SEARCH_TV).mock(side_effect=_by_language(
            [billions_zh, d_billions], [billions_en, d_billions]
        ))
        d = decide(_content("tv"), p, client.search(p, "tv"), _CFG)
    assert d.action == "stop" or d.candidate.tmdb_id == 62852


def test_ampersand_and_the_word_and_are_the_same_title():
    """`Love.Death.and.Robots` vs TMDB's `Love, Death & Robots`."""
    raw = tv_result(86831, "Love, Death & Robots", year="2019")
    cand = score_candidate(raw, parsed(title_en="Love Death and Robots", season=4), "tv")
    assert "title exact" in cand.reason


def test_candidate_carries_its_title_score():
    raw = movie_result(1, "Pegasus", year="2019")
    cand = score_candidate(raw, parsed(title_en="Pegasus", year=2019), "movie")
    assert cand.title_score == 1.0
