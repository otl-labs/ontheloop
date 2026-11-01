"""Vector data for the --lambda-telemetry-smoke append gate (the twin; T2.36, slice 2a-2). Data and pure builders only: it
imports nothing from src.telemetry (the gate must not depend on the code it proves), and it does no I/O.

A golden four-table payload in wire form for one session under a given project_ref, the negative cases as (name,
mutation, expected status, error_type, expected field), and the size builders: a request whose invoke measure is exactly
the body cap, one just over it, a 2000-row request, and an omission row. Shared by the live gate and its hermetic test.

The invoke measure is len(json.dumps(S)) with S the exact body string sent (S = json.dumps(payload) in
core._sigv4_invoke): Lambda's 6 MiB limit applies to the invocation event, in which the body is re-escaped as a JSON string.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

PRODUCER = "deploy_smoke_gate"
PARSER_VERSION = 1
INLINE_THRESHOLD_BYTES = 65536
MAX_INVOKE_BODY_BYTES = 5 * 1024 * 1024
MAX_ROWS_PER_TABLE = 2000
OVER_CAP_TARGET = 5_600_000
_DENSE_CHUNK = '{"a":"\\\\x","b":["<",">","&"]},'
_FULL_ROW_CONTENT = 60000
_ROW_OVERHEAD = 1500


def wire_ts(moment: datetime) -> str:
    """The wire timestamp grammar: UTC with exactly three fraction digits."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def measure(body: dict[str, Any]) -> int:
    """The invoke measure of a request body: len(json.dumps(S)), S the body string the gate sends."""
    return len(json.dumps(json.dumps(body)))


