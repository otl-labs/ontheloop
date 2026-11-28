"""The text-free per-finding record a verdict check run carries, and the code-built TLDR line (rec-5742; stdlib only).

The record is one check-text line '<!-- persona-verdict-findings v1 {...} -->'. It holds ids, anchors and enum values only,
never a title, summary or any model text; '<' and '>' are JSON-escaped so the line cannot close its comment. The TLDR line
prints only integers, a route id and values copied from docs/contracts/telemetry_persona_findings.yaml's accepted values.
"""

from __future__ import annotations

import json
import re
from typing import Any

LINE_PREFIX = "<!-- persona-verdict-findings v1 "
LINE_SUFFIX = " -->"
MAX_FINDINGS = 200
MAX_LINE_BYTES = 16000
SAFE = re.compile(r"[A-Za-z0-9_./:#\[\]-]{1,200}")
ROUTE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)?")
REDACTED = "[redacted]"
FREE_KEYS = ("id", "anchor", "target", "item")
VERDICTS = ("PROCEED", "REVISE", "NO_FLAGS", "FLAGS_FOUND", "BLOCK")
SEVERITIES = ("Critical", "High", "Medium", "Low", "blocking", "nit", "BLOCK", "WARN", "NOTE", "CONFLICT", "CITE", "RELATED")
ORIGINS = ("mechanical", "judgement")
RUN_OUTCOMES = ("verdict", "persona_failure", "host_failure", "unreadable")
CONCLUSIONS = ("success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required")
RECORD_KEYS = (
    "route",
    "head_sha",
    "run_id",
    "run_attempt",
    "run_ref",
    "session_started_at",
    "definition_sha256",
    "served_models",
    "served_model",
    "run_outcome",
    "failure_id",
    "result",
    "findings_total",
    "truncated",
    "redacted",
)
_LINE = re.compile(re.escape(LINE_PREFIX) + r"(\{.*\})" + re.escape(LINE_SUFFIX) + r"\s*$")


def _item_lists(result: dict[str, Any]) -> list[list[Any]]:
    """The result's finding lists in document order: top-level lists and roadmap.rows."""
    lists: list[list[Any]] = []
    for value in result.values():
        if isinstance(value, list):
            lists.append(value)
        elif isinstance(value, dict):
            lists += [v for v in value.values() if isinstance(v, list)]
    return lists


def _clean(result: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """A deep copy of result with every id and anchor string that is not a safe token redacted, and the redaction count."""
    redacted = 0

    def item(value: Any) -> Any:
        nonlocal redacted
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        for key, val in value.items():
            if key in FREE_KEYS and isinstance(val, str) and not SAFE.fullmatch(val):
                out[key] = REDACTED
                redacted += 1
            else:
                out[key] = val
        return out

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [item(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return walk(result), redacted


def _escape(text: str) -> str:
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


def _line(record: dict[str, Any]) -> str:
    return LINE_PREFIX + _escape(json.dumps(record, sort_keys=True, separators=(",", ":"))) + LINE_SUFFIX


def _drop_last(result: dict[str, Any]) -> bool:
    for found in reversed(_item_lists(result)):
        if found:
            found.pop()
            return True
    return False


def render(facts: dict[str, Any], result: dict[str, Any] | None) -> str:
    """The findings line for the record facts and the restricted result (None when the run produced none)."""
    body: dict[str, Any] = {k: facts.get(k) for k in RECORD_KEYS}
    cleaned, redacted = _clean(result) if result is not None else (None, 0)
    total = sum(len(lst) for lst in _item_lists(cleaned)) if cleaned is not None else 0
    body.update(result=cleaned, findings_total=total, truncated=False, redacted=redacted)
    while cleaned is not None:
        kept = sum(len(lst) for lst in _item_lists(cleaned))
        line = _line(body)
        if kept <= MAX_FINDINGS and len(line.encode("utf-8")) <= MAX_LINE_BYTES:
            return line
        body["truncated"] = True
        if not _drop_last(cleaned):
            break
    return _line(body)


def line_of(text: str | None) -> str | None:
    """The last findings line of text, verbatim, when it holds a valid record."""
    for line in reversed(str(text or "").splitlines()):
        if _LINE.match(line):
            return line if parse(line) is not None else None
    return None


def parse(text: str | None) -> dict[str, Any] | None:
    """The record of the last findings line in text, else None."""
    for line in reversed(str(text or "").splitlines()):
        match = _LINE.match(line)
        if match:
            try:
                record = json.loads(match.group(1))
            except ValueError:
                return None
            return record if isinstance(record, dict) and isinstance(record.get("route"), str) else None
    return None


def _counts(record: dict[str, Any], key: str, order: tuple[str, ...]) -> list[str]:
    result = record.get("result")
    counts = dict.fromkeys(order, 0)
    for found in _item_lists(result) if isinstance(result, dict) else []:
        for entry in found:
            value = entry.get(key) if isinstance(entry, dict) else None
            if key == "severity" and value is None and isinstance(entry, dict):
                value = entry.get("lane")
            if value in counts:
                counts[value] += 1
    return [f" {name}={n}" for name, n in counts.items() if n]


def tldr_line(route: str, conclusion: str, record: dict[str, Any] | None, url: str) -> str:
    """The code-built TLDR line for one route; every printed value is an integer, a route id or an accepted value."""
    head = f"persona-verdict/{route} ({conclusion if conclusion in CONCLUSIONS else 'unknown'})"
    result = record.get("result") if isinstance(record, dict) else None
    outcome = record.get("run_outcome") if isinstance(record, dict) else None
    total = record.get("findings_total") if isinstance(record, dict) else None
    if record is None or not (ROUTE.fullmatch(route) and outcome in RUN_OUTCOMES and type(total) is int and total >= 0):
        return f"{head}: no findings record {url}"
    verdict = result.get("verdict") if isinstance(result, dict) else None
    word = verdict if verdict in VERDICTS else outcome
    tail = "".join(_counts(record, "severity", SEVERITIES) + _counts(record, "origin", ORIGINS))
    return f"{head}: {word} {total} findings{tail} {url}"
