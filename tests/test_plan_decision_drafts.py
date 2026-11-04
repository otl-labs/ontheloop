"""Planning-time decision_drafts report (PLAN-decision-serves-kind-grammar, Decision 243 clause 8)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.roadmap import plan_decision_drafts as pdd
from scripts.roadmap.plan_obligations import main

REPO = Path(__file__).resolve().parents[1]
_FIXTURE = REPO / "tests/fixtures/decision_drafts/placeholder_conforming.txt"
_ENV_TAIL = "significance:\n  value: numbered_decision\n  justification: r.\nkind:\n  value: direction\n  justification: j.\n"


def _draft(
    number: str = "{{N}}", env_number: str | None = None, kind: bool = True, extra: str = "", title: str = "Drafted"
) -> str:
    tail = _ENV_TAIL if kind else _ENV_TAIL.split("kind:")[0]
    return (
        f"## Decision {number}: {title}\n\n```yaml\nnumber: {env_number or number}\n{tail}{extra}```\n\n**Status:** Decided\n"
    )


def _plan(tmp_path: Path, text: str, slug: str = "demo") -> Path:
    path = tmp_path / f"PLAN-{slug}.yaml"
    path.write_text(yaml.safe_dump({"slug": slug, "intent": "x", "context": [text]}), encoding="utf-8")
    return path


def _sidecar(tmp_path: Path, blocks: list[dict], slug: str = "demo") -> None:
    d = tmp_path / "docs" / "plans" / "approved"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.yaml").write_text(yaml.safe_dump({"schema_version": 1, "slug": slug, "blocks": blocks}), encoding="utf-8")


def _lines(path: Path, root: Path | None = None) -> list[str]:
    return pdd.report_for_plan(path, root)


class TestPlanText:
    def test_conforming_placeholder_draft_reports_clean(self, tmp_path: Path) -> None:
        assert _lines(_plan(tmp_path, _draft())) == ["decision_drafts: 1 drafted, 0 findings"]

    def test_missing_kind_reports_a_fail_quoting_the_test(self, tmp_path: Path) -> None:
        lines = _lines(_plan(tmp_path, _draft(kind=False)))
        assert lines[0] == "decision_drafts: 1 drafted, 1 findings"
        assert lines[1].startswith("  FAIL {{N}} kind: kind element is missing")
        assert "If the entry's own clauses were undone" in lines[1]

    def test_missing_significance_and_never_numbered_routing_fail(self, tmp_path: Path) -> None:
        no_sig = _draft().replace(_ENV_TAIL.split("kind:")[0], "")
        assert any("significance" in f.field for f in pdd.findings_for_plan(_plan(tmp_path, no_sig)))
        never = _draft().replace("numbered_decision", "operational_fact")
        assert any(
            f.field == "significance" and "Never a numbered Decision" in f.detail.replace("never", "Never")
            for f in pdd.findings_for_plan(_plan(tmp_path, never))
        )

    def test_literal_draft_with_a_later_reversal_conditions_stanza_passes(self, tmp_path: Path) -> None:
        text = _draft() + "\n```yaml reversal-conditions\ncondition: x\n```\n"
        assert _lines(_plan(tmp_path, text)) == ["decision_drafts: 1 drafted, 0 findings"]

    @pytest.mark.parametrize("blank", [False, True])
    def test_both_folded_scalar_shapes_report_the_one_envelope_fail(self, tmp_path: Path, blank: bool) -> None:
        sep = "\n" if blank else " "
        text = f"## Decision {{{{N}}}}: Folded{sep}```yaml number: {{{{N}}}} kind: x ```"
        found = pdd.findings_for_plan(_plan(tmp_path, text))
        assert [(f.field, f.detail) for f in found] == [("envelope", "folded scalar: put the draft in a literal (|) block")]

    def test_quoted_scalars_pass(self, tmp_path: Path) -> None:
        for quote in ('"', "'"):
            text = _draft().replace("justification: j.", f"justification: {quote}j.{quote}")
            assert _lines(_plan(tmp_path, text)) == ["decision_drafts: 1 drafted, 0 findings"]

    def test_an_anchor_heading_with_no_title_is_not_a_draft(self, tmp_path: Path) -> None:
        assert _lines(_plan(tmp_path, "see\n## Decision 229:\nthen text")) == ["decision_drafts: none"]

    def test_a_concrete_number_is_one_warn_never_a_number_fail(self, tmp_path: Path) -> None:
        found = pdd.findings_for_plan(_plan(tmp_path, _draft(number="9999", title="Not landed")))
        assert [(f.severity, f.field, f.detail) for f in found] == [
            ("WARN", "number", "assign at implementation: Decision 217 cl.5")
        ]

    def test_findings_accept_str_and_path_and_carry_title_and_field(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, _draft(kind=False))
        for arg in (plan, str(plan)):
            found = pdd.findings_for_plan(arg)
            assert found[0].title == "Drafted" and found[0].field == "kind"

    def test_a_plan_with_no_drafted_decision_reports_none(self, tmp_path: Path) -> None:
        assert _lines(_plan(tmp_path, "plain text")) == ["decision_drafts: none"]

    def test_an_existing_entry_quoted_in_the_plan_is_not_a_draft(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DECISIONS.md").write_text("## Decision 5: Already here\n", encoding="utf-8")
        text = "## Decision 5: Already here\n\nquoted anchor text\n"
        assert _lines(_plan(tmp_path, text), root=tmp_path) == ["decision_drafts: none"]

    def test_an_unreadable_plan_is_a_finding_not_a_raise(self, tmp_path: Path) -> None:
        found = pdd.findings_for_plan(tmp_path / "missing.yaml")
        assert found[0].severity == "FAIL" and found[0].title == "" and found[0].field == "path"
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [", encoding="utf-8")
        assert pdd.findings_for_plan(bad)[0].field == "path"
        scalar = tmp_path / "scalar.yaml"
        scalar.write_text("just text", encoding="utf-8")
        assert pdd.findings_for_plan(scalar) == []


class TestSidecar:
    def test_a_sidecar_block_draft_is_found_and_a_quoted_before_heading_is_not(self, tmp_path: Path) -> None:
        quoted = "## Decision 5: Existing\n\nold\n"
        blocks = [{"id": "b1", "file": "docs/DECISIONS.md", "before": quoted, "after": quoted + "\n" + _draft()}]
        _sidecar(tmp_path, blocks)
        assert _lines(_plan(tmp_path, "x"), root=tmp_path) == ["decision_drafts: 1 drafted, 0 findings"]

    def test_a_sidecar_draft_without_kind_fails(self, tmp_path: Path) -> None:
        _sidecar(
            tmp_path,
            [{"id": "b1", "file": "docs/DECISIONS.md", "before": "anchor\n", "after": "anchor\n" + _draft(kind=False)}],
        )
        assert any(f.field == "kind" for f in pdd.findings_for_plan(_plan(tmp_path, "x"), root=tmp_path))

    def test_other_files_are_ignored(self, tmp_path: Path) -> None:
        _sidecar(tmp_path, [{"id": "b1", "file": "docs/other.md", "before": "a\n", "after": "a\n" + _draft(kind=False)}])
        assert _lines(_plan(tmp_path, "x"), root=tmp_path) == ["decision_drafts: none"]

    def test_an_unparseable_sidecar_is_a_finding(self, tmp_path: Path) -> None:
        d = tmp_path / "docs" / "plans" / "approved"
        d.mkdir(parents=True)
        (d / "demo.yaml").write_text("not: a sidecar\n", encoding="utf-8")
        found = pdd.findings_for_plan(_plan(tmp_path, "x"), root=tmp_path)
        assert found[0].field == "sidecar" and found[0].title == ""


class TestDraftFile:
    def test_the_committed_fixture_reports_clean_through_the_cli(self, capsys) -> None:
        assert main(["--decision-draft", str(_FIXTURE)]) == 0
        assert capsys.readouterr().out.strip() == "decision_drafts: 1 drafted, 0 findings"

    def test_a_draft_missing_kind_prints_a_fail_and_still_exits_zero(self, tmp_path: Path, capsys) -> None:
        draft = tmp_path / "d.txt"
        draft.write_text(_draft(kind=False), encoding="utf-8")
        assert main(["--decision-draft", str(draft)]) == 0
        assert "  FAIL {{N}} kind:" in capsys.readouterr().out

    def test_an_unreadable_draft_is_a_finding(self, tmp_path: Path) -> None:
        assert pdd.findings_for_draft(tmp_path / "nope.txt")[0].field == "path"

    def test_plan_cli_appends_the_section_and_exits_zero(self, tmp_path: Path, capsys) -> None:
        assert main(["--plan", str(_plan(tmp_path, "plain text"))]) == 0
        assert "decision_drafts: none" in capsys.readouterr().out


class TestMissingContracts:
    def test_a_missing_contract_set_still_yields_findings_and_never_raises(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(pdd, "ROOT", tmp_path)
        found = pdd.findings_for_plan(_plan(tmp_path, _draft()), root=tmp_path)
        assert all(isinstance(f, pdd.Finding) for f in found)
