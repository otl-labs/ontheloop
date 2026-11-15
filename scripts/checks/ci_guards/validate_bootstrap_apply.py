"""terraform/bootstrap CI apply fence (PLAN-bootstrap-ci-gated-apply, Decision 257).

The two bootstrap CI roles are reachable only from main's terraform-bootstrap-apply.yml, the apply role only behind one
operator approval. Each predicate takes parsed or raw input so the red cases run on synthetic data; the registered check
loads the real tree.

  (a) triggers are exactly push to main (the listed paths) and workflow_dispatch with the one prove_gate input; no
      pull_request*, workflow_call, workflow_run or schedule
  (b) jobs are exactly preview (tf-trusted-plan, deployment: false) and apply (literal tf-bootstrap-apply); both if-guards
      require refs/heads/main in every disjunct and apply needs preview
  (c) role/bootstrap-preview is assumed only by the preview job and role/bootstrap-apply only by the apply job, and no
      other workflow or composite action names either
  (d) the apply job's first step after checkout is the approvals preflight for tf-bootstrap-apply
  (e) no upload-artifact, no tee or cat of a plan file, and no terraform invocation outside quiet_run.sh
  (f) both roles' job_workflow_ref in bootstrap_ci_apply.tf names this workflow at refs/heads/main and their subjects
      name these two Environments
  (g) .github/CODEOWNERS covers the workflow, both scripts and this check
  (h) each job runs the gate's screen step before any credential or terraform step
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards.validate_trusted_plan_environment import _disjuncts, _env_name, _jobs, _owned, _steps, _triggers
from scripts.verify_ci_workflow import _load

WORKFLOW = "terraform-bootstrap-apply.yml"
GATE_ENV = "tf-bootstrap-apply"
PREVIEW_ENV = "tf-trusted-plan"
APPLY_ROLE = "bootstrap-apply"
PREVIEW_ROLE = "bootstrap-preview"
HCL = "terraform/bootstrap/bootstrap_ci_apply.tf"
APPLY_SCRIPT = "scripts/ci/terraform_bootstrap_apply.sh"
GATE_SCRIPT = "scripts/ci/terraform_bootstrap_apply_gate.py"
OWNED = (
    f".github/workflows/{WORKFLOW}",
    APPLY_SCRIPT,
    GATE_SCRIPT,
    "scripts/ci/terraform_bootstrap_apply_classify.py",
    "scripts/ci/terraform_bootstrap_apply_screen.py",
    "scripts/checks/ci_guards/validate_bootstrap_apply.py",
)
MAIN_REF = f"otl-labs/ontheloop/.github/workflows/{WORKFLOW}@refs/heads/main"
PUSH_PATHS = frozenset(
    {
        "terraform/bootstrap/**",
        f".github/workflows/{WORKFLOW}",
        "scripts/ci/terraform_bootstrap_apply*",
        "scripts/ci/plan_names_core.py",
        "scripts/ci/environment_reviewer_preflight.py",
        "scripts/ci/terraform_hcl_screen.py",
        "scripts/ci/terraform_hcl_screen_policy.py",
        "scripts/ci/terraform_hcl_lexer.py",
        "config/terraform-version",
        "config/aws-region",
        ".github/actions/aws-region/**",
        ".github/actions/materialise-tfvars/**",
    }
)
_MAIN = "github.ref == 'refs/heads/main'"
_TF_CALL_RE = re.compile(r"(^|[\s;&|(])terraform\s+-")
_PLAN_LEAK_RE = re.compile(r"\b(tee|cat)\b[^\n]*(plan\.(bin|json)|apply\.(bin|json))")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _roles(job: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for s in _steps(job):
        with_ = _dict(s.get("with"))
        if "role-to-assume" in with_:
            out.add(str(with_["role-to-assume"]).rsplit("/", 1)[-1])
    return out


def check_triggers(wf: dict[str, Any] | None) -> list[str]:
    if wf is None:
        return [f"a: {WORKFLOW} is missing"]
    on = _dict(wf.get("on"))
    push = _dict(on.get("push"))
    out = []
    if _triggers(wf) != {"push", "workflow_dispatch"} or push.get("branches") != ["main"] or set(push) - {"branches", "paths"}:
        out.append("a: on: must be exactly push (branches [main]) and workflow_dispatch")
    if set(push.get("paths") or []) != PUSH_PATHS:
        out.append("a: the push paths must be exactly the plan's list")
    if set(_dict(_dict(on.get("workflow_dispatch")).get("inputs"))) != {"prove_gate"}:
        out.append("a: workflow_dispatch must take exactly one input, prove_gate")
    return out


def check_jobs(wf: dict[str, Any] | None) -> list[str]:
    if wf is None:
        return []
    jobs = _jobs(wf)
    out = []
    if set(jobs) != {"preview", "apply"}:
        out.append("b: the workflow's jobs must be exactly preview and apply")
    apply, preview = jobs.get("apply", {}), jobs.get("preview", {})
    env = _dict(preview.get("environment"))
    if _env_name(preview) != PREVIEW_ENV or env.get("deployment") is not False:
        out.append(f"b: the preview job must declare {PREVIEW_ENV} with deployment: false")
    if _env_name(apply) != GATE_ENV or not isinstance(apply.get("environment"), str):
        out.append(f"b: the apply job must declare environment {GATE_ENV} as a literal")
    for jid in ("preview", "apply"):
        if any(_MAIN not in d for d in _disjuncts(jobs.get(jid, {}).get("if"))):
            out.append(f"b: the {jid} job's if must require refs/heads/main in every disjunct")
    if apply.get("needs") not in ("preview", ["preview"]):
        out.append("b: the apply job must need preview")
    return out


def check_roles(wf: dict[str, Any] | None, others: dict[str, str]) -> list[str]:
    jobs = _jobs(wf or {})
    out = []
    if [j for j, job in jobs.items() if APPLY_ROLE in _roles(job)] != ["apply"] or _roles(jobs.get("apply", {})) != {
        APPLY_ROLE
    }:
        out.append(f"c: only the apply job may assume {APPLY_ROLE}, and it assumes nothing else")
    if [j for j, job in jobs.items() if PREVIEW_ROLE in _roles(job)] != ["preview"] or _roles(jobs.get("preview", {})) != {
        PREVIEW_ROLE
    }:
        out.append(f"c: only the preview job may assume {PREVIEW_ROLE}, and it assumes nothing else")
    for path, text in sorted(others.items()):
        if path.endswith(WORKFLOW):
            continue
        for role in (APPLY_ROLE, PREVIEW_ROLE):
            if re.search(rf"role/{role}\b", text):
                out.append(f"c: {path} names {role}")
    return out


def check_approvals_first(wf: dict[str, Any] | None) -> list[str]:
    steps = _steps(_jobs(wf or {}).get("apply", {}))
    after = [s for s in steps if not str(s.get("uses", "")).startswith("actions/checkout@")]
    first = str(after[0].get("run", "")) if after else ""
    ok = "environment_reviewer_preflight.py approvals" in first and f"--environment {GATE_ENV}" in first
    return [] if ok else [f"d: the apply job's first step after checkout must be the approvals preflight for {GATE_ENV}"]


def check_no_leaks(wf: dict[str, Any] | None, script_text: str) -> list[str]:
    out = []
    bodies = [s for job in _jobs(wf or {}).values() for s in _steps(job)]
    for s in bodies:
        if str(s.get("uses", "")).startswith("actions/upload-artifact@"):
            out.append("e: no step may upload an artifact")
    texts = [str(s["run"]) for s in bodies if isinstance(s.get("run"), str)] + [script_text]
    for text in texts:
        if _PLAN_LEAK_RE.search(text):
            out.append("e: no step may tee or cat a plan file")
        for line in text.splitlines():
            if _TF_CALL_RE.search(line) and "$q" not in line and "quiet_run.sh" not in line:
                out.append("e: a terraform invocation runs outside quiet_run.sh")
                break
    return out


def check_hcl(text: str) -> list[str]:
    out = []
    m = re.search(r"bootstrap_ci_workflow_refs\s*=\s*\[(.*?)\]", text, re.S)
    if not m or re.findall(r'"([^"]+)"', m.group(1)) != [MAIN_REF]:
        out.append("f: bootstrap_ci_workflow_refs must be exactly this workflow at refs/heads/main")
    if len(re.findall(r'job_workflow_ref"\s*=\s*local\.bootstrap_ci_workflow_refs', text)) != 2:
        out.append("f: both roles' job_workflow_ref must be local.bootstrap_ci_workflow_refs")
    for var, env in (("bootstrap_ci_apply_env", GATE_ENV), ("bootstrap_ci_preview_env", PREVIEW_ENV)):
        if not re.search(rf'{var}\s*=\s*"{env}"', text) or f"environment:${{local.{var}}}" not in text:
            out.append(f"f: a role subject must name the {env} Environment")
    return out


def check_codeowners(codeowners: str) -> list[str]:
    return [f"g: {p} is not code-owned" for p in OWNED if not _owned(p, codeowners)]


def check_screen_first(wf: dict[str, Any] | None) -> list[str]:
    out = []
    for jid, job in _jobs(wf or {}).items():
        steps = _steps(job)
        screen = next(
            (i for i, s in enumerate(steps) if "terraform_bootstrap_apply_gate.py screen" in str(s.get("run", ""))), None
        )
        gated = [
            i
            for i, s in enumerate(steps)
            if "configure-aws-credentials@" in str(s.get("uses", ""))
            or "terraform" in str(s.get("run", "")).replace("screen", "")
            and "terraform_bootstrap_apply_gate.py" not in str(s.get("run", ""))
            and "environment_reviewer_preflight" not in str(s.get("run", ""))
        ]
        if screen is None or any(i < screen for i in gated):
            out.append(f"h: the {jid} job must run the gate's screen step before any credential or terraform step")
    return out


def run_all(wf: dict[str, Any] | None, others: dict[str, str], hcl: str, codeowners: str, script_text: str) -> list[list[str]]:
    return [
        check_triggers(wf),
        check_jobs(wf),
        check_roles(wf, others),
        check_approvals_first(wf),
        check_no_leaks(wf, script_text),
        check_hcl(hcl),
        check_codeowners(codeowners),
        check_screen_first(wf),
    ]


def _load_tree() -> tuple[dict[str, Any] | None, dict[str, str], str, str, str]:
    root = _common.ROOT
    others = {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for pattern in (".github/workflows/*.y*ml", ".github/actions/**/action.y*ml")
        for p in sorted(root.glob(pattern))
    }
    wf_path = root / ".github" / "workflows" / WORKFLOW
    workflow = _load(str(wf_path)) if wf_path.exists() else None
    return (
        workflow,
        others,
        (root / HCL).read_text(encoding="utf-8"),
        (root / ".github" / "CODEOWNERS").read_text(encoding="utf-8"),
        (root / APPLY_SCRIPT).read_text(encoding="utf-8"),
    )


@registry.register("validate_bootstrap_apply", owner="platform")
def validate_bootstrap_apply(failed: list[str]) -> None:
    """Assert the terraform/bootstrap CI apply fence. See module docstring."""
    print("\n=== terraform/bootstrap CI apply fence ===")
    try:
        groups = run_all(*_load_tree())
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        AttributeError,
        IndexError,
        TypeError,
        yaml.YAMLError,
        SyntaxError,
    ) as exc:
        print(f"  FAIL: could not load or parse the inputs: {exc}")
        failed.append("bootstrap-apply: inputs unreadable, so no predicate ran")
        registry.examined(0, unit="invariants")
        return
    violations = [x for group in groups for x in group]
    for violation in violations:
        print(f"  FAIL: {violation}")
        failed.append(f"bootstrap-apply: {violation}")
    registry.examined(len(groups), unit="invariants")
    if not violations:
        print("  PASS: predicates (a)-(h)")
