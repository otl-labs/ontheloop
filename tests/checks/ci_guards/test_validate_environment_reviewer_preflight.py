"""Red/green tests for validate_environment_reviewer_preflight (rec-4979, rec-4961)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.ci_guards import _manifest
from scripts.checks.ci_guards import validate_environment_reviewer_preflight as v
from scripts.verify_ci_workflow import _load

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def tree() -> tuple[dict[str, Any], dict[str, str], str, str]:
    wfs = {p.name: _load(str(p)) for p in sorted((_ROOT / ".github" / "workflows").glob("*.yml"))}
    tf = {p.name: p.read_text(encoding="utf-8") for p in sorted((_ROOT / "terraform" / "github").glob("*.tf"))}
    return (
        wfs,
        tf,
        (_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8"),
        (_ROOT / v.SCRIPT).read_text(encoding="utf-8"),
    )


@pytest.fixture
def wfs(tree) -> dict[str, Any]:
    return copy.deepcopy(tree[0])


@pytest.fixture
def declared(tree) -> dict[str, dict[str, bool]]:
    return v.declared_environments(tree[1])


def _gated(wfs, fname: str, job: str) -> dict[str, Any]:
    return wfs[fname]["jobs"][job]


def _violations(wfs, tree, declared) -> list[str]:
    return v.check_gated_jobs(wfs, declared)[0] + v.check_environment_names(wfs, declared) + v.check_upstream(wfs, declared)


_GATED_FLOOR = (
    ("approved-text-approval.yml", "approve"),
    ("publish-ontheloop.yml", "publish"),
    ("reconcile.yml", "gated-apply-reconcile"),
    ("terraform-apply-sandbox.yml", "gated-apply"),
    ("terraform-bootstrap-apply.yml", "apply"),
    ("terraform-github-apply.yml", "apply"),
)


def test_bootstrap_apply_jobs_check_their_environment(tree) -> None:
    wfs = copy.deepcopy(tree[0])
    assert v.run_all(wfs, *tree[1:])[0] == []
    apply = wfs["terraform-bootstrap-apply.yml"]["jobs"]["apply"]
    apply["steps"].insert(1, {"run": "echo before the approvals check"})
    assert any("terraform-bootstrap-apply.yml" in x for x in v.run_all(wfs, *tree[1:])[0])
    dropped = copy.deepcopy(tree[0])
    preview = dropped["terraform-bootstrap-apply.yml"]["jobs"]["preview"]
    preview["steps"] = [
        s for s in preview["steps"] if "environment_reviewer_preflight.py environment" not in str(s.get("run", ""))
    ]
    assert any("terraform-bootstrap-apply.yml" in x for x in v.run_all(dropped, *tree[1:])[0])


def test_real_tree_is_green(tree) -> None:
    out, examined = v.run_all(*tree)
    assert out == [] and examined >= len(_GATED_FLOOR)


def test_real_tree_gates_every_reviewer_job(tree, declared) -> None:
    wfs = tree[0]
    envs = v.reviewer_envs(declared)
    assert {"approved-text", "tf-gated-apply", "pypi"} <= envs and "deployment-approval-probe" not in envs
    gated = [(f, j) for f, w in wfs.items() for j, job in v._jobs(w).items() if v._env_name(job) in envs]
    assert len(gated) == len(set(gated)) and set(_GATED_FLOOR) <= set(gated)
    for fname, jname in gated:
        name = v._env_name(wfs[fname]["jobs"][jname])
        steps = [s for s in v._steps(wfs[fname]["jobs"][jname]) if not v._is_checkout(s)]
        assert steps[0]["run"].strip() == v._approvals_cmd(name), (fname, jname)


def test_exempt_probe_and_branch_policy_only_environment_pass_without_the_step(tree, declared) -> None:
    assert "tf-trusted-plan" in declared and "tf-trusted-plan" not in v.reviewer_envs(declared)
    assert "deployment-approval-probe" in declared and "deployment-approval-probe" not in v.reviewer_envs(declared)


def test_newly_declared_reviewer_environment_is_enforced_without_a_code_change(wfs, tree) -> None:
    tf = dict(tree[1])
    tf["new.tf"] = 'resource "github_repository_environment" "x" {\n  environment = "brand-new"\n  reviewers {\n  }\n}\n'
    wfs["new.yml"] = {
        "jobs": {"j": {"environment": "brand-new", "permissions": {"actions": "read"}, "steps": [{"run": "true"}]}}
    }
    out = v.check_gated_jobs(wfs, v.declared_environments(tf))[0]
    assert any("new.yml:j must run" in x for x in out)


def test_a_unknown_environment_name_is_refused(wfs, declared) -> None:
    _gated(wfs, "tf-gated-apply-prototype.yml", "gate-smoke-test")["environment"] = "typo-gated-apply"
    assert any(x.startswith("a:") and "typo-gated-apply" in x for x in v.check_environment_names(wfs, declared))


def test_a_expression_environment_name_is_refused(wfs, declared) -> None:
    _gated(wfs, "publish-ontheloop.yml", "publish")["environment"] = {"name": "${{ inputs.env }}"}
    assert any("expression" in x for x in v.check_environment_names(wfs, declared))


def test_b_ui_managed_name_also_declared_in_hcl_fails(tree) -> None:
    declared = v.declared_environments(tree[1])
    declared["pypi"] = {"reviewers": True, "no_bypass": False}
    assert any(x.startswith("b:") and "rec-4549" in x for x in v.check_environment_names({}, declared))


def test_c_gated_job_without_the_step_is_refused(wfs, declared) -> None:
    job = _gated(wfs, "publish-ontheloop.yml", "publish")
    job["steps"] = [s for s in job["steps"] if "run" not in s or "preflight" not in s["run"]]
    assert any("publish-ontheloop.yml:publish must run" in x for x in v.check_gated_jobs(wfs, declared)[0])


def test_c_step_after_a_credential_step_is_refused(wfs, declared) -> None:
    job = _gated(wfs, "tf-gated-apply-prototype.yml", "gate-smoke-test")
    job["steps"].insert(1, {"uses": "./.github/actions/aws-region"})
    assert any("gate-smoke-test must run" in x for x in v.check_gated_jobs(wfs, declared)[0])


def test_c_wrong_environment_literal_is_refused(wfs, declared) -> None:
    job = _gated(wfs, "approved-text-approval.yml", "approve")
    step = next(s for s in job["steps"] if "preflight" in s.get("run", ""))
    step["run"] = step["run"].replace("approved-text", "pypi")
    assert any("approve must run" in x for x in v.check_gated_jobs(wfs, declared)[0])


def test_c_wrong_token_or_reviewer_mapping_is_refused(wfs, declared) -> None:
    job = _gated(wfs, "publish-ontheloop.yml", "publish")
    step = next(s for s in job["steps"] if "preflight" in s.get("run", ""))
    step["env"]["GH_TOKEN"] = "${{ secrets.X }}"
    assert any("must map GH_TOKEN" in x for x in v.check_gated_jobs(wfs, declared)[0])


def test_c_missing_actions_permission_is_refused(wfs, declared) -> None:
    _gated(wfs, "publish-ontheloop.yml", "publish")["permissions"].pop("actions")
    assert any("must grant actions" in x for x in v.check_gated_jobs(wfs, declared)[0])


def test_c_workflow_level_actions_permission_counts(wfs, declared) -> None:
    job = _gated(wfs, "publish-ontheloop.yml", "publish")
    job.pop("permissions")
    wfs["publish-ontheloop.yml"]["permissions"] = {"actions": "write"}
    assert v.check_gated_jobs(wfs, declared)[0] == []


def _ref_pinned_job(delegate_wd: str | None, run: str, side: bool = True) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [{"uses": "actions/checkout@v7", "with": {"ref": "x"}}]
    if side:
        steps.append({"uses": "actions/checkout@v7", "with": {"path": v.SIDE_CHECKOUT}})
    step: dict[str, Any] = {"name": "wake", "run": run}
    if delegate_wd:
        step["working-directory"] = delegate_wd
    return {"jobs": {"j": {"steps": steps + [step]}}}


def test_c_one_line_delegate_in_a_ref_pinned_job_needs_the_side_checkout_directory(declared) -> None:
    bad = _ref_pinned_job(None, "python3 -m scripts.ci.apply_wake --job x")
    assert any("working-directory" in x for x in v.check_gated_jobs({"w.yml": bad}, declared)[0])
    good = _ref_pinned_job(v.SIDE_CHECKOUT, "python3 -m scripts.ci.apply_wake --job x")
    assert v.check_gated_jobs({"w.yml": good}, declared)[0] == []


def test_c_approvals_step_in_a_ref_pinned_side_checkout_job_needs_the_directory(declared) -> None:
    cmd = v._approvals_cmd("tf-gated-apply")
    wf = _ref_pinned_job(None, cmd)
    wf["jobs"]["j"]["environment"] = "tf-gated-apply"
    wf["jobs"]["j"]["permissions"] = {"actions": "read"}
    wf["jobs"]["j"]["steps"][-1]["env"] = {"GH_TOKEN": v.TOKEN_EXPR, "REVIEWER_LOGINS": v.LOGINS_EXPR}
    assert any("working-directory" in x for x in v.check_gated_jobs({"w.yml": wf}, declared)[0])
    wf["jobs"]["j"]["steps"][-1]["working-directory"] = v.SIDE_CHECKOUT
    assert v.check_gated_jobs({"w.yml": wf}, declared)[0] == []


def test_c_multiline_recheck_body_and_path_form_calls_pass_in_the_same_job(declared) -> None:
    wf = _ref_pinned_job(None, 'OUT=$(python3 -m scripts.ci.reconcile_target)\necho "$OUT"')
    wf["jobs"]["j"]["steps"].append({"name": "p", "run": 'python3 "$GITHUB_WORKSPACE/scripts/ci/stale.py"'})
    assert v.check_gated_jobs({"w.yml": wf}, declared)[0] == []


def test_c_ref_pinned_job_without_a_side_checkout_runs_a_delegate_in_place(declared) -> None:
    wf = _ref_pinned_job(None, "python3 -m scripts.ci.something", side=False)
    assert v.check_gated_jobs({"w.yml": wf}, declared)[0] == []


def test_d_missing_upstream_check_is_refused(wfs, declared) -> None:
    job = _gated(wfs, "terraform-github-apply.yml", "preview")
    job["steps"] = [s for s in job["steps"] if "preflight" not in s.get("run", "")]
    assert any(x.startswith("d:") and "preview must run" in x for x in v.check_upstream(wfs, declared))


def test_d_flag_present_where_hcl_declares_no_bypass_and_absent_otherwise(wfs, declared) -> None:
    assert declared["approved-text"]["no_bypass"] is True and declared["tf-gated-apply"]["no_bypass"] is False
    render = _gated(wfs, "approved-text-approval.yml", "render")
    step = next(s for s in render["steps"] if "preflight" in s.get("run", ""))
    step["run"] = step["run"].replace(" --require-no-admin-bypass", "")
    assert any("render must run" in x for x in v.check_upstream(wfs, declared))
    preview = _gated(wfs, "terraform-github-apply.yml", "preview")
    step = next(s for s in preview["steps"] if "preflight" in s.get("run", ""))
    step["run"] += " --require-no-admin-bypass"
    assert any("preview must run" in x for x in v.check_upstream(wfs, declared))


def test_d_missing_actions_permission_or_job_is_refused(wfs, declared) -> None:
    _gated(wfs, "terraform-github-apply.yml", "preview")["permissions"].pop("actions")
    assert any("preview must grant actions" in x for x in v.check_upstream(wfs, declared))
    del wfs["approved-text-approval.yml"]["jobs"]["render"]
    assert any("must define jobs render and approve" in x for x in v.check_upstream(wfs, declared))
    assert any("must define jobs" in x for x in v.check_upstream({}, declared))


def test_d_gated_job_with_an_ungoverned_environment_is_skipped(wfs, declared) -> None:
    _gated(wfs, "terraform-github-apply.yml", "apply")["environment"] = "tf-trusted-plan"
    assert v.check_upstream(wfs, declared) == []


def test_e_non_stdlib_import_and_runner_file_names_are_refused(tree) -> None:
    text = tree[3] + "\nimport yaml\nfrom scripts import x\nfrom . import y\nGITHUB_" + "ENV = 1\n"
    out = v.check_script(text)
    assert any("non-stdlib yaml" in x for x in out) and any("non-stdlib scripts" in x for x in out)
    assert any("non-stdlib ." in x for x in out) and any("must not name" in x for x in out)
    assert v.check_script(tree[3]) == []


def test_f_uncovered_codeowners_path_is_refused() -> None:
    out = v.check_codeowners("/scripts/ci/environment_reviewer_preflight.py @o\n")
    assert out == [f"f: .github/CODEOWNERS does not cover {v.GUARD}"]
    assert len(v.check_codeowners("")) == 2


def test_registered_check_passes_on_the_real_tree_and_reports_unreadable_inputs(monkeypatch) -> None:
    failed: list[str] = []
    v.validate_environment_reviewer_preflight(failed)
    assert failed == []
    monkeypatch.setattr(v, "_load", lambda _p: (_ for _ in ()).throw(yaml.YAMLError("bad")))
    v.validate_environment_reviewer_preflight(failed)
    assert failed == ["reviewer-gate-preflight: inputs unreadable, so no predicate ran"]


def test_registered_check_reports_each_violation(monkeypatch) -> None:
    monkeypatch.setattr(v, "run_all", lambda *a: (["c: boom"], 3))
    failed: list[str] = []
    v.validate_environment_reviewer_preflight(failed)
    assert failed == ["reviewer-gate-preflight: c: boom"]


def test_check_is_registered_in_the_fast_tier_manifest() -> None:
    entry = next(e for e in _manifest.ENTRIES if e.name == "validate_environment_reviewer_preflight")
    assert entry.pre is True and ".github/workflows/**" in entry.pre_globs


def test_a_resource_without_an_environment_argument_is_ignored() -> None:
    assert v.declared_environments({"x.tf": 'resource "github_repository_environment" "x" {\n  repository = "r"\n}\n'}) == {}


@pytest.mark.parametrize("key,value", [("if", "github.event_name != 'workflow_dispatch'"), ("continue-on-error", True)])
def test_c_conditional_or_maskable_approvals_step_is_refused(wfs, declared, key, value) -> None:
    job = _gated(wfs, "publish-ontheloop.yml", "publish")
    next(s for s in job["steps"] if "preflight" in s.get("run", ""))[key] = value
    assert any("no if: and no continue-on-error" in x for x in v.check_gated_jobs(wfs, declared)[0])


def test_c_continue_on_error_on_the_upstream_check_is_refused_but_if_is_allowed(wfs, declared) -> None:
    job = _gated(wfs, "terraform-github-apply.yml", "preview")
    step = next(s for s in job["steps"] if "preflight" in s.get("run", ""))
    assert "if" in step and v.check_upstream(wfs, declared) == []
    step["continue-on-error"] = True
    assert any("must not set continue-on-error" in x for x in v.check_upstream(wfs, declared))


def test_c_gated_job_with_a_ref_checkout_needs_the_side_checkout(wfs, declared) -> None:
    job = _gated(wfs, "reconcile.yml", "gated-apply-reconcile")
    job["steps"] = [s for s in job["steps"] if (s.get("with") or {}).get("path") != v.SIDE_CHECKOUT]
    assert any("needs a .dispatch-tooling side checkout" in x for x in v.check_gated_jobs(wfs, declared)[0])
