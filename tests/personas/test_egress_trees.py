"""egress_trees: pack, copy_tree, the refusing extract and the launcher's check-request."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import egress_trees as T


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / ".github").mkdir(parents=True)
    (root / ".claude").mkdir()
    (root / ".git").mkdir()
    (root / "pkg" / "__pycache__").mkdir(parents=True)
    (root / ".github" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (root / ".claude" / "x.md").write_text("x\n", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "pkg" / "a.pyc").write_bytes(b"\x00")
    (root / "pkg" / "__pycache__" / "a.cpython.pyc").write_bytes(b"\x00")
    (root / "bad\nname").write_text("x", encoding="utf-8")
    os.symlink(root / "pkg" / "a.py", root / "link.py")
    return root


def _members(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


WANT = {".github", ".github/ci.yml", ".claude", ".claude/x.md", "pkg", "pkg/a.py"}


def _tar(*members: tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for item in members:
            info, data = item if isinstance(item, tuple) else (item, b"")
            tar.addfile(info, io.BytesIO(data) if data else None)
    return buffer.getvalue()


def _file(name: str, data: bytes = b"x") -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _special(name: str, kind: bytes, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info


def _extract(data: bytes, dest: Path, length: int | None = None) -> None:
    T.extract(io.BytesIO(data), len(data) if length is None else length, dest)


def test_pack_then_extract_keeps_dotfiles_and_skips_git_and_links(tmp_path: Path) -> None:
    root = _source(tmp_path)
    out = tmp_path / "head.tar"
    members, skipped = T.pack(root, out)
    assert members == len(WANT) and skipped >= 5
    _extract(out.read_bytes(), tmp_path / "dest")
    assert _members(tmp_path / "dest") == WANT
    assert oct(os.stat(tmp_path / "dest" / "pkg" / "a.py").st_mode & 0o777) == "0o644"
    assert oct(os.stat(tmp_path / "dest" / "pkg").st_mode & 0o777) == "0o755"
    T.copy_tree(root, tmp_path / "copy")
    assert _members(tmp_path / "copy") == WANT
    assert oct(os.stat(tmp_path / "copy" / ".github" / "ci.yml").st_mode & 0o777) == "0o644"


@pytest.mark.parametrize(
    "members",
    [
        [_file("/abs")],
        [_file("../up")],
        [_file("a/../b")],
        [_file(".git/config")],
        [_file("x/.git/config")],
        [_file("ctl\x01name")],
        [(_special("l", tarfile.SYMTYPE, "/etc/passwd"))],
        [(_special("h", tarfile.LNKTYPE, "a"))],
        [(_special("d", tarfile.CHRTYPE))],
        [_file("dup"), _file("dup")],
    ],
)
def test_extract_refuses_bad_members(tmp_path: Path, members: list[Any]) -> None:
    dest = tmp_path / "dest"
    with pytest.raises(T.TreesRefused):
        _extract(_tar(*members), dest)
    assert not dest.exists()


def test_extract_refuses_caps_short_streams_and_an_existing_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _tar(_file("a"), _file("b"))
    monkeypatch.setattr(T, "TREE_MAX_MEMBERS", 1)
    with pytest.raises(T.TreesRefused, match="too many"):
        _extract(data, tmp_path / "m")
    monkeypatch.setattr(T, "TREE_MAX_MEMBERS", 100)
    monkeypatch.setattr(T, "TREE_MAX_BYTES", 1)
    with pytest.raises(T.TreesRefused):
        _extract(data, tmp_path / "b")
    monkeypatch.setattr(T, "TREE_MAX_BYTES", 1 << 30)
    with pytest.raises(T.TreesRefused, match="short"):
        _extract(data, tmp_path / "s", length=len(data) + 5)
    (tmp_path / "e").mkdir()
    with pytest.raises(T.TreesRefused, match="exists"):
        _extract(data, tmp_path / "e")
    for bad in (0, -1, True, T.TREE_MAX_BYTES + 1):
        with pytest.raises(T.TreesRefused):
            T.extract(io.BytesIO(data), bad, tmp_path / f"n{bad}")
    assert not any(p.name in ("m", "b", "s") for p in tmp_path.iterdir())


def test_extract_cli_reads_stdin(tmp_path: Path) -> None:
    data = _tar(_file("dir/file", b"hello"))
    script = Path(T.__file__)
    ok = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "extract", "head", str(len(data))],
        input=data,
        cwd=tmp_path,
        capture_output=True,
    )
    assert ok.returncode == 0 and (tmp_path / "head" / "dir" / "file").read_bytes() == b"hello"
    assert b"LAUNCHER-INFO head_tree_bytes=" in ok.stderr
    bad = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "extract", "head", str(len(data))],
        input=data,
        cwd=tmp_path,
        capture_output=True,
    )
    assert bad.returncode == 2 and b"TREES-REFUSED" in bad.stderr
    usage = subprocess.run([sys.executable, "-I", "-S", str(script), "nope"], capture_output=True)
    assert usage.returncode == 2


GOOD = [
    {"mode": "run"},
    {"mode": "run", "trees": ["main"]},
    {"mode": "run", "trees": ["head", "main"], "head_tree_bytes": 10},
    {"mode": "probe"},
]
BAD = [
    {"mode": "probe", "trees": ["head"], "head_tree_bytes": 1},
    {"mode": "probe", "head_tree_bytes": 1},
    {"mode": "run", "trees": []},
    {"mode": "run", "trees": "head"},
    {"mode": "run", "trees": [1]},
    {"mode": "run", "trees": ["other"]},
    {"mode": "run", "trees": ["main", "main"]},
    {"mode": "run", "trees": ["head"]},
    {"mode": "run", "trees": ["main"], "head_tree_bytes": 5},
    {"mode": "run", "head_tree_bytes": 5},
    {"mode": "run", "trees": ["head"], "head_tree_bytes": True},
    {"mode": "run", "trees": ["head"], "head_tree_bytes": "5"},
    {"mode": "run", "trees": ["head"], "head_tree_bytes": 1.5},
    {"mode": "run", "trees": ["head"], "head_tree_bytes": 0},
    {"mode": "run", "trees": ["head"], "head_tree_bytes": T.TREE_MAX_BYTES + 1},
    {"mode": "x"},
    [],
]


def test_check_request_refuses_bad_tree_fields() -> None:
    for good in GOOD:
        T.check_request(good)
    for bad in BAD:
        with pytest.raises(T.TreesRefused):
            T.check_request(bad)
    script = Path(T.__file__)
    for body, code in ((GOOD[2], 0), (BAD[2], 2)):
        done = subprocess.run(
            [sys.executable, "-I", "-S", str(script), "check-request"], input=json.dumps(body).encode(), capture_output=True
        )
        assert done.returncode == code
    garbage = subprocess.run([sys.executable, "-I", "-S", str(script), "check-request"], input=b"{", capture_output=True)
    assert garbage.returncode == 2
