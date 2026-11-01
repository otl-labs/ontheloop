"""Red-before/green-after invariants for the telemetry C9-C13 contract amendment
(PLAN-telemetry-lifecycle-friction-amendment, feedback-loop review items C9-C13).

Loads each amended contract through scripts.contracts load_contract. Phrase assertions are
case-insensitive over whitespace-normalised text. rec-4146's node lives in
test_contract_amendments.py, where its acceptance pins it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.contracts import load_contract

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts"
_TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_agents", "telemetry_transcripts")
_NEW_DATE = "2026-10-05"
_RETIRED_STATE_RULE = (
    "no close row is the running",
    "absent a close row, the session is running",
    "outcome is the session's terminal",
    "no close row yet",
    "has an open/resume row and no close row",
)
_PROCESS_EVENT_POINT = {"event_kind": ["point"], "observation_type": ["process_event"]}


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def _doc(table: str):
    return load_contract(_CONTRACTS_DIR / f"{table}.yaml")


def _raw(table: str) -> dict:
    return yaml.safe_load((_CONTRACTS_DIR / f"{table}.yaml").read_text(encoding="utf-8"))


def _new_entries(entries) -> list:
    return [e for e in entries if e.date == _NEW_DATE]


def _has(entries, change_class: str, semantic_break: bool) -> bool:
    return any(e.change_class.value == change_class and e.semantic_break is semantic_break for e in _new_entries(entries))


def _live_strings(node: object) -> list[str]:
    if isinstance(node, dict):
        return [s for k, v in node.items() if k not in ("amendment_log", "previous_versions") for s in _live_strings(v)]
    if isinstance(node, list):
        return [s for item in node for s in _live_strings(item)]
    return [node] if isinstance(node, str) else []


def test_session_state_and_close_identity_declared() -> None:
    sessions = _doc("telemetry_sessions")
    fields = sessions.fields
    state = fields["state"]
    assert state.nullable is True and state.type == "str"
    assert state.dq_intent["accepted_values"]["values"] == ["running", "abandoned", "success", "failed", "cancelled"]
    derivation = state.derivation
    assert derivation["timing"] == "read" and derivation["realized"] is False
    assert derivation["derived_by"] == "reader_verb:session_state_and_duration"
    for column in (
        "parent_session_id", "event_kind", "event_timestamp", "source_ordinal", "event_id", "outcome",
        "telemetry_agents.session_id", "telemetry_agents.event_kind", "telemetry_agents.event_timestamp",
        "telemetry_agents.outcome", "telemetry_observations.session_id", "telemetry_observations.event_timestamp",
        "telemetry_observations.observation_type", "telemetry_observations.outcome", "telemetry_observations.event_id",
        "telemetry_transcripts.session_id", "telemetry_transcripts.event_timestamp",
    ):  # fmt: skip
        assert column in derivation["inputs"], column
    semantics = _norm(state.semantics)
    assert (
        "latest lifecycle row, ordered by event_timestamp, then source_ordinal, then close ranked last on a tie, then event_id"
        in semantics
    )
    assert "after each producer's own r5 dedupe" in semantics and "annotate rows are excluded" in semantics
    assert "no turn opened after it" in semantics and "stopped mid-turn" in semantics
    assert "idle threshold" in semantics and "24 h" in semantics and "owned by 2b" in semantics
    assert "named-read registry" in semantics and "makes the session running again and nothing is stored" in semantics
    assert "agent-run open/close pair" in semantics and "timeout or throttled -> failed" in semantics
    assert "stays on telemetry_agents.outcome" in semantics
    assert _has(state.amendment_log, "field_add", True)

    duration = fields["duration_seconds"]
    assert duration.derivation["derived_by"] == "reader_verb:session_state_and_duration"
    for column in ("parent_session_id", "source_ordinal", "event_id", "telemetry_agents.session_id",
                   "telemetry_agents.event_kind", "telemetry_agents.event_timestamp"):  # fmt: skip
        assert column in duration.derivation["inputs"], column
    duration_text = _norm(duration.semantics)
    assert "latest close's event_timestamp minus session_started_at, wall-clock" in duration_text
    assert "idle gap between a close and a later resume" in duration_text
    assert "agent-run close minus open" in duration_text
    assert "null until a close row exists" in _norm(duration.description)
    assert _has(duration.amendment_log, "governance_note_add", True)

    outcome = fields["outcome"]
    assert _has(outcome.amendment_log, "governance_note_add", True)
    outcome_text = _norm(outcome.semantics)
    assert "derived from the transcript, never from sessionend hook input" in outcome_text
    assert "session_postflight" not in _norm(outcome.populated_by)
    for name, text in (
        ("outcome", _norm(outcome.description) + " " + outcome_text),
        ("duration_seconds", duration_text + " " + _norm(duration.description)),
        (
            "event_kind",
            _norm(_raw("telemetry_sessions")["fields"]["event_kind"]["dq_intent_local"]["accepted_values"]["note"]),
        ),
        ("governance_notes", _norm(sessions.governance_notes)),
    ):
        for retired in _RETIRED_STATE_RULE:
            assert retired not in text, (name, retired)

    identity = _norm(fields["external_ref"].governance_notes_local)
    assert "one close row is written per finalization" in identity and "'<uuid>#0/close'" in identity
    assert "last root-stream record carrying a uuid" in identity
    ordinal = _norm(fields["source_ordinal"].governance_notes_local)
    assert "last root-stream record carrying a uuid" in ordinal
    assert "last root-stream record carrying a uuid" in _norm(fields["event_timestamp"].governance_notes_local)
    assert _has(fields["external_ref"].amendment_log, "governance_note_add", True)
    assert _has(fields["event_timestamp"].amendment_log, "governance_note_add", True)
    assert "resume after a close makes the session running again" in _norm(sessions.governance_notes)

    agents_duration = _doc("telemetry_agents").fields["duration_seconds"]
    agents_text = _norm(agents_duration.semantics)
    assert "telemetry_sessions.state" in agents_text and "abandoned past the same idle threshold" in agents_text
    assert "failed, timeout or throttled -> failed" in agents_text
    assert _has(agents_duration.amendment_log, "governance_note_add", True)
    assert _new_entries(sessions.amendment_log) and _new_entries(_doc("telemetry_agents").amendment_log)


def test_friction_rollups_read_the_rules_file() -> None:
    sessions = _doc("telemetry_sessions")
    rework, exception = sessions.fields["rework_total"], sessions.fields["exception_total"]
    for field in (rework, exception):
        assert (
            field.derivation["realized"] is False and field.derivation["derived_by"] == "reader_verb:session_friction_rollup"
        )
        for column in (
            "event_kind",
            "observation_id",
            "severity",
            "outcome",
            "parent_observation_id",
            "event_timestamp",
            "source_ordinal",
        ):
            assert f"telemetry_observations.{column}" in field.derivation["inputs"], column
        assert _has(field.amendment_log, "governance_note_add", True)
    rework_text = _norm(rework.semantics)
    assert (
        "process_events at severity error, warning or critical, plus tool_call closes with outcome error or blocked"
        in rework_text
    )
    assert "at least repeat_threshold consecutive error closes of the same tool inside one turn" in rework_text
    assert "a same-tool blocked close breaks a run" in rework_text
    assert (
        "governed, versioned rules file" in rework_text
        and "named-read registry" in rework_text
        and "owned by 2b" in rework_text
    )
    assert "classifier_version is stamped on every response and is never a column" in rework_text
    exception_text = _norm(exception.semantics)
    assert "an exact signature beats the generic rule" in exception_text and "not counted twice" in exception_text
    assert "after r5 dedupe" in rework_text

    observations = _doc("telemetry_observations")
    assert "reader response stamp, never a column" in _norm(observations.governance_notes)
    assert "rules file" in _norm(observations.fields["name"].semantics)
    assert "rules file" in _norm(observations.fields["time_lost_seconds"].semantics)
    assert _has(observations.fields["time_lost_seconds"].amendment_log, "prose_improvement", False)

    for table in _TABLES:
        doc = _doc(table)
        for text in _live_strings(yaml.safe_load((_CONTRACTS_DIR / f"{table}.yaml").read_text(encoding="utf-8"))):
            normalised = _norm(text)
            if "labels table" in normalised or "rec-4032" in normalised:
                assert "materialised" in normalised, (table, text)
        assert doc.contract.id == table
        for line in (_CONTRACTS_DIR / f"{table}.yaml").read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                assert "rec-4032" not in line, (table, line)


def test_severity_closed_and_name_required_on_process_event() -> None:
    doc = _doc("telemetry_observations")
    severity = doc.fields["severity"]
    assert severity.dq_intent["accepted_values"]["values"] == ["info", "warning", "error", "critical"]
    assert severity.dq_intent["required_when"] == _PROCESS_EVENT_POINT
    assert "enforcement deferred" not in _norm(severity.semantics) and "open set" not in _norm(severity.semantics)
    assert _has(severity.amendment_log, "accepted_values_narrow", True)

    name = doc.fields["name"]
    assert name.dq_intent["required_when"] == _PROCESS_EVENT_POINT
    assert "pattern" not in name.dq_intent and "write_time_exemptions" not in name.dq_intent
    assert [entry["when"] for entry in name.dq_intent["pattern_when"]] == [{"observation_type": ["process_event"]}]
    semantics = _norm(name.semantics)
    assert "'<source>:<signature>'" in semantics and "not yet write-enforced" not in semantics
    assert "write-enforced by dq_intent.pattern_when" in semantics and "decision 210 cl.1" in semantics
    assert "residual" not in semantics
    assert _has(name.amendment_log, "governance_note_add", True)


def test_process_event_parentage_declared() -> None:
    field = _doc("telemetry_observations").fields["parent_observation_id"]
    text = _norm(field.governance_notes_local)
    assert "a process_event's parent is the tool_call the hook gated when that call is in the stream" in text
    assert "else the enclosing turn, else null (a root)" in text
    assert "cross-row" in text and "prose only" in text and "rec-4101" in text
    assert _has(field.amendment_log, "governance_note_add", True)


def test_cost_usd_inputs_are_effective_dated() -> None:
    field = _doc("telemetry_observations").fields["cost_usd"]
    assert "event_timestamp" in field.derivation["inputs"] and "provider" in field.derivation["inputs"]
    assert "effective at event_timestamp" in field.derivation["formula"]
    text = _norm(field.semantics)
    assert "effective-dated" in text and "rec-4031" in text
    assert _has(field.amendment_log, "governance_note_add", True)


def test_window_verb_reading_declared() -> None:
    notes = _doc("telemetry_sessions").governance_notes
    match = re.search(r"WINDOW VERBS.*?egress line under Decision 88\.", " ".join(notes.split()))
    assert match, "WINDOW VERBS note missing"
    text = _norm(match.group(0))
    assert "per-session derivations" in text and "single calendar-day partition" in text
    assert "its own maximum window in the reader's named-read registry" in text and "stamps its read_version" in text
    assert "no contract-wide day count" in text and "decision 88" in text and "cd.52" in text
    assert not re.search(r"\b\d+[- ]days?\b", text)
    assert not re.search(r"day\(session_started_at\)", text)


def test_no_stale_enforcement_premise() -> None:
    banned = (
        "enforcement deferred",
        "phase 4 telemetry dq config",
        "dq_intent only",
        "report-only",
        "target-state",
        "no generated artifact",
    )
    for table in _TABLES:
        raw = yaml.safe_load((_CONTRACTS_DIR / f"{table}.yaml").read_text(encoding="utf-8"))
        for text in _live_strings(raw):
            normalised = _norm(text)
            for phrase in banned:
                assert phrase not in normalised, (table, phrase)
        projects_to = raw["contract"]["projects_to"]
        assert projects_to["artifact"] == "config/lambda/ducklake/field_semantics.yaml", table
        assert projects_to["table"] == table and projects_to["generator"] == "scripts/schema_to_field_semantics.py", table
        assert "runs in-process before then, so no later flip is owed" in _norm(projects_to["note"]), table
        assert "provisioned in the production telemetry catalog" in _norm(raw["governance"]["write_path"]), table
        notes = _norm(raw["governance_notes"])
        assert (
            "a column is not null at write when `nullable` is false or dq_intent declares not_null, "
            "unless the field carries `required_when`, which then governs" in notes
        ), table
        assert "every declared rule is write-enforced (decision 210)" in notes, table
        assert "a staged rule is spelled with exclude_before or an owned exemption" in notes, table
        assert "_is_not_null" in raw["governance_notes"] and "whatever their enforced value" not in notes, table
        assert _new_entries(load_contract(_CONTRACTS_DIR / f"{table}.yaml").amendment_log), table
        for line in (_CONTRACTS_DIR / f"{table}.yaml").read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                assert "dq_intent only" not in line.lower() and "target-state" not in line.lower(), (table, line)

    sessions = _doc("telemetry_sessions")
    for name in ("workflow", "ci_outcome"):
        text = _norm(sessions.fields[name].semantics)
        assert "closed set would be declared here and enforced at write (decision 210 cl.3)" in text, name
        assert _has(sessions.fields[name].amendment_log, "prose_improvement", False), name
    agents = _doc("telemetry_agents")
    assert "closed set would be declared here when ratified (decision 210 cl.3)" in _norm(agents.fields["provider"].semantics)
    assert _has(agents.fields["provider"].amendment_log, "prose_improvement", False)
    trigger = agents.fields["trigger"]
    assert trigger.dq_intent["accepted_values"]["values"] == [
        "subagent_sync",
        "subagent_background",
        "workflow_run",
        "workflow_dispatch",
        "push",
        "schedule",
        "eventbridge",
        "manual",
        "smoke_test",
    ]
    assert "closed and write-enforced" in _norm(trigger.semantics)
    observations = _doc("telemetry_observations")
    assert "closed set would be declared here and enforced at write" in _norm(observations.fields["provider"].semantics)
    assert _has(observations.fields["provider"].amendment_log, "prose_improvement", False)
