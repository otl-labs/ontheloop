"""Process probes shared by the process-group tests (tests/checks/test__process_group.py and
tests/checks/verification/test__vp_replay_budget.py). Not a test module: no name starts with test_."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

PID_RECORD = re.compile(r"[0-9]+\n")


def read_published_pid(pidfile: Path, timeout: float = 5.0) -> int:
    """The PID a shell published with ``echo $! > pidfile``, once fully written.

    The redirection creates the file before echo writes to it, so existence alone can expose an
    empty file (rec-4182); echo's trailing newline is what marks the record complete."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            text = pidfile.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        if PID_RECORD.fullmatch(text):
            return int(text)
        if time.monotonic() >= deadline:
            pytest.fail(f"{pidfile} never held a complete PID record within {timeout}s (last read {text!r})")
        time.sleep(0.05)


def pid_is_dead(pid: int) -> bool:
    """True iff /proc/<pid>/stat is absent, or reports zombie state 'Z' -- never
    ProcessLookupError alone, which measures reaping rather than death (plan-critique measured
    1.46-1.72s of zombie reap latency in this container)."""
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return True
    close_paren = text.rfind(")")
    state = text[close_paren + 2] if close_paren != -1 else ""
    return state == "Z"


def pid_state(pid: int) -> str:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return "gone"
    close_paren = text.rfind(")")
    return text[close_paren + 2] if close_paren != -1 else ""
