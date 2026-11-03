"""Red-before/green-after invariants for the telemetry contract-risk amendments R3/R6/R7
(rec-4058, rec-4059, rec-4060; PLAN-telemetry-contract-risk-amendments).

Loads each amended contract through scripts.contracts load_contract and asserts the write-time
behaviour and amendment_log bookkeeping the recs' acceptance commands require. Phrase assertions
are case-insensitive over whitespace-normalised text. This module does not exist on the
pre-change tree (red before every test here). R8 (rec-4061 parser_version bump policy, rec-4062
model_call tiebreak, and the grain-enforced-at-write data-modeling rule, Decision 207) adds its
own three tests to this module (PLAN-telemetry-grain-and-generations).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts.contracts import load_contract
from src.telemetry.identity import decode_time_prefix, derive_entity_key

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "contracts"


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _has_governance_note_add_break(entries) -> bool:
    return any(entry.change_class.value == "governance_note_add" and entry.semantic_break for entry in entries)


def test_parent_observation_existence_is_dq_not_write_time() -> None:
    doc = load_contract(_CONTRACTS_DIR / "parent-observation-id.yaml")
    field = doc.fields["parent_observation_id"]

    validation = _norm(field.write_time_validation)
    governance = _norm(field.governance_notes)

    assert "must reference an existing telemetry_observations.observation_id" not in validation
    assert "never checks" in validation and "parent" in validation
    assert "quiescent" in validation
    assert "dangling parent" in validation and "transient" in validation and "permanent" in validation
    assert "rec-4101" in validation

    assert "rec-4101" in governance
    assert "quiescent" in governance

    raw_text = (_CONTRACTS_DIR / "_joins.yaml").read_text(encoding="utf-8")
    normalised = _norm(raw_text)
    assert "write-time + dq-relationships concern" not in normalised
    assert "referential integrity is a write-time" not in normalised
    assert "never a write-time check" in normalised

    assert _has_governance_note_add_break(field.amendment_log)


def test_project_ref_resolution_is_pinned() -> None:
    doc = load_contract(_CONTRACTS_DIR / "project-id.yaml")
    field = doc.fields["project_id"]

    semantics = _norm(field.semantics)

    assert "never re-bound to a different project_id" in semantics
    assert "canonical_project_id" in semantics and "read-side only" in semantics
    assert "root" in semantics and "project_ref" in semantics
    assert "pinned once" in semantics
    assert "first record" in semantics
    assert "never" in semantics and "per-turn" in semantics
    assert "defers emission" in semantics

    assert _has_governance_note_add_break(field.amendment_log)


def test_session_started_at_handoff_is_exact_ms() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    field = doc.fields["session_started_at"]

    semantics = _norm(field.semantics)

    assert "epoch-millisecond" in semantics
    assert "decode_time_prefix" in semantics
    assert "event_id" in semantics and "never an event_id" in semantics
    assert "never seconds" in semantics or ("seconds" in semantics and "never" in semantics)
    assert "defers emission" in semantics or "defer" in semantics
    assert "full millisecond value" in semantics or "full ms value" in semantics

    assert _has_governance_note_add_break(field.amendment_log)

    session_started_at = datetime(2026, 9, 26, 12, 0, 0, 123000, tzinfo=timezone.utc)
    session_id = derive_entity_key(
        "telemetry_sessions:session_id",
        "01M3TENANTTENANTTENANTTENA",
        "01M3PRJTPRJTPRJTPRJTPRJTPZ",
        "root-session-ref",
        session_started_at,
    )
    recovered = decode_time_prefix(session_id)
    assert recovered == session_started_at


def test_parser_version_bump_policy_declared() -> None:
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    parser_version = envelope.fields["parser_version"]
    producer = envelope.fields["producer"]

    pv_description = _norm(parser_version.description)
    pv_semantics = _norm(parser_version.semantics)
    assert "hand-maintained" in pv_description and "per-producer" in pv_description
    assert "increments iff" in pv_description
    assert "never decrements" in pv_description and "never reused" in pv_description
    assert "identity-spec change" in pv_description and "bumps every producer" in pv_description
    assert "from its own build/version identity" not in _norm(parser_version.populated_by)
    assert "replayable producer" in pv_semantics and "cursor" in pv_semantics

    for table_name in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
        doc = load_contract(_CONTRACTS_DIR / f"{table_name}.yaml")
        producer_field = doc.fields["producer"]
        assert producer_field.dq_intent_local is not None
        assert producer_field.dq_intent_local["not_null"] is True
        assert any(entry.semantic_break for entry in producer_field.amendment_log)

    assert "part of the telemetry grain key" in _norm(producer.description) or "grain key" in _norm(producer.description)


def test_model_call_tiebreak_prefers_final_usage() -> None:
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    governance = _norm(envelope.governance_notes)

    assert "event_timestamp desc" in governance
    assert "final usage wins" in governance or "final usage numbers win" in governance
    assert "then created_timestamp asc" in governance or "created_timestamp asc" in governance
    assert "then producer asc" in governance
    assert "then event_id asc" in governance
    assert "model_call dedupe" not in governance

    from tests.fixtures.telemetry_dedupe_reference import compute_generations, resolve_table

    generations = compute_generations(
        [
            {
                "producer": "claude_code",
                "event_id": "sess-open",
                "parser_version": 1,
                "session_id": "s1",
                "event_kind": "open",
                "created_timestamp": "2026-09-25T10:00:00+00:00",
                "content": "v1",
            }
        ]
    )
    rows = [
        {
            "producer": "claude_code",
            "event_id": "mc-partial",
            "parser_version": 1,
            "session_id": "s1",
            "created_timestamp": "2026-09-25T10:01:00+00:00",
            "content": "partial",
            "observation_id": "obs-1",
            "event_timestamp": "2026-09-25T10:01:00+00:00",
        },
        {
            "producer": "claude_code",
            "event_id": "mc-final",
            "parser_version": 1,
            "session_id": "s1",
            "created_timestamp": "2026-09-25T10:02:00+00:00",
            "content": "final",
            "observation_id": "obs-1",
            "event_timestamp": "2026-09-25T10:02:00+00:00",
        },
    ]
    survivors, _ = resolve_table(rows, generations, is_model_call=True)
    assert [row["event_id"] for row in survivors] == ["mc-final"]


def test_grain_enforced_at_write_rule_declared() -> None:
    standard_path = _CONTRACTS_DIR / "data-modeling-standard.yaml"
    standard = yaml.safe_load(standard_path.read_text(encoding="utf-8"))
    rules = {rule["id"]: rule for rule in standard["rules"]}
    assert "grain-enforced-at-write" in rules
    rule_statement = rules["grain-enforced-at-write"]["statement"]

    match = re.search(r"Decision (\d+)", rule_statement)
    assert match, "grain-enforced-at-write rule must cite its Decision number"
    decision_number = match.group(1)

    walk = standard["design_time_walk"]
    assert "grain-enforced-at-write" in walk and f"Decision {decision_number}" in walk

    decisions_text = (_REPO_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    section_marker = f"## Decision {decision_number}:"
    assert section_marker in decisions_text, f"DECISIONS.md must carry a section for Decision {decision_number}"
    section_start = decisions_text.index(section_marker)
    next_section = decisions_text.find("\n## Decision ", section_start + 1)
    section = decisions_text[section_start : next_section if next_section != -1 else len(decisions_text)]
    section_norm = _norm(section)

    for owner in ("rec-4121", "rec-4063", "rec-4024", "rec-4025"):
        assert owner in section_norm, f"Decision {decision_number} must name coverage owner {owner}"
    assert "ops_smoke_events" in section_norm
    assert "scd2 history readers" in section_norm and "exempt" in section_norm

    for anchor in ("decision 81", "decision 84"):
        assert anchor in decisions_text.lower()
    assert decisions_text.count(f"Decision {decision_number}") >= 2  # the section itself plus the two dated annotations


def _contract_text(name: str) -> str:
    return (_CONTRACTS_DIR / f"{name}.yaml").read_text(encoding="utf-8")


_WRITE_CONFORMANCE_DATE = "2026-10-04"
_WRITE_CONFORMANCE_PLAN = "PLAN-telemetry-write-conformance"


def _write_conformance_entry(amendment_log) -> dict:
    """The one raw-YAML amendment_log entry this slice's tests were written for, wherever it sits in the log."""
    found = [
        e
        for e in amendment_log
        if str(e.get("date")) == _WRITE_CONFORMANCE_DATE and _WRITE_CONFORMANCE_PLAN in (e.get("summary") or "")
    ]
    assert len(found) == 1, f"expected exactly one {_WRITE_CONFORMANCE_PLAN} entry, found {len(found)}"
    return found[0]


