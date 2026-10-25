"""Real release names from the NAS, copied verbatim from the spec.

The `￡` separators, full-width characters, and the apostrophe in
`Director's.Cut` all matter — do not "clean these up".

Each entry is (release_name, expected_fields). Only the fields listed are
asserted, so an entry can pin `title_zh` without committing to a `season`.
"""

# fmt: off
CORPUS: list[tuple[str, dict]] = [
    (
        "长安的荔枝.The.Litchi.Road.S01.2025.2160p.WEB-DL.H265.AAC-HHWEB",
        {"title_en": "The Litchi Road", "title_zh": "长安的荔枝", "year": 2025, "season": 1},
    ),
    (
        "闪电侠.The.Flash.2023.BluRay.1080p.x265.10bit.DDP7.1.MNHD-FRDS",
        {"title_en": "The Flash", "title_zh": "闪电侠", "year": 2023, "season": None},
    ),
    (
        "[雪国列车(剧版) 第一季].Snowpiercer.2020.S01.1080p.NF.WEB-DL.H264.DDP5.1-PTerWEB",
        {"title_en": "Snowpiercer", "title_zh": "雪国列车", "year": 2020, "season": 1},
    ),
    (
        "[雪国列车(剧版) 第二季].Snowpiercer.2021.S02.1080p.NF.WEB-DL.H264.DDP5.1-PTerWEB",
        {"title_en": "Snowpiercer", "title_zh": "雪国列车", "year": 2021, "season": 2},
    ),
    (
        "[霍元甲]Fearless.Version.Director's.Cut.2006.CHN.Blu-ray.1080p.x264.DTS-HD.MA.7.1-BtsHD.mkv",
        {"title_en": "Fearless", "title_zh": "霍元甲", "year": 2006, "season": None},
    ),
    (
        "[飞驰人生2].Pegasus.2.2024.2160p.60fps.V2.WEB-DL.HEVC.10bit.DTS5.1.6Audios-QHstudIo",
        {"title_en": "Pegasus 2", "title_zh": "飞驰人生2", "year": 2024, "season": None},
    ),
    (
        "飞驰人生3.Pegasus.3.2026.2160p.WEB-DL.H265.HDR.DDP5.1.2Audios-HHWEB",
        {"title_en": "Pegasus 3", "title_zh": "飞驰人生3", "year": 2026, "season": None},
    ),
    (
        "飞驰人生.Pegasus.2019.BluRay.1080p.x265.10bit.MNHD-FRDS",
        {"title_en": "Pegasus", "title_zh": "飞驰人生", "year": 2019, "season": None},
    ),
    (
        "[首尔之春].12.12.The.Day.2023.HKG.BluRay.1080p.x264.DDP5.1-CMCT",
        {"title_zh": "首尔之春", "year": 2023, "season": None},
    ),
    (
        "黑镜.Black.Mirror.S06.2023.1080p.NF.WEB-DL.H264.DDP5.1.Atmos-OurTV",
        {"title_en": "Black Mirror", "title_zh": "黑镜", "year": 2023, "season": 6},
    ),
    (
        "[黑镜].Black.Mirror.S07.2025.1080p.NF.WEB-DL.x264.DDP.5.1.Atmos-CMCTV",
        {"title_en": "Black Mirror", "title_zh": "黑镜", "year": 2025, "season": 7},
    ),
    (
        "黑镜S01-S05.Black.Mirror.2011-2019.1080p.WEB-DL.x265.AC3￡cXcY@FRDS",
        {"title_en": "Black Mirror", "title_zh": "黑镜"},
    ),
    (
        "龙门飞甲.2011.HKG.1080p.国粤双语.简繁中字￡CMCT星辰",
        {"title_en": None, "title_zh": "龙门飞甲", "year": 2011, "season": None},
    ),
    (
        "12 Monkeys S01 EUR BluRay 1080p DTS-HD MA 5.1 x265.10bit-CHD",
        {"title_en": "12 Monkeys", "title_zh": None, "season": 1},
    ),
    (
        "A Quiet Place.2018.UHD.BluRay.2160p.Atmos.TrueHD.7.1.HDR.x265.10bit-CHD",
        {"title_en": "A Quiet Place", "title_zh": None, "year": 2018, "season": None},
    ),
    (
        "El.Camino.a.Breaking.Bad.Movie.2019.2160p.NF.WEB-DL.DDP5.1.Atmos.HDR.HEVC-HHWEB",
        {"title_zh": None, "year": 2019, "season": None},
    ),
    (
        "Eight.Hours.Dont.Make.a.Day.1972.E01-E05.CC.BluRay.1080p.DTS-HD.MA.1.0.x265.10bit-BeiTai",
        {"title_en": "Eight Hours Dont Make a Day", "title_zh": None, "year": 1972},
    ),
    (
        "CE教学实例应用-由浅入深学习游戏修改 [全十课]",
        {"title_en": None},
    ),
    (
        "[黑亚当]Black.Adam.2022.Blu-ray.1080p.x264.Atmos.TrueHD.7.1-BtsHD.mkv",
        {"title_en": "Black Adam", "title_zh": "黑亚当", "year": 2022, "season": None},
    ),
    (
        "3.Body.Problem.S01.2024.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-HHWEB",
        {"title_en": "3 Body Problem", "title_zh": None, "year": 2024, "season": 1},
    ),
    (
        "Arcane.S02.2021.1080p.NF.WEB-DL.DDP5.1.Atmos.DV.H.265-HHWEB",
        {"title_en": "Arcane", "title_zh": None, "year": 2021, "season": 2},
    ),
    (
        "喜剧之王单口季.King.of.Comedy.S03.2024.1080p.WEB-DL.H.264.AAC2.0-HHWEB",
        {"title_en": "King of Comedy", "title_zh": "喜剧之王单口季", "year": 2024, "season": 3},
    ),
    (
        "Silo.S03.2160p.ATVP.WEB-DL.HDR.H.265.DDP5.1.Atmos-ADWeb",
        {"title_en": "Silo", "title_zh": None, "season": 3},
    ),
    (
        # guessit reads a lone "X2" as a bonus-feature marker and returns no
        # title at all; the leading-token fallback has to recover it.
        "X2.2003.2160p.DSNP.WEB-DL.DDP5.1.HDR.HEVC-HHWEB",
        {"title_en": "X2", "title_zh": None, "year": 2003, "season": None},
    ),
]
# fmt: on
