"""The parser_version 10 contract text (PLAN-telemetry-hook-denial-names): envelope line and ports SA-02 clause."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-telemetry-hook-denial-names"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def _effective_lines(name: str) -> int:
    lines = (CONTRACTS / name).read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))


def test_envelope_declares_parser_version_10_directly_after_r8() -> None:
    text = (CONTRACTS / "telemetry-event-envelope.yaml").read_text(encoding="utf-8")
    v9, r8, v10 = (text.index(token) for token in ("  PARSER_VERSION 9 (", "  R8: non-replayable", "  PARSER_VERSION 10 ("))
    assert v9 < r8 < v10
    assert text[v9:r8].count("PARSER_VERSION 10") == 0 and len(text[v9:r8]) < 2000
    line = text[v10 : text.index("\n", v10)]
    for token in (
        PLAN,
        "rec-4959",
        "HD-1",
        "HD-2",
        "HD-3",
        "HD-4",
        "HOOK-BLOCK v1",
        "permissionDecision.source",
        "hook:<event>.<tool>",
    ):
        assert token in line, token
    assert "DQ intents" in text[text.index("\n", v10) :]


def test_envelope_amendment_log_holds_the_parser_version_10_entry() -> None:
    entry = next(e for e in _load("telemetry-event-envelope.yaml")["amendment_log"] if PLAN in str(e.get("summary")))
    assert entry["semantic_break"] is True and "parser_version 10" in entry["summary"]


def test_ports_sa02_names_the_hook_denial_assumption() -> None:
    doc = _load("ports.yaml")
    sa02 = next(a for a in doc["structural_assumptions"] if a["id"] == "SA-02")["assumption"]
    assert "a hook's block may survive only in the denied tool_result text" in sa02
    assert any(PLAN in str(e.get("summary")) for e in doc["amendment_log"])


def test_contracts_stay_within_the_line_cap() -> None:
    assert _effective_lines("telemetry-event-envelope.yaml") <= 500
    assert _effective_lines("ports.yaml") <= 500
