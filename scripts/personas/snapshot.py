"""The fetch verb and the snapshot read helper: the head tree as untrusted data (rec-5041 part 1).

The head tarball is extracted into $PERSONA_SNAPSHOT with tarfile's data filter, refusing (not skipping) any member
that is not a regular file or directory, or whose name is absolute or leaves the tree. Nothing here imports or runs
anything from the snapshot. Stdlib only.
"""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath

from scripts.personas import resolve_pr

STATE = Path("prepared") / "snapshot.json"


class SnapshotRefused(Exception):
    pass


def _clean(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SnapshotRefused(f"member {name!r} is absolute or leaves the tree")
    return path


def extract(data: bytes, dest: Path) -> int:
    """Extract a GitHub tarball (one top-level directory) into dest; return the member count. Raises SnapshotRefused."""
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as exc:
        raise SnapshotRefused(f"unreadable tarball: {exc}") from exc
    with tar:
        members = tar.getmembers()
        for member in members:
            if not (member.isreg() or member.isdir()):
                raise SnapshotRefused(f"member {member.name!r} is not a regular file or directory")
            _clean(member.name)
        for member in members:
            parts = _clean(member.name).parts[1:]
            if not parts:
                continue
            member.name = str(PurePosixPath(*parts))
            tar.extract(member, path=dest, filter="data")
            count += 1
    return count


def read_bytes(root: Path, relpath: str) -> bytes | None:
    """The file's bytes, None when absent; a non-regular file or a symlinked path component is refused (lstat)."""
    _clean(relpath)
    current = root
    parts = PurePosixPath(relpath).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(mode):
            raise SnapshotRefused(f"{relpath} passes through a symlink")
        last = index == len(parts) - 1
        if last and not stat.S_ISREG(mode):
            raise SnapshotRefused(f"{relpath} is not a regular file")
        if not last and not stat.S_ISDIR(mode):
            raise SnapshotRefused(f"{relpath} passes through a non-directory")
    return current.read_bytes()


def snapshot_dir() -> Path:
    value = os.environ.get("PERSONA_SNAPSHOT")
    if not value:
        raise RuntimeError("PERSONA_SNAPSHOT is not set")
    return Path(value)


def main(argv: list[str]) -> int:
    dest = snapshot_dir()
    resolution = json.loads(resolve_pr.RESOLUTION.read_text(encoding="utf-8"))
    status, raw = resolve_pr.request(
        "GET",
        f"/repos/{os.environ['GITHUB_REPOSITORY']}/tarball/{resolution['head_sha']}",
        os.environ.get("GH_TOKEN"),
        accept="application/vnd.github+json",
    )
    state: dict[str, object]
    if status != 200:
        state = {"ok": False, "reason": f"tarball fetch returned {status}"}
    else:
        try:
            extract(raw, dest)
            state = {"ok": True, "reason": ""}
        except SnapshotRefused as exc:
            state = {"ok": False, "reason": str(exc)}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    print(f"PERSONA-VERDICT snapshot ok={str(state['ok']).lower()}")
    return 0
