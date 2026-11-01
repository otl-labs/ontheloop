"""Independent re-derivation of the telemetry derivation reader verbs from deduped stored rows (Decision 84 I-3: pure functions
over passed-in rows, no catalog, no SQL).

Written from the Class A contract formulas, never from src/common/ducklake_event_read_sql.py:
  state (SE-1, SE-2), duration_seconds (SE-3, truncated toward zero), process_event_total, rework_total, exception_total
      docs/contracts/telemetry_sessions.yaml (state; duration_seconds; steps_completed_total; the friction totals)
  run state, duration and the token totals     docs/contracts/telemetry_agents.yaml
  cost                                          docs/contracts/telemetry_observations.yaml (cost_usd), realized subset only:
      reported, allowance and unpriced as in derivation_cases.yaml c15, because the formula's price table is rec-4031's.
  generations, retirement, collapse             docs/contracts/telemetry-event-envelope.yaml (R4, R5), in dedupe.py

Governed data this module shares with the reader, by import: IDLE_THRESHOLD_SECONDS and FRICTION_RULES (the rules are data;
the matcher below is this module's own), VERB_FIELDS (output column names only) and EVENT_READS[verb].tables (the scope of a
verb's three stamps: generations, conflicted_grain_keys and derivation_conflicted cover only the tables the verb composes,
four for the state and window verbs, sessions and observations for the roll-ups; no contract text states this, so the registry
is its rule source). The compared-column set behind the conflict report is dedupe.py's default.

Declared conventions with no contract text: model_call_count, usage_missing_count,
label_counts (sorted by label), unmapped_count (facts labelled unmapped_block) and cost_source follow the verb's EVENT_READS
description, FRICTION_RULES and the committed derivation_cases.yaml (c15, and c15b: a local row is NULL
with cost_source local whatever it reports) and friction_vectors.yaml expectations. Five further output conventions
that no contract or committed vector pins were aligned to the verbs' observable behaviour once this module
passed the committed cases (declared here, not derived): a roll-up answers one row for every session holding a sessions or
observations row (zeros, an empty label_counts); a token total is NULL when no model_call carries that count;
usage_missing_count counts model_calls missing tokens_input or tokens_output; only a severity-error hook is a blocking hook
that absorbs its blocked tool_call; a sub-agent session with no agent-run row reads running or abandoned by idle time.

RESIDUAL. Agreement with the verbs proves the SQL implements the same reading of the contract text as this module, not that
the formula is right; the hand-authored derived_golden files and the contract text carry the formula itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from scripts.telemetry_verify.dedupe import Row, compute_generations, normalise_row, parse_timestamp, resolve_table
from src.common.ducklake_event_read_verbs import EVENT_READS, FRICTION_RULES, IDLE_THRESHOLD_SECONDS
from src.common.ducklake_reader_client import VERB_FIELDS

SESSIONS, OBSERVATIONS, AGENTS, TRANSCRIPTS = (
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_agents",
    "telemetry_transcripts",
)
STATE_VERBS = ("session_state_and_duration", "agent_run_state_and_duration", "sessions_window", "sessions_window_all_purposes")
ROLLUP_VERBS = (
    "agent_run_token_rollup",
    "session_process_event_rollup",
    "session_friction_rollup",
    "model_call_cost_estimate",
)
_BLOCKING_SEVERITY = "error"
_FACT_SEVERITIES = ("error", "warning", "critical")
_FAILED_OUTCOMES = ("failed", "timeout", "throttled")
_TOKEN_COLUMNS = ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation")

Tables = Mapping[str, Iterable[Row]]


class Scope:
    """One verb call's deduped rows: survivors per table, the per-session generations and conflicted grain keys."""

    def __init__(self, tables: Tables, verb: str, session_ids: Iterable[str] | None = None) -> None:
        wanted = None if session_ids is None else set(session_ids)
        self.verb = verb
        self.tables: dict[str, list[Row]] = {}
        self.conflicts: set[tuple[str, str, str, str, int]] = set()
        scoped = {
            name: [row for row in map(normalise_row, tables.get(name) or []) if wanted is None or row["session_id"] in wanted]
            for name in (SESSIONS, OBSERVATIONS, AGENTS, TRANSCRIPTS)
        }
        self.generations = compute_generations(scoped[SESSIONS])
        for name in EVENT_READS[verb].tables:
            collapse = (lambda r: r.get("observation_type") == "model_call") if name == OBSERVATIONS else False
            kept, conflicted = resolve_table(name, scoped[name], self.generations, entity_collapse=collapse)
            self.tables[name] = kept
            owner: dict[tuple[str, str, int], str] = {}
            for row in scoped[name]:
                key = (row["producer"], row["event_id"], row["parser_version"])
                owner[key] = min(owner.get(key, row["session_id"]), row["session_id"])
            self.conflicts |= {(owner[key], name, *key) for key in conflicted}

    def rows(self, table: str, session_id: str) -> list[Row]:
        return [row for row in self.tables.get(table, []) if row["session_id"] == session_id]

    def stamps(self, session_id: str) -> dict[str, Any]:
        pairs = sorted((producer, version) for (producer, sid), version in self.generations.items() if sid == session_id)
        keys = sorted(key[1:] for key in self.conflicts if key[0] == session_id)
        return {
            "generations": [{"producer": producer, "parser_version": version} for producer, version in pairs],
            "conflicted_grain_keys": [
                {"table_name": table, "producer": producer, "event_id": event_id, "parser_version": version}
                for table, producer, event_id, version in keys
            ],
            "derivation_conflicted": bool(keys),
        }

    def output(self, verb_row: Mapping[str, Any]) -> dict[str, Any]:
        row = {**verb_row, **self.stamps(verb_row["session_id"])}
        return {column: row[column] for column in VERB_FIELDS[self.verb]}  # type: ignore[union-attr]

    def known(self, session_id: str) -> bool:
        """The session has a surviving sessions or observations row (the roll-ups' id set)."""
        return bool(self.rows(SESSIONS, session_id) or self.rows(OBSERVATIONS, session_id))


