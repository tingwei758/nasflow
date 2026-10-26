"""The container files encode two hard-won facts; these tests keep them true.

1. Hardlinks cannot cross bind mounts (EXDEV, verified), so downloads and
   staging must be reached through ONE mount, at the same path as on the host.
2. nasflow never touches the library, so /volume1/Media is never mounted.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
EXAMPLE = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))


def _list(key: str) -> list[str]:
    """Entries of the service's `<key>:` list, skipping comment lines."""
    lines = COMPOSE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"{key}:")
    entries = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if not stripped.startswith("- "):
            break
        entries.append(stripped[2:].split(" #")[0].strip().strip('"'))
    assert entries, f"no {key} found"
    return entries


def _shared_mount() -> str:
    mounts = [v for v in _list("volumes") if not v.split(":")[1] == "/config"]
    assert len(mounts) == 1, mounts
    return mounts[0]


def test_compose_needs_no_env_file():
    assert "${" not in COMPOSE
    assert not (ROOT / ".env.example").exists()


def test_library_is_never_mounted():
    assert "/volume1/Media" not in COMPOSE


def test_downloads_and_staging_share_one_mount_at_the_host_path():
    host, container = _shared_mount().split(":")[:2]
    assert host == container


def test_shared_mount_is_not_read_only():
    """`:ro` on either side of a hardlink makes os.link fail with EXDEV."""
    assert not _shared_mount().endswith(":ro")


def test_example_config_paths_live_inside_the_shared_mount():
    qb_root = Path(_shared_mount().split(":")[0])
    for key in ("downloads_root", "staging_root"):
        assert qb_root in Path(EXAMPLE[key]).parents, key


def test_config_folder_is_mounted_where_the_example_db_lives():
    assert Path(EXAMPLE["db_path"]).parent == Path("/config")
    assert any(v.split(":")[1] == "/config" for v in _list("volumes"))


def test_port_is_published_on_the_lan_address_only():
    (port,) = _list("ports")
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+:\d+", port)
    assert not port.startswith(("0.0.0.0", "127."))


def test_published_port_matches_the_config_port():
    (port,) = _list("ports")
    assert int(port.rsplit(":", 1)[1]) == EXAMPLE["bind_port"]


def test_runs_as_qbittorrent_file_owner():
    assert re.search(r'^\s*user: "911:911"', COMPOSE, flags=re.M)


def test_secrets_never_enter_the_image():
    for pattern in ("config.toml", "*.db", ".venv"):
        assert pattern in DOCKERIGNORE.split(), pattern
