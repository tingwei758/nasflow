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
    make_files(
        tmp_path,
        [f"Eight.Hours.Dont.Make.a.Day.1972.E0{n}.CC.BluRay.mkv" for n in range(1, 6)],
        size=3_000_000_000,
    )
    c = scan_content(tmp_path, MEDIA)
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