def _whole_seconds(delta: timedelta) -> int:
    return int(delta.total_seconds())


def _ordinal(row: Row) -> int:
    return -1 if row.get("source_ordinal") is None else row["source_ordinal"]


def _lifecycle_key(row: Row) -> tuple[Any, ...]:
    return (row["event_timestamp"], _ordinal(row), row["event_kind"] == "close", row["event_id"])


def _close_key(row: Row) -> tuple[Any, ...]:
    return (row["event_timestamp"], _ordinal(row), row["event_id"])


def _last_event(scope: Scope, session_id: str) -> datetime | None:
    stamps = [row["event_timestamp"] for table in scope.tables for row in scope.rows(table, session_id)]
    return max(stamps) if stamps else None


def _idle_state(last: datetime | None, as_of: datetime) -> str:
    return "abandoned" if last is not None and (as_of - last).total_seconds() > IDLE_THRESHOLD_SECONDS else "running"


def _run_state(scope: Scope, session_id: str, as_of: datetime, runs: list[Row]) -> dict[str, Any]:
    opened = min((r for r in runs if r["event_kind"] == "open"), key=_close_key, default=None)
    closed = max((r for r in runs if r["event_kind"] == "close"), key=_close_key, default=None)
    if closed is None:
        return {"state": _idle_state(_last_event(scope, session_id), as_of), "outcome": None, "duration_seconds": None}
    state = "success" if closed.get("outcome") == "success" else "failed"
    duration = None if opened is None else _whole_seconds(closed["event_timestamp"] - opened["event_timestamp"])
    return {"state": state, "outcome": closed.get("outcome"), "duration_seconds": duration}


def _agent_runs(scope: Scope, session_id: str) -> dict[Any, list[Row]]:
    runs: dict[Any, list[Row]] = {}
    for row in scope.rows(AGENTS, session_id):
        runs.setdefault(row.get("agent_run_id"), []).append(row)
    return runs


_TURN_OUTCOMES = {"success": "success", "error": "failed", "interrupted": "cancelled", "blocked": "failed"}


def _turn_rows(scope: Scope, session_id: str, kind: str) -> list[Row]:
    return [r for r in scope.rows(OBSERVATIONS, session_id) if r.get("observation_type") == "turn" and r["event_kind"] == kind]


def _root_end(
    scope: Scope, session_id: str, sessions: list[Row], ordered: list[Row], last: datetime | None, as_of: datetime
) -> tuple[Any, Any, int | None]:
    """A root session's state, outcome and duration under SE-1..SE-3 (telemetry_sessions.yaml state, duration_seconds)."""
    closes = [r for r in sessions if r["event_kind"] == "close"]
    producer_close = max(closes, key=_close_key, default=None)
    opens = _turn_rows(scope, session_id, "open")
    turn_open = max(opens, key=_close_key, default=None)
    turn_closes = _turn_rows(scope, session_id, "close")
    own = [r for r in turn_closes if turn_open is not None and r.get("observation_id") == turn_open.get("observation_id")]
    own_close = max(own, key=_close_key, default=None)
    any_close = max(turn_closes, key=_close_key, default=None)
    tip = ordered[-1] if ordered else None
    idle = _idle_state(last, as_of) == "abandoned"
    stands = (
        tip is not None
        and producer_close is not None
        and tip["event_kind"] == "close"
        and (turn_open is None or (turn_open["event_timestamp"], _ordinal(turn_open)) <= _close_key(producer_close)[:2])
    )
    if stands and tip is not None:
        state: Any = tip.get("outcome")
        outcome: Any = tip.get("outcome")
    elif own_close is not None and own_close.get("outcome") is not None:
        mapped = _TURN_OUTCOMES.get(own_close["outcome"])
        state, outcome = (mapped, mapped) if idle else ("running", None)
    else:
        state, outcome = ("abandoned" if idle else "running"), None
    ends = [r["event_timestamp"] for r in (producer_close, any_close) if r is not None]
    duration = _whole_seconds(max(ends) - sessions[0]["session_started_at"]) if ends else None
    return state, outcome, duration


