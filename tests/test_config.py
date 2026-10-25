import textwrap
from pathlib import Path

import pytest

from flow.config import load_config

SRC = Path(__file__).resolve().parents[1] / "src"

MINIMAL_TOML = """
downloads_root = "/volume1/docker/qb/downloads"
staging_root = "/volume1/docker/qb/staging"
db_path = "/config/flow.db"
qb_url = "http://localhost:8080"
qb_username = "admin"
qb_password = "secret-pw"
tmdb_api_key = "secret-key"
"""


def write_toml(tmp_path: Path, body: str) -> Path:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(MINIMAL_TOML + textwrap.dedent(body))
    return cfg_file


def write_minimal(tmp_path: Path) -> Path:
    return write_toml(tmp_path, "")


def write_toml_without(tmp_path: Path, field: str) -> Path:
    kept = [ln for ln in MINIMAL_TOML.strip().splitlines() if not ln.startswith(field)]
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("\n".join(kept))
    return cfg_file


def test_load_config_reads_toml(tmp_path):
    cfg = load_config(write_minimal(tmp_path))
    assert cfg.staging_root == Path("/volume1/docker/qb/staging")
    assert cfg.container_downloads_prefix == "/downloads"
    assert cfg.score_accept == 0.80


def test_env_overrides_tmdb_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_TMDB_API_KEY", "from-env")
    cfg = load_config(write_minimal(tmp_path))
    assert cfg.tmdb_api_key == "from-env"


def test_env_overrides_qb_password(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_QB_PASSWORD", "pw-from-env")
    cfg = load_config(write_minimal(tmp_path))
    assert cfg.qb_password == "pw-from-env"


def test_missing_required_field_raises(tmp_path):
    with pytest.raises(ValueError, match="staging_root"):
        load_config(write_toml_without(tmp_path, "staging_root"))


def test_unknown_field_raises(tmp_path):
    with pytest.raises(ValueError, match="typo_field"):
        load_config(write_toml(tmp_path, '\ntypo_field = 1\n'))


def test_repr_does_not_leak_api_key(tmp_path):
    cfg = load_config(write_minimal(tmp_path))
    assert "secret" not in repr(cfg)


def test_layout_and_media_sections_override_defaults(tmp_path):
    cfg = load_config(
        write_toml(
            tmp_path,
            """
            [layout]
            movies_dirname = "电影"
            episode_filename = "{title}.{season}x{episode:02d}"

            [media]
            video_extensions = [".mkv"]
            """,
        )
    )
    assert cfg.layout.movies_dirname == "电影"
    assert cfg.layout.episode_filename == "{title}.{season}x{episode:02d}"
    assert cfg.media.video_extensions == (".mkv",)


def test_defaults_match_the_spec(tmp_path):
    cfg = load_config(write_minimal(tmp_path))
    assert cfg.layout.movies_dirname == "Movies" and cfg.layout.tv_dirname == "TV Shows"
    assert cfg.layout.season_dirname.format(season=1) == "Season 1"
    assert cfg.layout.episode_filename.format(title="黑镜", season=6, episode=1) == "黑镜 - S06E01"
    assert cfg.layout.specials_season == 0 and cfg.layout.default_season == 1
    assert cfg.media.companion_extensions == ()
    assert cfg.require_english_title is True


def test_no_hardcoded_paths_or_templates_outside_config():
    forbidden = ["/volume1", "/downloads", "TV Shows", "Season ", ".mkv", ".srt"]
    for py in (SRC / "flow").rglob("*.py"):
        if py.name == "config.py":
            continue
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{py.name} hardcodes {token!r}"
