"""Pins ports.yaml's SA-14 (verdict chain shell) and PC-12 (Decision 263 clause 4) records."""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]


def _ports() -> dict:
    return yaml.safe_load((_REPO / "docs/contracts/ports.yaml").read_text(encoding="utf-8"))


def test_sa14_records_the_verdict_chain_shell() -> None:
    rows = [r for r in _ports()["structural_assumptions"] if r["id"] == "SA-14"]
    assert len(rows) == 1
    row = rows[0]
    assert row["location"] == "scripts/personas/"
    assert row["owning_item"] == "T4.24:c9"
    for token in ("persona-verdict.yml", "workflow_run", "head_sha", "Checks API", "github-actions[bot]", "docs/plans/"):
        assert token in row["assumption"], token


def test_pc12_records_decision_263_clause_4() -> None:
    rows = [r for r in _ports()["coupling_findings"] if r["id"] == "PC-12"]
    assert len(rows) == 1
    row = rows[0]
    assert {"verdict_plane", "persona_compute_host", "secrets"} <= set(row["ports"])
    assert row["owning_item"] == "dec-263"
    assert (_REPO / row["location"]).is_file()
    assert "Decision 263 clause 4" in row["description"]
    assert "assumes no AWS identity" in row["description"]