def _session_state(scope: Scope, session_id: str, as_of: datetime) -> dict[str, Any] | None:
    sessions = scope.rows(SESSIONS, session_id)
    if not sessions:
        return None
    ordered = sorted((r for r in sessions if r["event_kind"] != "annotate"), key=_lifecycle_key)
    parent = next((r["parent_session_id"] for r in sessions if r.get("parent_session_id")), None)
    last = _last_event(scope, session_id)
    if parent is not None:
        runs = _agent_runs(scope, session_id)
        latest = max(runs.values(), key=lambda g: max(_close_key(r) for r in g), default=[])
        derived = (
            _run_state(scope, session_id, as_of, latest)
            if latest
            else {"state": _idle_state(last, as_of), "outcome": None, "duration_seconds": None}
        )
    else:
        state, outcome, duration = _root_end(scope, session_id, sessions, ordered, last, as_of)
        derived = {"state": state, "outcome": outcome, "duration_seconds": duration}
    return {
        "session_id": session_id,
        "parent_session_id": parent,
        "project_id": sessions[0].get("project_id"),
        "session_started_at": sessions[0]["session_started_at"],
        **derived,
        "last_event_timestamp": last,
        "as_of": as_of,
        "idle_threshold_seconds": IDLE_THRESHOLD_SECONDS,
    }


def session_state_and_duration(tables: Tables, session_id: str, as_of: Any) -> list[dict[str, Any]]:
    scope = Scope(tables, "session_state_and_duration", [session_id])
    row = _session_state(scope, session_id, parse_timestamp(as_of))
    return [] if row is None else [scope.output(row)]


def agent_run_state_and_duration(tables: Tables, session_id: str, as_of: Any) -> list[dict[str, Any]]:
    scope = Scope(tables, "agent_run_state_and_duration", [session_id])
    when = parse_timestamp(as_of)
    out = []
    for run_id, runs in sorted(_agent_runs(scope, session_id).items(), key=lambda kv: min(_close_key(r) for r in kv[1])):
        opener = next((r for r in runs if r["event_kind"] == "open"), runs[0])
        out.append(
            scope.output(
                {
                    "agent_run_id": run_id,
                    "session_id": session_id,
                    "agent_type": opener.get("agent_type"),
                    **_run_state(scope, session_id, when, runs),
                    "last_event_timestamp": _last_event(scope, session_id),
                    "as_of": when,
                    "idle_threshold_seconds": IDLE_THRESHOLD_SECONDS,
                }
            )
        )
    return out


