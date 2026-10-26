from dataclasses import replace
from pathlib import Path

from nasflow.config import LayoutConfig
from nasflow.layout import plan_links
from nasflow.models import Candidate, Content, Identification, VideoFile

LAYOUT = LayoutConfig()
STAGING = Path("/staging")


def ident(title_zh, year, kind, title_en="English Title"):
    return Identification(
        candidate=Candidate(
            tmdb_id=1, kind=kind, title_zh=title_zh, title_en=title_en,
            year=year, overview="", poster_path=None, score=0.95, reason="",
        ),
        kind=kind,
    )


def ident_en(title_en, year, kind):
    return ident(None, year, kind, title_en=title_en)


def video(name, season=None, episode=None, size=8_000_000_000):
    return VideoFile(path=Path("/dl") / name, size=size, season=season, episode=episode)


def content(kind, videos, subtitles=()):
    return Content(root=Path("/dl"), kind=kind, videos=list(videos),
                   subtitles=list(subtitles), reason="")


def dests(links):
    return {link.dest for link in links}


movie_content = content("movie", [video("Pegasus.2019.mkv")])
bm_s06 = content("tv", [video("Black.Mirror.S06E01.mkv", 6, 1)])
bm_special = content("tv", [video("Black.Mirror.S00E00.mkv", 0, 0)])
bm_pack = content("tv", [
    video("Black.Mirror.S01E01.mkv", 1, 1),
    video("Black.Mirror.S04E03.mkv", 4, 3),
])
cunk = content("tv", [video("Cunk.on.Earth.S01E01.mkv", 1, 1)])
litchi_e35_only = content("tv", [video("The.Litchi.Road.S01E35.mp4", 1, 35)])
movie_with_srt = content("movie", [video("Pegasus.2019.mkv")],
                         [Path("/dl/Pegasus.2019.zh.srt")])
movie_with_nfo = content("movie", [video("Pegasus.2019.mkv")])


def test_movie_path():
    links = plan_links(movie_content, ident("龙门飞甲", 2011, "movie"), STAGING, LAYOUT)
    assert links[0].dest == STAGING / "Movies/龙门飞甲 (2011)/龙门飞甲 (2011).mkv"


def test_layout_templates_are_honoured():
    custom = replace(LAYOUT, movies_dirname="电影",
                     movie_dirname="{title}.{year}", movie_filename="{title}.{year}")
    links = plan_links(movie_content, ident("龙门飞甲", 2011, "movie"), STAGING, custom)
    assert links[0].dest == STAGING / "电影/龙门飞甲.2011/龙门飞甲.2011.mkv"


def test_season_template_is_honoured():
    custom = replace(LAYOUT, season_dirname="S{season:02d}")
    assert "S06" in str(plan_links(bm_s06, ident("黑镜", 2011, "tv"), STAGING, custom)[0].dest)


def test_episode_path_uses_season_n_not_zero_padded():
    links = plan_links(bm_s06, ident("黑镜", 2011, "tv"), STAGING, LAYOUT)
    assert STAGING / "TV Shows/黑镜 (2011)/Season 6/黑镜 - S06E01.mkv" in dests(links)


def test_special_goes_to_season_zero():
    links = plan_links(bm_special, ident("黑镜", 2011, "tv"), STAGING, LAYOUT)
    assert STAGING / "TV Shows/黑镜 (2011)/Season 0/黑镜 - S00E00.mkv" in dests(links)


def test_english_title_when_no_chinese():
    links = plan_links(cunk, ident_en("Cunk on Earth", 2022, "tv"), STAGING, LAYOUT)
    assert "Cunk on Earth (2022)" in str(links[0].dest)


def test_show_year_is_series_year_not_episode_year():
    links = plan_links(bm_pack, ident("黑镜", 2011, "tv"), STAGING, LAYOUT)
    assert all("黑镜 (2011)" in str(link.dest) for link in links)


def test_partial_season_plans_only_the_episodes_present():
    links = plan_links(litchi_e35_only, ident("长安的荔枝", 2025, "tv"), STAGING, LAYOUT)
    assert dests(links) == {
        STAGING / "TV Shows/长安的荔枝 (2025)/Season 1/长安的荔枝 - S01E35.mp4"
    }


def test_episode_without_season_uses_default_season():
    c = content("tv", [video("Eight.Hours.E03.mkv", None, 3)])
    links = plan_links(c, ident_en("Eight Hours", 1972, "tv"), STAGING, LAYOUT)
    assert STAGING / "TV Shows/Eight Hours (1972)/Season 1/Eight Hours - S01E03.mkv" in dests(links)


def test_subtitle_renamed_to_match_video_stem():
    links = plan_links(movie_with_srt, ident("龙门飞甲", 2011, "movie"), STAGING, LAYOUT)
    assert STAGING / "Movies/龙门飞甲 (2011)/龙门飞甲 (2011).zh.srt" in dests(links)


def test_nfo_and_posters_not_planned():
    links = plan_links(movie_with_nfo, ident("龙门飞甲", 2011, "movie"), STAGING, LAYOUT)
    assert not any(link.dest.suffix in {".nfo", ".jpg"} for link in links)


def test_path_separator_in_title_is_sanitized():
    links = plan_links(movie_content, ident("12/12: The Day", 2023, "movie"), STAGING, LAYOUT)
    assert "/" not in links[0].dest.name
    assert links[0].dest.parent.parent == STAGING / "Movies"


def test_fullwidth_colon_is_preserved():
    links = plan_links(movie_content, ident("007：幽灵党", 2015, "movie"), STAGING, LAYOUT)
    assert "007：幽灵党 (2015)" in str(links[0].dest)


def test_source_paths_are_the_scanned_files():
    links = plan_links(movie_content, ident("龙门飞甲", 2011, "movie"), STAGING, LAYOUT)
    assert links[0].source == Path("/dl/Pegasus.2019.mkv")


def test_unknown_content_plans_nothing():
    c = content("unknown", [video("A.mkv"), video("B.mkv")])
    assert plan_links(c, ident("X", 2000, "movie"), STAGING, LAYOUT) == []


# ---- review: a video with no episode marker is never filed as an episode ----

def test_unmarked_video_in_a_tv_download_is_not_planned():
    """唐顿庄园S01-S06 ships the Downton Abbey films; they are not S01E00."""
    episode = VideoFile(Path("/dl/pack/Downton.Abbey.S01E01.mkv"), 2_000_000_000, 1, 1)
    film = VideoFile(Path("/dl/pack/Downton.Abbey.2019.1080p.mkv"), 9_000_000_000, None, None)
    pack = Content(root=Path("/dl/pack"), kind="tv", videos=[episode, film], subtitles=[])
    links = plan_links(pack, ident("唐顿庄园", 2010, "tv"), STAGING, LAYOUT)
    assert [link.source for link in links] == [episode.path]
    assert not any("E00" in str(link.dest) for link in links)
