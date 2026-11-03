"""model_call usage and request columns (rules US-1..US-4). Pure: reads only the response's owner record and, for a sync
sub-agent's last first-turn call, the parent's spawn-result usage; every value satisfies its column's write rule or is
NULL and counted."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from src.turn_capture.streams import Response

EFFORT_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
_EFFORT = re.compile(EFFORT_PATTERN)
_USAGE_KEYS = (
    ("tokens_input", "input_tokens"),
    ("tokens_output", "output_tokens"),
    ("tokens_cache_read", "cache_read_input_tokens"),
    ("tokens_cache_creation", "cache_creation_input_tokens"),
)
_INPUT_CLASSES = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_SPLIT_KEYS = (
    ("cache_creation_1h_tokens", "ephemeral_1h_input_tokens"),
    ("cache_creation_5m_tokens", "ephemeral_5m_input_tokens"),
)


def _int_or_zero(value: Any, diag: Counter[str]) -> int:
    if type(value) is int:
        return value
    if value is not None:
        diag["non_int_usage"] += 1
    return 0


def _thinking_tokens(usage: dict[str, Any]) -> int | None:
    details = usage.get("output_tokens_details")
    if isinstance(details, dict) and type(details.get("thinking_tokens")) is int:
        return details["thinking_tokens"]
    return None


def _split(usage: dict[str, Any], total: int, diag: Counter[str]) -> dict[str, int | None]:
    """US-1: the 1h/5m cache-write split; a value above tokens_cache_creation is NULL (the column's at_most rule)."""
    raw = usage.get("cache_creation")
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, int | None] = {}
    for col, key in _SPLIT_KEYS:
        value = raw.get(key)
        if type(value) is not int:
            out[col] = None
        elif value > total:
            diag["cache_split_exceeds_total"] += 1
            out[col] = None
        else:
            out[col] = value
    return out


def _corrected(usage: dict[str, Any], spawn_usage: dict[str, Any], diag: Counter[str]) -> bool:
    """US-3: the spawn result's usage replaces the record's only on an exact match of the three input classes."""
    if all(type(spawn_usage.get(k)) is int and spawn_usage.get(k) == usage.get(k) for k in _INPUT_CLASSES):
        diag["spawn_result_corrected"] += 1
        return True
    diag["spawn_result_mismatch"] += 1
    return False


def usage_columns(
    resp: Response, spawn_usage: dict[str, Any] | None, diag: Counter[str], *, subagent: bool = False
) -> dict[str, Any]:
    owner = resp.owner
    message = owner.data["message"]
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    if not isinstance(message.get("usage"), dict):
        diag["absent_usage"] += 1
    if absent := sum(1 for _, key in _USAGE_KEYS if key not in usage):
        diag["absent_usage_key"] += absent
    final = isinstance(message.get("stop_reason"), str)
    source = "record"
    if spawn_usage is not None and not final and owner.data.get("isAbortedMidStream") is not True:
        if _corrected(usage, spawn_usage, diag):
            usage, source, final = spawn_usage, "spawn_result", True
    if not final:
        diag["usage_not_final:subagent" if subagent else "usage_not_final:root"] += 1
    tokens = {col: _int_or_zero(usage.get(key), diag) for col, key in _USAGE_KEYS}
    requested = owner.data.get("requestedModel")
    effort = owner.data.get("effort")
    if effort is not None and not (isinstance(effort, str) and _EFFORT.fullmatch(effort)):
        diag["effort_unparsed"] += 1
        effort = None
    return {
        **tokens,
        "reasoning_tokens": _thinking_tokens(usage),
        **_split(usage, tokens["tokens_cache_creation"], diag),
        "requested_model": requested if isinstance(requested, str) and requested else None,
        "reasoning_effort": effort,
        "usage_final": final,
        "usage_source": source,
    }
