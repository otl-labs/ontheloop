"""Predicate n of validate_github_settings_apply: the provider-mirror wiring (rec-5005, Decision 248 cl.4 Update).

Each job of terraform-github-apply.yml runs the fail-open mirror fetch after its AWS credentials step and before any
step that runs terraform; the preview plan step and the apply script name the init-failure classifier. Code-owned
(CODEOWNERS), so the guard cannot be weakened through this helper unseen. Imports nothing from the guard that imports it.
"""

from __future__ import annotations

from typing import Any

from scripts.checks import _common
from scripts.checks.ci_guards.validate_trusted_plan_environment import _steps

MIRROR_ARN = "arn:aws:s3:::agent-platform-data-lake/tf-provider-mirror/registry.terraform.io/integrations/github/*"
MIRROR_PREFIX = "s3://agent-platform-data-lake/tf-provider-mirror/"
MIRROR_RUN = "bash scripts/ci/terraform_github_apply_mirror.sh fetch"
CLASSIFY = 'terraform_github_apply_mirror.sh" classify'
APPLY_SCRIPT_PATH = "scripts/ci/terraform_github_apply.sh"
_CREDENTIALS = "aws-actions/configure-aws-credentials@"


def _index(steps: list[dict[str, Any]], pred: Any) -> int | None:
    return next((i for i, s in enumerate(steps) if pred(s)), None)


def _mirror_faults(jid: str, steps: list[dict[str, Any]]) -> list[str]:
    mirrors = [s for s in steps if s.get("id") == "mirror"]
    if len(mirrors) != 1:
        return ["the step with id mirror must exist exactly once"]
    mirror = mirrors[0]
    at = steps.index(mirror)
    faults = []
    if str(mirror.get("run", "")).strip() != MIRROR_RUN or mirror.get("env") != {"MIRROR_PREFIX": MIRROR_PREFIX}:
        faults.append(f"it must run exactly `{MIRROR_RUN}` with env MIRROR_PREFIX={MIRROR_PREFIX}")
    creds = _index(steps, lambda s: str(s.get("uses", "")).startswith(_CREDENTIALS))
    if creds is None or at < creds:
        faults.append("it must come after the configure-aws-credentials step")
    first_tf = _index(steps, lambda s: s is not mirror and "terraform" in str(s.get("run", "")))
    if first_tf is not None and at > first_tf:
        faults.append("it must come before the first step that runs terraform")
    token = _index(steps, lambda s: s.get("id") == "app_token")
    if jid == "preview" and token is not None and at > token:
        faults.append("it must come before the plan App token is minted")
    return faults


def _classify_faults(jobs: dict[str, Any], apply_script: str) -> list[str]:
    out = []
    plan = next((s for s in _steps(jobs.get("preview", {})) if s.get("id") == "plan"), {})
    init = next((ln for ln in str(plan.get("run", "")).splitlines() if " init " in ln), "")
    failed = init.find("TF_GITHUB_PREVIEW_FAILED init")
    named = init.find(CLASSIFY)
    if not (0 <= named < failed):
        out.append(
            "n: the preview plan step's init failure branch must call the classifier before TF_GITHUB_PREVIEW_FAILED init"
        )
    start = apply_script.find("terraform_step() {")
    body = apply_script[start : apply_script.find("\n}\n", start)] if start >= 0 else ""
    if CLASSIFY not in body:
        out.append("n: terraform_step in the apply script must call the classifier on a failed step")
    return out


def check_mirror_wiring(jobs: dict[str, Any], apply_script: str | None = None) -> list[str]:
    """Predicate n: one line per job's mirror step however many faults it has, plus one per missing classify call."""
    if apply_script is None:
        apply_script = (_common.ROOT / APPLY_SCRIPT_PATH).read_text(encoding="utf-8")
    out = []
    for jid in ("preview", "apply"):
        faults = _mirror_faults(jid, _steps(jobs.get(jid, {})))
        if faults:
            out.append(f"n: {jid}: " + "; ".join(faults))
    return out + _classify_faults(jobs, apply_script)
