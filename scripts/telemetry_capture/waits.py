"""The runner's event waits (rules RW-1..RW-4): when a pass may start, never what it emits.

Stdlib plus src.turn_capture.witness. A wait reads the root file once, then only its appended newline-terminated lines
every POLL_S, and parses nothing beyond the record type, the promptId and isMeta keys and the witness shape. Each wait
returns a fixed token; a timeout still lets the pass run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.turn_capture.witness import is_witness_record

POLL_S = 0.2
WITNESS_WAIT_S = 30.0
SETTLE_QUIET_S = 1.0
SETTLE_BOUND_S = 5.0
PROMPT_WAIT_S = 10.0
WITNESS_SEEN = "witness_seen"
WITNESS_TIMEOUT = "witness_wait_timeout"
SETTLE_TIMEOUT = "settle_timeout"
PROMPT_SEEN = "prompt_seen"
PROMPT_TIMEOUT = "prompt_wait_timeout"
Clock = Callable[[], float]
Sleep = Callable[[float], None]


class _Tail:
    """The root file's complete lines, read incrementally."""

    def __init__(self, path: Path) -> None:
        self.path, self.offset, self.partial = path, 0, b""

    def advance(self) -> tuple[list[dict[str, Any] | None], bool]:
        """(the newly completed lines parsed, None for a malformed one; whether any byte arrived)."""
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except OSError:
            return [], False
        self.offset += len(chunk)
        *lines, self.partial = (self.partial + chunk).split(b"\n")
        return [_parse(line) for line in lines], bool(chunk)


def _parse(line: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _is_prompt(data: dict[str, Any]) -> bool:
    return data.get("type") == "user" and isinstance(data.get("promptId"), str) and data.get("isMeta") is not True


def _settle(tail: _Tail, clock: Clock, sleep: Sleep) -> str:
    """Rule RW-1: the root unchanged for SETTLE_QUIET_S, bound SETTLE_BOUND_S."""
    start = quiet_since = clock()
    while True:
        sleep(POLL_S)
        _, grew = tail.advance()
        now = clock()
        quiet_since = now if grew else quiet_since
        if now - quiet_since >= SETTLE_QUIET_S:
            return WITNESS_SEEN
        if now - start >= SETTLE_BOUND_S:
            return SETTLE_TIMEOUT


def wait_for_witness(root_file: Path, clock: Clock, sleep: Sleep) -> str:
    """Rule RW-1: a witness later than both the root's last prompt record and its last assistant record, then a settle."""
    tail = _Tail(root_file)
    start = clock()
    index = last_prompt = last_assistant = last_witness = -1
    while True:
        records, _ = tail.advance()
        for data in records:
            index += 1
            if data is None:
                continue
            if is_witness_record(data):
                last_witness = index
            elif data.get("type") == "assistant":
                last_assistant = index
            elif _is_prompt(data):
                last_prompt = index
        if last_witness > max(last_prompt, last_assistant):
            return _settle(tail, clock, sleep)
        if clock() - start >= WITNESS_WAIT_S:
            return WITNESS_TIMEOUT
        sleep(POLL_S)


def wait_for_prompt(root_file: Path, prompt_id: str, clock: Clock, sleep: Sleep) -> str:
    """Rule RW-2: a root prompt record carrying *prompt_id*, bound PROMPT_WAIT_S."""
    tail = _Tail(root_file)
    start = clock()
    while True:
        records, _ = tail.advance()
        if any(data is not None and _is_prompt(data) and data["promptId"] == prompt_id for data in records):
            return PROMPT_SEEN
        if clock() - start >= PROMPT_WAIT_S:
            return PROMPT_TIMEOUT
        sleep(POLL_S)