def sessions_window(
    tables: Tables, projects: Mapping[str, str], day_from: str, day_to: str, as_of: Any, *, all_purposes: bool = False
) -> list[dict[str, Any]]:
    verb = "sessions_window_all_purposes" if all_purposes else "sessions_window"
    first = datetime.fromisoformat(day_from).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(day_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
    ids = {
        row["session_id"] for row in map(normalise_row, tables.get(SESSIONS) or []) if first <= row["session_started_at"] < end
    }
    scope = Scope(tables, verb, ids)
    when = parse_timestamp(as_of)
    rows = [row for sid in ids if (row := _session_state(scope, sid, when)) is not None and row["parent_session_id"] is None]
    if not all_purposes:
        rows = [row for row in rows if projects.get(row["project_id"]) == "real"]
    rows.sort(key=lambda r: (r["session_started_at"], r["session_id"]))
    return [scope.output(row) for row in rows]


def _model_calls(scope: Scope, session_id: str) -> list[Row]:
    return [r for r in scope.rows(OBSERVATIONS, session_id) if r.get("observation_type") == "model_call"]


def agent_run_token_rollup(tables: Tables, session_id: str) -> list[dict[str, Any]]:
    scope = Scope(tables, "agent_run_token_rollup", [session_id])
    if not scope.known(session_id):
        return []
    calls = _model_calls(scope, session_id)
    totals: dict[str, int | None] = {}
    for column in _TOKEN_COLUMNS:
        present = [r[column] for r in calls if r.get(column) is not None]
        totals[f"{column}_total"] = sum(present) if present else None
    missing = sum(1 for r in calls if r.get("tokens_input") is None or r.get("tokens_output") is None)
    return [scope.output({"session_id": session_id, **totals, "model_call_count": len(calls), "usage_missing_count": missing})]


def session_process_event_rollup(tables: Tables, session_id: str) -> list[dict[str, Any]]:
    scope = Scope(tables, "session_process_event_rollup", [session_id])
    if not scope.known(session_id):
        return []
    observed = scope.rows(OBSERVATIONS, session_id)
    events = sum(1 for r in observed if r.get("observation_type") == "process_event")
    steps = sum(1 for r in observed if r.get("observation_type") == "step" and r["event_kind"] == "close")
    return [scope.output({"session_id": session_id, "process_event_total": events, "steps_completed_total": steps})]


def _matching_rule(observation_type: str, signature: str | None, disposition: str | None) -> tuple[Any, ...] | None:
    exact = generic = None
    for rule in FRICTION_RULES["rules"]:
        _, rule_type, rule_signature, rule_disposition, _, _ = rule
        if rule_type != observation_type or rule_disposition != disposition:
            continue
        if rule_signature is None:
            generic = generic or rule
        elif rule_signature == signature:
            exact = exact or rule
    return exact or generic


def friction_facts(observations: Iterable[Row]) -> list[tuple[str, str, str]]:
    """(ref, label, class) of every friction fact of one session's deduped observations."""
    rows = list(observations)
    facts: list[tuple[str, str, str]] = []
    blocking_hooks: set[Any] = set()
    for row in rows:
        if row.get("observation_type") != "process_event" or row.get("severity") not in _FACT_SEVERITIES:
            continue
        rule = _matching_rule("process_event", row.get("name"), row["severity"])
        if rule is not None:
            facts.append((row["observation_id"], rule[4], rule[5]))
            if row["severity"] == _BLOCKING_SEVERITY:
                blocking_hooks.add(row.get("parent_observation_id"))
    closes = sorted(
        (r for r in rows if r.get("observation_type") == "tool_call" and r["event_kind"] == "close"),
        key=lambda r: (r["event_timestamp"], _ordinal(r)),
    )
    for row in closes:
        outcome = row.get("outcome")
        if outcome not in ("error", "blocked") or (outcome == "blocked" and row["observation_id"] in blocking_hooks):
            continue
        rule = _matching_rule("tool_call", None, outcome)
        if rule is not None:
            facts.append((row["observation_id"], rule[4], rule[5]))
    runs: dict[tuple[Any, Any], list[list[Row]]] = {}
    for row in closes:
        stream = runs.setdefault((row.get("parent_observation_id"), row.get("name")), [[]])
        if row.get("outcome") == "error":
            stream[-1].append(row)
        elif stream[-1]:
            stream.append([])
    for stream in runs.values():
        for run in stream:
            if len(run) >= FRICTION_RULES["repeat_threshold"]:
                facts.append((run[0]["observation_id"], FRICTION_RULES["run_label"], FRICTION_RULES["run_class"]))
    return facts


def session_friction_rollup(tables: Tables, session_id: str) -> list[dict[str, Any]]:
    scope = Scope(tables, "session_friction_rollup", [session_id])
    if not scope.known(session_id):
        return []
    facts = friction_facts(scope.rows(OBSERVATIONS, session_id))
    counts: dict[str, int] = {}
    for _, label, _ in facts:
        counts[label] = counts.get(label, 0) + 1
    return [
        scope.output(
            {
                "session_id": session_id,
                "rework_total": sum(1 for f in facts if f[2] == "rework"),
                "exception_total": sum(1 for f in facts if f[2] == "exception"),
                "label_counts": [{"label": label, "n": n} for label, n in sorted(counts.items())],
                "unmapped_count": counts.get("unmapped_block", 0),
                "classifier_version": FRICTION_RULES["classifier_version"],
            }
        )
    ]


def model_call_cost_estimate(tables: Tables, session_id: str) -> list[dict[str, Any]]:
    scope = Scope(tables, "model_call_cost_estimate", [session_id])
    out = []
    for row in sorted(_model_calls(scope, session_id), key=lambda r: (r["event_timestamp"], r["observation_id"])):
        if row.get("billing_shape") == "fixed_non_rollover_allowance":
            cost, source = None, "allowance"
        elif row.get("billing_shape") == "local":
            cost, source = None, "local"
        elif row.get("cost_usd_reported") is not None:
            cost, source = row["cost_usd_reported"], "reported"
        else:
            cost, source = None, "unpriced"
        out.append(
            scope.output(
                {
                    "session_id": session_id,
                    "observation_id": row["observation_id"],
                    "event_timestamp": row["event_timestamp"],
                    "model": row.get("model"),
                    "provider": row.get("provider"),
                    "billing_shape": row.get("billing_shape"),
                    "tokens_input": row.get("tokens_input"),
                    "tokens_output": row.get("tokens_output"),
                    "cost_usd": cost,
                    "cost_source": source,
                }
            )
        )
    return out
