import ast
import textwrap
from pathlib import Path

import pytest

from nasflow.config import load_config

SRC = Path(__file__).resolve().parents[1] / "src"

MINIMAL_TOML = """
downloads_root = "/volume1/docker/qb/downloads"
staging_root = "/volume1/docker/qb/staging"
db_path = "/config/nasflow.db"
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


def test_config_file_is_the_only_source_of_settings(tmp_path, monkeypatch):
    for var in ("NASFLOW_TMDB_API_KEY", "NASFLOW_QB_PASSWORD", "NASFLOW_QB_URL"):
        monkeypatch.setenv(var, "from-env")
    cfg = load_config(write_minimal(tmp_path))
    assert "from-env" not in (cfg.tmdb_api_key, cfg.qb_password, cfg.qb_url)


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


FORBIDDEN = ["/volume1", "/downloads", "TV Shows", "Season ", ".mkv", ".srt"]


def _code_string_literals(source: str) -> list[str]:
    """Every string literal in `source` that is not a docstring.

    Comments never reach the AST, so they are excluded for free. Docstrings are
    excluded deliberately: explaining `/downloads` in prose is documentation,
    while writing it in code is the thing the spec forbids.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_no_hardcoded_paths_or_templates_outside_config():
    for py in (SRC / "nasflow").rglob("*.py"):
        if py.name == "config.py":
            continue
        for literal in _code_string_literals(py.read_text(encoding="utf-8")):
            for token in FORBIDDEN:
                assert token not in literal, f"{py.name} hardcodes {token!r} in {literal!r}"


def test_the_guard_actually_catches_a_violation():
    """The guard is only worth having if it fails on a real offender."""
    offender = 'def f():\n    return "/volume1/docker/qb/staging"\n'
    literals = _code_string_literals(offender)
    assert any("/volume1" in lit for lit in literals)


def test_the_guard_ignores_docstrings_and_comments():
    documented = '"""Maps /downloads onto the host."""\n# also /volume1 here\nX = 1\n'
    assert _code_string_literals(documented) == []


def test_defaults_tuned_from_the_real_nas_dry_run():
    """0.10: on the 252 real downloads every top pick 0.10-0.14 ahead was right."""
    from nasflow.config import Config as C
    defaults = C(downloads_root=Path("/d"), staging_root=Path("/s"), db_path=Path("/db"),
                 qb_url="x", qb_username="u", qb_password="p", tmdb_api_key="k")
    assert defaults.score_gap == 0.10
    assert defaults.bind_port == 9987


# ---- review: staging and downloads must not overlap -------------------------

def _with_roots(tmp_path, downloads, staging):
    body = MINIMAL_TOML.replace(
        'downloads_root = "/volume1/docker/qb/downloads"', f'downloads_root = "{downloads}"'
    ).replace('staging_root = "/volume1/docker/qb/staging"', f'staging_root = "{staging}"')
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(body)
    return cfg_file


@pytest.mark.parametrize(
    "downloads,staging",
    [
        ("/volume1/docker/qb/downloads", "/volume1/docker/qb"),            # staging contains downloads
        ("/volume1/docker/qb", "/volume1/docker/qb/staging"),              # downloads contains staging
        ("/volume1/docker/qb/downloads", "/volume1/docker/qb/downloads"),  # the same folder
        ("/volume1/docker/qb/downloads", "/volume1/docker/qb/downloads/x/../"),
    ],
)
def test_overlapping_roots_are_refused(tmp_path, downloads, staging):
    with pytest.raises(ValueError, match="overlap"):
        load_config(_with_roots(tmp_path, downloads, staging))


def test_sibling_roots_with_a_shared_prefix_are_fine(tmp_path):
    cfg = load_config(_with_roots(tmp_path, "/volume1/docker/qb/down", "/volume1/docker/qb/downloads2"))
    assert cfg.staging_root.name == "downloads2"


def test_existing_torrents_are_skipped_on_first_run_by_default(tmp_path):
    assert load_config(write_minimal(tmp_path)).skip_existing_on_first_run is True


def test_health_settings_default_when_absent(tmp_path):
    cfg = load_config(write_minimal(tmp_path))
    assert (cfg.health_min_free_gb, cfg.health_tick_s) == (20, 30)


def test_health_settings_are_overridable(tmp_path):
    cfg = load_config(write_toml(tmp_path, """
        health_min_free_gb = 50
        health_tick_s = 5
    """))
    assert (cfg.health_min_free_gb, cfg.health_tick_s) == (50, 5)
