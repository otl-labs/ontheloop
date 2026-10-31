"""plan_lint row dispatch: the contract's gating flag decides the side; unmatched rows and faults are findings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.roadmap import plan_lint
from tests.fixtures.plan_lint_fixtures import make_plan, write, write_contract


def _plan() -> dict:
    return make_plan(("docs/contracts/x.yaml", "Modify", "p"))


def test_clean_fixture_gives_nothing(tmp_path: Path) -> None:
    write_contract(tmp_path)
    result = plan_lint.derive(_plan(), tmp_path, report=True)
    assert result.gating == []


def test_row_without_derivation_is_one_gating_finding(tmp_path: Path) -> None:
    write_contract(tmp_path, gating={"brand_new_row": False})
    result = plan_lint.derive(_plan(), tmp_path)
    assert len(result.gating) == 1 and "brand_new_row" in result.gating[0]


def test_derivation_without_row_is_one_gating_finding(tmp_path: Path) -> None:
    write_contract(tmp_path)
    contract = tmp_path / "docs/contracts/plan-obligations.yaml"
    contract.write_text(contract.read_text().replace("size_headroom", "other"), encoding="utf-8")
    result = plan_lint.derive(_plan(), tmp_path)
    assert any("size_headroom" in line and "no row" in line for line in result.gating)


def test_gating_flag_is_read_from_the_contract(tmp_path: Path) -> None:
    write(tmp_path, "docs/contracts/x.yaml", "a: 1\n")
    plan = make_plan(("docs/contracts/x.yaml", "Modify", "p"))
    write_contract(tmp_path)
    assert plan_lint.derive(plan, tmp_path, report=True).gating == []
    write_contract(tmp_path, gating={"size_headroom": True})
    assert any("docs/contracts/x.yaml" in line for line in plan_lint.derive(plan, tmp_path, report=True).gating)


def test_raising_derivation_is_contained_on_its_own_side(tmp_path: Path) -> None:
    write_contract(tmp_path)

    def boom(_ctx: object) -> list[str]:
        raise RuntimeError("kaput")

    with patch.dict(plan_lint._DISPATCH, {"pre_glob_closure": boom, "size_headroom": boom}):
        result = plan_lint.derive(_plan(), tmp_path, report=True)
    assert any("pre_glob_closure" in x and "kaput" in x for x in result.gating)
    assert any("size_headroom" in x and "kaput" in x for x in result.advisory)


def test_malformed_section_is_a_finding_never_a_raise(tmp_path: Path) -> None:
    write(tmp_path, "docs/contracts/plan-obligations.yaml", "derived_findings: nope\n")
    result = plan_lint.derive(_plan(), tmp_path)
    assert len(result.gating) == 1 and "derived_findings" in result.gating[0]
    assert plan_lint.derive(_plan(), tmp_path / "absent").gating


def test_non_boolean_gating_flag_is_a_finding(tmp_path: Path) -> None:
    write(tmp_path, "docs/contracts/plan-obligations.yaml", "derived_findings:\n  rows:\n    size_headroom: {gating: maybe}\n")
    assert "size_headroom" in plan_lint.derive(_plan(), tmp_path).gating[0]


def test_malformed_frozen_companions_is_a_finding(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/contracts/plan-obligations.yaml",
        "derived_findings:\n  rows:\n    size_headroom: {gating: false}\nfrozen_companions: 3\n",
    )
    assert "frozen_companions" in plan_lint.derive(_plan(), tmp_path).gating[0]


def test_advisory_rows_do_not_run_without_report(tmp_path: Path) -> None:
    write_contract(tmp_path)
    called: list[str] = []
    with patch.dict(plan_lint._DISPATCH, {"size_headroom": lambda ctx: called.append("x") or []}):
        plan_lint.derive(_plan(), tmp_path, report=False)
    assert called == []
