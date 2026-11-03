"""Claude Code transcript source access (stdlib json/pathlib).

Tree layout: <sessionId>.jsonl, <sessionId>/subagents/agent-<agentId>.jsonl,
<sessionId>/tool-results/<basename>. A line is a record iff newline-terminated and json.loads yields a dict;
a trailing partial line is NOT consumed; a terminated non-object line is malformed (counted, consumed, never
a row source). Sidecars resolve only as <session dir>/tool-results/<basename(persistedOutputPath)> and are read
either whole (sidecar) or as 64 KiB chunks (sidecar_chunks), so a sidecar above the cap is never materialised.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol

from src.telemetry.timestamps import TimestampError, parse_iso8601_utc

ROOT = "root"
SIDECAR_CHUNK_BYTES = 65536


class TranscriptTree(Protocol):
    session_id: str

    def agent_ids(self) -> list[str]: ...

    def read(self, key: str) -> bytes | None: ...

    def sidecar(self, basename: str) -> bytes | None: ...

    def sidecar_size(self, basename: str) -> int | None: ...

    def sidecar_chunks(self, basename: str) -> Iterator[bytes]: ...


class FsTree:
    def __init__(self, root_file: Path) -> None:
        self.root_file = root_file
        self.session_id = root_file.stem
        self._dir = root_file.parent / root_file.stem

    def agent_ids(self) -> list[str]:
        found = (self._dir / "subagents").glob("agent-*.jsonl")
        return sorted(p.stem[len("agent-") :] for p in found)

    def read(self, key: str) -> bytes | None:
        path = self.root_file if key == ROOT else self._dir / "subagents" / f"agent-{key}.jsonl"
        return path.read_bytes() if path.is_file() else None

    def sidecar(self, basename: str) -> bytes | None:
        path = self._dir / "tool-results" / basename
        return path.read_bytes() if path.is_file() else None

    def sidecar_size(self, basename: str) -> int | None:
        path = self._dir / "tool-results" / basename
        return path.stat().st_size if path.is_file() else None

    def sidecar_chunks(self, basename: str) -> Iterator[bytes]:
        path = self._dir / "tool-results" / basename
        if not path.is_file():
            return
        with path.open("rb") as handle:
            while chunk := handle.read(SIDECAR_CHUNK_BYTES):
                yield chunk


class MemTree:
    """In-memory twin of FsTree: files maps a relative path to its text body."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        roots = [p for p in files if "/" not in p and p.endswith(".jsonl")]
        if len(roots) != 1:
            raise ValueError("a tree needs exactly one root .jsonl file")
        self.session_id = roots[0][: -len(".jsonl")]

    def agent_ids(self) -> list[str]:
        prefix = f"{self.session_id}/subagents/agent-"
        return sorted(p[len(prefix) : -len(".jsonl")] for p in self.files if p.startswith(prefix) and p.endswith(".jsonl"))

    def read(self, key: str) -> bytes | None:
        rel = f"{self.session_id}.jsonl" if key == ROOT else f"{self.session_id}/subagents/agent-{key}.jsonl"
        body = self.files.get(rel)
        return None if body is None else body.encode("utf-8")

    def _sidecar_bytes(self, basename: str) -> bytes | None:
        body = self.files.get(f"{self.session_id}/tool-results/{basename}")
        return None if body is None else body.encode("utf-8")

    def sidecar(self, basename: str) -> bytes | None:
        return self._sidecar_bytes(basename)

    def sidecar_size(self, basename: str) -> int | None:
        data = self._sidecar_bytes(basename)
        return None if data is None else len(data)

    def sidecar_chunks(self, basename: str) -> Iterator[bytes]:
        data = self._sidecar_bytes(basename) or b""
        for start in range(0, len(data), SIDECAR_CHUNK_BYTES):
            yield data[start : start + SIDECAR_CHUNK_BYTES]


