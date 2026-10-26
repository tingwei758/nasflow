from pathlib import Path

import pytest

from flow.layout import PlannedLink
from flow.linker import LinkConflict, OutsideStaging, execute_links, prune_empty_dirs


def make_source(tmp_path: Path, name: str = "src.mkv", data: bytes = b"payload") -> Path:
    src = tmp_path / "downloads" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return src


def test_creates_hardlink_not_copy(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "staging/Movies/X (2020)/X (2020).mkv"
    execute_links([PlannedLink(src, dest)], tmp_path / "staging")
    assert dest.stat().st_ino == src.stat().st_ino


def test_creates_missing_parent_dirs(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "staging/a/b/c/X.mkv"
    execute_links([PlannedLink(src, dest)], tmp_path / "staging")
    assert dest.exists()


def test_existing_identical_link_is_noop(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "staging/X.mkv"
    link = PlannedLink(src, dest)
    execute_links([link], tmp_path / "staging")
    execute_links([link], tmp_path / "staging")
    assert dest.stat().st_ino == src.stat().st_ino


def test_existing_different_file_raises_conflict(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "staging/X.mkv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"other")
    with pytest.raises(LinkConflict):
        execute_links([PlannedLink(src, dest)], tmp_path / "staging")


def test_conflict_leaves_no_partial_links(tmp_path):
    src_a = make_source(tmp_path, "a.mkv")
    src_b = make_source(tmp_path, "b.mkv")
    good = PlannedLink(src_a, tmp_path / "staging/good.mkv")
    blocker = tmp_path / "staging/bad.mkv"
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"occupied")
    with pytest.raises(LinkConflict):
        execute_links([good, PlannedLink(src_b, blocker)], tmp_path / "staging")
    assert not good.dest.exists()


def test_conflict_does_not_remove_preexisting_file(tmp_path):
    src = make_source(tmp_path)
    blocker = tmp_path / "staging/bad.mkv"
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"occupied")
    with pytest.raises(LinkConflict):
        execute_links([PlannedLink(src, blocker)], tmp_path / "staging")
    assert blocker.read_bytes() == b"occupied"


def test_source_is_never_modified(tmp_path):
    src = make_source(tmp_path)
    before = src.stat()
    execute_links([PlannedLink(src, tmp_path / "staging/X.mkv")], tmp_path / "staging")
    after = src.stat()
    assert src.exists() and after.st_mtime == before.st_mtime
    assert src.read_bytes() == b"payload"


def test_missing_source_raises_conflict_not_filenotfound(tmp_path):
    missing = tmp_path / "downloads" / "gone.mkv"
    with pytest.raises(LinkConflict):
        execute_links([PlannedLink(missing, tmp_path / "staging/X.mkv")], tmp_path / "staging")


def test_prune_removes_empty_dirs_only(tmp_path):
    staging = tmp_path / "staging"
    (staging / "Movies/X (2011)").mkdir(parents=True)
    keep = staging / "Movies/Y (2012)/y.mkv"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"x")
    removed = prune_empty_dirs(staging)
    assert staging / "Movies/X (2011)" in removed
    assert keep.exists()
    assert staging.exists()


def test_prune_removes_nested_empty_dirs(tmp_path):
    staging = tmp_path / "staging"
    (staging / "TV Shows/Show (2020)/Season 1").mkdir(parents=True)
    prune_empty_dirs(staging)
    assert not (staging / "TV Shows/Show (2020)").exists()
    assert staging.exists()


def test_prune_never_deletes_files(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    lone = staging / "loose.mkv"
    lone.write_bytes(b"x")
    prune_empty_dirs(staging)
    assert lone.exists()


def test_prune_on_missing_root_is_noop(tmp_path):
    assert prune_empty_dirs(tmp_path / "nope") == []


# ---- confinement: downloads are mounted read-write, so the code must hold the line


def test_refuses_destination_outside_staging(tmp_path):
    src = make_source(tmp_path)
    (tmp_path / "staging").mkdir()
    escape = tmp_path / "downloads/evil.mkv"
    with pytest.raises(OutsideStaging):
        execute_links([PlannedLink(src, escape)], tmp_path / "staging")
    assert not escape.exists()


def test_refuses_dotdot_escape(tmp_path):
    src = make_source(tmp_path)
    (tmp_path / "staging").mkdir()
    escape = tmp_path / "staging/../downloads/evil.mkv"
    with pytest.raises(OutsideStaging):
        execute_links([PlannedLink(src, escape)], tmp_path / "staging")
    assert not (tmp_path / "downloads/evil.mkv").exists()


def test_refuses_escape_through_a_symlink_inside_staging(tmp_path):
    src = make_source(tmp_path)
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging/sneaky").symlink_to(tmp_path / "downloads")
    with pytest.raises(OutsideStaging):
        execute_links([PlannedLink(src, tmp_path / "staging/sneaky/evil.mkv")], tmp_path / "staging")
    assert not (tmp_path / "downloads/evil.mkv").exists()


def test_refuses_the_staging_root_itself_as_a_destination(tmp_path):
    src = make_source(tmp_path)
    (tmp_path / "staging").mkdir()
    with pytest.raises(OutsideStaging):
        execute_links([PlannedLink(src, tmp_path / "staging")], tmp_path / "staging")


def test_one_escaping_link_blocks_the_whole_batch_before_any_write(tmp_path):
    src = make_source(tmp_path)
    good = PlannedLink(src, tmp_path / "staging/Movies/X/X.mkv")
    bad = PlannedLink(src, tmp_path / "elsewhere/X.mkv")
    with pytest.raises(OutsideStaging):
        execute_links([good, bad], tmp_path / "staging")
    assert not (tmp_path / "staging/Movies").exists()


def test_outside_staging_is_a_link_conflict(tmp_path):
    """So the pipeline stops the item for a human instead of crashing."""
    assert issubclass(OutsideStaging, LinkConflict)


def test_prefix_sibling_is_not_inside(tmp_path):
    """/x/staging2 must not count as inside /x/staging."""
    src = make_source(tmp_path)
    (tmp_path / "staging").mkdir()
    with pytest.raises(OutsideStaging):
        execute_links([PlannedLink(src, tmp_path / "staging2/X.mkv")], tmp_path / "staging")
