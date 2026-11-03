"""When the in-session source check is due, and the fingerprint of the code whose rows it compares.

Imports only the standard library and src.turn_capture.transcript, so it can never break a capture pass's imports. Pure apart
from producer_fingerprint's file reads. The check is an alarm, never a gate (Decision 210 cl.2): nothing here decides whether a
pass sends, only whether one more process runs after it.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import src.turn_capture.transcript as transcript

VERIFY_FIRST_SENT_PASS = 10
VERIFY_EVERY_SENT_PASSES = 50
MAX_CHECKS_PER_TREE_PER_DAY = 3
VERIFY_RETRY_S = 1800
VERIFY_RETRY_MAX_S = 21600
VERIFY_GUARD_S = 1500
EVENTS = ("Stop", "StopFailure")
OUTCOMES = ("sent", "no_rows")
FINGERPRINTED_TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")
_FIELD_SEMANTICS = Path("config") / "lambda" / "ducklake" / "field_semantics.yaml"


def utc_day(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def verify_due(
    cursor: Any,
    root_bytes: bytes,
    verify_state: Mapping[str, Any],
    *,
    event: str,
    final: bool,
    outcome: str,
    now: float,
) -> bool:
    """True when a check should run after this pass; *verify_state* already holds this pass's sent_passes advance."""
    if cursor is None or event not in EVENTS or final or outcome not in OUTCOMES:
        return False
    if now < (verify_state.get("next_attempt_at") or 0):
        return False
    if (verify_state.get("checks_by_day") or {}).get(utc_day(now), 0) >= MAX_CHECKS_PER_TREE_PER_DAY:
        return False
    if (verify_state.get("scheduled_at") or 0) > (verify_state.get("settled_at") or 0):
        return True
    if _new_cost_state_record(cursor, root_bytes, verify_state):
        return True
    passes = verify_state.get("sent_passes") or 0
    return outcome == "sent" and (passes == VERIFY_FIRST_SENT_PASS or (passes > 0 and passes % VERIFY_EVERY_SENT_PASSES == 0))


def _new_cost_state_record(cursor: Any, root_bytes: bytes, verify_state: Mapping[str, Any]) -> bool:
    """A cost-state record in the consumed root, not inherited, at a file line above both the covered and the held line."""
    if b'"cost-state"' not in root_bytes:
        return False
    lines = transcript.read_lines(root_bytes, cursor.lines_consumed.get(transcript.ROOT, 0))
    floor = max(verify_state.get("last_ledger_line", -1), verify_state.get("held_ledger_line", -1))
    skip = transcript.inherited_lines(lines)
    return any(
        item.data is not None and item.data.get("type") == "cost-state" and item.line > floor and item.line not in skip
        for item in lines
    )


def _repo_root() -> Path:
    return Path(transcript.__file__).resolve().parents[2]


def _semantics_blocks(text: str) -> bytes:
    """The four ops_tables.telemetry_* blocks, each from its two-space key line to the next two-space or top-level key,
    skipping every accepted_values list (registry-sourced, never a comparison input)."""
    kept: list[str] = []
    in_ops = in_block = False
    skipping: int | None = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if line and indent == 0:
            in_ops, in_block, skipping = line.rstrip() == "ops_tables:", False, None
            continue
        if not in_ops:
            continue
        stripped = line.strip()
        if indent == 2 and stripped.endswith(":"):
            in_block, skipping = stripped[:-1] in FINGERPRINTED_TABLES, None
        if not in_block:
            continue
        if skipping is not None:
            if stripped.startswith("- ") and indent >= skipping:
                continue
            skipping = None
        if stripped.startswith("accepted_values:"):
            skipping = indent if stripped == "accepted_values:" else None
            continue
        kept.append(line)
    return "\n".join(kept).encode("utf-8")


@functools.lru_cache(maxsize=1)
def producer_fingerprint() -> str:
    """sha256 over the code that emits a tree's rows and derives its keys and the telemetry tables' comparison column roles."""
    root = _repo_root()
    digest = hashlib.sha256()
    paths = sorted((root / "src" / "turn_capture").glob("*.py")) + [
        root / "src" / "telemetry" / "timestamps.py",
        root / "src" / "telemetry" / "identity.py",
    ]
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<absent>")
        digest.update(b"\0")
    try:
        digest.update(_semantics_blocks((root / _FIELD_SEMANTICS).read_text(encoding="utf-8")))
    except OSError:
        digest.update(b"<absent>")
    return digest.hexdigest()
