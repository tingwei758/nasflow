"""Filesystem writes are confined to linker.py.

Downloads and staging share one read-write mount (hardlinks cannot cross bind
mounts), so the kernel no longer keeps nasflow away from the files qBittorrent is
seeding. These tests keep every write behind the one module that checks it.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "nasflow"

WRITE_METHODS = {
    "link", "unlink", "remove", "removedirs", "rename", "rmdir", "mkdir", "makedirs",
    "write_text", "write_bytes", "touch", "symlink_to", "hardlink_to", "symlink",
    "chmod", "chown", "truncate", "rmtree", "copy", "copy2", "copyfile", "copytree", "move",
}

# Module -> write calls it may make. linker.py is the confinement point; db.py
# creates the directory for its own database, which lives on the config volume.
ALLOWED = {
    "linker.py": WRITE_METHODS,
    "db.py": {"mkdir"},
}


def _write_calls(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any(n.split(".")[0] == "shutil" for n in names):
                found.append("shutil")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in WRITE_METHODS:
                found.append(func.attr)
            # os.replace, but not str.replace / dataclasses.replace
            if func.attr == "replace" and isinstance(func.value, ast.Name) and func.value.id == "os":
                found.append("os.replace")
        if isinstance(func, ast.Name) and func.id == "open":
            mode = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "mode"), None
            )
            reads = mode is None or (
                isinstance(mode, ast.Constant) and set(str(mode.value)) <= set("rbt")
            )
            if not reads:
                found.append("open(write)")
    return found


def test_writes_only_happen_in_allowed_modules():
    for py in SRC.rglob("*.py"):
        allowed = ALLOWED.get(py.name, set())
        for call in _write_calls(ast.parse(py.read_text(encoding="utf-8"))):
            assert call in allowed, f"{py.relative_to(SRC)} makes a filesystem write: {call}"


def test_guard_catches_the_usual_offenders():
    offenders = {
        "p.unlink()": "unlink",
        "import shutil": "shutil",
        "open(p, 'w')": "open(write)",
        "open(p)": None,
        "open(p, 'rb')": None,
        "os.replace(a, b)": "os.replace",
        "s.replace('a', 'b')": None,
        "p.write_text('x')": "write_text",
    }
    for source, expected in offenders.items():
        found = _write_calls(ast.parse(source))
        if expected is None:
            assert found == [], source
        else:
            assert expected in found, source
