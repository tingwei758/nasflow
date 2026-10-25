from pathlib import Path

import pytest

from flow.pathmap import to_container_path, to_host_path

PFX = "/downloads"
ROOT = Path("/volume1/docker/qb/downloads")
P = "/downloads/Foo/Bar.mkv"


def test_container_path_maps_to_host():
    assert to_host_path("/downloads/Foo.mkv", PFX, ROOT) == ROOT / "Foo.mkv"


def test_prefix_is_configurable():
    assert to_host_path("/data/t/Foo.mkv", "/data/t", Path("/mnt/dl")) == Path("/mnt/dl/Foo.mkv")


def test_nested_path_preserved():
    assert to_host_path(P, PFX, ROOT) == ROOT / "Foo" / "Bar.mkv"


def test_prefix_itself_maps_to_root():
    assert to_host_path(PFX, PFX, ROOT) == ROOT


def test_trailing_slash_on_prefix_tolerated():
    assert to_host_path("/downloads/Foo.mkv", "/downloads/", ROOT) == ROOT / "Foo.mkv"


def test_path_outside_prefix_raises():
    with pytest.raises(ValueError):
        to_host_path("/elsewhere/Foo.mkv", PFX, ROOT)


def test_sibling_prefix_is_not_a_match():
    """/downloads2 must not be treated as living under /downloads."""
    with pytest.raises(ValueError):
        to_host_path("/downloads2/Foo.mkv", PFX, ROOT)


def test_roundtrip():
    assert to_container_path(to_host_path(P, PFX, ROOT), PFX, ROOT) == P
