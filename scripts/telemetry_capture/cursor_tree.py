"""The transcript tree as a capture cursor consumed it, and the full emission set the current producer builds from it.

CursorTree is a TranscriptTree view: each stream reads as its first cursor.lines_consumed lines, an absent key is no
stream, and a sidecar reads as the cursor pinned it (pinned absent: absent; pinned present: the current bytes, or
SidecarMutated when the sha no longer matches). expected_rows is the producer's own parser-bump path over that view, so the
comparison holds by the producer's incremental-equals-full property (record_turn.py's R7 docstring). Imports the standard
library and src.turn_capture only.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterator, Sequence

from src.turn_capture.cursor import CaptureCursor
from src.turn_capture.record_turn import PARSER_VERSION, CaptureResult, record_turn
from src.turn_capture.refs import model_call_ref
from src.turn_capture.transcript import TranscriptTree


class SidecarMutated(Exception):
    """A sidecar the cursor pinned present has changed or vanished: the view cannot reproduce what the cursor saw."""


class CursorTree:
    def __init__(self, tree: TranscriptTree, cursor: CaptureCursor) -> None:
        self._tree, self._cursor = tree, cursor
        self.session_id = tree.session_id

    def agent_ids(self) -> list[str]:
        return [key for key in self._tree.agent_ids() if key in self._cursor.lines_consumed]

    def read(self, key: str) -> bytes | None:
        wanted = self._cursor.lines_consumed.get(key)
        data = self._tree.read(key) if wanted is not None else None
        if data is None:
            return None
        return b"".join(line + b"\n" for line in data.split(b"\n")[:-1][:wanted])

    def _pinned(self, basename: str) -> tuple[bool, str | None]:
        pins = self._cursor.sidecars
        return basename in pins, pins.get(basename)

    def sidecar(self, basename: str) -> bytes | None:
        pinned, sha = self._pinned(basename)
        if pinned and sha is None:
            return None
        data = self._tree.sidecar(basename)
        if pinned and (data is None or hashlib.sha256(data).hexdigest() != sha):
            raise SidecarMutated(basename)
        return data

    def sidecar_size(self, basename: str) -> int | None:
        pinned, sha = self._pinned(basename)
        if pinned and sha is None:
            return None
        size = self._tree.sidecar_size(basename)
        if pinned and size is None:
            raise SidecarMutated(basename)
        return size

    def sidecar_chunks(self, basename: str) -> Iterator[bytes]:
        pinned, sha = self._pinned(basename)
        if pinned and sha is None:
            return
        hasher = hashlib.sha256()
        for chunk in self._tree.sidecar_chunks(basename):
            hasher.update(chunk)
            yield chunk
        if pinned and hasher.hexdigest() != sha:
            raise SidecarMutated(basename)


def expected_rows(view: TranscriptTree, cursor: CaptureCursor, *, session_final: bool | None = None) -> CaptureResult:
    """The full emission set at the cursor: record_turn's documented parser-bump path (every row of every cut is rebuilt)."""
    return record_turn(
        view,
        dataclasses.replace(cursor, parser_version=PARSER_VERSION + 1),
        project_ref=cursor.project_ref,
        session_final=cursor.finalized if session_final is None else session_final,
    )


def call_refs(message_ids: Sequence[str]) -> list[str]:
    return [model_call_ref(message_id) for message_id in message_ids]