def test_write_conformance_lookup_survives_growth_only() -> None:
    entry = {"date": _WRITE_CONFORMANCE_DATE, "summary": f"{_WRITE_CONFORMANCE_PLAN} (slice 2a-1): x"}
    newer = {"date": "2026-10-05", "summary": "PLAN-other: y"}
    assert _write_conformance_entry([newer, entry]) is entry
    for log in (
        [newer],
        [entry, dict(entry)],
        [{"date": _WRITE_CONFORMANCE_DATE, "summary": "PLAN-ops-rule-source: z"}],
    ):
        with pytest.raises(AssertionError):
            _write_conformance_entry(log)


def _entries(entries, change_class: str) -> bool:
    return any(entry.change_class.value == change_class and entry.semantic_break for entry in entries)


def test_session_pin_is_earliest_timestamp() -> None:
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    field = envelope.fields["session_started_at"]
    semantics, description = _norm(field.semantics), _norm(field.description)
    assert "earliest parseable record timestamp" in description and "first timestamped record" not in description
    assert "earliest timestamp" in semantics and "never the first record in file order" in semantics
    assert "re-pin" in semantics and "emitted no row under that pin" in semantics
    assert "producer halt" in semantics and "never re-pinned and never emitted" in semantics
    assert "migration_story" not in semantics
    assert _entries(field.amendment_log, "governance_note_add")
    assert any(
        "earliest" in (e.summary or "").lower() and "safe only because no" in (e.migration_story or "")
        for e in field.amendment_log
    )

    governance = _norm(envelope.governance_notes)
    assert "with one carve-out" in governance and "re-pins only while the cursor has emitted no row" in governance
    assert "timestamp precision" in governance and "exactly three fraction digits" in governance

    event_timestamp = envelope.fields["event_timestamp"]
    assert event_timestamp.dq_intent["not_before"] == "session_started_at"
    assert event_timestamp.dq_intent["max_after_write_seconds"] == 300
    skew = _norm(event_timestamp.semantics)
    assert "different clocks" in skew and "never an exact comparison" in skew and "300 s" in skew
    assert _entries(event_timestamp.amendment_log, "governance_note_add")
    envelope_log = yaml.safe_load(_contract_text("telemetry-event-envelope"))["amendment_log"]
    _write_conformance_entry(envelope_log)
    assert envelope.contract.contract_version == 1


