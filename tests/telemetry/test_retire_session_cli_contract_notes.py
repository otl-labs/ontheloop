"""The amended contracts and Decision 199 carry the session CLI retirement and the parser_version 6 rules (rec-4589)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks.structural._classify import effective_lines
from tests.fixtures.retired_session_cli import RETIRED_SYMBOLS

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "contracts"
PLAN = "PLAN-retire-session-cli"
DATE = "2026-10-24"


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / f"{name}.yaml").read_text(encoding="utf-8"))


def logged(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if PLAN in str(e.get("summary")) and e.get("date") == DATE]


def flat(value: Any) -> str:
    return " ".join(str(value).split())


def test_observations_name_rule_accepts_the_status_source() -> None:
    name = load("telemetry_observations")["fields"]["name"]
    pattern = name["dq_intent"]["pattern_when"][0]["pattern"]
    match = re.match(r"\^\(([A-Za-z|]+)\):", pattern)
    assert match and match.group(1).split("|") == ["hook", "gate", "precommit", "status"]
    assert re.fullmatch(pattern, "status:ops_portal.file_rec.skipped")
    assert not re.fullmatch(pattern, "never_on_main")
    assert "the source is hook, gate, precommit or status" in flat(name["semantics"])


def test_observations_declares_the_status_rows() -> None:
    contract = load("telemetry_observations")
    name = contract["fields"]["name"]
    semantics = flat(name["semantics"])
    for token in (
        "From parser_version 6",
        "status:<source>.skipped (severity warning)",
        "status:<source>.failed (severity error)",
        "last 200 output lines",
        "(-m scripts. or scripts/)",
        "gate_event.<n>",
        "done emits nothing",
        "reason is never stored",
        "192 characters is dropped and counted",
        "echoed or printed whole STATUS line",
        "rec-4605",
        "hook_fault",
        "unmapped_block",
    ):
        assert token in semantics, token
    entries = logged(name["amendment_log"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["semantic_break"] is True and "Decision 210 cl.4" in entry["summary"]
    assert re.search(r"census:\s*0 violators \(\d{4}-\d{2}-\d{2}\)", entry["migration_story"])
    path = CONTRACTS / "telemetry_observations.yaml"
    assert effective_lines(path.read_text(encoding="utf-8")) <= 498


def test_sessions_declares_workflow_attribution() -> None:
    field = load("telemetry_sessions")["fields"]["workflow"]
    semantics = flat(field["semantics"])
    for token in (
        "From parser_version 6",
        "first Skill tool call at or before the open bound",
        "planning reads plan",
        "audit-prompt reads audit",
        "plan-critique, decision-scout, code-review and executor-rca read under their own names",
        "an errored call counting",
        "a child's command-name tag is never read",
        "a workflow begun after the first turn reads the fallback",
        "read-time join on parent_session_id, never stored",
        "a top-level run's root reads its agent's source_registry key",
    ):
        assert token in semantics, token
    assert "REQUIRED_FIELDS" not in semantics
    assert len(logged(field["amendment_log"])) == 1


def test_session_id_names_no_live_legacy_producer() -> None:
    contract = load("session-id")
    field = contract["fields"]["session_id"]
    for key in ("semantics", "populated_by"):
        text = flat(field[key])
        assert not any(symbol in text for symbol in RETIRED_SYMBOLS), key
        assert "live producer" not in text and "LIVE legacy" not in text, key
    assert "rec-4589" in flat(field["semantics"])
    notes = flat(field["governance_notes"])
    assert "live-producer" not in notes and "legacy UUID4" not in notes and "rec-4030" in notes
    assert "planning_session_ref" not in notes
    entries = logged(field["amendment_log"])
    assert len(entries) == 1 and entries[0]["semantic_break"] is True
    raw = (CONTRACTS / "session-id.yaml").read_text(encoding="utf-8")
    assert "legacy-producer migration" not in raw


def test_joins_names_no_live_legacy_minter() -> None:
    contract = load("_joins")
    join = contract["joins"]["planning_session_id_join_execution_plans_to_sessions"]
    text = flat(join["description"])
    assert "legacy" not in text and "live UUID4" not in text
    assert "executor's Claude CLI session id" in text and "rec-4030 owns" in text
    entries = logged(contract["amendment_log"])
    assert len(entries) == 1 and entries[0]["semantic_break"] is False


def test_decision_199_records_the_retirement() -> None:
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    start = text.index("## Decision 199:")
    section = text[start : text.index("\n## Decision 200:", start)]
    updates = [line for line in section.splitlines() if line.startswith(f"> **Update ({DATE}):**")]
    assert len(updates) == 1
    assert "rec-4589" in updates[0] and "retired rather than migrated" in updates[0] and "PR #969" in updates[0]
    clause = next(line for line in section.splitlines() if line.startswith("3. Identity"))
    assert "rec-4589" not in clause and "is not yet migrated to this derivation, tracked as pending rec-4030." in clause
