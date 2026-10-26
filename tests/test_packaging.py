"""The container files encode two hard-won facts; these tests keep them true.

1. Hardlinks cannot cross bind mounts (EXDEV, verified), so downloads and
   staging must be reached through ONE mount, at the same path as on the host.
2. flow never touches the library, so /volume1/Media is never mounted.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")


def _env() -> dict[str, str]:
    pairs = re.findall(r"^([A-Z_]+)=(.*)$", ENV_EXAMPLE, flags=re.M)
    return {k: v.strip() for k, v in pairs}


def _volumes() -> list[str]:
    """Entries of the service's `volumes:` list, skipping comment lines."""
    lines = COMPOSE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "volumes:")
    entries = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if not stripped.startswith("- "):
            break
        entries.append(stripped[2:].split(" #")[0].strip().strip('"'))
    assert entries, "no volumes found"
    return entries


def test_library_is_never_mounted():
    for text in (COMPOSE, ENV_EXAMPLE):
        assert "/volume1/Media" not in text


def test_downloads_and_staging_share_one_mount_at_the_host_path():
    shared = [v for v in _volumes() if v.startswith("${FLOW_QB_ROOT")]
    assert len(shared) == 1
    host, container = shared[0].split(":")[:2]
    assert host == container == "${FLOW_QB_ROOT}"


def test_shared_mount_is_not_read_only():
    """`:ro` on either side of a hardlink makes os.link fail with EXDEV."""
    shared = next(v for v in _volumes() if v.startswith("${FLOW_QB_ROOT"))
    assert not shared.endswith(":ro")


def test_example_config_paths_live_inside_the_shared_mount():
    cfg = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    qb_root = Path(_env()["FLOW_QB_ROOT"])
    for key in ("downloads_root", "staging_root"):
        assert qb_root in Path(cfg[key]).parents, key


def test_port_is_published_on_the_lan_address_only():
    assert re.search(r'"\$\{FLOW_LAN_IP[^}]*\}:\$\{FLOW_PORT[^}]*\}:\d+"', COMPOSE)
    assert "0.0.0.0" not in COMPOSE


def test_runs_as_qbittorrent_file_owner_by_default():
    assert '${FLOW_PUID:-911}:${FLOW_PGID:-911}' in COMPOSE


def test_secrets_never_enter_the_image():
    for pattern in ("config.toml", ".env", "*.db", ".venv"):
        assert pattern in DOCKERIGNORE.split(), pattern


def test_env_example_has_no_secrets():
    env = _env()
    assert not any("KEY" in k or "PASSWORD" in k for k in env if env[k] not in ("", "change-me"))


def test_published_port_matches_the_config_port():
    cfg = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
    container_port = re.search(r'\$\{FLOW_PORT[^}]*\}:(\d+)"', COMPOSE).group(1)
    assert int(container_port) == cfg["bind_port"] == int(_env()["FLOW_PORT"])
