"""The parser_version 8 contract text (PLAN-telemetry-claude-p-capture-adapter): the four Class A notes and realization moves,
host_run_ref's required_when, the reader verb line, the CI allow-list row, the ports coupling and the assumption row."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-telemetry-claude-p-capture-adapter"
TOP_LEVEL = ["workflow_run", "workflow_dispatch", "push", "schedule", "eventbridge", "manual", "smoke_test"]


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def logged(entries: list[Any]) -> list[Any]:
    """The amendment_log entries this plan wrote (dated the build day, naming the plan)."""
    return [e for e in entries if PLAN in str(e.get("summary")) and str(e.get("date")) == "2026-10-30"]


def test_telemetry_agents_requires_host_run_ref_on_top_level_triggers_and_realizes_both_columns() -> None:
    doc = load("telemetry_agents.yaml")
    column = doc["fields"]["host_run_ref"]
    assert column["dq_intent"]["required_when"] == {"trigger": TOP_LEVEL}
    assert (
        "exclude_before" not in str(column["dq_intent"])
        and "claude_code producer from parser_version 8" in column["populated_by"]
    )
    assert len(logged(column["amendment_log"])) == 1
    produced = doc["realization"]["produced"]["claude_code"]
    assert {"workflow_run_id", "host_run_ref"} <= set(produced)
    assert "T3.20:c9" not in doc["realization"]["unproduced"]
    assert "claude_code producer from parser_version 8" in doc["fields"]["workflow_run_id"]["populated_by"]
    assert "parser_version 8 the claude_code producer writes these rows" in " ".join(doc["governance_notes"].split())
    assert "keyed by its closing record" in doc["run_shapes"]["top_level"]["external_ref"]
    assert len(logged(doc["amendment_log"])) == 1


def test_the_envelope_carries_the_parser_version_8_note_after_the_7_note() -> None:
    doc = load("telemetry-event-envelope.yaml")
    notes = doc["governance_notes"]
    seven, eight, r8 = (notes.index(m) for m in ("PARSER_VERSION 7 (", "PARSER_VERSION 8 (", "R8: non-replayable producers"))
    assert seven < eight < r8
    note = notes[eight:r8]
    for needle in ("host-run sidecar", "P-5", "pin_conflict", "withheld", "top-level telemetry_agents run rows", "rule SW-1"):
        assert needle in note, needle
    assert len(logged(doc["amendment_log"])) == 1


def test_transcripts_and_sessions_notes() -> None:
    transcripts = load("telemetry_transcripts.yaml")
    assert (
        "from parser_version 8 on every row that would carry content"
        in transcripts["fields"]["content_omitted_reason"]["populated_by"]
    )
    assert "From parser_version 8" in " ".join(transcripts["governance_notes"].split())
    assert len(logged(transcripts["amendment_log"])) == 1
    sessions = load("telemetry_sessions.yaml")
    note = " ".join(sessions["governance_notes"].split())
    assert "TOP-LEVEL ROOTS (parser_version 8" in note and "host.checkout_sha" in note
    assert "host.sha is the triggering commit and never feeds checkout_sha" in note and "no live git call" in note
    assert len(logged(sessions["amendment_log"])) == 1


def test_the_reader_contract_names_host_run_ref_on_telemetry_root_sessions() -> None:
    doc = load("ducklake_reader.yaml")
    text = " ".join((CONTRACTS / "ducklake_reader.yaml").read_text(encoding="utf-8").split())
    assert "telemetry_root_sessions(day_from, day_to)" in text
    assert "host_run_ref (the root's top-level run identity" in text
    assert len(logged(doc["amendment_log"])) == 1


def test_project_configuration_carries_the_ci_allow_list_row_and_the_new_pin_reader() -> None:
    doc = load("project-configuration.yaml")
    rows = {r["id"]: r for r in doc["settings"]} if "settings" in doc else {r["id"]: r for r in doc["rows"]}
    row = rows["project.telemetry_capture.ci_workflows"]
    assert row["this_repository"]["current_home"] == "config/telemetry/capture.yaml" and row["set_by"] == "user"
    assert "scripts/telemetry_capture/ci_pass.py" in row["readers"] and "scripts/telemetry_capture/switch.py" in row["readers"]
    assert "scripts/telemetry_capture/ci_pass.py" in rows["project.ref"]["readers"]
    assert len(logged(doc["amendment_log"])) == 1


def test_ports_name_ci_pass_and_record_the_adapter_assumption() -> None:
    doc = load("ports.yaml")
    pc01 = next(c for c in doc["coupling_findings"] if c["id"] == "PC-01")
    assert "scripts/telemetry_capture/ci_pass.py" in pc01["description"]
    sa06 = next(a for a in doc["structural_assumptions"] if a["id"] == "SA-06")
    assert sa06["location"].startswith("scripts/telemetry_capture/ci_pass.py") and sa06["owning_item"] == "T4.24:c9"
    assert "--session-id" in sa06["assumption"] and "host-run.json" in sa06["assumption"]
    assert len(logged(doc["amendment_log"])) == 1
