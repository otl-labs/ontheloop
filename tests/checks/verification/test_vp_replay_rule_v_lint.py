"""rec-5547: hermetic authoring rule (v) enforced at plan-PR time (docs/contracts/vp-red-before.yaml rule_v_lint)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks.verification import _vp_replay_rule_v
from tests.checks.verification.validate_vp_replay.conftest import _RedBeforeFixture, validate_vp_replay

_ROOT = Path(__file__).resolve().parents[3]


def _plan_doc(command: str, scope: list[tuple[str, str]]) -> dict:
    return {
        "schema_version": 2,
        "slug": "rule-v-fixture",
        "intent": "Fixture plan for the rule (v) lint.",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "plan_path": "docs/plans/PLAN-rule-v-fixture.yaml",
        "phase": "Test fixture",
        "scope": [{"file": f, "action": a, "purpose": "fixture"} for f, a in scope],
        "acceptance_criteria": ["dummy"],
        "verification_plan": [
            {
                "step": 2,
                "phase": "pre-deploy",
                "hermetic": True,
                "action": "re-run the suite",
                "command": command,
                "expected": "Exit 0.",
                "fix_if": "n/a",
            }
        ],
        "execution_steps": ["dummy"],
    }


def test_hermetic_whole_directory_in_scope_step_is_refused(tmp_path: Path) -> None:
    (tmp_path / "tests" / "checks").mkdir(parents=True)
    (tmp_path / "tests" / "checks" / "test_old.py").write_text("", encoding="utf-8")
    plan_rel = "docs/plans/PLAN-rule-v-fixture.yaml"
    command = "bin/venv-python -m pytest tests/checks tests/test_new.py scripts/ci_rca/test_x.py -q"
    doc = _plan_doc(command, [("tests/checks/test_extra.py", "Create"), ("tests/test_new.py", "Modify")])
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / plan_rel).write_text(yaml.dump(doc), encoding="utf-8")
    failed: list[str] = []
    _vp_replay_rule_v.rule_v_pre_pass([plan_rel], tmp_path, {plan_rel}, set(), failed)
    assert len(failed) == 1
    message = failed[0]
    assert message.startswith(f"vp-hermetic-rule-v {plan_rel}:2:")
    assert "tests/checks" in message and "tests/test_new.py" in message
    assert message.index("node or class selectors") < message.index("hermetic: false")
    assert "-k" in message


def test_contract_names_the_rule_v_lint() -> None:
    contract = yaml.safe_load((_ROOT / "docs/contracts/vp-red-before.yaml").read_text(encoding="utf-8"))
    assert "rule_v_lint" in contract
    assert "rule_v_lint" in contract["hermetic_authoring"]
    assert "validate_hermetic_fast_tier_collectability" in contract["rule_v_lint"]
    assert contract["contract"]["contract_version"] >= 10
    assert any("rule_v_lint" in str(entry) for entry in contract["amendment_log"])


def test_validate_vp_replay_runs_the_rule_v_pre_pass(tmp_path: Path) -> None:
    step = {
        "step": 1,
        "phase": "pre-deploy",
        "hermetic": True,
        "action": "whole-file re-run",
        "command": "bin/venv-python -m pytest scripts/dummy.py -q",
        "expected": "Exit 0.",
        "fix_if": "n/a",
    }
    repo, rel = _RedBeforeFixture().build(tmp_path, "rule-v-e2e", [step])
    failed: list[str] = []
    validate_vp_replay(failed, changed_files=[rel], root=repo)
    assert any(f.startswith(f"vp-hermetic-rule-v {rel}:1:") for f in failed)
