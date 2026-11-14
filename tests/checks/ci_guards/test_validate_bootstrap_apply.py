"""Red/green tests for validate_bootstrap_apply (PLAN-bootstrap-ci-gated-apply, Decision 257): one real-tree node and
one red node per predicate (a) to (h) on synthetic input."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.checks.ci_guards import validate_bootstrap_apply as v

Tree = tuple[dict[str, Any] | None, dict[str, str], str, str, str]


@pytest.fixture(scope="module")
def tree() -> Tree:
    return v._load_tree()


@pytest.fixture
def wf(tree: Tree) -> dict[str, Any]:
    assert tree[0] is not None
    return copy.deepcopy(tree[0])


def _step(job: dict[str, Any], needle: str) -> int:
    return next(i for i, s in enumerate(job["steps"]) if needle in str(s.get("run", "")) or needle in str(s.get("uses", "")))


def test_real_tree_passes_every_predicate(tree: Tree) -> None:
    assert [x for group in v.run_all(*tree) for x in group] == []


def test_a_pull_request_trigger_is_refused(wf: dict[str, Any]) -> None:
    wf["on"]["pull_request"] = {}
    assert any(x.startswith("a:") for x in v.check_triggers(wf))


def test_a_extra_dispatch_input_or_path_is_refused(wf: dict[str, Any]) -> None:
    wf["on"]["workflow_dispatch"]["inputs"]["extra"] = {"type": "string"}
    wf["on"]["push"]["paths"].append("docs/**")
    assert len([x for x in v.check_triggers(wf) if x.startswith("a:")]) == 2


def test_b_wrong_environment_missing_main_guard_or_needs_is_refused(wf: dict[str, Any]) -> None:
    wf["jobs"]["apply"]["environment"] = "tf-gated-apply"
    wf["jobs"]["apply"]["if"] = "${{ needs.preview.outputs.has_changes == 'true' }}"
    wf["jobs"]["preview"]["environment"] = {"name": "tf-trusted-plan"}
    del wf["jobs"]["apply"]["needs"]
    assert len([x for x in v.check_jobs(wf) if x.startswith("b:")]) == 4


def test_c_the_apply_role_in_the_preview_job_is_refused(wf: dict[str, Any], tree: Tree) -> None:
    job = wf["jobs"]["preview"]
    job["steps"][_step(job, "configure-aws-credentials")]["with"]["role-to-assume"] = "arn:aws:iam::1:role/bootstrap-apply"
    assert any(x.startswith("c:") for x in v.check_roles(wf, tree[1]))


def test_c_another_workflow_naming_either_role_is_refused(wf: dict[str, Any]) -> None:
    others = {".github/workflows/other.yml": "role-to-assume: arn:aws:iam::1:role/bootstrap-apply\n"}
    assert v.check_roles(wf, others) == ["c: .github/workflows/other.yml names bootstrap-apply"]


def test_d_a_step_before_the_approvals_check_is_refused(wf: dict[str, Any]) -> None:
    steps = wf["jobs"]["apply"]["steps"]
    steps.insert(1, {"run": "echo hi"})
    assert any(x.startswith("d:") for x in v.check_approvals_first(wf))


def test_e_artifact_upload_plan_cat_and_bare_terraform_are_refused(wf: dict[str, Any]) -> None:
    wf["jobs"]["preview"]["steps"].append({"uses": "actions/upload-artifact@" + "0" * 40})
    wf["jobs"]["preview"]["steps"].append({"run": 'cat "$RUNNER_TEMP/plan.json"'})
    wf["jobs"]["preview"]["steps"].append({"run": "terraform -chdir=terraform/bootstrap plan"})
    found = v.check_no_leaks(wf, "")
    assert len(found) == 3 and all(x.startswith("e:") for x in found)
    assert v.check_no_leaks(None, "terraform -chdir=x apply") == ["e: a terraform invocation runs outside quiet_run.sh"]


def test_f_a_job_workflow_ref_on_another_file_is_refused(tree: Tree) -> None:
    hcl = tree[2].replace("terraform-bootstrap-apply.yml@refs/heads/main", "other.yml@refs/heads/main")
    assert any(x.startswith("f:") for x in v.check_hcl(hcl))
    assert any(x.startswith("f:") for x in v.check_hcl(tree[2].replace('"tf-bootstrap-apply"', '"tf-other"')))


def test_g_a_codeowners_line_removed_is_refused(tree: Tree) -> None:
    kept = "\n".join(ln for ln in tree[3].splitlines() if "validate_bootstrap_apply" not in ln)
    assert v.check_codeowners(kept) == ["g: scripts/checks/ci_guards/validate_bootstrap_apply.py is not code-owned"]


def test_h_a_screen_step_after_the_credentials_step_is_refused(wf: dict[str, Any]) -> None:
    job = wf["jobs"]["preview"]
    screen = job["steps"].pop(_step(job, "terraform_bootstrap_apply_gate.py screen"))
    job["steps"].insert(_step(job, "configure-aws-credentials") + 1, screen)
    assert any(x.startswith("h:") for x in v.check_screen_first(wf))
    del wf["jobs"]["apply"]["steps"][_step(wf["jobs"]["apply"], "terraform_bootstrap_apply_gate.py screen")]
    assert len(v.check_screen_first(wf)) == 2
