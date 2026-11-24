"""Mirror test for scripts/checks/deps/validate_fast_tier.py (rec-5445): the fast-tier contract's cap and ceiling."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.deps import affected_tests, selection_budget, validate_fast_tier

pytestmark = pytest.mark.unit

_INPUTS = (
    validate_fast_tier._CONTRACT_PARTS,
    validate_fast_tier._CI_PARTS,
    validate_fast_tier._CAP_SOURCE,
    validate_fast_tier._CEILING_SOURCE,
)


def _real(parts: tuple[str, ...]) -> dict:
    return yaml.safe_load(_common.ROOT.joinpath(*parts).read_text(encoding="utf-8"))


def _problems(contract: dict, workflow: dict) -> list[str]:
    return validate_fast_tier.problems(contract, workflow, affected_tests.CAP, selection_budget.CEILING_SECONDS)


def _copy_inputs(tmp_path: Path) -> Path:
    for parts in _INPUTS:
        target = tmp_path.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_common.ROOT.joinpath(*parts), target)
    return tmp_path


def test_real_contract_and_workflow_agree() -> None:
    assert _problems(_real(validate_fast_tier._CONTRACT_PARTS), _real(validate_fast_tier._CI_PARTS)) == []


def test_ceiling_matches_the_ci_timeouts() -> None:
    workflow = _real(validate_fast_tier._CI_PARTS)
    assert validate_fast_tier.derived_ceiling_seconds(workflow) == selection_budget.CEILING_SECONDS
    moved = copy.deepcopy(workflow)
    moved["jobs"]["pr-validate"]["timeout-minutes"] += 10
    contract = _real(validate_fast_tier._CONTRACT_PARTS)
    assert any("ci.yml derivation" in e for e in _problems(contract, moved))


def test_cap_drift_fails() -> None:
    contract = copy.deepcopy(_real(validate_fast_tier._CONTRACT_PARTS))
    contract["selection"]["cap_modules"] = affected_tests.CAP + 1
    assert any("cap_modules" in e for e in _problems(contract, _real(validate_fast_tier._CI_PARTS)))


def test_source_constants_equal_the_imported_ones() -> None:
    root = _common.ROOT
    assert validate_fast_tier.module_constant(root, validate_fast_tier._CAP_SOURCE, "CAP") == affected_tests.CAP
    ceiling = validate_fast_tier.module_constant(root, validate_fast_tier._CEILING_SOURCE, "CEILING_SECONDS")
    assert ceiling == selection_budget.CEILING_SECONDS


def test_annotated_constant_is_read_and_a_missing_one_raises(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("X: int\nY = 1\nCAP: int = 35\n", encoding="utf-8")
    assert validate_fast_tier.module_constant(tmp_path, ("m.py",), "CAP") == 35
    with pytest.raises(ValueError, match="no top-level literal NOPE"):
        validate_fast_tier.module_constant(tmp_path, ("m.py",), "NOPE")


def test_ceiling_constant_drift_fails() -> None:
    contract, workflow = _real(validate_fast_tier._CONTRACT_PARTS), _real(validate_fast_tier._CI_PARTS)
    drifted = validate_fast_tier.problems(contract, workflow, affected_tests.CAP, selection_budget.CEILING_SECONDS + 60)
    assert any("CEILING_SECONDS" in e for e in drifted)


def test_missing_fast_tier_step_reads_as_underived() -> None:
    workflow = copy.deepcopy(_real(validate_fast_tier._CI_PARTS))
    workflow["jobs"]["pr-validate"]["steps"] = []
    assert validate_fast_tier.derived_ceiling_seconds(workflow) is None


def test_missing_job_timeout_reads_as_underived() -> None:
    workflow = copy.deepcopy(_real(validate_fast_tier._CI_PARTS))
    del workflow["jobs"]["pr-validate"]["timeout-minutes"]
    assert validate_fast_tier.derived_ceiling_seconds(workflow) is None
    assert validate_fast_tier.derived_ceiling_seconds({}) is None


def test_a_new_bounded_step_lowers_the_derivation_and_fails() -> None:
    workflow = copy.deepcopy(_real(validate_fast_tier._CI_PARTS))
    before = validate_fast_tier.derived_ceiling_seconds(workflow)
    assert before is not None
    workflow["jobs"]["pr-validate"]["steps"].append({"name": "extra", "run": "true", "timeout-minutes": 3})
    assert validate_fast_tier.derived_ceiling_seconds(workflow) == before - 180
    assert any("ci.yml derivation" in e for e in _problems(_real(validate_fast_tier._CONTRACT_PARTS), workflow))


def test_a_non_integer_step_timeout_reads_as_underived() -> None:
    workflow = copy.deepcopy(_real(validate_fast_tier._CI_PARTS))
    workflow["jobs"]["pr-validate"]["steps"].append({"name": "extra", "run": "true", "timeout-minutes": "${{ inputs.t }}"})
    assert validate_fast_tier.derived_ceiling_seconds(workflow) is None
    assert any("ci.yml derivation" in e for e in _problems(_real(validate_fast_tier._CONTRACT_PARTS), workflow))


def test_a_timeout_on_the_fast_tier_step_itself_is_not_subtracted() -> None:
    workflow = copy.deepcopy(_real(validate_fast_tier._CI_PARTS))
    before = validate_fast_tier.derived_ceiling_seconds(workflow)
    for step in workflow["jobs"]["pr-validate"]["steps"]:
        if "validate --pre" in str(step.get("run", "")):
            step["timeout-minutes"] = 40
    assert validate_fast_tier.derived_ceiling_seconds(workflow) == before


def test_registered_check_passes_on_the_real_tree(capsys: pytest.CaptureFixture) -> None:
    failed: list[str] = []
    validate_fast_tier.validate_fast_tier(failed)
    assert failed == [], capsys.readouterr().out


def test_registered_check_fails_on_drift(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _copy_inputs(tmp_path)
    contract_path = root.joinpath(*validate_fast_tier._CONTRACT_PARTS)
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    contract["selection"]["cap_modules"] = affected_tests.CAP + 1
    contract_path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    failed: list[str] = []
    validate_fast_tier.validate_fast_tier(failed, root=root)
    assert failed == [validate_fast_tier._CHECK_LABEL]
    assert "FAIL: selection.cap_modules" in capsys.readouterr().out


def test_unreadable_input_fails_loudly(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _copy_inputs(tmp_path)
    root.joinpath(*validate_fast_tier._CONTRACT_PARTS).write_text("- a list, not a mapping\n", encoding="utf-8")
    failed: list[str] = []
    validate_fast_tier.validate_fast_tier(failed, root=root)
    assert failed == [validate_fast_tier._CHECK_LABEL]
    assert "could not load inputs: ValueError" in capsys.readouterr().out


def test_registered_in_both_tiers() -> None:
    assert "validate_fast_tier" in {s.name for s in registry.pre_sequence() if s.kind == "check"}
    assert "validate_fast_tier" in {s.name for s in registry.full_sequence() if s.kind == "check"}
