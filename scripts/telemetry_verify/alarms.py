"""The shared filing helper for telemetry verification alarms (alarm, not gate: Decision 210 cl.2, Decision 61).

Every verification finding class files or updates exactly one telemetry_verify rec through scripts.rec_episode.run_episode and
never closes it (an operator or a later plan closes a rec once its cause is fixed). Rec text carries counts, class names, the
scope label, parser_version and one fixed sentence only: never a row value, transcript content, session id or ref.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Collection, Mapping, MutableMapping

from scripts.rec_episode import run_episode

ALARM_SOURCE = "telemetry_verify"
ALARM_MIN_INTERVAL_S = 21_600
# Fixed wording, alone longer than the portal's 80-character context floor, so no class or scope can make a context too short.
_CONTEXT_NOTE = (
    "A telemetry verification check counted findings of this class in this scope. "
    "The check updates this rec while they recur and never closes it."
)


@dataclass
class AlarmOutcome:
    """Counts per outcome and the action taken for each class (a declared or rate-limited class is not an episode action)."""

    filed: int = 0
    updated: int = 0
    unchanged: int = 0
    rate_limited: int = 0
    declared: int = 0
    actions: dict[str, str] = field(default_factory=dict)


def _context(cls: str, count: int, scope: str, parser_version: int | None) -> str:
    text = f"finding_class={cls} count={count} scope={scope}"
    text = text if parser_version is None else f"{text} parser_version={parser_version}"
    return f"{text} {_CONTEXT_NOTE}"


def _matches(cls: str) -> Callable[[dict[str, Any]], bool]:
    token = re.compile(r"(?:^|[\s,])finding_class=" + re.escape(cls) + r"(?:[\s,]|$)")
    return lambda rec: token.search(rec.get("context") or "") is not None


def raise_alarms(
    findings: Mapping[str, int],
    *,
    scope: str,
    alarm_at: MutableMapping[str, datetime] | None,
    now: datetime,
    portal: Callable[[str, dict[str, Any]], Any] | None,
    file: str,
    acceptance_for: Callable[[str], str],
    declared: Collection[str] = (),
    parser_version: int | None = None,
    rows: list[dict[str, Any]] | None = None,
    reader: Any = None,
) -> AlarmOutcome:
    """File or update one rec per finding class with a positive count; declared classes are counted, never filed.

    *alarm_at* is the caller's own per-class map of last-call times: a class acted on under ALARM_MIN_INTERVAL_S ago is skipped
    and counted rate_limited (None disables the limit). A class that prefixes another never matches its neighbour's rec.
    """
    outcome = AlarmOutcome()
    for cls in sorted(findings):
        count = findings[cls]
        if count <= 0:
            continue
        if cls in declared:
            outcome.declared += 1
            continue
        last = None if alarm_at is None else alarm_at.get(cls)
        if last is not None and (now - last).total_seconds() < ALARM_MIN_INTERVAL_S:
            outcome.rate_limited += 1
            continue
        context = _context(cls, count, scope, parser_version)

        def build_fields(cls: str = cls, context: str = context) -> dict[str, Any]:
            return {
                "title": f"telemetry verification alarm: {cls}",
                "file": file,
                "status": "open",
                "source": ALARM_SOURCE,
                "priority": "High",
                "effort": "S",
                "risk": "low",
                "verification_tier": "V2",
                "context": context,
                "acceptance": acceptance_for(cls),
            }

        def build_update(existing: dict[str, Any], context: str = context) -> dict[str, Any] | None:
            return None if existing.get("context") == context else {"context": context}

        result = run_episode(
            source=ALARM_SOURCE,
            over_threshold=True,
            build_fields=build_fields,
            build_update=build_update,
            build_close=lambda existing: {},  # never called: over_threshold is always true, so an episode is never closed
            sub_key=_matches(cls),
            sub_key_fields=("context",),
            portal_caller=portal,
            rows=rows,
            reader=reader,
        )
        action = result["action"]
        outcome.actions[cls] = action
        if action == "file":
            outcome.filed += 1
        elif action == "update":
            outcome.updated += 1
        else:
            outcome.unchanged += 1
        if alarm_at is not None:
            alarm_at[cls] = now
    return outcome
