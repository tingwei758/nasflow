from dataclasses import replace
from pathlib import Path

from flow.config import MediaConfig
from flow.scan import scan_content

MEDIA = MediaConfig()
BIG = 8_000_000_000


def make_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)
    return path


def make_files(root: Path, names: list[str], size: int) -> None:
    for name in names:
        make_file(root / name, size)


def make_black_mirror_pack(root: Path) -> Path:
    pack = root / "黑镜S01-S05.Black.Mirror.2011-2019.1080p.WEB-DL.x265.AC3￡cXcY@FRDS"
    seasons = {1: 3, 2: 3, 3: 6, 4: 6, 5: 3}
    for season, episodes in seasons.items():
        season_dir = pack / f"黑镜S0{season}.Black.Mirror.1080p.Blu-ray.x265.AC3￡cXcY@FRDS"
        for ep in range(1, episodes + 1):
            make_file(season_dir / f"Black.Mirror.S0{season}E0{ep}.1080p.x265.mkv", 2_000_000_000)
        make_file(season_dir / f"Black.Mirror.S0{season}_海报.jpg", 50_000)
    make_file(
        pack / "黑镜S03.Black.Mirror.1080p.Blu-ray.x265.AC3￡cXcY@FRDS"
        / "Black.Mirror.S03E00.Special.White.Christmas.2014.1080p.x265.mkv",
        2_000_000_000,
    )
    return pack


def test_episode_files_classify_as_tv(tmp_path):
    make_files(
        tmp_path,
        [
            "黑镜.Black.Mirror.S06E01.2023.1080p.NF.WEB-DL.H264-OurTV.mkv",
            "黑镜.Black.Mirror.S06E02.2023.1080p.NF.WEB-DL.H264-OurTV.mkv",
        ],
        size=2_000_000_000,
    )
    c = scan_content(tmp_path, MEDIA)
    assert c.kind == "tv"
    assert {(v.season, v.episode) for v in c.videos} == {(6, 1), (6, 2)}


def test_single_video_no_markers_is_movie(tmp_path):
    make_files(tmp_path, ["Pegasus.2019.BluRay.1080p.mkv"], size=BIG)
    make_files(tmp_path, ["cover.jpg", "x.nfo"], size=50_000)
    c = scan_content(tmp_path, MEDIA)
    assert c.kind == "movie" and len(c.videos) == 1


def test_nested_multi_season_pack(tmp_path):
    c = scan_content(make_black_mirror_pack(tmp_path), MEDIA)
    assert c.kind == "tv"
    assert (3, 0) in {(v.season, v.episode) for v in c.videos}
    assert len({v.season for v in c.videos}) == 5


def test_single_file_torrent_content_path_is_the_file(tmp_path):
    f = make_file(tmp_path / "[黑亚当]Black.Adam.2022.Blu-ray.1080p.mkv", 11_000_000_000)
    c = scan_content(f, MEDIA)
    assert c.kind == "movie" and c.videos[0].path == f


def test_episode_markers_without_season(tmp_path):
    # A real release folder as root: pytest's own tmp dir name ("..._s0")
    # would itself read as a season tag.
    root = tmp_path / "Eight.Hours.Dont.Make.a.Day.1972.E01-E05.CC.BluRay.1080p"
    make_files(
        root,
        [f"Eight.Hours.Dont.Make.a.Day.1972.E0{n}.CC.BluRay.mkv" for n in range(1, 6)],
        size=3_000_000_000,
    )
    c = scan_content(root, MEDIA)
    assert c.kind == "tv"
    assert {v.episode for v in c.videos} == {1, 2, 3, 4, 5}
    assert all(v.season is None for v in c.videos)


def test_samples_and_small_files_ignored(tmp_path):
    make_files(tmp_path, ["Movie.2020.mkv"], size=BIG)
    make_files(tmp_path, ["sample.mkv"], size=30_000_000)
    assert len(scan_content(tmp_path, MEDIA).videos) == 1


def test_extensions_come_from_config(tmp_path):
    make_files(tmp_path, ["Movie.2020.mkv", "Movie.2020.webm"], size=BIG)
    narrow = replace(MEDIA, video_extensions=(".webm",))
    videos = scan_content(tmp_path, narrow).videos
    assert len(videos) == 1 and videos[0].path.suffix == ".webm"


