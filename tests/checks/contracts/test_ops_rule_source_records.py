"""The recorded ops rule-source choice (rec-4158 plan A): Decision annotations, the write-time-enforcement statement, the
contract's own header, the lineage note and the re-grounded roadmap items. Decision numbers resolve from headers."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.fixtures.decision_entry import authored_bytes, cap_bytes, decision_entry

_ROOT = Path(__file__).resolve().parents[3]
_DECISIONS = (_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
_CONTRACTS = _ROOT / "docs" / "contracts"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"^>\s?", "", text, flags=re.MULTILINE).split()).lower()


def _section(number: int) -> str:
    marker = f"## Decision {number}:"
    assert _DECISIONS.count(marker) == 1, f"DECISIONS.md must carry exactly one header for Decision {number}"
    start = _DECISIONS.index(marker)
    end = _DECISIONS.find("\n## Decision ", start + 1)
    return _DECISIONS[start : end if end != -1 else len(_DECISIONS)]


def _standard_statement() -> str:
    standard = yaml.safe_load((_CONTRACTS / "data-modeling-standard.yaml").read_text(encoding="utf-8"))
    return next(rule for rule in standard["rules"] if rule["id"] == "write-time-enforcement")["statement"]


def test_decision_annotations() -> None:
    number = int(re.match(r"Decision (\d+):", _standard_statement().strip()).group(1))
    body = _section(number)
    assert authored_bytes(decision_entry(_DECISIONS, number)) <= cap_bytes()
    annotation = re.search(r"^\[Amendment \d{4}-\d{2}-\d{2}, rec-4158:.*?\]$", body, re.MULTILINE | re.DOTALL)
    assert annotation, f"Decision {number} must end with a dated [Amendment ..., rec-4158: ...] annotation"
    text = _norm(annotation.group(0))
    for anchor in (
        "class a contract",
        "ops_recommendations.yaml",
        "field_semantics.yaml",
        "repository_state",
        "no date moves later",
    ):
        assert anchor in text, anchor
    assert body.rstrip().endswith("---") and body.index(annotation.group(0)) > body.index("**Related:**")
    for decision, anchors in (
        (65, ("class a contract", "parity-checked copy", "rec-4158 plan c", "t1.6 c7", "t2.29 c7")),
        (64, ("2026-05-01", "floor", "rule-effective date", "never a moved anchor", "ratchet")),
    ):
        update = re.search(r"> \*\*Update \(2026-\d{2}-\d{2}\):\*\* \(rec-4158.*?(?:\n\n|\Z)", _section(decision), re.DOTALL)
        assert update, f"Decision {decision} must carry a dated rec-4158 update"
        for anchor in anchors:
            assert anchor in _norm(update.group(0)), (decision, anchor)


def test_standard_records_the_ops_source() -> None:
    statement = _norm(_standard_statement())
    assert "is rec-4158's" not in statement and "choice among" not in statement
    for phrase in (
        "owning class a contract",
        "field_semantics.yaml is its generated projection",
        "dq-side artefacts",
        "write_time_exemptions {class, reason, owner}",
        "class cross_row or temporary names its owning rec",
        "class repository_state",
        "older rows",
        "exclude_before",
        "decision 64 anchor 2026-05-01",
        "never a moved anchor",
        "a date never moves later",
        "validate_ops_rule_source",
        "ops writer (src/common/ducklake_write_rules.py over src/row_rules/rules.py",
        "--lambda-row-rules smoke gate",
        "validate_write_time_enforcement_conformance",
        "rec-4167",
        "rec-4166",
        "decision 181 cl.2",
        "the portal -> write_validators.py gate_write, the writer's own gate run as a derived pre-check",
        "inserts every rule, updates and upserts undated rules only",
        "ops.yaml -> dq backstops only, held never stricter than the contract by validate_ops_rule_source",
    ):
        assert phrase in statement, phrase
    assert "(its portal row-rule gate plan)" not in statement


def test_ops_recommendations_names_one_authority() -> None:
    text = (_CONTRACTS / "ops_recommendations.yaml").read_text(encoding="utf-8")
    header = text.split("\ncontract:\n", 1)[0]
    assert "rec.py" not in header
    notes = yaml.safe_load(text)["governance_notes"]
    assert "rec.py" not in notes and "ONE declared source" in notes
    doc = yaml.safe_load(text)
    log = doc["amendment_log"]
    assert any(
        e["change_class"] == "accepted_values_narrow"
        and e["semantic_break"] is True
        and "PLAN-ops-rule-source" in e["summary"]
        for e in log
    )
    status = doc["fields"]["status"]
    assert "in_progress" not in status["description"] + status["semantics"] and "deferred" not in status["description"]
    assert any(e["change_class"] == "prose_improvement" and e["semantic_break"] is False for e in status["amendment_log"])
    assert "Null for cross-cutting" not in doc["fields"]["file"]["semantics"]
    assert "write_time" not in doc["fields"]["source"]["dq_intent_local"]["not_null"]


def test_roadmap_is_regrounded() -> None:
    roadmap = yaml.safe_load((_ROOT / "docs" / "ROADMAP-PLATFORM.yaml").read_text(encoding="utf-8"))
    flat = yaml.safe_dump(roadmap, width=10_000)
    assert (
        "Amendment 2026-10-04, rec-4158, Decision 210 cl.3 -- schema and write-time rules live in the owning Class A" in flat
    )
    assert (
        "Amendment 2026-10-04, rec-4158 / Decision 210 cl.3 -- for the ops tables the owning Class A contract displaces"
        in flat
    )
    items = _items_by_id(roadmap)
    t16 = items["T1.6"]
    assert "Annotated metadata" not in t16["name"] and "declared rules" in t16["name"]
    assert "Class A contracts' declared rules" in t16["intent"]
    assert "Annotated" not in t16["decomposition_hints"]["atomic_plans"][2]
    c7 = next(c["text"] for c in t16["exit_criteria"] if c["text"].startswith("[Deferred phase]"))
    assert "Annotated" not in c7 and "scripts/contract_rules.py" in c7
    t229 = items["T2.29"]
    assert "ops_recommendations Class A contract" in t229["intent"] and "validate_ops_rule_source" in t229["intent"]
    criteria = {c["id"]: c for c in t229["exit_criteria"]}
    assert len(criteria) == len(t229["exit_criteria"]) and "c1" in criteria and "c9" in criteria
    c5 = criteria["c5"]["text"]
    assert "exclude_before 2026-09-01" in c5 and "rec-4158" in c5
    c6 = criteria["c6"]
    assert c6["status"] == "met" and c6["met_by"] == "ops-portal-row-rule-gate"
    for anchor in ("gate_write", "writer's own gate", "Class A contract's projection", "retired, not repointed", "rec-4158"):
        assert anchor in c6["text"], anchor
    assert "Annotated" not in c6["text"] and "rec-4158 plan C" not in c6["text"]
    assert any(
        c["text"].startswith("get_rec_write_guidance()")
        and "Class A contract (docs/contracts/ops_recommendations.yaml)" in c["text"]
        for c in criteria.values()
    )
    assert t229["status"] == "in_progress" and "ops-portal-row-rule-gate" in t229["progress_note"]


def test_portal_gate_records_land() -> None:
    """AC6: Decision 65 carries this plan's dated update, and ports.yaml records the portal's pre-check coupling."""
    update = re.search(
        r"> \*\*Update \(2026-\d{2}-\d{2}\):\*\* \(rec-4158 portal row-rule gate.*?(?:\n\n|\Z)", _section(65), re.DOTALL
    )
    assert update, "Decision 65 must carry the portal row-rule gate update"
    text = _norm(update.group(0))
    for anchor in (
        "write_time flags are retired",
        "derived pre-check",
        "never authoritative",
        "undated rules only",
        "t1.6 c7",
    ):
        assert anchor in text, anchor
    ports = yaml.safe_load((_CONTRACTS / "ports.yaml").read_text(encoding="utf-8"))
    coupling = next(c for c in ports["coupling_findings"] if c["location"] == "scripts/ops_portal/write_validators.py")
    assert coupling["status"] == "accepted" and coupling["owning_item"] == "dec-210"
    assert coupling["ports"] == ["work_item_storage", "ops_verbs"]
    description = _norm(coupling["description"])
    assert "repository projection equals the deployed one" in description and "which decision 210 cl.4 intends" in description


def _items_by_id(node, found=None) -> dict:
    found = {} if found is None else found
    if isinstance(node, dict):
        if isinstance(node.get("id"), str) and node.get("id") in ("T1.6", "T2.29") and "exit_criteria" in node:
            found[node["id"]] = node
        for value in node.values():
            _items_by_id(value, found)
    elif isinstance(node, list):
        for value in node:
            _items_by_id(value, found)
    return found


def test_source_lineage_note_matches() -> None:
    lineage = yaml.safe_load((_CONTRACTS / "source-lineage.yaml").read_text(encoding="utf-8"))
    note = _norm(lineage["fields"]["registry_key"]["governance_notes"])
    assert "write_time:true" not in note and "enforced" not in note.replace("not enforced", "")
    assert 'ops_recommendations adds not_null with exclude_before "2026-05-01" through dq_intent_local' in note


def test_title_is_not_nullable() -> None:
    doc = yaml.safe_load((_CONTRACTS / "ops_recommendations.yaml").read_text(encoding="utf-8"))
    title = doc["fields"]["title"]
    assert title["nullable"] is False
    assert "accepted by the schema gate" not in title["semantics"]
    assert any(
        e["change_class"] == "not_null_tighten"
        and e["semantic_break"] is True
        and "declarative" in e["migration_story"]
        and "rec-4171" in e["migration_story"]
        for e in title["amendment_log"]
    )


def test_writer_contract_names_row_rule() -> None:
    doc = yaml.safe_load((_CONTRACTS / "ducklake_writer.yaml").read_text(encoding="utf-8"))
    for verb in ("file_ops", "update_ops", "write_ops"):
        assert "row_rule" in doc["verbs"][verb]["typed_errors"], verb
        assert "row_rule" in doc["verbs"][verb]["response_codes"][422], verb
    assert any("row rules" in line.lower() and "before any merge" in line.lower() for line in doc["audit_invariants"])
    assert any(e["change_class"] == "field_add" and e["semantic_break"] is True for e in doc["amendment_log"])


def test_dormant_refusal_is_recorded_with_its_owners() -> None:
    roadmap = (_ROOT / "docs" / "ROADMAP-PLATFORM.yaml").read_text(encoding="utf-8")
    flat = _norm(roadmap)
    assert (
        _norm("docs/contracts/ops_priority_queue.yaml declares the queue's row rules, they project into field_semantics.yaml")
        in flat
    )
    assert (
        "dormant refusal on ops_priority_queue is lifted, before the producer's first write (decision 210 cl.4, rec-4158)"
        in flat
    )
    assert "row rules live in a class a table contract docs/contracts/ops_execution_plans.yaml" in flat
    assert "dormant refusal on ops_execution_plans lifts only once those rules project into field_semantics.yaml" in flat
    writer = yaml.safe_load((_CONTRACTS / "ducklake_writer.yaml").read_text(encoding="utf-8"))
    invariant = next(line for line in writer["audit_invariants"] if line.startswith("Dormant refusal"))
    for anchor in ("write_ops, update_ops and file_ops REFUSE", "ops_priority_queue", "ops_execution_plans", "naming dormant"):
        assert anchor in invariant, anchor
    assert "create_ops_tables still provisions them" in invariant and "T4.3 c6" in invariant and "T4.5 c2" in invariant
    assert any(e["semantic_break"] is True and "dormant refusal" in e["summary"] for e in writer["amendment_log"])
    substrate = _norm((_CONTRACTS / "storage-substrate.yaml").read_text(encoding="utf-8"))
    assert "dormant: write_ops, file_ops and update_ops refuse this table" in substrate
    assert "owner t4.3 c6" in substrate and "owner t4.5 c2" in substrate
    counters = yaml.safe_load((_CONTRACTS / "ops_entity_counters.yaml").read_text(encoding="utf-8"))
    assert any(
        line.startswith("Writer-internal disposition") and "DDL NOT NULL" in line and "control_health" in line
        for line in counters["audit_invariants"]
    )
