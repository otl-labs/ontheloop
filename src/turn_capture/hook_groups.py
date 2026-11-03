"""Hook-record rules for late and twinned SessionStart records (rules HG-1..HG-3). Pure, stdlib only.

Claude Code writes a late-loaded plugin's SessionStart hook records after the first turn, and writes each
stdout-printing hook run twice (a heldContent copy and a plain copy). Every verdict reads only the record and the
records before it in file order (envelope R7): the caller passes what it has already seen.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

RESUME_HOOK = "SessionStart:resume"
TURNLESS_HOOKS = ("SessionStart:startup", "SessionStart:clear", RESUME_HOOK)
MAX_DURATION_MS = 86_400_000
LATE_START_HOOKS = TURNLESS_HOOKS[:2]


def twin_key(att: dict[str, Any]) -> tuple[Any, ...] | None:
    """Rule HG-1: records sharing this key are one hook run; None (never a twin) without a string toolUseID."""
    use_id = att.get("toolUseID")
    if not isinstance(use_id, str):
        return None
    return (use_id, att.get("hookEvent"), att.get("hookName"), att.get("command"), att.get("durationMs"))


def is_late_session_start(name: str, prompt_seen: bool) -> bool:
    """Rule HG-2: a startup or clear hook record written after an own prompt record of the stream."""
    return prompt_seen and name.startswith(LATE_START_HOOKS)


def late_resume_start(ts: datetime | None, duration_ms: Any, latest_reply: datetime | None) -> bool:
    """Rule HG-3: the hook started before the latest prompt or assistant record (ts alone without a usable durationMs)."""
    if ts is None or latest_reply is None:
        return False
    usable = type(duration_ms) is int and 0 <= duration_ms <= MAX_DURATION_MS
    started = ts - timedelta(milliseconds=duration_ms) if usable else ts
    return started < latest_reply
