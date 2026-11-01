"""Red/green tests for validate_trusted_plan_environment (PLAN-trusted-plan-identity, rec-4636)."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.checks.ci_guards import validate_trusted_plan_environment as v
from scripts.verify_ci_workflow import _load

_ROOT = Path(__file__).resolve().parents[3]
_TRUSTED = "terraform-plan-trusted.yml"


@pytest.fixture(scope="module")
def real_workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _load(str(p)) for p in sorted((_ROOT / ".github" / "workflows").glob("*.yml"))}


@pytest.fixture(scope="module")
def real_terraform() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted((_ROOT / "terraform" / "github").glob("*.tf"))}


@pytest.fixture(scope="module")
def real_codeowners() -> str:
    return (_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")


@pytest.fixture
def wfs(real_workflows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return copy.deepcopy(real_workflows)


def _job(workflows: dict[str, dict[str, Any]], job: str) -> dict[str, Any]:
    return workflows[_TRUSTED]["jobs"][job]


def test_real_tree_is_green(real_workflows, real_terraform, real_codeowners) -> None:
    assert v.run_all(real_workflows, real_terraform, real_codeowners, v._repo_reader()) == [[]] * 9


def test_real_tree_governs_the_single_plan_job(real_workflows) -> None:
    governed = {jid for jid, _ in v._governed(_TRUSTED, real_workflows[_TRUSTED])}
    assert governed == {"plan"}
    assert v._can_run_on_prt(real_workflows[_TRUSTED], _job(real_workflows, "plan"))
    assert v.TEMP_NO_ENV_JOBS == frozenset()


def test_a_third_workflow_naming_the_environment(wfs) -> None:
    wfs["other.yml"] = {"on": "push", "jobs": {"j": {"environment": v.ENV, "steps": []}}}
    assert any("references" in x for x in v.check_environment_references(wfs))


def test_a_name_by_expression(wfs) -> None:
    wfs["other.yml"] = {"on": "push", "jobs": {"j": {"environment": {"name": "${{ inputs.e }}"}, "steps": []}}}
    assert any("expression" in x for x in v.check_environment_references(wfs))


def test_non_trusted_pull_request_target_with_id_token(wfs) -> None:
    wfs["other.yml"] = {"on": {"pull_request_target": {}}, "jobs": {"j": {"permissions": {"id-token": "write"}, "steps": []}}}
    assert v.check_prt_minting(wfs)


def test_non_trusted_workflow_level_write_all(wfs) -> None:
    wfs["other.yml"] = {"on": ["pull_request_target"], "permissions": "write-all", "jobs": {"j": {"steps": []}}}
    assert v.check_prt_minting(wfs)


def test_workflow_level_id_token_inherited_by_job_without_permissions(wfs) -> None:
    wfs["other.yml"] = {"on": ["pull_request_target"], "permissions": {"id-token": "write"}, "jobs": {"j": {"steps": []}}}
    assert v.check_prt_minting(wfs)


def test_workflow_call_in_the_trusted_file(wfs) -> None:
    wfs[_TRUSTED]["on"]["workflow_call"] = {}
    assert any("workflow_call" in x for x in v.check_trusted_files(wfs))


def test_missing_trusted_file(wfs) -> None:
    del wfs[_TRUSTED]
    assert any("missing" in x for x in v.check_trusted_files(wfs))


def test_id_token_job_without_the_environment(wfs) -> None:
    del _job(wfs, "plan")["environment"]
    assert any("without environment" in x for x in v.check_minting_jobs(wfs))


def test_a_no_env_probe_job_now_fails(wfs) -> None:
    wfs[_TRUSTED]["jobs"]["claims-probe-no-env"] = {
        "if": _job(wfs, "plan")["if"],
        "permissions": {"id-token": "write", "contents": "read"},
        "steps": [],
    }
    assert any("claims-probe-no-env" in x and "without environment" in x for x in v.check_minting_jobs(wfs))


@pytest.mark.parametrize(
    "condition",
    [
        "github.event_name == 'workflow_dispatch' || true",
        "github.event.pull_request.head.repo.full_name == github.repository || true",
        "github.event_name == 'pull_request_target'",
        None,
    ],
)
def test_unguarded_if_forms(wfs, condition) -> None:
    job = _job(wfs, "plan")
    if condition is None:
        del job["if"]
    else:
        job["if"] = condition
    assert any("neither" in x for x in v.check_minting_jobs(wfs))


def test_guard_forms_that_pass() -> None:
    wf = {"on": [v._PRT]}
    ok = f"${{{{ ({v._DISPATCH}) || ({v._SAME_REPO}) }}}}"
    assert v._guarded({"if": ok})
    assert v._guarded({"if": f'github.event_name == "pull_request_target" && {v._SAME_REPO}'})
    assert not v._can_run_on_prt(wf, {"if": "github.event_name == 'workflow_dispatch'"})


@pytest.mark.parametrize("expr", ["github.event.pull_request.head.ref", "github.head_ref"])
def test_run_body_interpolating_pr_controlled_value(wfs, expr) -> None:
    _job(wfs, "plan")["steps"].append({"run": f"echo ${{{{ {expr} }}}}"})
    assert v.check_run_bodies(wfs)


def _mutate_tf(files: dict[str, str], old: str, new: str) -> dict[str, str]:
    out = dict(files)
    key = next(k for k, t in out.items() if old in t)
    out[key] = out[key].replace(old, new, 1)
    return out


def _trusted_block(files: dict[str, str]) -> str:
    return v._blocks("\n".join(files.values()), "github_repository_environment")["tf_trusted_plan"]


@pytest.mark.parametrize(
    ("arg", "bad"),
    [
        ("can_admins_bypass", "true"),
        ("protected_branches", "true"),
        ("custom_branch_policies", "false"),
        ("prevent_destroy", "false"),
    ],
)
def test_environment_argument_breaches(real_terraform, arg, bad) -> None:
    block = _trusted_block(real_terraform)
    mutated = re.sub(rf"(?m)^(\s*{arg}\s*=\s*).+$", rf"\g<1>{bad}", block)
    assert mutated != block
    assert v.check_terraform(_mutate_tf(real_terraform, block, mutated))


def test_environment_with_a_reviewers_block(real_terraform) -> None:
    block = _trusted_block(real_terraform)
    assert v.check_terraform(_mutate_tf(real_terraform, block, block.rstrip("}") + "  reviewers {\n    users = []\n  }\n}"))


def test_policy_branch_pattern_star(real_terraform) -> None:
    assert v.check_terraform(_mutate_tf(real_terraform, 'branch_pattern = "main"', 'branch_pattern = "*"'))


def test_second_deployment_policy(real_terraform) -> None:
    kind = "github_repository_environment_deployment_policy"
    extra = {"extra.tf": f'resource "{kind}" "again" {{\n  environment = "tf-trusted-plan"\n  branch_pattern = "main"\n}}\n'}
    assert any("exactly one deployment policy" in x for x in v.check_terraform({**real_terraform, **extra}))


def test_missing_import_block(real_terraform) -> None:
    mutated = _mutate_tf(
        real_terraform, "to = github_repository_environment.tf_trusted_plan\n", "to = github_repository_environment.gone\n"
    )
    assert any("import block" in x for x in v.check_terraform(mutated))


@pytest.mark.parametrize("key", ["ref", "repository"])
def test_checkout_with_ref_or_repository(wfs, key) -> None:
    _job(wfs, "plan")["steps"][0]["with"][key] = "${{ github.event.pull_request.head.sha }}"
    assert v.check_checkouts(wfs)


def test_checkout_without_persist_credentials_false(wfs) -> None:
    del _job(wfs, "plan")["steps"][0]["with"]["persist-credentials"]
    assert v.check_checkouts(wfs)


def test_unowned_script_and_unowned_action(wfs, real_codeowners) -> None:
    _job(wfs, "plan")["steps"].append({"run": "python3 -I scripts/ci/unowned.py"})
    _job(wfs, "plan")["steps"].append({"uses": "./.github/actions/unowned"})
    out = v.check_owned_and_isolated(wfs, real_codeowners)
    assert any("scripts/ci/unowned.py" in x for x in out)
    assert any(".github/actions/unowned" in x for x in out)


@pytest.mark.parametrize("body", ["python3 scripts/ci/oidc_claims_probe.py", "python3 -m scripts.ci.oidc_claims_probe"])
def test_python_without_isolated_mode(wfs, real_codeowners, body) -> None:
    _job(wfs, "plan")["steps"].append({"run": body})
    assert any("without -I" in x for x in v.check_owned_and_isolated(wfs, real_codeowners))


def test_codeowners_matching_forms() -> None:
    text = "/scripts/ci/a.py @o\n/.github/actions/x/ @o\n/.github/workflows/terraform-*.yml @o\n"
    assert v._owned("scripts/ci/a.py", text)
    assert v._owned(".github/actions/x", text)
    assert not v._owned("scripts/ci/b.py", text)


@pytest.mark.parametrize("body", ["python3 -I ./scripts/ci/unowned.py", "python3 -I $GITHUB_WORKSPACE/scripts/ci/unowned.py"])
def test_unowned_script_in_dotted_forms(wfs, real_codeowners, body) -> None:
    _job(wfs, "plan")["steps"].append({"run": body})
    assert any("scripts/ci/unowned.py" in x for x in v.check_owned_and_isolated(wfs, real_codeowners))


@pytest.mark.parametrize("body", ["/usr/bin/python3 x.py", "python3.12 x.py", "env python3 x.py", "bin/venv-python x.py"])
def test_python_invocation_forms_without_isolated_mode(wfs, real_codeowners, body) -> None:
    _job(wfs, "plan")["steps"].append({"run": body})
    assert any("without -I" in x for x in v.check_owned_and_isolated(wfs, real_codeowners))


def test_unreadable_input_fails_instead_of_skipping(monkeypatch) -> None:
    def boom(_path: str) -> dict[str, Any]:
        raise OSError("unreadable")

    monkeypatch.setattr(v, "_load", boom)
    failed: list[str] = []
    v.validate_trusted_plan_environment(failed)
    assert any("unreadable" in x for x in failed)


def test_trigger_shapes() -> None:
    assert v._triggers({"on": "push"}) == {"push"}
    assert v._triggers({"on": ["push", "pull_request"]}) == {"push", "pull_request"}
    assert v._triggers({"on": {"push": {}}}) == {"push"}
    assert v._triggers({}) == set()


def test_unbalanced_block_is_returned_whole() -> None:
    assert v._balanced_block("{ open", 0) == "{ open"


def test_no_environment_declared() -> None:
    assert any("no github_repository_environment" in x for x in v.check_terraform({"a.tf": "# nothing"}))


def test_codeowners_unanchored_basename_pattern() -> None:
    assert v._owned("scripts/ci/a.py", "a.py @o\n")
    assert not v._owned("scripts/ci/b.py", "a.py @o\n")


def test_registered_check_passes_on_the_real_tree(capsys) -> None:
    failed: list[str] = []
    v.validate_trusted_plan_environment(failed)
    assert failed == []
    assert "PASS" in capsys.readouterr().out


def test_registered_check_reports_each_violation(monkeypatch) -> None:
    monkeypatch.setattr(v, "run_all", lambda *_: [["a: synthetic breach"], []])
    failed: list[str] = []
    v.validate_trusted_plan_environment(failed)
    assert failed == ["trusted-plan-environment: a: synthetic breach"]


def test_pass_line_ignores_unrelated_earlier_failures(capsys) -> None:
    failed = ["other-check: unrelated"]
    v.validate_trusted_plan_environment(failed)
    assert failed == ["other-check: unrelated"]
    assert "PASS: predicates (a)-(i)" in capsys.readouterr().out


def test_no_pass_line_when_this_check_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr(v, "run_all", lambda *_: [["a: synthetic breach"]])
    v.validate_trusted_plan_environment([])
    assert "PASS" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "body",
    ["cat x.py | python3", '"python3" x.py', "$(which python3) x.py", "echo hi && python3", "python3 -XI x.py"],
)
def test_more_python_forms_without_isolated_mode(wfs, real_codeowners, body) -> None:
    _job(wfs, "plan")["steps"].append({"run": body})
    assert any("without -I" in x for x in v.check_owned_and_isolated(wfs, real_codeowners))


@pytest.mark.parametrize("flag", ["-I", "-IB", "-sI"])
def test_isolated_flag_clusters_that_pass(flag) -> None:
    assert v._ISOLATED_FLAG_RE.fullmatch(flag)


@pytest.mark.parametrize(
    "body",
    [
        "python3 -I -m scripts.ci.unowned",
        "bash ${{ github.workspace }}/scripts/ci/unowned.sh",
        'bash "${GITHUB_WORKSPACE}"/scripts/ci/unowned.sh',
    ],
)
def test_unowned_module_and_workspace_expression_forms(wfs, real_codeowners, body) -> None:
    _job(wfs, "plan")["steps"].append({"run": body})
    assert any("scripts/ci/unowned" in x for x in v.check_owned_and_isolated(wfs, real_codeowners))


def test_codeowners_last_match_wins_and_ownerless_line_clears() -> None:
    assert not v._owned("scripts/ci/a.py", "/scripts/ @o\n/scripts/ci/a.py\n")
    assert v._owned("scripts/ci/a.py", "/scripts/ci/a.py\n/scripts/ @o\n")


def test_yaml_extension_workflows_are_scanned(tmp_path, monkeypatch) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "terraform" / "github").mkdir(parents=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text("", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "other.yaml").write_text(
        "on: [pull_request_target]\njobs:\n  j:\n    permissions:\n      id-token: write\n    steps: []\n", encoding="utf-8"
    )
    monkeypatch.setattr(v._common, "ROOT", tmp_path)
    failed: list[str] = []
    v.validate_trusted_plan_environment(failed)
    assert any("b: other.yaml:j" in x for x in failed)


def test_empty_workflow_file_fails_cleanly(tmp_path, monkeypatch) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "terraform" / "github").mkdir(parents=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text("", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "empty.yml").write_text("", encoding="utf-8")
    monkeypatch.setattr(v._common, "ROOT", tmp_path)
    failed: list[str] = []
    v.validate_trusted_plan_environment(failed)
    assert any("unreadable" in x for x in failed)


def _reader(files: dict[str, str]):
    return lambda rel: files.get(rel)


def _own(*paths: str) -> str:
    return "".join(f"/{p} @o\n" for p in paths)


def test_git_fetch_or_checkout_in_a_governed_run_body(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"run": "git fetch origin +refs/pull/1/merge:x"})
    assert any("git fetch or git checkout" in x for x in v.check_checkouts(wfs))
    wfs2 = copy.deepcopy(wfs)
    _job(wfs2, "plan")["steps"][-1] = {"run": "git checkout main -- terraform/personal"}
    assert any("git fetch or git checkout" in x for x in v.check_checkouts(wfs2))


def test_git_in_a_composite_run_body_is_found(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"uses": "./.github/actions/x"})
    files = {".github/actions/x/action.yml": "runs:\n  using: composite\n  steps:\n    - run: git fetch origin\n"}
    assert any("git fetch" in x for x in v.check_checkouts(wfs, _reader(files)))


def test_unowned_script_reached_through_a_composite(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"uses": "./.github/actions/x"})
    files = {
        ".github/actions/x/action.yml": (
            "runs:\n  using: composite\n  steps:\n    - run: python3 -I scripts/ci/deep.py\n"
            "    - run: bash ${{ github.action_path }}/helper.sh\n"
        )
    }
    out = v.check_owned_and_isolated(wfs, _own(".github/actions/x/"), _reader(files))
    assert any("scripts/ci/deep.py" in x for x in out)
    assert not any("helper.sh" in x for x in out)
    out = v.check_owned_and_isolated(wfs, "", _reader(files))
    assert any(".github/actions/x/helper.sh" in x for x in out)


def test_unowned_module_in_an_executed_scripts_import_closure(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"run": "python3 -I scripts/ci/entry.py"})
    files = {
        "scripts/ci/entry.py": "import sys\nsys.path.append('.')\nfrom scripts.ci import helper\n",
        "scripts/ci/helper.py": "from scripts.lib.deep import thing\n",
        "scripts/lib/deep.py": "x = 1\n",
    }
    owned = _own("scripts/ci/entry.py", "scripts/ci/helper.py", "scripts/__init__.py", "scripts/ci/__init__.py")
    out = v.check_owned_and_isolated(wfs, owned, _reader(files))
    assert any("imports scripts/lib/deep.py" in x for x in out)
    assert any("imports scripts/lib/__init__.py" in x for x in out)
    assert not any("imports scripts/ci/helper.py" in x for x in out)


def test_package_init_that_does_not_exist_yet_must_still_be_owned(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"run": "python3 -I scripts/ci/entry.py"})
    files = {"scripts/ci/entry.py": "from scripts.ci.helper import x\n", "scripts/ci/helper.py": "x = 1\n"}
    out = v.check_owned_and_isolated(
        wfs, _own("scripts/ci/entry.py", "scripts/ci/helper.py", "scripts/__init__.py"), _reader(files)
    )
    assert any("scripts/ci/__init__.py" in x for x in out)


def test_executed_script_inserting_the_repo_root_first(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"run": "python3 -I scripts/ci/entry.py"})
    files = {"scripts/ci/entry.py": "import sys\nsys.path.insert(0, '.')\n"}
    out = v.check_owned_and_isolated(wfs, _own("scripts/ci/entry.py"), _reader(files))
    assert any("sys.path position 0" in x for x in out)
    files = {"scripts/ci/entry.py": "import sys\nsys.path.append('.')\n"}
    assert not any("position 0" in x for x in v.check_owned_and_isolated(wfs, _own("scripts/ci/entry.py"), _reader(files)))


def test_comment_lines_in_run_bodies_name_no_executed_path(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"run": "# parity with scripts/checks/other.py\necho ok"})
    out = v.check_owned_and_isolated(wfs, _own("scripts/ci/x.py"), _reader({}))
    assert not any("other.py" in x for x in out)


@pytest.mark.parametrize("uses", ["actions/checkout@v7", "actions/checkout@" + "a" * 39, "docker://alpine:3", "o/r@main"])
def test_tag_or_short_sha_pinned_action_in_a_governed_job(wfs, uses) -> None:
    _job(wfs, "plan")["steps"].append({"uses": uses})
    assert any("40-hex" in x for x in v.check_pinned_actions(wfs))


def test_sha_pinned_action_with_a_version_comment_passes(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"uses": "o/r@" + "a" * 40})
    assert v.check_pinned_actions(wfs) == []


def test_unpinned_action_inside_a_composite_is_found(wfs) -> None:
    _job(wfs, "plan")["steps"].append({"uses": "./.github/actions/x"})
    files = {".github/actions/x/action.yml": "runs:\n  using: composite\n  steps:\n    - uses: o/r@v1\n"}
    assert any("o/r@v1" in x for x in v.check_pinned_actions(wfs, _reader(files)))


def test_unparseable_inputs_degrade_to_empty_results() -> None:
    assert v._safe_yaml(None) is None
    assert v._safe_yaml("a: [unclosed") is None
    assert v._imported_paths("def (:", lambda rel: None) == set()
    assert v._inserts_repo_root_first("def (:") is False
