"""rec-4637 acceptance: the real workflows have the trusted plan shape (Decision 238), and each rule has a red case."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
TRUSTED = ROOT / ".github" / "workflows" / "terraform-plan-trusted.yml"
APPLY = ROOT / ".github" / "workflows" / "terraform-apply-sandbox.yml"
NEON = ROOT / "scripts" / "ci" / "neon_api_key.sh"

ORDER = [
    "overlay",
    "screen",
    "assume",
    "mirror",
    "init",
    "plan",
    "show",
    "backstop",
    "guard",
    "persist",
    "digest",
    "fit_check",
]
SHA_RE = re.compile(r"@[0-9a-f]{40}$")
CREDENTIAL_USES = ("aws-actions/configure-aws-credentials",)


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(wf: dict[str, Any]) -> list[dict[str, Any]]:
    return wf["jobs"]["plan"]["steps"]


def _by_id(wf: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next((s for s in _steps(wf) if s.get("id") == step_id), {})


def _named(wf: dict[str, Any], name: str) -> dict[str, Any]:
    return next((s for s in _steps(wf) if s.get("name") == name), {})


def _trigger(wf: dict[str, Any]) -> dict[str, Any]:
    return wf.get("on") or wf.get(True) or {}


def _trusted_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    trig = _trigger(wf)
    prt = trig.get("pull_request_target") or {}
    if set(trig) != {"pull_request_target"}:
        out.append("trigger: only pull_request_target may trigger the workflow")
    if prt.get("types") != ["opened", "synchronize", "reopened"] or prt.get("paths") != ["terraform/personal/**"]:
        out.append("trigger: types or paths changed")
    if list(wf["jobs"]) != ["plan"]:
        out.append("jobs: exactly one job named plan")
    job = wf["jobs"].get("plan", {})
    cond = str(job.get("if", ""))
    if "head.repo.full_name == github.repository" not in cond or "base.ref == 'main'" not in cond:
        out.append("gate: same-repo and base main")
    if job.get("environment") != "tf-trusted-plan":
        out.append("environment: tf-trusted-plan")
    steps = _steps(wf)
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    if (
        len(checkouts) != 1
        or set(checkouts[0].get("with", {})) != {"persist-credentials"}
        or checkouts[0]["with"]["persist-credentials"] is not False
    ):
        out.append("checkout: one checkout, no ref or repository input, persist-credentials false")
    ids = [s.get("id") for s in steps]
    positions = [ids.index(i) if i in ids else -1 for i in ORDER]
    if -1 in positions or positions != sorted(positions):
        out.append("order: overlay, screen, assume, mirror, init, plan, show, backstop, guard, persist, digest, fit_check")
    screen_idx = ids.index("screen") if "screen" in ids else len(ids)
    if any(str(s.get("uses", "")).startswith(CREDENTIAL_USES) for s in steps[:screen_idx]):
        out.append("credentials: no credential step before the screen")
    if _by_id(wf, "assume").get("continue-on-error"):
        out.append("assume: no continue-on-error")
    out += _plan_command_violations(str(_by_id(wf, "plan").get("run", "")))
    out += _comment_violations(wf)
    out += _persist_violations(wf)
    out += _output_violations(wf)
    out += _fit_check_violations(wf)
    for step in steps:
        uses = str(step.get("uses", ""))
        if uses and not uses.startswith("./") and not SHA_RE.search(uses.split(" ")[0]):
            out.append(f"pin: {uses} is not SHA-pinned")
    return out


def _plan_command_violations(run: str) -> list[str]:
    out: list[str] = []
    flat = " ".join(run.split())
    wrappers = ["trusted_plan_env.sh", "neon_api_key.sh", "quiet_run.sh", "terraform plan"]
    at = [flat.find(w) for w in wrappers]
    if -1 in at or at != sorted(at):
        out.append("plan: trusted_plan_env.sh, neon_api_key.sh run, quiet_run.sh, terraform in that order")
    if 'neon_api_key.sh" run --' not in flat:
        out.append("plan: neon_api_key.sh must be invoked as run")
    if "-lock=false" not in flat or "-refresh=false" in flat or "-var-file" in flat:
        out.append("plan: -lock=false, no -refresh=false, no -var-file")
    if re.search(r"quiet_run\.sh\" \S+ -- terraform plan", flat) is None:
        out.append("plan: quiet_run.sh is the innermost wrapper around terraform")
    if re.search(r"(>|\btee\b)", flat):
        out.append("plan: no redirect or tee outside quiet_run.sh")
    return out


def _comment_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    refusal = _named(wf, "Post the refusal comment")
    cond = str(refusal.get("if", ""))
    if "steps.assume.outcome" in cond and "!=" not in cond:
        out.append("refusal: must not require the credential step")
    if "!cancelled()" not in cond or "steps.overlay.conclusion != 'skipped'" not in cond:
        out.append("refusal: runs unless cancelled, once the overlay step has run")
    if "!cancelled()" not in str(
        _named(wf, "Post the plan comment").get("if", "")
    ) or "steps.assume.outcome == 'success'" not in str(_named(wf, "Post the plan comment").get("if", "")):
        out.append("comment: plan comment runs unless cancelled, after a successful assume")
    body = str(_named(wf, "Post the plan comment").get("run", ""))
    build, check, post = body.find("BODY=$("), body.find('redaction_check.py" assert-clean'), body.find("gh pr comment")
    if min(build, check, post) < 0 or not build < check < post:
        out.append("comment: build the body, assert-clean, then post")
    return out


FIT_CHECK_RUN = (
    'python3 -I scripts/ci/terraform_plan_digest.py --literals-from terraform/personal --fit-check "$RUNNER_TEMP/plan.json"'
)


def _fit_check_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    step = _by_id(wf, "fit_check")
    if str(step.get("run", "")).strip() != FIT_CHECK_RUN:
        out.append("fit_check: the run body is exactly the one prescribed line")
    if any(key in step for key in ("if", "continue-on-error", "shell", "working-directory")):
        out.append("fit_check: no if, continue-on-error, shell or working-directory")
    ids = [s.get("id") for s in _steps(wf)]
    names = [s.get("name") for s in _steps(wf)]
    fit = ids.index("fit_check") if "fit_check" in ids else len(ids)
    for comment in ("Post the refusal comment", "Post the plan comment"):
        if comment in names and names.index(comment) < fit:
            out.append(f"fit_check: must come before {comment!r}")
    return out


def _persist_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for sid in ("persist", "digest"):
        step = _by_id(wf, sid)
        if "if" in step or "guard" in str(step.get("run", "")) or "VERDICT" in str(step.get("run", "")):
            out.append(f"{sid}: must not depend on the guard verdict")
    persist = str(_by_id(wf, "persist").get("run", ""))
    if "tfplan/personal/${PR_NUMBER}/${HEAD_SHA}.bin" not in persist:
        out.append("persist: key must be tfplan/personal/<pr>/<head sha>.bin")
    digest = str(_by_id(wf, "digest").get("run", ""))
    if "statuses/${HEAD_SHA}" not in digest or "context=terraform-plan-digest" not in digest:
        out.append("digest: status must be posted on HEAD_SHA with the terraform-plan-digest context")
    return out


def _output_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for step in _steps(wf):
        run = str(step.get("run", ""))
        if re.search(r"terraform show(?!.*-json)", run) or re.search(
            r"(?<![\w-])(cat|head|tail)(?![\w-])(?!\s+config/terraform-version)", run
        ):
            out.append(f"output: step {step.get('id') or step.get('name')} prints plan text")
        if "upload-artifact" in str(step.get("uses", "")):
            out.append("output: no artifact upload")
    return out


def _apply_violations(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    jobs = wf["jobs"]
    if "speculative-plan" in jobs:
        out.append("apply: speculative-plan job must be gone")
    job = jobs.get("ducklake-artifacts")
    if job is None:
        return [*out, "apply: ducklake-artifacts job missing"]
    text = yaml.safe_dump(job)
    if re.search(r"terraform-init-retry|setup-terraform|\bterraform (plan|init|show)\b|tfplan|plan\.bin", text):
        out.append("apply: ducklake-artifacts runs no terraform and writes no tfplan")
    return out


def _neon_violations(text: str) -> list[str]:
    check = text.split("check)", 1)[1].split("run)", 1)[0]
    return ["neon: check must not skip pull_request_target"] if "pull_request_target" in check else []


def _mutate(path: Path, fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    wf = copy.deepcopy(_load(path))
    fn(wf)
    return wf


def _set_run(step_id: str, old: str, new: str) -> Callable[[dict[str, Any]], None]:
    def apply(wf: dict[str, Any]) -> None:
        step = _by_id(wf, step_id)
        assert old in step["run"], (step_id, old)
        step["run"] = step["run"].replace(old, new)

    return apply


def _drop_key(step_id: str, key: str) -> Callable[[dict[str, Any]], None]:
    return lambda wf: _by_id(wf, step_id).pop(key, None)


def _swap(first: str, second: str) -> Callable[[dict[str, Any]], None]:
    def apply(wf: dict[str, Any]) -> None:
        steps = _steps(wf)
        a = next(i for i, s in enumerate(steps) if s.get("id") == first)
        b = next(i for i, s in enumerate(steps) if s.get("id") == second)
        steps[a], steps[b] = steps[b], steps[a]

    return apply


def _set_job(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    return lambda wf: wf["jobs"]["plan"].__setitem__(key, value)


def _extra_trigger(wf: dict[str, Any]) -> None:
    _trigger(wf)["workflow_call"] = {}


def _checkout_ref(wf: dict[str, Any]) -> None:
    next(s for s in _steps(wf) if "checkout" in str(s.get("uses", "")))["with"]["ref"] = "refs/pull/1/head"


def _unpin(wf: dict[str, Any]) -> None:
    step = _by_id(wf, "setup_tf")
    step["uses"] = step["uses"].split("@")[0] + "@v4"


def _plan_comment_post_first(wf: dict[str, Any]) -> None:
    step = _named(wf, "Post the plan comment")
    step["run"] = step["run"].replace('redaction_check.py" assert-clean', 'redaction_check.py" other')


def _refusal_needs_credentials(wf: dict[str, Any]) -> None:
    _named(wf, "Post the refusal comment")["if"] = "steps.assume.outcome == 'success'"


def _print_plan(wf: dict[str, Any]) -> None:
    _by_id(wf, "guard")["run"] += '\ncat "$RUNNER_TEMP/tf-plan.log"\n'


def _upload(wf: dict[str, Any]) -> None:
    _steps(wf).append({"uses": "actions/upload-artifact@" + "a" * 40, "with": {"path": "x"}})


def _status_on_base(wf: dict[str, Any]) -> None:
    step = _by_id(wf, "digest")
    step["run"] = step["run"].replace("statuses/${HEAD_SHA}", "statuses/${GITHUB_SHA}")


MUTATIONS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("extra trigger", _extra_trigger, "trigger:"),
    ("gate without base", _set_job("if", "github.event.pull_request.head.repo.full_name == github.repository"), "gate:"),
    ("no environment", lambda wf: wf["jobs"]["plan"].pop("environment"), "environment:"),
    ("checkout ref", _checkout_ref, "checkout:"),
    ("assume before screen", _swap("screen", "assume"), "order:"),
    ("continue-on-error", lambda wf: _by_id(wf, "assume").__setitem__("continue-on-error", True), "assume:"),
    ("refresh false", _set_run("plan", "-lock=false", "-lock=false -refresh=false"), "plan: -lock"),
    ("var file", _set_run("plan", "-input=false", "-input=false -var-file=x.tfvars"), "plan: -lock"),
    ("wrapper order", _set_run("plan", "quiet_run.sh", "quiet_run.sh_"), "plan:"),
    ("redirect", _set_run("plan", "-out=plan.bin", "-out=plan.bin > out.txt"), "plan: no redirect"),
    ("refusal needs credentials", _refusal_needs_credentials, "refusal:"),
    ("no assert-clean", _plan_comment_post_first, "comment:"),
    ("refusal always", lambda wf: _named(wf, "Post the refusal comment").__setitem__("if", "always()"), "refusal:"),
    ("plan comment always", lambda wf: _named(wf, "Post the plan comment").__setitem__("if", "always()"), "comment:"),
    ("persist gated", lambda wf: _by_id(wf, "persist").__setitem__("if", "steps.guard.outputs.verdict == 'PASS'"), "persist:"),
    ("persist key", _set_run("persist", "${PR_NUMBER}/${HEAD_SHA}", "${HEAD_SHA}"), "persist:"),
    ("status on base sha", _status_on_base, "digest:"),
    ("prints plan text", _print_plan, "output:"),
    ("upload artifact", _upload, "output:"),
    ("unpinned action", _unpin, "pin:"),
    ("fit_check before digest", _swap("digest", "fit_check"), "order:"),
    ("fit_check removed", lambda wf: _steps(wf).remove(_by_id(wf, "fit_check")), "order:"),
    ("fit_check body", _set_run("fit_check", "--fit-check", "--fit-report"), "fit_check: the run body"),
    ("fit_check if", lambda wf: _by_id(wf, "fit_check").__setitem__("if", "always()"), "fit_check: no if"),
    (
        "fit_check continue-on-error",
        lambda wf: _by_id(wf, "fit_check").__setitem__("continue-on-error", True),
        "fit_check: no if",
    ),
    ("fit_check shell", lambda wf: _by_id(wf, "fit_check").__setitem__("shell", "bash"), "fit_check: no if"),
    (
        "fit_check working-directory",
        lambda wf: _by_id(wf, "fit_check").__setitem__("working-directory", "x"),
        "fit_check: no if",
    ),
    (
        "fit_check after comment",
        lambda wf: _steps(wf).append(_steps(wf).pop(next(i for i, s in enumerate(_steps(wf)) if s.get("id") == "fit_check"))),
        "fit_check: must come before",
    ),
]


def test_real_trusted_workflow_has_the_trusted_shape() -> None:
    assert _trusted_violations(_load(TRUSTED)) == []


@pytest.mark.parametrize(("label", "mutation", "expected"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_rule_has_a_red_case(label: str, mutation: Callable[[dict[str, Any]], None], expected: str) -> None:
    found = _trusted_violations(_mutate(TRUSTED, mutation))
    assert any(f.startswith(expected) for f in found), (label, found)


def test_apply_sandbox_has_no_speculative_plan_and_a_terraform_free_artifact_job() -> None:
    assert _apply_violations(_load(APPLY)) == []


def test_apply_sandbox_reintroduced_speculative_plan_is_red() -> None:
    wf = _load(APPLY)
    wf["jobs"]["speculative-plan"] = {"steps": []}
    assert any("speculative-plan" in v for v in _apply_violations(wf))


def test_apply_sandbox_artifact_job_running_terraform_is_red() -> None:
    wf = _load(APPLY)
    wf["jobs"]["ducklake-artifacts"]["steps"].append({"run": "terraform plan -out=plan.bin"})
    assert any("runs no terraform" in v for v in _apply_violations(wf))


def test_neon_check_does_not_skip_pull_request_target() -> None:
    text = NEON.read_text(encoding="utf-8")
    assert _neon_violations(text) == []
    mutated = text.replace("            pull_request)", "            pull_request | pull_request_target)", 1)
    assert mutated != text and _neon_violations(mutated)
