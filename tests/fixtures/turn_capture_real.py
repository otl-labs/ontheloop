"""Real-transcript helpers shared by the two integration modules (no test_ prefix; stdlib only).

discover(), snapshot() and PrefixTree are the conformance module's originals, moved verbatim. raw_inherited() reads a
root file independently of src.turn_capture: the lines a fork copied (forkedFrom, or before the first attachment of the
LAST SessionStart:fork hook group) and the fork boundary (1 + that attachment's line index, 0 without a fork).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from src.turn_capture.transcript import ROOT, FsTree


def discover() -> list[Path]:
    override = os.environ.get("TURN_CAPTURE_REAL_TRANSCRIPTS")
    if not override:
        return sorted(Path.home().glob(".claude/projects/*/*.jsonl"))
    found: list[Path] = []
    for item in override.split(os.pathsep):
        path = Path(item).expanduser()
        found += sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    return found


def snapshot(paths: list[Path], into: Path) -> list[Path]:
    out = []
    for i, src in enumerate(paths):
        dest = into / f"t{i}"
        dest.mkdir(parents=True)
        shutil.copy2(src, dest / src.name)
        session_dir = src.parent / src.stem
        if session_dir.is_dir():
            shutil.copytree(session_dir, dest / src.stem)
        out.append(dest / src.name)
    return out


class PrefixTree(FsTree):
    def __init__(self, root_file: Path, limit: int) -> None:
        super().__init__(root_file)
        self.limit = limit

    def read(self, key: str) -> bytes | None:
        data = super().read(key)
        if key != ROOT or data is None:
            return data
        return b"".join(line + b"\n" for line in data.split(b"\n")[: self.limit])


def raw_inherited(path: Path) -> tuple[set[int], int]:
    """(inherited line indexes, fork boundary) of a ROOT file; a sub-agent file inherits nothing."""
    if path.name.startswith("agent-"):
        return set(), 0
    forked: set[int] = set()
    first_attachment, in_group = None, False
    for index, raw in enumerate(path.read_bytes().split(b"\n")[:-1]):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if "forkedFrom" in data:
            forked.add(index)
        attachment = data.get("attachment")
        if data.get("type") in ("user", "assistant"):
            in_group = False
        elif isinstance(attachment, dict) and str(attachment.get("hookName") or "").startswith("SessionStart:fork"):
            if not in_group:
                first_attachment = index
            in_group = True
    before = set(range(first_attachment)) if first_attachment is not None else set()
    return forked | before, 0 if first_attachment is None else first_attachment + 1
