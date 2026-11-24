"""Capacity helpers for the daily verification monitor (PLAN-monitor-read-budget, rec-5385, rec-5420).

Pure and standard library only. The monitor reads every non-persona (Claude Code) tree and, past a declared floor of persona
(litellm) trees, counts the unread rest as litellm_sampled_out instead of coverage incomplete (Decision 235).
"""

from __future__ import annotations

import hashlib
import math
import threading
from typing import Any, Mapping, Sequence, TypeVar

MONITOR_WORKERS = 2
LITELLM_FLOOR = 60
LITELLM_ROOT_PREFIX = "litellm:"
SLOW_CALL_S = 10

T = TypeVar("T", bound=Mapping[str, Any])


def is_litellm(root: Mapping[str, Any]) -> bool:
    return str(root.get("entity_ref") or "").startswith(LITELLM_ROOT_PREFIX)


def _hash_key(root: Mapping[str, Any]) -> str:
    return hashlib.sha256(str(root["session_id"]).encode("utf-8")).hexdigest()


def read_order(quiet: Sequence[T]) -> list[T]:
    """Non-litellm roots in the given order, then litellm roots in a stable session-id hash order."""
    plain = [r for r in quiet if not is_litellm(r)]
    return plain + sorted((r for r in quiet if is_litellm(r)), key=_hash_key)


def litellm_read(statuses: Sequence[str], litellm_flags: Sequence[bool]) -> int:
    """Litellm trees compared; moved or collided trees are not clean samples."""
    return sum(1 for s, flag in zip(statuses, litellm_flags) if flag and s == "compared")


def sampled_out(statuses: Sequence[str], litellm_flags: Sequence[bool], floor: int) -> int:
    """Litellm trees refused by the budget that are excused: no non-litellm tree went unread by budget and at least
    min(floor, litellm trees to read) litellm trees were compared."""
    if any(s == "budget" and not flag for s, flag in zip(statuses, litellm_flags)):
        return 0
    if litellm_read(statuses, litellm_flags) < min(floor, sum(1 for flag in litellm_flags if flag)):
        return 0
    return sum(1 for s, flag in zip(statuses, litellm_flags) if flag and s == "budget")


class CallStats:
    """Lock-guarded call seconds of every reader call (the calls reader_calls counts)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seconds: list[float] = []

    def record(self, seconds: float) -> None:
        with self._lock:
            self._seconds.append(seconds)

    @property
    def calls_over_10s(self) -> int:
        with self._lock:
            return sum(1 for s in self._seconds if s > SLOW_CALL_S)

    @property
    def call_p95_s(self) -> int:
        with self._lock:
            if not self._seconds:
                return 0
            ordered = sorted(self._seconds)
            return int(math.ceil(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]))
