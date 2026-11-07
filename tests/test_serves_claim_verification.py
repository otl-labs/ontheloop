"""PLAN-serves-claim-verification (rec-4915): 12p serves verdict pins, contract pins, drafts-report INFO lines."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.north_star_models import load_north_star
from scripts.roadmap import plan_decision_drafts as pdd

REPO = Path(__file__).resolve().parents[1]
_SKILL = REPO / ".claude/skills/plan-critique/SKILL.md"
_ENTRY = REPO / "docs/contracts/decision-entry.yaml"
_ENV_TAIL = "significance:\n  value: numbered_decision\n  justification: r.\nkind:\n  value: direction\n  justification: j.\n"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _draft(serves: list[str] | None = None, title: str = "Drafted", related: str = "") -> str:
    extra = f"serves: [{', '.join(serves)}]\n" if serves else ""
    rel = f"\n**Related:** {related}\n" if related else ""
    return f"## Decision {{{{N}}}}: {title}\n\n```yaml\nnumber: {{{{N}}}}\n{_ENV_TAIL}{extra}```\n\n**Status:** Decided\n{rel}"


def _plan(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "PLAN-demo.yaml"
    path.write_text(yaml.safe_dump({"slug": "demo", "intent": "x", "context": [text]}), encoding="utf-8")
    return path


def _entry(ns_id: str):
    return next(p for p in load_north_star().principles if p.id == ns_id)


def _infos(lines: list[str]) -> list[str]:
    return [x for x in lines if x.lstrip().startswith("INFO")]


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


class TestDraftsReportInfo:
    def test_one_id_prints_one_info_line(self, tmp_path: Path) -> None:
        entry = _entry("NS.6")
        lines = pdd.report_for_plan(_plan(tmp_path, _draft(["NS.6"])))
        assert lines[0] == "decision_drafts: 1 drafted, 0 findings"
        (info,) = _infos(lines)
        assert f'principle "{entry.principle}"' in info
        assert f'failure_signal "{_flat(entry.failure_signal)}"' in info
        assert f"carried_by [{', '.join(str(n) for n in entry.carried_by)}]" in info
        assert "links none" in info

    def test_related_line_naming_a_carrier_is_a_link(self, tmp_path: Path) -> None:
        carrier = _entry("NS.6").carried_by[0]
        (info,) = _infos(pdd.report_for_plan(_plan(tmp_path, _draft(["NS.6"], related=f"Decision {carrier}"))))
        assert f"links {carrier} " in info

    def test_amends_edge_into_carried_by_is_a_link(self, tmp_path: Path) -> None:
        carrier = _entry("NS.6").carried_by[0]
        (info,) = _infos(pdd.report_for_plan(_plan(tmp_path, _draft(["NS.6"], title=f"Drafted (amends Decision {carrier})"))))
        assert f"links {carrier} " in info

    def test_two_ids_print_two_lines_in_envelope_order(self, tmp_path: Path) -> None:
        ids = sorted(load_north_star().ids())[:2]
        lines = _infos(pdd.report_for_plan(_plan(tmp_path, _draft(list(reversed(ids))))))
        assert [x.split()[3].rstrip(":") for x in lines] == list(reversed(ids))

    def test_unknown_id_prints_fail_and_no_info(self, tmp_path: Path) -> None:
        lines = pdd.report_for_plan(_plan(tmp_path, _draft(["NS.9999"])))
        assert any(x.startswith("  FAIL") and "serves" in x for x in lines)
        assert _infos(lines) == []

    def test_draft_without_serves_prints_none(self, tmp_path: Path) -> None:
        assert _infos(pdd.report_for_plan(_plan(tmp_path, _draft()))) == []

    def test_findings_never_include_info(self, tmp_path: Path) -> None:
        findings = pdd.findings_for_plan(_plan(tmp_path, _draft(["NS.6"])))
        assert all(f.severity != "INFO" for f in findings)

    def test_missing_north_star_prints_none_without_raising(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdd, "ROOT", tmp_path)
        assert _infos(pdd.report_for_plan(_plan(tmp_path, _draft(["NS.6"])), tmp_path)) == []