def test_tool_result_capture_policy_declared() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_transcripts.yaml")
    fields = doc.fields
    purposes = fields["purpose"].dq_intent["accepted_values"]["values"]
    assert "tool_output" in purposes and "the full persisted output of a tool call as its own row" in _norm(
        fields["purpose"].semantics
    )
    assert "'<source_record_id>#<block_index>/full'" in _norm(fields["purpose"].semantics)

    content = fields["content"]
    assert _norm(content.description).startswith("the payload as the model received/produced it")
    assert "as the model saw it" in _norm(content.semantics) and "never the sidecar" in _norm(content.semantics)
    intent = content.dq_intent
    assert intent["content_inline_threshold_bytes"] == 65536 and intent["full_output_cap_bytes"] == 8388608
    assert intent["integrity"] is True
    assert intent["exactly_one_of"]["fields"] == ["content", "content_uri", "content_omitted_reason"]
    assert intent["representation_of"] == ["content_sha256", "content_bytes"]
    assert fields["content_uri"].dq_intent["representation_of"] == ["content_sha256", "content_bytes"]
    assert "write_time_exemptions" not in intent
    assert "claim-check route" in _norm(content.semantics) and "begin_blobs" in _norm(content.semantics)

    omitted = fields["content_omitted_reason"]
    assert omitted.nullable is True and omitted.dq_intent["accepted_values"]["values"] == ["oversize", "withheld_by_policy"]
    assert "raw source bytes' identity" in _norm(omitted.semantics)
    assert fields["content_sha256"].dq_intent["pattern"] == "^[0-9a-f]{64}$"
    assert "raw source bytes" in _norm(fields["content_sha256"].semantics)

    truncated = _norm(fields["content_truncated"].description)
    assert "incomplete copy" in truncated and "preview" in truncated and "omission row" in truncated
    assert "defect detector" in _norm(fields["content_truncated"].semantics)
    for name in ("purpose", "content", "content_truncated", "content_sha256"):
        assert _entries(fields[name].amendment_log, "governance_note_add") or _entries(
            fields[name].amendment_log, "accepted_values_extend"
        )
    assert "/full" in _norm(fields["external_ref"].governance_notes_local) and "/full" in _norm(
        fields["entity_ref"].governance_notes_local
    )
    assert any(entry.date == "2026-10-04" for entry in doc.amendment_log)


