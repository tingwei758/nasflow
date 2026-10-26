from dataclasses import replace
from pathlib import Path

from flow.config import Config
from flow.decide import decide
from flow.models import Candidate, Content, ParsedName, VideoFile

CFG = Config(
    downloads_root=Path("/dl"), staging_root=Path("/staging"), db_path=Path("/db"),
    qb_url="http://qb", qb_username="u", qb_password="p", tmdb_api_key="k",
)

parsed_pegasus = ParsedName("Pegasus", "飞驰人生", 2019, None, "x")
parsed_longmen_no_english = ParsedName(None, "龙门飞甲", 2011, None, "x")


def c(score, tmdb_id=1):
    return Candidate(
        tmdb_id=tmdb_id, kind="movie", title_zh="龙门飞甲", title_en="Dragon Gate",
        year=2011, overview="", poster_path=None, score=score, reason="r",
    )


def _content(kind, count=1, reason=""):
    videos = [
        VideoFile(Path(f"/dl/{i}.mkv"), 8_000_000_000, None, None) for i in range(count)
    ]
    return Content(root=Path("/dl"), kind=kind, videos=videos, subtitles=[], reason=reason)


movie_content = _content("movie")
unknown_content = _content("unknown", 2, "ambiguous: 2 videos with no episode markers")


def test_confident_unique_match_links():
    d = decide(movie_content, parsed_pegasus, [c(0.93), c(0.30, 2)], CFG)
    assert d.action == "link" and d.candidate.score == 0.93


def test_score_below_accept_stops():
    assert decide(movie_content, parsed_pegasus, [c(0.60)], CFG).action == "stop"


def test_close_top_two_stops_even_when_top_is_high():
    d = decide(movie_content, parsed_pegasus, [c(0.91), c(0.85, 2)], CFG)
    assert d.action == "stop" and "ambiguous" in d.reason


def test_chinese_only_name_stops_despite_high_score():
    d = decide(movie_content, parsed_longmen_no_english, [c(0.94), c(0.41, 2)], CFG)
    assert d.action == "stop" and "no English title" in d.reason


def test_require_english_title_is_configurable():
    relaxed = replace(CFG, require_english_title=False)
    d = decide(movie_content, parsed_longmen_no_english, [c(0.94), c(0.41, 2)], relaxed)
    assert d.action == "link"


def test_unknown_classification_stops():
    d = decide(unknown_content, parsed_pegasus, [c(0.99)], CFG)
    assert d.action == "stop" and "ambiguous" in d.reason


def test_no_candidates_stops():
    d = decide(movie_content, parsed_pegasus, [], CFG)
    assert d.action == "stop" and "no TMDB match" in d.reason


def test_thresholds_come_from_config():
    strict = replace(CFG, score_accept=0.95)
    assert decide(movie_content, parsed_pegasus, [c(0.93), c(0.10, 2)], strict).action == "stop"


def test_gap_threshold_comes_from_config():
    forgiving = replace(CFG, score_gap=0.01)
    d = decide(movie_content, parsed_pegasus, [c(0.91), c(0.85, 2)], forgiving)
    assert d.action == "link"


def test_single_confident_candidate_links_without_a_runner_up():
    assert decide(movie_content, parsed_pegasus, [c(0.93)], CFG).action == "link"


def test_reason_is_always_populated_when_stopping():
    assert decide(unknown_content, parsed_pegasus, [], CFG).reason


def test_stop_carries_no_candidate():
    assert decide(movie_content, parsed_pegasus, [c(0.10)], CFG).candidate is None


def test_candidates_need_not_be_pre_sorted():
    d = decide(movie_content, parsed_pegasus, [c(0.30, 2), c(0.93, 1)], CFG)
    assert d.action == "link" and d.candidate.tmdb_id == 1


def test_tv_content_links_like_movies():
    tv = Content(
        root=Path("/dl"), kind="tv",
        videos=[VideoFile(Path("/dl/S01E01.mkv"), 2_000_000_000, 1, 1)],
        subtitles=[], reason="",
    )
    assert decide(tv, ParsedName("Black Mirror", "黑镜", 2011, 6, "x"), [c(0.95)], CFG).action == "link"


def test_gap_exactly_at_threshold_links_despite_float_error():
    """1.00 - 0.90 is 0.0999... in floating point; a 0.10 gap must still pass."""
    at_threshold = replace(CFG, score_gap=0.10)
    d = decide(movie_content, parsed_pegasus, [c(1.00), c(0.90, 2)], at_threshold)
    assert d.action == "link"


def test_score_exactly_at_accept_links_despite_float_error():
    assert 0.7 + 0.1 < 0.80  # the trap: 0.7999999999999999
    assert decide(movie_content, parsed_pegasus, [c(0.7 + 0.1)], CFG).action == "link"


def test_tv_download_with_unmarked_videos_stops_and_names_them():
    tv = Content(
        root=Path("/dl"), kind="tv", subtitles=[], reason="",
        videos=[
            VideoFile(Path("/dl/Downton.Abbey.S01E01.mkv"), 2_000_000_000, 1, 1),
            VideoFile(Path("/dl/Downton.Abbey.2019.1080p.mkv"), 9_000_000_000, None, None),
        ],
    )
    d = decide(tv, ParsedName("Downton Abbey", "唐顿庄园", 2010, None, "x"), [c(0.99)], CFG)
    assert d.action == "stop"
    assert "1 video without an episode marker" in d.reason
    assert "Downton.Abbey.2019.1080p.mkv" in d.reason
