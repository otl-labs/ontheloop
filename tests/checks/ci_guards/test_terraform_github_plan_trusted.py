"""rec-4638 acceptance: terraform-github-plan.yml has the trusted plan shape (Decision 238); each rule has a red case."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "terraform-github-plan.yml"
PERSONAL = ROOT / ".github" / "workflows" / "terraform-plan-trusted.yml"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"
ORDER = ["overlay", "screen", "assume", "mint", "init", "plan", "show", "backstop", "report"]
SHA_RE = re.compile(r"@[0-9a-f]{40}$")
CREDENTIAL_USES = ("aws-actions/configure-aws-credentials", "actions/create-github-app-token")
LOG_NAMES = ("tf-init.log", "tf-plan.log", "tf-show.log")
KEY = "TF_GITHUB_PLAN_APP_PRIVATE_KEY"
ADMITTED_READS = {"config/terraform-version", "$RUNNER_TEMP/reviewer_ids", "$RUNNER_TEMP/visibility.json"}


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _load(text: str) -> dict[str, Any]:
    return yaml.safe_load(text)


def _steps(wf: dict[str, Any]) -> list[dict[str, Any]]:
    return wf["jobs"]["plan"]["steps"]


def _by_id(wf: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next((s for s in _steps(wf) if s.get("id") == step_id), {})


def _post(wf: dict[str, Any]) -> dict[str, Any]:
    return next((s for s in _steps(wf) if s.get("name") == "Post the comment"), {})


def _header(text: str) -> list[str]:
    lines = text.splitlines()
    return [ln for ln in lines[: next(i for i, ln in enumerate(lines) if ln.startswith("on:"))] if ln.startswith("#")]


def _trigger_violations(wf: dict[str, Any]) -> list[str]:
    trig = wf.get("on") or wf.get(True) or {}
    prt = trig.get("pull_request_target") or {}
    ok = set(trig) == {"pull_request_target"} and prt.get("types") == ["opened", "synchronize", "reopened"]
    return [] if ok and prt.get("paths") == ["terraform/github/**"] else ["trigger"]


def _job_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    job = wf["jobs"].get("plan", {})
    cond = str(job.get("if", ""))
    gates = ("head.repo.full_name == github.repository", "base.ref == 'main'", "github.actor != 'dependabot[bot]'")
    if list(wf["jobs"]) != ["plan"] or not all(g in cond for g in gates):
        out.append("job: one job named plan gated on same-repo, base main and Dependabot")
    if job.get("environment") != {"name": "tf-trusted-plan", "deployment": False} or wf.get("permissions") != {}:
        out.append("job: environment tf-trusted-plan and top-level permissions {}")
    if wf.get("defaults") != {"run": {"shell": "bash"}} or any("shell" in s for s in _steps(wf)):
        out.append("shell: workflow-level defaults.run.shell is bash and no step sets shell")
    if str(wf.get("concurrency", {}).get("group", "")) == str(_load(_text(PERSONAL)).get("concurrency", {}).get("group", "")):
        out.append("concurrency: group must differ from terraform-plan-trusted.yml")
    return out


def _step_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    steps = _steps(wf)
    ids = [s.get("id") for s in steps]
    positions = [ids.index(i) if i in ids else -1 for i in ORDER]
    if -1 in positions or positions != sorted(positions):
        out.append("order: " + ", ".join(ORDER))
    screen_at = ids.index("screen") if "screen" in ids else len(ids)
    if any(str(s.get("uses", "")).startswith(CREDENTIAL_USES) for s in steps[:screen_at]):
        out.append("credentials: no credential step before the screen")
    if _by_id(wf, "assume").get("continue-on-error"):
        out.append("assume: no continue-on-error")
    assume = _by_id(wf, "assume").get("with", {})
    if not str(assume.get("role-to-assume", "")).endswith("role/agent-platform-github-ci-trusted-github-plan"):
        out.append("assume: wrong role")
    if assume.get("role-session-name") != "tf-trusted-github-plan":
        out.append("assume: wrong session name")
    if not {"owner", "repositories"} <= set(_by_id(wf, "mint").get("with", {})):
        out.append("mint: owner and repositories are required")
    for step_id in ("init", "plan", "show"):
        if "quiet_run.sh" not in str(_by_id(wf, step_id).get("run", "")):
            out.append(f"{step_id}: wrapped by quiet_run.sh")
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    if len(checkouts) != 1 or set(checkouts[0].get("with", {})) != {"persist-credentials"}:
        out.append("checkout: one checkout, no ref or repository input")
    for step in steps:
        uses = str(step.get("uses", ""))
        if uses and not uses.startswith("./") and not SHA_RE.search(uses.split(" ")[0]):
            out.append(f"pin: {uses} is not SHA-pinned")
    return out


def _run_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for step in _steps(wf):
        run = str(step.get("run", ""))
        wrapped = step.get("id") in ("init", "plan", "show")
        if any(name in run for name in LOG_NAMES) and not wrapped:
            out.append(f"log: {step.get('name')} names a plan log")
        if re.search(r"terraform\s+(-\S+\s+)*show\b", run) and "-json" not in run:
            out.append("log: terraform show without -json")
        if re.search(r"terraform\s+(-\S+\s+)*(apply|import)\b|upload-artifact|aws s3 cp", run) or "GITHUB_ENV" in run:
            out.append(f"mutating: {step.get('name')}")
        if "${{ github.event.pull_request" in run:
            out.append("injection: PR expression in a run body")
        for m in re.finditer(r"(?<![\w-])(cat|head|tail)\s+(\S+)", run):
            if m.group(2).strip('")') not in ADMITTED_READS:
                out.append(f"log: {step.get('name')} uses {m.group(1)} on {m.group(2)}")
    return out


def _post_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    post = _post(wf)
    cond = str(post.get("if", ""))
    if "!cancelled()" not in cond or "steps.checkout.outcome == 'success'" not in cond:
        out.append("post: runs unless cancelled, once checkout succeeded")
    body = str(post.get("run", ""))
    build, check, gh = (
        body.find("BODY=$("),
        body.find('terraform_github_plan_comment.py" assert-clean'),
        body.find("gh pr comment"),
    )
    if min(build, check, gh) < 0 or not build < check < gh:
        out.append("post: build the body, assert-clean it, then post")
    steps = _steps(wf)
    before = steps[: steps.index(post)] if post in steps else steps
    needed = {s["id"] for s in before if s.get("id")} - {"checkout"}
    if any(not s.get("id") for s in before):
        out.append("map: every step before the post step has an id")
    mapped = set(re.findall(r"steps\.(\w+)\.outcome", " ".join(str(v) for v in (post.get("env") or {}).values())))
    if needed - mapped:
        out.append("map: the category map must name every step before the post step")
    if any(k for k in (post.get("env") or {}) if "REVIEWER_IDS" in k.upper() or "USER_IDS" in k.upper()):
        out.append("env: no reviewer ids in step env")
    return out


def _header_violations(text: str) -> list[str]:
    header = _header(text)
    out = [f"header: line {ln!r}" for ln in header if KEY in ln and ("repository secret" in ln or "tf-trusted-plan" not in ln)]
    joined = " ".join(ln.lstrip("# ").strip() for ln in header)
    if "Accepted residual until VP 17" not in joined or "repository-level secret" not in joined:
        out.append("header: the VP 17 residual sentence")
    return out


def _codeowners_violations(owners: str) -> list[str]:
    needed = ("/scripts/ci/plan_names_core.py", "/scripts/ci/terraform_github_plan_comment.py")
    return [f"codeowners: {p}" for p in needed if p not in owners]


def violations(text: str, owners: str | None = None) -> list[str]:
    wf = _load(text)
    return [
        *_trigger_violations(wf),
        *_job_violations(wf),
        *_step_violations(wf),
        *_run_violations(wf),
        *_post_violations(wf),
        *_header_violations(text),
        *_codeowners_violations(_text(CODEOWNERS) if owners is None else owners),
    ]


def test_real_github_plan_workflow_has_the_trusted_shape() -> None:
    assert violations(_text()) == []


def _mutations() -> dict[str, Callable[[str], str]]:
    def drop_ids(text: str) -> str:
        return text.replace("        id: probe\n", "")

    return {
        "trigger": lambda t: t.replace("on:\n  pull_request_target:", "on:\n  pull_request:", 1),
        "paths": lambda t: t.replace(
            "      - 'terraform/github/**'\n", "      - 'terraform/github/**'\n      - '.github/workflows/x.yml'\n", 1
        ),
        "gate": lambda t: t.replace(" && github.event.pull_request.base.ref == 'main'", "", 1),
        "environment": lambda t: t.replace("    environment: {name: tf-trusted-plan, deployment: false}\n", "", 1),
        "deployment_true": lambda t: t.replace("deployment: false}", "deployment: true}", 1),
        "permissions": lambda t: t.replace("permissions: {}\n", "permissions:\n  contents: read\n", 1),
        "defaults": lambda t: t.replace("defaults:\n  run:\n    shell: bash\n\n", "", 1),
        "step_shell": lambda t: t.replace(
            "      - name: Screen the PR's Terraform\n        id: screen\n",
            "      - name: Screen the PR's Terraform\n        id: screen\n        shell: sh\n",
            1,
        ),
        "concurrency": lambda t: t.replace("terraform-github-plan-pr-", "terraform-plan-trusted-", 1),
        "role": lambda t: t.replace("trusted-github-plan\n", "trusted-plan\n", 1),
        "session": lambda t: t.replace("role-session-name: tf-trusted-github-plan", "role-session-name: x", 1),
        "continue_on_error": lambda t: t.replace(
            "        id: assume\n", "        id: assume\n        continue-on-error: true\n", 1
        ),
        "mint_scope": lambda t: t.replace("          repositories: ${{ github.event.repository.name }}\n", "", 1),
        "unpinned": lambda t: t.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7", "actions/checkout@v7", 1
        ),
        "apply": lambda t: t.replace("terraform -chdir=terraform/github show", "terraform -chdir=terraform/github apply", 1),
        "print_log": lambda t: t.replace('cat "$RUNNER_TEMP/visibility.json"', 'cat "$RUNNER_TEMP/tf-plan.log"', 1),
        "show_without_json": lambda t: t.replace("show -json plan.bin", "show plan.bin", 1),
        "post_before_check": lambda t: t.replace("gh pr comment", "XX", 1).replace(
            "printf '%s' \"$BODY\" |", 'gh pr comment "$PR_NUMBER" --body "$BODY"\n          printf \'%s\' "$BODY" |', 1
        ),
        "check_removed": lambda t: t.replace(
            " assert-clean \\\n            --values-file", " noop \\\n            --values-file", 1
        ),
        "step_missing_id": drop_ids,
        "map_missing": lambda t: t.replace("          O_PROBE: ${{ steps.probe.outcome }}\n", "", 1),
        "key_in_repo_secret_header": lambda t: t.replace(
            "# Configuration: tf-trusted-plan environment secret " + KEY, "# Configuration: repository secret " + KEY, 1
        ),
        "residual_missing": lambda t: t.replace("Accepted residual until VP 17", "Accepted residual", 1),
    }


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_each_shape_rule_has_a_red_case(name: str) -> None:
    text = _text()
    mutated = _mutations()[name](text)
    assert mutated != text, f"mutation {name} did not change the workflow"
    assert violations(mutated), name


def test_credential_step_moved_above_the_screen_is_red() -> None:
    wf = _load(_text())
    steps = _steps(wf)
    ids = [s.get("id") for s in steps]
    moved = copy.deepcopy(wf)
    moved["jobs"]["plan"]["steps"].insert(ids.index("screen"), moved["jobs"]["plan"]["steps"].pop(ids.index("assume")))
    assert any(v.startswith("credentials") for v in _step_violations(moved))


def test_loader_rule_codeowners_must_name_the_core_and_the_gate() -> None:
    owners = _text(CODEOWNERS)
    assert _codeowners_violations(owners) == []
    assert _codeowners_violations(owners.replace("/scripts/ci/plan_names_core.py", "/scripts/ci/other.py"))
    assert _codeowners_violations(owners.replace("/scripts/ci/terraform_github_plan_comment.py", "/scripts/ci/other.py"))


@pytest.mark.parametrize("path", [WORKFLOW, PERSONAL], ids=lambda p: p.name)
def test_trusted_workflows_default_to_bash_pipefail(path: Path) -> None:
    assert _load(_text(path)).get("defaults") == {"run": {"shell": "bash"}}
