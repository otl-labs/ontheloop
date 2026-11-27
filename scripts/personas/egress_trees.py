"""Stdlib-only tree packer and extractor for the persona egress launcher (rec-5724).

Installed beside the root launcher, which runs it as a subprocess (extract as the unprivileged proxy user, check-request
as root); imported from the checkout by the floor (copy_tree) and by materialise (pack). Head-tree content is data: members
are never executed, only written as plain files by an extract that refuses anything but regular files and directories.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tarfile
from pathlib import Path
from typing import Any, BinaryIO

TREE_MAX_MEMBERS = 200000
TREE_MAX_BYTES = 1 << 30
TREE_NAMES = ("head", "main")
EXIT_REFUSED = 2
_SKIP_DIRS = {".git", "__pycache__"}
_CHUNK = 1 << 20


class TreesRefused(Exception):
    pass


def _bad_name(name: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in name)


def _skipped(name: str, mode: int) -> bool:
    if name in _SKIP_DIRS or name.endswith(".pyc") or _bad_name(name):
        return True
    return not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))


def walk(root: Path) -> tuple[list[tuple[str, int]], int]:
    """Sorted lstat walk: (relative path, mode) for kept directories and regular files, and the skipped count."""
    kept: list[tuple[str, int]] = []
    skipped = 0

    def visit(directory: Path, prefix: str) -> None:
        nonlocal skipped
        for name in sorted(os.listdir(directory)):
            mode = os.lstat(directory / name).st_mode
            if _skipped(name, mode):
                skipped += 1
                continue
            rel = f"{prefix}{name}"
            kept.append((rel, mode))
            if stat.S_ISDIR(mode):
                visit(directory / name, rel + "/")

    visit(Path(root), "")
    return kept, skipped


def _open_regular(path: Path) -> BinaryIO:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def pack(root: Path, out: Path) -> tuple[int, int]:
    """Write an uncompressed tar of the kept walk to out; returns (members, skipped)."""
    kept, skipped = walk(Path(root))
    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as tar:
        for rel, mode in kept:
            info = tarfile.TarInfo(rel)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if stat.S_ISDIR(mode):
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
                continue
            with _open_regular(Path(root) / rel) as handle:
                info.size = os.fstat(handle.fileno()).st_size
                info.mode = 0o644
                tar.addfile(info, handle)
    return len(kept), skipped


def copy_tree(src: Path, dest: Path) -> int:
    kept, _ = walk(Path(src))
    dest = Path(dest)
    dest.mkdir(mode=0o755)
    os.chmod(dest, 0o755)
    for rel, mode in kept:
        target = dest / rel
        if stat.S_ISDIR(mode):
            target.mkdir(mode=0o755)
            os.chmod(target, 0o755)
            continue
        with _open_regular(Path(src) / rel) as handle:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(handle, out, _CHUNK)
            os.chmod(target, 0o644)
    return len(kept)


class _Limited:
    def __init__(self, stream: BinaryIO, length: int) -> None:
        self.stream = stream
        self.remaining = length

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        want = self.remaining if size is None or size < 0 else min(size, self.remaining)
        data = self.stream.read(want)
        if not data:
            raise TreesRefused("short stream")
        self.remaining -= len(data)
        return data


def _member_ok(member: tarfile.TarInfo, seen: set[str]) -> str:
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
        raise TreesRefused("member is not a regular file or directory")
    name = member.name.rstrip("/") if member.isdir() else member.name
    parts = name.split("/")
    if name.startswith("/") or not name or any(p in ("", ".", "..") for p in parts):
        raise TreesRefused("member name is absolute or has a dot part")
    if ".git" in parts:
        raise TreesRefused("member has a .git component")
    if _bad_name(name) or "\\" in name:
        raise TreesRefused("member name has a control character")
    if name in seen:
        raise TreesRefused("duplicate member")
    seen.add(name)
    return name


def _extract_into(stream: BinaryIO, length: int, dest: Path) -> None:
    limited = _Limited(stream, length)
    seen: set[str] = set()
    unpacked = 0
    try:
        tar = tarfile.open(fileobj=limited, mode="r|")  # type: ignore[call-overload]
        for member in tar:
            if len(seen) >= TREE_MAX_MEMBERS:
                raise TreesRefused("too many members")
            name = _member_ok(member, seen)
            target = dest / name
            if member.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                os.chmod(target, 0o755)
                continue
            unpacked += member.size
            if unpacked > TREE_MAX_BYTES:
                raise TreesRefused("unpacked bytes over the cap")
            os.makedirs(target.parent, mode=0o755, exist_ok=True)
            source = tar.extractfile(member)
            assert source is not None
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(source, out, _CHUNK)
            os.chmod(target, 0o644)
        while limited.remaining:
            limited.read(_CHUNK)
    except tarfile.TarError as exc:
        raise TreesRefused(f"bad tar ({type(exc).__name__})") from exc
    except OSError as exc:
        raise TreesRefused(f"write failed ({type(exc).__name__})") from exc


def free_bytes(path: str = ".") -> int:
    info = os.statvfs(path)
    return info.f_bavail * info.f_frsize


def extract(stream: BinaryIO, length: int, dest: str | Path) -> None:
    """Read exactly length tar bytes from stream into dest; on any refusal dest is removed and TreesRefused is raised."""
    dest = Path(dest)
    if os.path.lexists(dest):
        raise TreesRefused("destination exists")
    if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= TREE_MAX_BYTES:
        raise TreesRefused("length out of range")
    os.mkdir(dest, 0o755)
    os.chmod(dest, 0o755)
    try:
        _extract_into(stream, length, dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def check_main(path: str) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        raise TreesRefused("main tree") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise TreesRefused("main tree")


def check_request(data: Any) -> None:
    if not isinstance(data, dict) or data.get("mode") not in ("probe", "run"):
        raise TreesRefused("check input is malformed")
    trees, size = data.get("trees"), data.get("head_tree_bytes")
    if data["mode"] == "probe":
        if "trees" in data or "head_tree_bytes" in data:
            raise TreesRefused("probe mode takes no trees")
        return
    if "trees" not in data:
        if "head_tree_bytes" in data:
            raise TreesRefused("head_tree_bytes without head")
        return
    if not isinstance(trees, list) or not trees:
        raise TreesRefused("trees is not a non-empty list")
    if any(not isinstance(t, str) or t not in TREE_NAMES for t in trees):
        raise TreesRefused("unknown tree name")
    if len(set(trees)) != len(trees):
        raise TreesRefused("repeated tree name")
    if "head" not in trees:
        if "head_tree_bytes" in data:
            raise TreesRefused("head_tree_bytes without head")
        return
    if "head_tree_bytes" not in data:
        raise TreesRefused("head needs head_tree_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= TREE_MAX_BYTES:
        raise TreesRefused("head_tree_bytes out of range")


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[0] == "extract":
            extract(sys.stdin.buffer, int(argv[2]), argv[1])
            print(f"LAUNCHER-INFO head_tree_bytes={argv[2]} run_free_bytes={free_bytes()}", file=sys.stderr)
            return 0
        if len(argv) == 2 and argv[0] == "check-main":
            check_main(argv[1])
            return 0
        if argv == ["check-request"]:
            try:
                data = json.loads(sys.stdin.buffer.read(1 << 16).decode("utf-8"))
            except ValueError as exc:
                raise TreesRefused("check input is not JSON") from exc
            check_request(data)
            return 0
    except (TreesRefused, ValueError) as exc:
        print(f"TREES-REFUSED {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print("usage: egress_trees.py extract DEST LENGTH | check-request", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
