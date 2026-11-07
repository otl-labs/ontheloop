"""The parser_version 9 contract text (PLAN-telemetry-capture-hook-labels): envelope line, SA-02, project.ref row."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-telemetry-capture-hook-labels"


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def test_envelope_declares_parser_version_9_after_8_and_before_r8() -> None:
    text = (CONTRACTS / "telemetry-event-envelope.yaml").read_text(encoding="utf-8")
    v8, v9, r8 = (text.index(token) for token in ("  PARSER_VERSION 8 (", "  PARSER_VERSION 9 (", "  R8: non-replayable"))
    assert v8 < v9 < r8
    line = text[v9:r8]
    for token in (PLAN, "rec-4939", "P-2", "PV-1", "WT-2", "H-1", "pin_ref_disagreement", "capture pin project_ref="):
        assert token in line, token
    assert len(line) < 2000
    entry = next(e for e in load("telemetry-event-envelope.yaml")["amendment_log"] if PLAN in str(e.get("summary")))
    assert str(entry["date"]) == "2026-11-07" and entry["semantic_break"] is True


def test_ports_sa02_names_the_label_and_stderr_assumption() -> None:
    doc = load("ports.yaml")
    sa02 = next(a for a in doc["structural_assumptions"] if a["id"] == "SA-02")["assumption"]
    for token in ("command hook N", "2.1.317", "statusMessage", "stderr lines", "legacy form"):
        assert token in sa02, token
    assert any(PLAN in str(e.get("summary")) for e in doc["amendment_log"])


def test_project_ref_row_names_the_stderr_echo() -> None:
    doc = load("project-configuration.yaml")
    row = next(r for r in _rows(doc) if r.get("id") == "project.ref")
    assert "capture pin project_ref=" in row["this_repository"]["note"]
    assert ".claude/hooks/session_start_log_turn.sh" in row["readers"]
    assert any(PLAN in str(e.get("summary")) for e in doc["amendment_log"])


def _rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for value in doc.values() if isinstance(value, list) for r in value if isinstance(r, dict)]