@dataclass(frozen=True)
class RawLine:
    line: int
    data: dict | None


def read_lines(data: bytes, limit: int | None = None) -> list[RawLine]:
    """Every newline-terminated line (up to *limit* of them) as a record or a malformed marker."""
    out: list[RawLine] = []
    for index, raw in enumerate(data.split(b"\n")[:-1]):
        if limit is not None and index >= limit:
            break
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        out.append(RawLine(index, parsed if isinstance(parsed, dict) else None))
    return out


def record_time(data: dict) -> datetime | None:
    stamp = data.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return parse_iso8601_utc(stamp)
    except TimestampError:
        return None


FORK_HOOK = "SessionStart:fork"
PIN_SCRIPT_STEM = "session_start_log_turn"
PIN_FLAG = "--project-ref="
_SCRIPT_TOKEN = re.compile(r"[^\s]+\.(py|sh)$")
_PIN_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")


def _hook_attachment(data: dict) -> dict | None:
    att = data.get("attachment")
    if data.get("type") == "attachment" and isinstance(att, dict) and str(att.get("type") or "").startswith("hook_"):
        return att
    return None


def inherited_lines(lines: list[RawLine]) -> set[int]:
    """Line indexes of the root records a fork copied (rule K-1), over exactly the lines given (prefix purity).

    A record is inherited iff it carries forkedFrom (/branch), or it lies before the first attachment of the LAST
    SessionStart:fork hook group (--fork-session). A group ends at the next user or assistant record.
    """
    boundary, in_group = None, False
    out: set[int] = set()
    for item in lines:
        data = item.data
        if data is None:
            continue
        if "forkedFrom" in data:
            out.add(item.line)
        if data.get("type") in ("user", "assistant"):
            in_group = False
            continue
        att = _hook_attachment(data)
        if att is not None and str(att.get("hookName") or "").startswith(FORK_HOOK) and not in_group:
            in_group, boundary = True, item.line
    if boundary is not None:
        out.update(item.line for item in lines if item.line < boundary)
    return out


def session_pin(lines: list[RawLine]) -> datetime | None:
    """The EARLIEST parseable timestamp among the root file's own records (inherited fork copies take no part).

    Not the first record in file order: SessionStart hook records are written after an earlier queue-operation record
    yet carry earlier timestamps.
    """
    skip = inherited_lines(lines)
    times = [
        when
        for item in lines
        if item.data is not None and item.line not in skip and (when := record_time(item.data)) is not None
    ]
    return min(times) if times else None


def hook_script(command: object) -> tuple[str | None, list[str]]:
    """(the first .py/.sh token's stem, all tokens) of a hook command, quotes stripped from each token (rule H-1)."""
    tokens = [t.strip("'\"") for t in command.split()] if isinstance(command, str) else []
    for token in tokens:
        if _SCRIPT_TOKEN.match(token):
            return posixpath.splitext(posixpath.basename(token))[0], tokens
    return None, tokens


def _unquoted(value: str) -> str:
    """hook_script strips token edges, so a quoted flag value arrives with its opening quote still attached (rec-4308)."""
    return value[1:] if value[:1] in ("'", '"') else value


def project_ref_pin(tree: TranscriptTree) -> str | None:
    """The project_ref the earliest SessionStart capture-hook attachment recorded, else None (rules P-1..P-4)."""
    lines = read_lines(tree.read(ROOT) or b"")
    skip = inherited_lines(lines)
    for item in lines:
        att = _hook_attachment(item.data) if item.data is not None and item.line not in skip else None
        if att is None or att.get("hookEvent") != "SessionStart":
            continue
        stem, tokens = hook_script(att.get("command"))
        refs = [_unquoted(t[len(PIN_FLAG) :]) for t in tokens if t.startswith(PIN_FLAG)]
        if stem == PIN_SCRIPT_STEM and len(refs) == 1 and _PIN_REF.fullmatch(refs[0]):
            return refs[0]
    return None