def test_several_large_videos_no_markers_is_unknown(tmp_path):
    make_files(tmp_path, ["A.mkv", "B.mkv"], size=BIG)
    c = scan_content(tmp_path, MEDIA)
    assert c.kind == "unknown" and "ambiguous" in c.reason


def test_no_video_files_is_unknown(tmp_path):
    make_files(tmp_path, ["lesson01.pdf", "notes.txt"], size=1_000_000)
    c = scan_content(tmp_path, MEDIA)
    assert c.kind == "unknown" and c.reason


def test_subtitles_collected(tmp_path):
    make_files(tmp_path, ["Movie.2020.mkv"], size=BIG)
    make_files(tmp_path, ["Movie.2020.zh.srt", "Movie.2020.ass"], size=40_000)
    c = scan_content(tmp_path, MEDIA)
    assert {p.name for p in c.subtitles} == {"Movie.2020.zh.srt", "Movie.2020.ass"}


def test_nfo_and_posters_not_collected(tmp_path):
    make_files(tmp_path, ["Movie.2020.mkv"], size=BIG)
    make_files(tmp_path, ["Movie.2020.nfo", "cover.jpg"], size=40_000)
    c = scan_content(tmp_path, MEDIA)
    assert c.subtitles == []


def test_missing_path_is_unknown_not_an_exception(tmp_path):
    c = scan_content(tmp_path / "gone", MEDIA)
    assert c.kind == "unknown"


def test_root_is_recorded(tmp_path):
    make_files(tmp_path, ["Movie.2020.mkv"], size=BIG)
    assert scan_content(tmp_path, MEDIA).root == tmp_path


# ---- found by the Task 14 dry run: `EPxx` episodes with a separate `Sxx` ----

def test_ep_marker_with_separate_season_marker(tmp_path):
    """庆余年 S02 files: `...S02.2024.Chinese.EP15...` must be S2E15, not Season 1."""
    root = tmp_path / "Joy.of.Life.S02.2024.Chinese.EP14-15.1080P.Disney.WEB-DL"
    for ep in (14, 15):
        make_file(root / f"庆余年.第二季.Joy.of.Life.S02.2024.Chinese.EP{ep}.1080P.Disney.WEB-DL.H265.DDP2.0-Vampire.mkv", BIG)
    content = scan_content(root, MediaConfig())
    assert content.kind == "tv"
    assert sorted((v.season, v.episode) for v in content.videos) == [(2, 14), (2, 15)]


def test_ep_marker_without_season_keeps_season_unknown(tmp_path):
    root = tmp_path / "Show.2020"
    make_file(root / "Show.2020.EP07.1080p.mkv", BIG)
    make_file(root / "Show.2020.EP08.1080p.mkv", BIG)
    content = scan_content(root, MediaConfig())
    assert content.kind == "tv"
    assert {(v.season, v.episode) for v in content.videos} == {(None, 7), (None, 8)}


def test_ep_inside_a_word_is_not_an_episode(tmp_path):
    root = tmp_path / "Deep.Blue.Sea.1999"
    make_file(root / "Deep.Blue.Sea.1999.Sleepless.EP.Cut.1080p.mkv", BIG)
    assert scan_content(root, MediaConfig()).kind == "movie"


# ---- review: the season of a file without Sxx comes from its folders --------

def test_bare_episode_takes_its_season_from_the_enclosing_folder(tmp_path):
    root = tmp_path / "Show.S02.2024.1080p.WEB-DL"
    make_file(root / "Show.EP15.mkv", BIG)
    make_file(root / "Show.EP16.mkv", BIG)
    assert {(v.season, v.episode) for v in scan_content(root, MEDIA).videos} == {(2, 15), (2, 16)}


def test_season_range_folder_names_no_season(tmp_path):
    root = tmp_path / "Show.S01-S03.Complete"
    make_file(root / "Show.E01.mkv", BIG)
    make_file(root / "Show.E02.mkv", BIG)
    assert {v.season for v in scan_content(root, MEDIA).videos} == {None}


def test_nearest_folder_season_wins(tmp_path):
    root = tmp_path / "Show.S01-S02.Pack"
    make_file(root / "Show.S02.Disc" / "Show.E03.mkv", BIG)
    make_file(root / "Show.S02.Disc" / "Show.E04.mkv", BIG)
    assert {v.season for v in scan_content(root, MEDIA).videos} == {2}