def test_tool_call_output_fields_declared() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_observations.yaml")
    fields = doc.fields
    capture = fields["output_capture"].dq_intent
    assert capture["accepted_values"]["values"] == [
        "not_persisted",
        "captured",
        "omitted_oversize",
        "unavailable",
        "no_result",
    ]
    assert capture["required_when"] == {"event_kind": ["close"], "observation_type": ["tool_call"]}
    for name in ("output_bytes", "output_sha256", "output_lines"):
        assert fields[name].dq_intent["required_when"] == {
            "output_capture": ["not_persisted", "captured", "omitted_oversize"]
        }, name
    assert fields["output_sha256"].dq_intent["pattern"] == "^[0-9a-f]{64}$"
    assert fields["output_bytes"].iceberg_type == fields["output_lines"].iceberg_type == "bigint"
    assert "raw" in _norm(fields["output_bytes"].description)
    log = [e for e in doc.amendment_log if e.date == "2026-10-04"]
    assert len(log) == 1 and log[0].change_class.value == "field_add" and "output_capture" in log[0].summary


def test_model_call_reasoning_fields_declared() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_observations.yaml")
    visibility = doc.fields["reasoning_visibility"].dq_intent
    assert visibility["accepted_values"]["values"] == ["full", "summarized", "omitted", "redacted", "none"]
    assert visibility["required_when"] == {"event_kind": ["point"], "observation_type": ["model_call"]}
    tokens = doc.fields["reasoning_tokens"]
    assert tokens.dq_intent["at_most"] == "tokens_output"
    assert tokens.dq_intent["null_or_zero_when"] == {"reasoning_visibility": ["none"]}
    assert "subset of tokens_output" in _norm(tokens.description) and "never a local estimate" in _norm(tokens.semantics)
    assert "first that applies" in _norm(doc.fields["reasoning_visibility"].semantics)
    assert any(entry.date == "2026-10-04" and "reasoning_visibility" in entry.summary for entry in doc.amendment_log)


def test_parser_version_bumps_on_recorded_input_constant() -> None:
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    field = envelope.fields["parser_version"]
    description = _norm(field.description)
    assert "registry-recorded producer input constant" in description and "full_output_cap_bytes" in description
    assert "even when the golden digest repeats" in description
    assert _entries(field.amendment_log, "governance_note_add")


def test_representation_only_columns_excluded_from_grain_compare() -> None:
    standard = yaml.safe_load(_contract_text("data-modeling-standard"))
    rules = {rule["id"]: rule for rule in standard["rules"]}
    statement = _norm(rules["grain-enforced-at-write"]["statement"])
    assert "representation-only columns" in statement and "dq_intent representation_of" in statement
    assert "still an equality compare that rejects loudly, never a tiebreak" in statement
    assert not re.search(r"telemetry_transcripts|content_uri", statement)
    first = re.search(r"Decision (\d+)", rules["grain-enforced-at-write"]["statement"])
    assert first and first.group(1) == "207"
    assert _write_conformance_entry(standard["amendment_log"])["semantic_break"] is True
    assert standard["version"] >= 5


