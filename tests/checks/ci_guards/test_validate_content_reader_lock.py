"""Red/green tests for validate_content_reader_lock (Decision 239 cl.5, Decision 272)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.ci_guards import validate_content_reader_lock as v
from scripts.verify_ci_workflow import _load

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def tree() -> dict[str, Any]:
    wf_dir = _ROOT / ".github" / "workflows"
    contracts = _ROOT / "docs" / "contracts"
    return {
        "wf": _load(str(wf_dir / v.WORKFLOW)),
        "workflows": {p.name: _load(str(p)) for p in sorted(wf_dir.glob("*.yml"))},
        "build": yaml.safe_load((contracts / "build-lambda.yaml").read_text(encoding="utf-8")),
        "capacity": yaml.safe_load((contracts / "actions-capacity.yaml").read_text(encoding="utf-8")),
        "evidence": yaml.safe_load((contracts / "github-actions-evidence.yaml").read_text(encoding="utf-8")),
    }


@pytest.fixture
def wf(tree) -> dict[str, Any]:
    return copy.deepcopy(tree["wf"])


def test_real_tree_is_green() -> None:
    assert v.run_all(_ROOT) == []


def test_a_other_workflow_naming_the_environment_or_an_expression_is_red(tree) -> None:
    workflows = copy.deepcopy(tree["workflows"])
    assert v.check_environment(workflows) == []
    workflows["ci.yml"]["jobs"]["x"] = {"environment": v.ENVIRONMENT, "steps": []}
    assert any(x.startswith("a:") for x in v.check_environment(workflows))
    workflows = copy.deepcopy(tree["workflows"])
    workflows[v.WORKFLOW]["jobs"]["deploy"]["environment"] = "${{ vars.ENV }}-" + v.ENVIRONMENT
    assert any("expression" in x for x in v.check_environment(workflows))


@pytest.mark.parametrize("trigger", ["workflow_call", "pull_request", "pull_request_target"])
def test_b_forbidden_trigger_is_red(wf, trigger) -> None:
    assert v.check_workflow_shape(wf) == []
    wf["on"][trigger] = {}
    assert any(trigger in x for x in v.check_workflow_shape(wf))


def test_b_deploy_job_as_a_reusable_call_or_missing_workflow_is_red(wf) -> None:
    wf["jobs"]["deploy"] = {"uses": "./.github/workflows/x.yml"}
    assert any("deploy job" in x for x in v.check_workflow_shape(wf))
    assert v.check_workflow_shape(None)


def test_c_other_workflow_or_zip_key_naming_the_function_is_red() -> None:
    assert v.check_other_names({"a.yml": "name: a", v.WORKFLOW: v.FUNCTION}, ["agent-platform-ducklake-writer"]) == []
    assert v.check_other_names({"a.yml": f"x {v.FUNCTION}"}, [])
    assert v.check_other_names({"a.yml": f"x {v.ZIP}"}, [])
    assert v.check_other_names({}, [v.FUNCTION])


def test_d_deploy_job_must_hold_on_held_and_pass_digests_through_env(wf) -> None:
    assert v.check_jobs(wf) == []
    wf["jobs"]["deploy"]["if"] = "always()"
    assert any("held" in x for x in v.check_jobs(wf))


def test_d_missing_env_and_expression_in_run_body_are_red(wf) -> None:
    step = next(s for s in wf["jobs"]["deploy"]["steps"] if "telemetry_content_deploy.sh deploy" in s.get("run", ""))
    step["env"].pop("RECORD_CODE_SHA256")
    assert any("RECORD_CODE_SHA256" in x for x in v.check_jobs(wf))
    step["env"].pop("CONTENT_SHA256")
    assert any("CONTENT_SHA256" in x for x in v.check_jobs(wf))
    wf2 = copy.deepcopy(wf)
    step2 = next(s for s in wf2["jobs"]["deploy"]["steps"] if "telemetry_content_deploy.sh deploy" in s.get("run", ""))
    step2["run"] = "bash x ${{ needs.build.outputs.sha256 }}"
    assert any("expression" in x for x in v.check_jobs(wf2))


def test_d_python_or_pip_after_the_credential_step_is_red(wf) -> None:
    wf["jobs"]["deploy"]["steps"].append({"name": "late", "run": "python -m x"})
    assert any("after the credential step" in x for x in v.check_jobs(wf))


def test_d_baseline_must_precede_the_summary(wf) -> None:
    steps = wf["jobs"]["build"]["steps"]
    i = next(i for i, s in enumerate(steps) if s.get("name") == "Prove the recorded baseline")
    steps.append(steps.pop(i))
    assert any("baseline step must precede" in x for x in v.check_jobs(wf))


def test_e_pathspecs_cover_the_set_and_bundle_every_py_file() -> None:
    pkg = v.PKG
    specs = v.PROTECTIVE
    files = [f"{pkg}/handler.py", f"{pkg}/manifest.yaml", f"{pkg}/deploy_config.json"]
    assert v.check_pathspecs(specs, files, {f"{pkg}/handler.py"}) == []
    assert any("miss" in x for x in v.check_pathspecs(specs[:2], files, {f"{pkg}/handler.py"}))
    assert any("not bundled" in x for x in v.check_pathspecs(specs, files, set()))
    assert any("unbundled" in x for x in v.check_pathspecs(specs, [*files, f"{pkg}/notes.txt"], {f"{pkg}/handler.py"}))


def test_f_each_contract_row_is_required(tree) -> None:
    build, capacity, evidence = (copy.deepcopy(tree[k]) for k in ("build", "capacity", "evidence"))
    assert v.check_contracts(build, capacity, evidence) == []
    build["deploy_channels"].pop("telemetry_content_function")
    assert any("governed_channel" in x for x in v.check_contracts(build, capacity, evidence))
    build = copy.deepcopy(tree["build"])
    build["cli_flags"].remove("--content-reader-only")
    assert any("cli_flags" in x for x in v.check_contracts(build, capacity, evidence))
    capacity["ledger"]["rows"] = [r for r in capacity["ledger"]["rows"] if r["id"] != "telemetry-content-deploy-per-merge"]
    assert any("ledger row" in x for x in v.check_contracts(tree["build"], capacity, evidence))
    evidence["governed_jobs"] = [r for r in evidence["governed_jobs"] if v.WORKFLOW not in r["workflow"]]
    assert sum("governed_jobs" in x for x in v.check_contracts(tree["build"], tree["capacity"], evidence)) == 2


def test_registered_check_reports_violations_and_unreadable_inputs(monkeypatch) -> None:
    failed: list[str] = []
    v.validate_content_reader_lock(failed)
    assert failed == []
    monkeypatch.setattr(v, "run_all", lambda root: ["a: boom"])
    v.validate_content_reader_lock(failed)
    assert failed == ["content-reader-lock: a: boom"]
    failed.clear()
    monkeypatch.setattr(v, "run_all", lambda root: (_ for _ in ()).throw(OSError("x")))
    v.validate_content_reader_lock(failed)
    assert failed and "unreadable" in failed[0]


def test_trigger_forms_and_missing_workflow_are_handled() -> None:
    assert v._triggers({"on": "push"}) == {"push"}
    assert v._triggers({"on": ["push", "workflow_dispatch"]}) == {"push", "workflow_dispatch"}
    assert v._triggers({"on": 3}) == set()
    assert v.check_jobs(None) == []


def test_d_deploy_job_without_a_credential_step_is_red(wf) -> None:
    steps = wf["jobs"]["deploy"]["steps"]
    wf["jobs"]["deploy"]["steps"] = [s for s in steps if not str(s.get("uses", "")).startswith("aws-actions/")]
    assert any("must configure credentials" in m for m in v.check_jobs(wf))


def test_e_a_source_without_the_declaration_yields_no_pathspecs() -> None:
    assert v._declared_pathspecs("X = 1\n") == ()
