"""The litellm producer: one run_persona events file plus its host-run sidecar map to Decision 199 rows (pure mapper).

Stdlib plus src.turn_capture only; never imported by scripts/run_persona.py or src/persona_backends. Rules:
LM-1 input: schema other than 2 is Refused; no integer started_at_ms, or a missing or invalid sidecar, is Deferred (LL-1).
LM-2 session: an open row at the pin and, with an end record, one close row (success or failed).
LM-3 top-level run: an open row at the pin (model NULL) and a close row from the end record (success, timeout or failed).
LM-4 model_call rows: one per provider attempt in send order, the probe record placed before the first fallback entry.
LM-5 refs: a provider response id names the row; every other row is '<session ref>/call/<turn>/<n>'.
LM-6 record row columns, usage_final and the reasoning_visibility order.
LM-7 loss rows: a cancelled attempt is interrupted, any other failed attempt an error, both zero-token and usage_final false.
LM-8 ordering: open rows 0, model_calls 1..N, close rows N+1; a row before the pin raises ProducerHalt.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.turn_capture.host_run import HostRun, parse_host_run, render_host_run_ref
from src.turn_capture.observations import OBSERVATION_COLUMNS
from src.turn_capture.refs import (
    litellm_call_ref,
    litellm_root_ref,
    litellm_run_ref,
    model_call_ref,
    role_ref,
    session_marker_ref,
)
from src.turn_capture.sessions import SESSION_COLUMNS, agent_row_base
from src.turn_capture.transcripts import Ctx, canonical_json

PARSER_VERSION = 1
PRODUCER = "litellm"
PRODUCER_VERSION = "litellm-1"
SCHEMA = 2
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FALLBACK = "warm_fetched_escape_hatch"
_CLASSES = ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation")


class ProducerHalt(Exception):
    """A row is timed before the session pin: nothing is written."""


@dataclass
class Mapped:
    batches: list[tuple[str, list[dict[str, Any]]]]
    diagnostics: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class Deferred:
    reason: str


@dataclass(frozen=True)
class Refused:
    reason: str


def _at(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


def _row(columns: tuple[str, ...], **cols: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(columns)
    row.update(cols)
    return {k: v for k, v in row.items() if v is not None}


def _is_int(value: Any) -> bool:
    return type(value) is int


def reasoning_visibility(message: Any, reasoning_tokens: Any) -> str:
    """LM-6: thinking text, then redacted-only blocks, then reasoning_content, then reported tokens, else none."""
    if isinstance(message, dict):
        blocks = [b for b in (message.get("thinking_blocks") or []) if isinstance(b, dict)]
        if any(isinstance(b.get("thinking"), str) and b["thinking"] for b in blocks):
            return "summarized"
        if blocks:
            return "redacted"
        content = message.get("reasoning_content")
        if isinstance(content, str) and content:
            return "full"
    if _is_int(reasoning_tokens) and reasoning_tokens > 0:
        return "omitted"
    return "none"


class _Builder:
    def __init__(self, events: dict[str, Any], host: HostRun) -> None:
        self.head = events["header"]
        self.turns = [t for t in events.get("turns") or [] if isinstance(t, dict)]
        self.end = events.get("end") if isinstance(events.get("end"), dict) else None
        self.host = host
        self.pin_ms = self.head["started_at_ms"]
        self.pin = _at(self.pin_ms)
        self.session = litellm_root_ref(render_host_run_ref(host))
        self.run = litellm_run_ref(self.session)
        self.diag: Counter[str] = Counter()
        self._position = 0
        self.ctx = Ctx(PRODUCER, PRODUCER_VERSION, PARSER_VERSION, self.pin, self._billing_shape(), 0, host)

    def _billing_shape(self) -> str:
        for turn in self.turns:
            record = turn.get("record")
            if isinstance(record, dict) and isinstance(record.get("billing_shape"), str):
                return record["billing_shape"]
        return "unknown"

    def when(self, ms: int) -> datetime:
        if ms < self.pin_ms:
            raise ProducerHalt(f"a row at {ms} ms is before the session pin {self.pin_ms} ms")
        return _at(ms)

    def obs(self, ordinal: int, entity: str, ms: int, **cols: Any) -> dict[str, Any]:
        return _row(
            OBSERVATION_COLUMNS,
            event_kind="point",
            event_timestamp=self.when(ms),
            session_started_at=self.pin,
            source_ordinal=ordinal,
            external_ref=cols.pop("external_ref"),
            entity_ref=entity,
            session_ref=self.session,
            producer=PRODUCER,
            producer_version=PRODUCER_VERSION,
            parser_version=PARSER_VERSION,
            observation_type="model_call",
            persona_backend="litellm",
            **cols,
        )

    def record_row(self, ordinal: int, turn: dict[str, Any], record: dict[str, Any], probe: bool) -> dict[str, Any]:
        position = "probe" if probe else self._position
        response_id = record.get("response_id")
        if isinstance(response_id, str) and response_id:
            entity, ext = response_id, model_call_ref(response_id)
        else:
            entity = litellm_call_ref(self.session, turn["turn"], position)
            ext = f"{entity}/model_call"
        ms = (
            record["responded_at_ms"]
            if _is_int(record.get("responded_at_ms"))
            else record["started_at_ms"] + record["latency_ms"]
            if _is_int(record.get("started_at_ms")) and _is_int(record.get("latency_ms"))
            else turn["ended_at_ms"]
        )
        absent = record.get("usage_absent") or []
        tokens = record.get("reasoning_tokens")
        message = None if probe else turn.get("message")
        return self.obs(
            ordinal,
            entity,
            ms,
            external_ref=ext,
            name="cold_start_probe" if probe else record["role"],
            outcome="success",
            model=record.get("model") or record.get("requested_model"),
            requested_model=record.get("requested_model"),
            provider=record.get("provider"),
            billing_shape=record.get("billing_shape"),
            reasoning_effort=record.get("reasoning_effort"),
            **{c: record.get(c) for c in _CLASSES},
            reasoning_tokens=tokens,
            cache_creation_1h_tokens=record.get("cache_creation_1h_tokens"),
            cache_creation_5m_tokens=record.get("cache_creation_5m_tokens"),
            attempt=None if probe else record.get("attempt"),
            usage_final=not absent,
            usage_source="record",
            reasoning_visibility=reasoning_visibility(message, tokens),
        )

    def loss_row(self, ordinal: int, turn: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        cancelled = entry.get("outcome") == "cancelled"
        entity = litellm_call_ref(self.session, turn["turn"], self._position)
        latency = entry.get("latency_ms")
        started = entry.get("started_at_ms")
        ms = started + latency if isinstance(started, int) and isinstance(latency, int) else turn["started_at_ms"]
        number = entry.get("attempt")
        meta = {"loss": "cancelled"} if cancelled else {"error": entry.get("outcome"), "loss": "usage_not_recorded"}
        return self.obs(
            ordinal,
            entity,
            ms,
            external_ref=f"{entity}/model_call",
            name=entry.get("role"),
            outcome="interrupted" if cancelled else "error",
            model=entry.get("model"),
            requested_model=entry.get("model"),
            provider=entry.get("provider"),
            billing_shape=entry.get("billing_shape"),
            **dict.fromkeys(_CLASSES, 0),
            attempt=number if isinstance(number, int) and number >= 1 else None,
            usage_final=False,
            reasoning_visibility="none",
            metadata=canonical_json(meta),
        )

    def model_calls(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for turn in self.turns:
            if turn.get("attempts_known") is not True:
                self.diag["attempts_unknown"] += 1
                continue
            entries = [a for a in turn.get("attempts") or [] if isinstance(a, dict)]
            probe = turn.get("probe_record")
            first_fallback = next(
                (i for i, a in enumerate(entries) if a.get("role") == _FALLBACK and (a.get("attempt") or 0) >= 1), None
            )
            at = len(entries) if first_fallback is None else first_fallback
            for index in range(len(entries) + 1):
                if index == at and isinstance(probe, dict):
                    rows.append(self.record_row(len(rows) + 1, turn, probe, True))
                if index == len(entries):
                    break
                self._position = index + 1
                entry = entries[index]
                if entry.get("outcome") != "ok":
                    rows.append(self.loss_row(len(rows) + 1, turn, entry))
                elif isinstance(turn.get("record"), dict):
                    rows.append(self.record_row(len(rows) + 1, turn, turn["record"], False))
                else:
                    self.diag["ok_without_record"] += 1
        return rows

    def run_close_model(self) -> str | None:
        for turn in reversed(self.turns):
            record = turn.get("record")
            if isinstance(record, dict):
                return record.get("model") or record.get("requested_model")
        return None

    def sessions(self, closes: int) -> list[dict[str, Any]]:
        base = {
            "session_started_at": self.pin,
            "entity_ref": self.session,
            "producer": PRODUCER,
            "producer_version": PRODUCER_VERSION,
            "parser_version": PARSER_VERSION,
            "workflow": self.host.agent,
            "execution_attempt": int(self.host.run_attempt),
        }
        rows = [
            _row(
                SESSION_COLUMNS,
                event_kind="open",
                event_timestamp=self.pin,
                source_ordinal=0,
                external_ref=session_marker_ref(self.session),
                **base,
            )
        ]
        if self.end is not None:
            ok = self.end.get("outcome") == "success"
            rows.append(
                _row(
                    SESSION_COLUMNS,
                    event_kind="close",
                    event_timestamp=self.when(self.end["ended_at_ms"]),
                    source_ordinal=closes,
                    external_ref=role_ref(f"{self.session}/end", 0, "close"),
                    outcome="success" if ok else "failed",
                    failure_reason=None if ok else self.end.get("failure") or self.end.get("error"),
                    **base,
                )
            )
        return rows

    def agents(self, closes: int) -> list[dict[str, Any]]:
        cols = {
            "entity_ref": self.run,
            "session_ref": self.session,
            "agent_type": self.host.agent,
            "version": str(self.head.get("contract_version")),
            "trigger": self.host.event_name,
            "workflow_run_id": self.host.run_id,
            "host_run_ref": render_host_run_ref(self.host),
            "definition_sha256": self.head.get("rendered_prompt_sha256"),
        }
        rows = [agent_row_base(self.ctx, "open", self.pin, 0, session_marker_ref(self.run), **cols)]
        if self.end is not None:
            failure = self.end.get("failure")
            if self.end.get("outcome") == "aborted":
                outcome = "failed"
            elif self.end.get("outcome") == "success":
                outcome = "success"
            else:
                outcome = "timeout" if failure == "budget_exhausted" else "failed"
            rows.append(
                agent_row_base(
                    self.ctx,
                    "close",
                    self.when(self.end["ended_at_ms"]),
                    closes,
                    role_ref(f"{self.session}/end", 0, "run_close"),
                    model=self.run_close_model(),
                    outcome=outcome,
                    error=None if outcome == "success" else self.end.get("error") or failure,
                    **cols,
                )
            )
        return rows


def map_run(events: Any, host_run: bytes | None) -> Mapped | Deferred | Refused:
    if not isinstance(events, dict) or not isinstance(events.get("header"), dict):
        return Refused("events file has no header")
    head = events["header"]
    if head.get("schema") != SCHEMA:
        return Refused(f"events schema {head.get('schema')!r} is not {SCHEMA}")
    if not _is_int(head.get("started_at_ms")):
        return Deferred("header has no integer started_at_ms")
    host = parse_host_run(host_run)
    if host is None:
        return Deferred("host-run sidecar is missing or invalid")
    build = _Builder(events, host)
    calls = build.model_calls()
    closes = len(calls) + 1
    agents = build.agents(closes)
    sessions = build.sessions(closes)
    return Mapped(
        [("telemetry_observations", calls), ("telemetry_agents", agents), ("telemetry_sessions", sessions)], build.diag
    )