def test_decision_annotations_for_hash_compare_and_omission() -> None:
    text = (_REPO_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")

    def section(number: int) -> str:
        start = text.index(f"\n## Decision {number}:")
        return text[start : text.index("\n---\n", start)]

    d207, d199 = section(207), section(199)
    marker = "> **Update (2026-10-04):** representation-only columns"
    note = next((line for line in d207.splitlines() if line.startswith(marker)), "")
    assert note.rstrip().endswith("(PLAN-telemetry-write-conformance).")
    assert d207.index(note) > d207.index("**Related:**")
    assert "not the content-hash tiebreak this entry rejects" in d207
    assert "> **Update (2026-10-04):** clause 5's content is the model-visible payload" in d199
    assert "typed omission row" in d199 and "last clause-5 annotation" in d199
    assert d199.index("Update (2026-09-28)") < d199.index("Update (2026-10-04)")


def test_write_time_decision_cited_by_its_merged_number() -> None:
    standard = yaml.safe_load(_contract_text("data-modeling-standard"))
    rule = next(r for r in standard["rules"] if r["id"] == "write-time-enforcement")
    cited = re.match(r"Decision (\d+):", rule["statement"])
    assert cited, "write-time-enforcement must open by citing its Decision"
    number = cited.group(1)
    text = (_REPO_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    header = re.search(rf"^## Decision {number}: (.*)$", text, re.MULTILINE)
    assert header and "enforced at the write boundary" in header.group(1)
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    semantics = envelope.fields["event_timestamp"].semantics
    assert re.search(rf"Write-enforced \(Decision {number} ", semantics), semantics
    for name in ("telemetry-event-envelope", "telemetry_transcripts", "telemetry_observations", "telemetry-lexicon"):
        added = re.findall(r"PLAN-telemetry-write-conformance[^\n]*?Decision (\d+)", _contract_text(name))
        assert set(added) <= {number}, (name, added)


def test_sessions_required_when_fields_are_nullable() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_sessions.yaml")
    for name in ("workflow", "execution_attempt"):
        field = doc.fields[name]
        assert field.nullable is True and field.dq_intent["required_when"] == {"event_kind": ["open"]}, name
        assert any(e.change_class.value == "type_widen" and "rec-4064" in e.summary for e in field.amendment_log), name


def test_lexicon_names_tool_output_and_omission() -> None:
    lexicon = yaml.safe_load(_contract_text("telemetry-lexicon"))
    text = _norm(_contract_text("telemetry-lexicon"))
    for term in ("term: tool_output", "term: output_capture", "term: payload omission", "term: reasoning visibility"):
        assert term in text, term
    assert "prompt | response | thinking | tool_input | tool_result | tool_output | system" in text
    assert "as the model saw it" in text and "user-facing progress-update thinking blocks" in text
    _write_conformance_entry(lexicon["amendment_log"])


def test_subagent_open_timestamp_and_open_marker_ref_declared() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_sessions.yaml")
    timestamp = _norm(doc.fields["event_timestamp"].governance_notes_local)
    assert "a sub-agent session's open row carries the root start" in timestamp
    assert "session_started_at is the tree's pin" in timestamp
    assert "a child's own start is its telemetry_agents open row" in timestamp
    external_ref = _norm(doc.fields["external_ref"].governance_notes_local)
    assert "the open marker is '<session entity_ref>#0/open'" in external_ref
    for name in ("event_timestamp", "external_ref"):
        log = [e for e in doc.fields[name].amendment_log if e.date == "2026-10-05"]
        assert len(log) == 1 and log[0].change_class.value == "governance_note_add" and log[0].semantic_break, name
        assert "rec-4146" in log[0].summary, name


_V3_DATE = "2026-10-14"
_V3_PLAN = "PLAN-telemetry-parser-v3"


def _v3_logged(entries) -> bool:
    return any(
        str(e.date) == _V3_DATE and e.change_class.value == "governance_note_add" and _V3_PLAN in (e.summary or "")
        for e in entries
    )


def test_claude_code_capture_billing_shape_source_declared() -> None:
    field = load_contract(_CONTRACTS_DIR / "telemetry_observations.yaml").fields["billing_shape"]
    text = _norm(field.populated_by)
    sentence = (
        "claude code session captures (producer claude_code) are fixed_non_rollover_allowance; metered api-key claude code "
        "use is out of scope (ci runs on the subscription oauth token), "
        "so the producer never emits metered_marginal (rec-4147)"
    )
    assert sentence in text
    assert _v3_logged(field.amendment_log)


def test_external_ref_scope_under_forked_history_declared() -> None:
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    external_ref = _norm(envelope.fields["external_ref"].semantics)
    for phrase in (
        "a forked or copied claude code session's copied records keep their uuids",
        "are the parent session's events, never the fork's",
        "recognising copies by forkedfrom or by their position before the fork's own sessionstart:fork hook group",
        "so no ref is re-scoped (r10 untouched)",
        "halts loudly (decision 207)",
        "makes the next pass halt on its moved session pin",
    ):
        assert phrase in external_ref, phrase
    pin = envelope.fields["session_started_at"]
    assert "take no part in the pin" in _norm(pin.semantics)
    assert "only the records it wrote itself count (see external_ref)" in _norm(pin.description)
    assert _norm(pin.populated_by).endswith("earliest root-transcript timestamp of the session's own records.")
    assert _v3_logged(envelope.fields["external_ref"].amendment_log) and _v3_logged(pin.amendment_log)


def test_parser_v3_cut_rules_are_declared_in_the_envelope() -> None:
    envelope = load_contract(_CONTRACTS_DIR / "telemetry-event-envelope.yaml")
    governance = _norm(envelope.governance_notes)
    assert "a recorded finalization cut, which treats the tree as final)" in governance
    assert (
        "incremental emission equals the emission set recomputed from the tree and its recorded finalization cuts"
        in governance
    )
    assert "equals a full re-parse" not in governance
    for phrase in (
        "the cuts survive a parser_version reset like the pins, so a bump re-emits every finalization's close row",
        "(cut_content_divergence)",
        "is superseded, never retracted (reanchored_close)",
        "not stored durably",
    ):
        assert phrase in governance, phrase
    assert "with one carve-out" in governance and "re-pins only while the cursor has emitted no row" in governance
    version = _norm(envelope.fields["parser_version"].semantics)
    assert "and the recorded finalization cuts persisted with its cursor across a reset" in version
    assert _v3_logged(envelope.amendment_log) and _v3_logged(envelope.fields["parser_version"].amendment_log)


def test_parser_v3_session_close_rules_are_declared() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_sessions.yaml")
    outcome = _norm(doc.fields["outcome"].governance_notes_local)
    assert (
        "cancelled when the last turn was interrupted" in outcome
        and "failure_reason carries that record's error kind" in outcome
    )
    assert "emits no row in any telemetry table until it has one" in outcome
    external_ref = _norm(doc.fields["external_ref"].governance_notes_local)
    assert (
        "at which the session's open row is due, unless an earlier finalization already wrote the same anchor" in external_ref
    )
    assert "one close row is written per finalization" in external_ref
    for name in ("event_timestamp", "source_ordinal", "external_ref"):
        assert "last root-stream record carrying a uuid and a parseable timestamp" in _norm(
            doc.fields[name].governance_notes_local
        ), name
    assert "open row is due" in _norm(doc.governance_notes)
    for name in ("outcome", "event_timestamp", "source_ordinal", "external_ref"):
        assert _v3_logged(doc.fields[name].amendment_log), name
    assert _v3_logged(doc.amendment_log)


def test_parser_v3_observation_and_transcript_surface_is_declared() -> None:
    doc = load_contract(_CONTRACTS_DIR / "telemetry_observations.yaml")
    kind = _norm(doc.fields["observation_type"].description)
    assert "their field additions are realized" in kind and "t3.20's build" not in kind
    assert "turn open and close carry sequence" in kind and "(decision 210 cl.1)" in kind
    name = _norm(doc.fields["name"].semantics)
    assert "only from structured markers in a bash tool_call's full output" in name and "role gate_event.<n>" in name
    parent = _norm(doc.fields["parent_observation_id"].governance_notes_local)
    assert "not hook-gated" in parent and "dq intent belongs to rec-4101" in parent
    outcome = _norm(doc.fields["outcome"].semantics)
    assert "stored once" not in outcome and "stored on each close row that observes it" in outcome
    assert "latest close by event_timestamp, then source_ordinal, then event_id" in outcome
    for field in ("observation_type", "name", "parent_observation_id", "outcome"):
        assert _v3_logged(doc.fields[field].amendment_log), field
    transcripts = load_contract(_CONTRACTS_DIR / "telemetry_transcripts.yaml")
    note = _norm(transcripts.governance_notes)
    assert "claude_code parser_version 3 emits prompt (human), response, thinking and tool_input (agent)" in note
    assert "attachment-derived system rows are not emitted; a fork's inherited records emit none" in note
    assert _v3_logged(transcripts.amendment_log)
