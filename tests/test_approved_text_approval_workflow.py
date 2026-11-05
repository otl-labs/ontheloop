"""Static shape of the approved-text approval workflow, its Terraform Environment, pr-validate's token wiring and the
CODEOWNERS closure (rec-4769). The test_deployment_approval_probe_workflow.py pattern."""

from __future__ import annotations

import re
from typing import Any

import yaml

from scripts import dependency_graph
from scripts.checks._common import ROOT
from scripts.roadmap import approved_text_github as adapter
from scripts.roadmap import approved_words, review_page

WORKFLOW = ROOT / ".github" / "workflows" / "approved-text-approval.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
ENVIRONMENTS_TF = ROOT / "terraform" / "github" / "environments.tf"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"
_SHA_PIN = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")


def _load(path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def _wf() -> dict[str, Any]:
    return _load(WORKFLOW)


def _steps(job: str) -> list[dict[str, Any]]:
    return _wf()["jobs"][job]["steps"]


def _runs(job: str) -> str:
    return "\n".join(s.get("run", "") for s in _steps(job))


def _tf_block(kind: str, name: str) -> str:
    text = ENVIRONMENTS_TF.read_text(encoding="utf-8")
    match = re.search(rf'resource "{kind}" "{name}" \{{.*?\n\}}\n', text, re.S)
    assert match, f"{kind}.{name} missing"
    return match.group(0)


class TestWorkflow:
    def test_dispatch_only_with_four_required_string_inputs(self) -> None:
        on = _wf()["on"]
        assert list(on) == ["workflow_dispatch"]
        inputs = on["workflow_dispatch"]["inputs"]
        assert list(inputs) == ["slug", "sha", "digest", "fingerprint"]
        assert all(i["required"] is True and i["type"] == "string" for i in inputs.values())

    def test_top_level_permissions_empty(self) -> None:
        assert _wf()["permissions"] == {}

    def test_render_job_is_main_only_and_read_only(self) -> None:
        job = _wf()["jobs"]["render"]
        assert job["if"] == "github.ref == 'refs/heads/main'"
        assert job["permissions"] == {"contents": "read"}
        assert "environment" not in job

    def test_approve_job_waits_on_the_gate_with_no_permissions(self) -> None:
        job = _wf()["jobs"]["approve"]
        assert job["needs"] == "render" and job["environment"] == adapter.ENVIRONMENT == "approved-text"
        assert job["permissions"] == {} and len(job["steps"]) == 1 and "run" in job["steps"][0]
        assert "uses" not in job["steps"][0]

    def test_wake_job_cannot_fail_the_run_and_has_only_the_comment_grant(self) -> None:
        job = _wf()["jobs"]["wake"]
        assert job["needs"] == "approve" and job["continue-on-error"] is True
        assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
        assert _wf()["jobs"]["wake"]["steps"][-1]["env"]["APPROVED_TEXT_GH_TOKEN"] == "${{ github.token }}"
        assert "approved_text_github wake" in _runs("wake")

    def test_the_approve_job_name_is_what_the_adapter_keys_on(self) -> None:
        assert adapter.APPROVE_JOB in _wf()["jobs"] and "name" not in _wf()["jobs"][adapter.APPROVE_JOB]

    def test_no_secrets_or_id_token_anywhere(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "id-token" not in text.replace("Never add id-token or secrets", "")
        assert set(re.findall(r"secrets\.(\w+)", text)) == set()

    def test_inputs_reach_shell_only_through_env(self) -> None:
        for job in _wf()["jobs"].values():
            for step in job["steps"]:
                assert "${{" not in step.get("run", ""), step
        env = _steps("render")[-1]["env"]
        assert env == {
            "SLUG": "${{ inputs.slug }}",
            "SHA": "${{ inputs.sha }}",
            "DIGEST": "${{ inputs.digest }}",
            "FINGERPRINT": "${{ inputs.fingerprint }}",
        }

    def test_render_guards_the_inputs_before_any_git_call_and_reads_data_only(self) -> None:
        run = _steps("render")[-1]["run"]
        assert run.index("^[0-9a-f]{40}$") < run.index("git fetch") and run.index("^[a-z0-9-]+$") < run.index("git fetch")
        assert 'git fetch --depth=1 origin --end-of-options "$SHA"' in run
        assert 'git show --end-of-options "$SHA:docs/plans/approved/$SLUG.yaml"' in run
        assert '--base-file-dir "$GITHUB_WORKSPACE"' in run and run.count("python -m") == 1
        assert '--expect-approved "$DIGEST"' in run and '--fingerprint "$FINGERPRINT"' in run
        assert '>> "$GITHUB_STEP_SUMMARY"' in run

    def test_both_python_jobs_install_exactly_the_two_packages_under_the_requirements_pins(self) -> None:
        for job in ("render", "wake"):
            assert "pip install pyyaml pydantic -c requirements.txt" in _runs(job).splitlines()
        assert re.search(r"^pyyaml==", (ROOT / "requirements.txt").read_text(encoding="utf-8"), re.M | re.I)

    def test_every_action_is_sha_pinned_and_checkouts_persist_no_credentials(self) -> None:
        for job in _wf()["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert _SHA_PIN.match(step["uses"]), step["uses"]
                    if step["uses"].startswith("actions/checkout"):
                        assert step["with"]["persist-credentials"] is False

    def test_the_run_name_grammar_equals_the_adapters_title(self) -> None:
        template = _wf()["run-name"]
        rendered = (
            template.replace("${{ inputs.slug }}", "demo")
            .replace("${{ inputs.fingerprint }}", "F")
            .replace("${{ inputs.digest }}", "D")
        )
        assert rendered == "approved-text demo F D"
        digest = "ab" * 32
        assert adapter.run_title("demo", digest) == f"approved-text demo {review_page.fingerprint(digest)} {digest}"

    def test_the_adapter_path_constant_is_this_file(self) -> None:
        assert adapter.WORKFLOW_PATH == f".github/workflows/{WORKFLOW.name}" and adapter.WORKFLOW_FILE == WORKFLOW.name


class TestPrValidate:
    def _job(self) -> dict[str, Any]:
        return _load(CI)["jobs"]["pr-validate"]

    def test_holds_actions_read(self) -> None:
        assert self._job()["permissions"] == {"contents": "read", "actions": "read"}

    def test_only_the_fast_tier_step_gets_the_dedicated_token_and_no_gh_token(self) -> None:
        carriers = [s for s in self._job()["steps"] if "APPROVED_TEXT_GH_TOKEN" in yaml.safe_dump(s)]
        assert [s["name"].startswith("Validate fast tier") for s in carriers] == [True]
        assert carriers[0]["env"]["APPROVED_TEXT_GH_TOKEN"] == "${{ github.token }}"
        assert "GH_TOKEN" not in carriers[0]["env"]


class TestTerraform:
    def test_environment_is_operator_only_with_no_admin_bypass_and_never_destroyed(self) -> None:
        block = _tf_block("github_repository_environment", "approved_text")
        assert 'environment         = "approved-text"' in block
        assert "users = var.gated_apply_reviewer_user_ids" in block
        assert "prevent_self_review = false" in block and "can_admins_bypass   = false" in block
        assert "protected_branches     = false" in block and "custom_branch_policies = true" in block
        assert "prevent_destroy = true" in block and not re.search(r"\b\d{5,}\b", block)

    def test_main_only_deployment_policy(self) -> None:
        block = _tf_block("github_repository_environment_deployment_policy", "approved_text_main")
        assert 'branch_pattern = "main"' in block and "github_repository_environment.approved_text.environment" in block

    def test_each_new_address_is_exempt_or_carries_an_import_block(self) -> None:
        from tests.checks.iam_tf import test_terraform_github_import_blocks as imports

        text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "terraform" / "github").glob("*.tf"))
        for address in (
            "github_repository_environment.approved_text",
            "github_repository_environment_deployment_policy.approved_text_main",
        ):
            assert address in imports.EXEMPT or re.search(rf"to\s*=\s*{re.escape(address)}\b", text), address

    def test_never_an_oidc_subject(self) -> None:
        for root in ("bootstrap", "personal"):
            for path in (ROOT / "terraform" / root).rglob("*"):
                if path.is_file() and path.suffix in {".tf", ".tfvars", ".yaml", ".json"}:
                    assert "approved-text" not in path.read_text(encoding="utf-8", errors="replace"), path


class TestCodeowners:
    def _owned(self) -> set[str]:
        paths = set()
        for line in CODEOWNERS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                paths.add(line.split()[0])
        return paths

    def _closure_paths(self) -> set[str]:
        graph = dependency_graph.import_subgraph(dependency_graph.build_graph())
        modules = {"scripts.roadmap.approved_words", "scripts.roadmap.approved_text_github"}
        for module in set(modules):
            modules |= set(dependency_graph.forward_closure(graph, module))
        paths = set()
        for module in modules:
            as_package = ROOT / module.replace(".", "/") / "__init__.py"
            rel = as_package if as_package.exists() else ROOT / (module.replace(".", "/") + ".py")
            paths.add("/" + rel.relative_to(ROOT).as_posix())
        return paths

    def test_the_workflow_and_every_path_of_the_render_and_wake_closure_are_code_owned(self) -> None:
        owned = self._owned()
        assert "/.github/workflows/approved-text-approval.yml" in owned
        missing = sorted(p for p in self._closure_paths() if p not in owned)
        assert not missing, f"import closure paths without a CODEOWNERS entry: {missing}"

    def test_the_closure_holds_nothing_under_checks_nor_the_check_side_modules(self) -> None:
        paths = self._closure_paths()
        assert not [p for p in paths if p.startswith("/scripts/checks/")]
        assert "/scripts/roadmap/approved_text.py" not in paths and "/scripts/roadmap/plan_document.py" not in paths

    def test_the_tests_and_fixtures_that_pin_the_approval_are_code_owned(self) -> None:
        owned = self._owned()
        for path in (
            "/tests/test_approved_text_provenance.py",
            "/tests/test_approved_text_github.py",
            "/tests/test_approved_text_approval_workflow.py",
            "/tests/test_review_page.py",
            "/tests/fixtures/approved_text/",
            "/tests/fixtures/approved_text_github/",
        ):
            assert path in owned and (ROOT / path.lstrip("/")).exists(), path

    def test_the_closure_modules_are_the_ones_this_plan_names(self) -> None:
        assert approved_words.__name__ == "scripts.roadmap.approved_words"
        assert {"/scripts/repo_identity.py", "/scripts/roadmap/review_page.py"} <= self._closure_paths()