def dense_content(size: int) -> str:
    """ASCII content full of quotes, backslashes and the characters <, > and &, of exactly `size` bytes."""
    return (_DENSE_CHUNK * (size // len(_DENSE_CHUNK) + 1))[:size]


def _common(index: int, started: datetime) -> dict[str, Any]:
    return {
        "event_timestamp": wire_ts(started + timedelta(milliseconds=index + 1)),
        "session_started_at": wire_ts(started),
        "producer": PRODUCER,
        "producer_version": "1",
        "parser_version": PARSER_VERSION,
    }


def session_ref(tag: str) -> str:
    return f"smoke-session-{tag}"


def transcript_row(tag: str, index: int, started: datetime, content: str) -> dict[str, Any]:
    data = content.encode("utf-8")
    return {
        **_common(index, started),
        "event_kind": "point",
        "external_ref": f"{tag}#t{index}",
        "entity_ref": f"{tag}#t{index}",
        "session_ref": session_ref(tag),
        "observation_ref": f"{tag}#obs0",
        "purpose": "prompt",
        "origin": "human",
        "content": content,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "content_bytes": len(data),
        "content_truncated": False,
    }


def observation_row(tag: str, index: int, started: datetime) -> dict[str, Any]:
    return {
        **_common(index, started),
        "event_kind": "point",
        "external_ref": f"{tag}#obs{index}",
        "entity_ref": f"{tag}#obs{index}",
        "session_ref": session_ref(tag),
        "observation_type": "turn",
    }


def status_observation_row(tag: str, index: int, started: datetime) -> dict[str, Any]:
    return {
        **observation_row(tag, index, started),
        "observation_type": "process_event",
        "name": "status:pc_smoke.skipped",
        "severity": "warning",
    }


def agent_row(tag: str, started: datetime) -> dict[str, Any]:
    return {
        **_common(0, started),
        "event_kind": "open",
        "external_ref": f"{tag}#agent0",
        "entity_ref": f"{tag}#agent0",
        "session_ref": session_ref(tag),
        "agent_type": "manual",
    }


def session_open_row(tag: str, started: datetime) -> dict[str, Any]:
    return {
        **_common(0, started),
        "event_kind": "open",
        "external_ref": f"{tag}#session-open",
        "entity_ref": session_ref(tag),
        "workflow": "implement",
        "execution_attempt": 1,
    }


def request(
    project_ref: str, batches: list[tuple[str, list[dict[str, Any]]]], component: str = "deploy-smoke"
) -> dict[str, Any]:
    return {
        "action": "append_telemetry",
        "project_ref": project_ref,
        "component": component,
        "batches": [{"table": table, "rows": rows} for table, rows in batches],
    }


def golden_body(project_ref: str, tag: str, started: datetime) -> dict[str, Any]:
    """Four tables, the telemetry_sessions marker batch last, one session, three observation rows (the last a status event)."""
    return request(
        project_ref,
        [
            (
                "telemetry_transcripts",
                [transcript_row(tag, 0, started, "smoke prompt"), transcript_row(tag, 1, started, "smoke response")],
            ),
            (
                "telemetry_observations",
                [observation_row(tag, 0, started), observation_row(tag, 1, started), status_observation_row(tag, 2, started)],
            ),
            ("telemetry_agents", [agent_row(tag, started)]),
            ("telemetry_sessions", [session_open_row(tag, started)]),
        ],
    )


GOLDEN_ROWS = {"telemetry_transcripts": 2, "telemetry_observations": 3, "telemetry_agents": 1, "telemetry_sessions": 1}


def _rows(body: dict[str, Any], table: str) -> list[dict[str, Any]]:
    return next(batch["rows"] for batch in body["batches"] if batch["table"] == table)


def _sessions_first(body: dict[str, Any]) -> dict[str, Any]:
    body["batches"].insert(0, body["batches"].pop())
    return body


def _fractionless(body: dict[str, Any]) -> dict[str, Any]:
    row = _rows(body, "telemetry_sessions")[0]
    row["session_started_at"] = re.sub(r"\.\d{3}Z$", "Z", row["session_started_at"])
    return body


def _never_on_main(body: dict[str, Any]) -> dict[str, Any]:
    row = _rows(body, "telemetry_observations")[0]
    row.update({"observation_type": "process_event", "name": "never_on_main", "severity": "info"})
    return body


def _oversize_inline(body: dict[str, Any]) -> dict[str, Any]:
    content = "x" * (INLINE_THRESHOLD_BYTES + 1)
    row = _rows(body, "telemetry_transcripts")[0]
    row.update(content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest(), content_bytes=len(content))
    return body


def _content_uri(body: dict[str, Any]) -> dict[str, Any]:
    row = _rows(body, "telemetry_transcripts")[0]
    row.pop("content")
    row["content_uri"] = "smoke/uri/not-a-real-key"
    return body


def _catalog_key(body: dict[str, Any]) -> dict[str, Any]:
    body["catalog"] = "ducklake_ops"
    return body


def _two_producers(body: dict[str, Any]) -> dict[str, Any]:
    _rows(body, "telemetry_agents")[0]["producer"] = PRODUCER + "_other"
    return body


def _lone_surrogate(body: dict[str, Any]) -> dict[str, Any]:
    _rows(body, "telemetry_sessions")[0]["workflow"] = "smoke\ud800"
    return body


def _nul_producer(body: dict[str, Any]) -> dict[str, Any]:
    for batch in body["batches"]:
        for row in batch["rows"]:
            row["producer"] = PRODUCER + "\x00"
    return body


def _drill_ref(tag: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["project_ref"] = f"drill:unregistered-{tag}"
        return body

    return mutate


@dataclass(frozen=True)
class NegativeVector:
    """A mutation of the golden body and what the writer must answer: status, error_type and one named body field."""

    name: str
    mutate: Callable[[dict[str, Any]], dict[str, Any]]
    status: int
    error_type: str
    field: str | None = None
    value: Any = None


def negative_vectors(tag: str) -> tuple[NegativeVector, ...]:
    return (
        NegativeVector("sessions_batch_first", _sessions_first, 400, "write_order"),
        NegativeVector("fractionless_timestamp", _fractionless, 422, "schema_gate", "column", "session_started_at"),
        NegativeVector("never_on_main_name", _never_on_main, 422, "row_rule", "rule", "pattern_when"),
        NegativeVector("inline_over_threshold", _oversize_inline, 422, "row_rule", "rule", "inline_within_threshold"),
        NegativeVector("content_uri", _content_uri, 400, "bad_request", "reason", "writer_derived"),
        NegativeVector("catalog_key", _catalog_key, 400, "bad_request", "reason", "unknown_key"),
        NegativeVector("two_producers", _two_producers, 400, "mixed_producer"),
        NegativeVector("lone_surrogate", _lone_surrogate, 400, "bad_request", "reason", "invalid_unicode"),
        NegativeVector("nul_producer", _nul_producer, 400, "bad_request", "reason", "invalid_unicode"),
        NegativeVector("unregistered_drill_ref", _drill_ref(tag), 403, "project_unregistered"),
    )


def conflicting_body(body: dict[str, Any]) -> dict[str, Any]:
    """The golden body cut to two observation rows, row 1 changed under the same grain key: the 409 vector."""
    changed = copy.deepcopy(body)
    rows = _rows(changed, "telemetry_observations")
    rows[1]["name"] = "changed-after-first-write"
    del rows[2:]
    return changed


CONFLICT_TABLE = "telemetry_observations"
CONFLICT_ROW = 1


def omission_body(project_ref: str, tag: str, started: datetime) -> dict[str, Any]:
    row = transcript_row(tag, 0, started, "")
    row.pop("content")
    row.update(
        {
            "content_omitted_reason": "oversize",
            "content_bytes": 9_000_000,
            "content_sha256": hashlib.sha256(b"smoke").hexdigest(),
        }
    )
    row["external_ref"] = row["entity_ref"] = f"{tag}#omitted"
    return request(project_ref, [("telemetry_transcripts", [row])])


def row_cap_body(project_ref: str, tag: str, started: datetime) -> dict[str, Any]:
    rows = [observation_row(tag, index, started) for index in range(MAX_ROWS_PER_TABLE)]
    return request(project_ref, [("telemetry_observations", rows)])


def _padded_row(tag: str, index: int, started: datetime, size: int, filler: int) -> dict[str, Any]:
    return transcript_row(tag, index, started, dense_content(size) + "a" * filler)


def size_body(project_ref: str, tag: str, started: datetime, target: int) -> dict[str, Any]:
    """An escape-dense transcripts request whose invoke measure is EXACTLY `target`, every row under the threshold."""

    def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return request(project_ref, [("telemetry_transcripts", rows)])

    rows: list[dict[str, Any]] = []
    while measure(build([*rows, _padded_row(tag, len(rows), started, _FULL_ROW_CONTENT, 0)])) <= target:
        rows.append(_padded_row(tag, len(rows), started, _FULL_ROW_CONTENT, 0))
    if target - measure(build(rows)) < _ROW_OVERHEAD:
        rows.pop()
    index = len(rows)
    low, high = 0, _FULL_ROW_CONTENT
    while low < high:
        mid = (low + high + 1) // 2
        if measure(build([*rows, _padded_row(tag, index, started, mid, 0)])) <= target:
            low = mid
        else:
            high = mid - 1
    filler = 0
    for _ in range(8):
        last = _padded_row(tag, index, started, low, filler)
        gap = target - measure(build([*rows, last]))
        if gap == 0:
            return build([*rows, last])
        filler += gap
    raise ValueError("could not reach the exact invoke measure")
