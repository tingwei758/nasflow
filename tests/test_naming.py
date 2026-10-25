import pytest

from flow.naming import parse_release_name

from .corpus import CORPUS


def assert_fields(parsed, expected: dict):
    for field, want in expected.items():
        got = getattr(parsed, field)
        assert got == want, f"{field}: expected {want!r}, got {got!r}"


@pytest.mark.parametrize("name,expected", CORPUS, ids=[c[0][:40] for c in CORPUS])
def test_corpus_parses(name, expected):
    assert_fields(parse_release_name(name), expected)


def test_strips_bracketed_chinese_prefix_with_spaces():
    p = parse_release_name(
        "[雪国列车(剧版) 第一季].Snowpiercer.2020.S01.1080p.NF.WEB-DL.H264.DDP5.1-PTerWEB"
    )
    assert p.title_en == "Snowpiercer"
    assert p.title_zh == "雪国列车"
    assert p.season == 1 and p.year == 2020


def test_chinese_only_name_has_no_english_title():
    p = parse_release_name("龙门飞甲.2011.HKG.1080p.国粤双语.简繁中字￡CMCT星辰")
    assert p.title_en is None
    assert p.title_zh == "龙门飞甲"
    assert p.year == 2011


def test_year_after_season():
    p = parse_release_name("长安的荔枝.The.Litchi.Road.S01.2025.2160p.WEB-DL.H265.AAC-HHWEB")
    assert p.title_en == "The Litchi Road" and p.season == 1 and p.year == 2025


def test_group_suffix_with_pound_sign_is_not_title():
    p = parse_release_name("黑镜S01-S05.Black.Mirror.2011-2019.1080p.WEB-DL.x265.AC3￡cXcY@FRDS")
    assert p.title_en == "Black Mirror"
    assert p.title_zh == "黑镜"


def test_region_tag_not_mistaken_for_title_part():
    p = parse_release_name("[首尔之春].12.12.The.Day.2023.HKG.BluRay.1080p.x264.DDP5.1-CMCT")
    assert p.year == 2023 and p.title_zh == "首尔之春"


def test_source_name_is_preserved_verbatim():
    name = "飞驰人生.Pegasus.2019.BluRay.1080p.x265.10bit.MNHD-FRDS"
    assert parse_release_name(name).source_name == name


def test_file_extension_stripped_before_parsing():
    p = parse_release_name("[黑亚当]Black.Adam.2022.Blu-ray.1080p.x264.Atmos.TrueHD.7.1-BtsHD.mkv")
    assert p.title_en == "Black Adam" and p.year == 2022


def test_trailing_season_qualifier_dropped_from_chinese_title():
    p = parse_release_name(
        "[雪国列车(剧版) 第二季].Snowpiercer.2021.S02.1080p.NF.WEB-DL.H264.DDP5.1-PTerWEB"
    )
    assert p.title_zh == "雪国列车"


def test_pure_chinese_non_media_name_yields_no_english_title():
    p = parse_release_name("CE教学实例应用-由浅入深学习游戏修改 [全十课]")
    assert p.title_en is None


def test_parsing_never_raises_on_odd_input():
    for odd in ("", ".", "￡", "[]", "1080p", "....mkv"):
        parse_release_name(odd)


def test_title_recovered_when_guessit_returns_none():
    p = parse_release_name("X2.2003.2160p.DSNP.WEB-DL.DDP5.1.HDR.HEVC-HHWEB")
    assert p.title_en == "X2" and p.year == 2003


def test_every_real_download_name_yields_some_title():
    """Regression guard: a name flow cannot title at all can never be matched."""
    for name, _ in CORPUS:
        p = parse_release_name(name)
        if "CE教学实例应用" in name:
            continue  # genuinely not media; no title expected
        assert p.title_en or p.title_zh, f"no title of either kind for {name!r}"
