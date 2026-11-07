"""PLAN-serves-claim-verification (rec-4915): 12p serves verdict pins and decision-entry.yaml contract pins."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
_SKILL = REPO / ".claude/skills/plan-critique/SKILL.md"
_ENTRY = REPO / "docs/contracts/decision-entry.yaml"


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestCritiquePins:
    def test_12p_adjudicates_each_serves_id(self) -> None:
        text = _flat(_SKILL.read_text(encoding="utf-8"))
        for phrase in (
            "kind and serves match its body",
            "for each `serves` id",
            "`serves.verification`",
            "Any mismatch is REVISE-worthy on its own",
            "does not fire it: an amendment carries no envelope",
            "Fires when EITHER",
            "AND the plan's drafted text contains a numbered",
            "[row, kind, NS.n]",
            "or no sentence supports it",
        ):
            assert phrase in text, phrase


class TestContractPins:
    def test_serves_semantics_and_verification(self) -> None:
        serves = yaml.safe_load(_ENTRY.read_text(encoding="utf-8"))["metadata_envelope"]["field_shapes"]["serves"]
        semantics = _flat(serves["semantics"])
        assert "plan-critique step 12p verifies the claim" in semantics
        assert "the decision scout verifies the claim" not in semantics
        verification = _flat(serves["verification"])
        for phrase in (
            "own_clauses",
            "an Intent or Problem sentence alone never counts",
            "corroborates and never substitutes",
            "is a witness, never the evidence",
            "never add text to fit the claim",
        ):
            assert phrase in verification, phrase
