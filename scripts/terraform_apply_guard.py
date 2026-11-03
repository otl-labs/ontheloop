#!/usr/bin/env python3
"""Deterministic guard for the sandbox auto-apply pipeline (Decision 77).

Parses `terraform show -json <planfile>` output and decides whether the plan is safe to
auto-apply in the sandbox environment without a human in the loop. The guard is the fail-closed
plan-CONTENT control (Decision 77 / CD.35): it, together with a subagent plan review, IS the
apply gate. Branch protection is now active (main-protection ruleset, Decision 83 / CD.20) but
deliberately non-wedging -- the guard + review remain the content gate. It MUST fail closed.

Exit codes (consumed by .github/workflows/terraform-apply-sandbox.yml):
  0  plan is safe: only create / update / no-op / read on non-IAM resources, no trust diffs, and
     any neon_* change is a pure create / no-op / read; an in-budget IAM inline-policy /
     attachment UPDATE on a managed boundary-carrying role (T2.25 / Decision 92 point 5); OR a
     resource-based-policy change matching the known-safe EventBridge invoke shape (T2.45 /
     DEP-06).
  2  plan is BLOCKED: contains a destroy, a replacement, a trust-policy (assume_role_policy) diff,
     an out-of-budget IAM-sensitive change, a non-create neon_* change, or a resource-based-policy
     change (Lambda permission, S3/SNS/Secrets-Manager/Glue resource policy, Lambda function URL)
     that is not the known-safe EventBridge shape (T2.45 / DEP-06). Requires a manual admin apply
     or a gated-apply Environment approval.
  1  internal / parse error (also blocks apply at the workflow level).

Detection contract (against `terraform show -json`, iterating .resource_changes[]):
  - BLOCK if .change.actions contains "delete" (covers ["delete"] destroys and the replacement
    pairs ["delete","create"] / ["create","delete"]).
  - BLOCK if .type is a neon_* resource AND .change.actions is not a pure ["create"]/["no-op"]/
    ["read"] (T2.16b / CD.34). The DuckLake catalog is the lakehouse's single point of total
    failure, so its third-party Neon resources auto-apply only as pure creates. An update is where
    an IP allow-list widening / role-credential rotation / project-setting change would land;
    delete + replace are already blocked by the rule above. A create is allowed on the strength of
    compensating controls (enforced TLS sslmode=require + a scoped neon_role + the DSN in Secrets
    Manager), NOT an IP allow-list -- Neon IP-Allow is Scale-plan-only and unavailable on the free
    tier, and egress here is dynamic (REPORT R3 / CD.34). The compensating controls are enforced in
    neon_ducklake_catalog.tf, not introspected here, so the guard stays robust against the
    sensitive/unknown attribute values a Neon create reports at plan time.
  - BLOCK if a trust attribute (assume_role_policy) differs between .change.before and
    .change.after on ANY resource. assume_role_policy is serialised as a JSON-encoded string, so
    it is normalised via json.loads before comparison (key-order/whitespace differences do not
    cause nuisance trips). Trust check runs BEFORE IAM classification (T2.25) so a trust diff on
    a managed role is always gated, never slips through as an in-budget update.
  - PASS (in-budget) if .type is in in_budget_resource_types, every .change.actions entry is an
    in_budget_action (SUBSET membership, budget v2 -- ["create","update"] admits a lone
    ["create"] or ["update"]; a replace pair such as ["delete","create"] is never a subset and
    stays gated), AND the target role (.change.after.role or .change.before.role) is a managed
    boundary-carrying role -- matched against in_budget_managed_role_prefix (a boundary-carrying
    agent-platform-* prefix, budget v2 / Decision 92 point 5 / Decision 144), with a fail-safe
    fallback to the v1 enumerated in_budget_managed_roles when no prefix is present. The apply
    role itself (agent-platform-github-ci-apply) is ALWAYS self-excluded: the widened prefix
    matches its own name, so an inline-policy write on it must never auto-apply -- the guard-side
    counterpart to github_ci_apply.tf's DenySelfInlinePolicyWrite (T2.23 self-grant break). Budget
    table loaded from terraform/bootstrap/authority_budget.json (override via TF_AUTHORITY_BUDGET
    env var). Missing or unparseable table = fail closed (all IAM treated as out-of-budget,
    Decision 77). This is a predicate widening to read the v2 budget shape -- NO new classification
    stage; the fail-closed control theory is retained verbatim.
  - BLOCK if .type is IAM-sensitive AND .change.actions is not ["no-op"]/["read"] AND not in-budget.
  - RESOURCE-POLICY stage (T2.45 / DEP-06), evaluated LAST (after IAM classification, so a delete
    or trust diff on one of these types is still caught by the earlier rules above): a non-inert
    change on aws_lambda_permission, aws_s3_bucket_policy, aws_sns_topic_policy,
    aws_secretsmanager_secret_policy, aws_glue_resource_policy, or aws_lambda_function_url BLOCKS,
    UNLESS it is the one known-safe shape -- an aws_lambda_permission whose principal ==
    "events.amazonaws.com" (exact), action == "lambda:InvokeFunction" (exact), AND function_name
    starts with "agent-platform-", ALL THREE conjunctively (the routine EventBridge-rule-invokes-
    Lambda wiring pattern; see terraform/personal/ducklake_maintenance.tf and prod_lambdas.tf).
    This is an allowlist, not a denylist: any other principal (including "*", an external account,
    or "s3.amazonaws.com" -- e.g. the existing S3-triggered permissions in prod_lambdas.tf), a
    missing/unparseable principal, a wrong action, or a non-agent-platform-prefixed function_name
    all BLOCK. The other five types have no auto-apply shape at all and always BLOCK on a
    non-inert change.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

IAM_SENSITIVE_TYPES = frozenset(
    {
        "aws_iam_role",
        "aws_iam_role_policy",
        "aws_iam_policy",
        "aws_iam_role_policy_attachment",
        "aws_iam_openid_connect_provider",
        "aws_iam_user",
        "aws_iam_group",
    }
)

# Attributes that carry a resource trust policy. Serialised by terraform as a JSON-encoded string.
TRUST_ATTRIBUTES = ("assume_role_policy",)

# Action sets that are inert for an IAM-sensitive resource (no privilege change).
_INERT_ACTIONS = (["no-op"], ["read"])

# Third-party Neon provider resources (T2.16b / CD.34). Type prefix kislerdm/neon exposes.
NEON_PROVIDER_PREFIX = "neon_"

# Action sets a neon_* resource may auto-apply with: a pure create (the only provisioning path) or an
# inert no-op/read. Anything else (notably ["update"]) is blocked; delete/replace are caught earlier
# by the "delete" rule. A bare create is allowed because compensating controls -- not an IP allow-list
# -- carry the posture (see the module docstring's Neon detection-contract bullet).
_NEON_SAFE_ACTIONS = (["create"], ["no-op"], ["read"])

# Default path for the authority budget table (T2.25 / Decision 92 point 5). Override via TF_AUTHORITY_BUDGET.
_BUDGET_DEFAULT_PATH = Path(__file__).parent.parent / "terraform" / "bootstrap" / "authority_budget.json"

# The apply role's own name. Under budget v2 the in_budget_managed_role_prefix (agent-platform-)
# matches this name, so _classify_iam_change self-excludes it: the pipeline must never auto-apply an
# inline-policy write on itself (guard-side counterpart to github_ci_apply.tf's DenySelfInlinePolicyWrite,
# preserving the T2.23 self-grant break / Decision 144).
_APPLY_ROLE_NAME = "agent-platform-github-ci-apply"

# Resource-based-policy types (T2.45 / DEP-06): unlike an identity policy (IAM_SENSITIVE_TYPES),
# these attach a policy directly to a non-IAM resource and can grant an external/wildcard
# principal without ever touching an aws_iam_* type. Classified by a dedicated stage appended
# LAST in evaluate_plan (after delete -> neon -> trust -> IAM). This list is a deliberately
# curated allowlist, not exhaustive of every AWS resource-based-policy type that exists (see
# docs/contracts/environment-taxonomy.yaml guard_classification for the known-incomplete
# note) -- a future addition (e.g. aws_sqs_queue_policy, aws_kms_key_policy) requires a deliberate
# guard extension, not an assumption of coverage.
RESOURCE_POLICY_TYPES = frozenset(
    {
        "aws_lambda_permission",
        "aws_s3_bucket_policy",
        "aws_sns_topic_policy",
        "aws_secretsmanager_secret_policy",
        "aws_glue_resource_policy",
        "aws_lambda_function_url",
    }
)

# The one known-safe aws_lambda_permission shape (routine EventBridge-rule-invokes-Lambda wiring,
# e.g. terraform/personal/ducklake_maintenance.tf, prod_lambdas.tf). ALL THREE predicates must
# hold conjunctively -- this is an allowlist, not a denylist (Decision 77 / Decision 92 point 5):
# any other principal, action, or function-name shape fails closed.
_EVENTBRIDGE_SAFE_PRINCIPAL = "events.amazonaws.com"
_EVENTBRIDGE_SAFE_ACTION = "lambda:InvokeFunction"
_AGENT_PLATFORM_FUNCTION_PREFIX = "agent-platform-"


# ---------------------------------------------------------------------------
# Redaction (Decision 101 public-content boundary): shared by resource-policy finding reasons
# (this repo is PUBLIC -- CI logs printing an unredacted finding would leak) and the reviewer digest.
# ---------------------------------------------------------------------------

# ARN token: "arn:aws:<service>:<region>:<account-or-empty>:<resource>". Matches up to the first
# whitespace/quote/backslash so a redacted ARN never bleeds into adjacent text.
_ARN_PATTERN = re.compile(r"arn:aws:[a-zA-Z0-9_\-]*:[a-zA-Z0-9_\-]*:[0-9]*:[^\s\"'\\]*")
# Bare 12-digit AWS account id, not part of a longer digit run (word-boundary via negative lookaround).
_ACCOUNT_ID_PATTERN = re.compile(r"(?<!\d)\d{12}(?!\d)")


def _redact(text: str) -> str:
    """Redact AWS ARNs and bare 12-digit account ids (Decision 101 public-content boundary).

    Order matters: ARN redaction runs first so an account id embedded inside an ARN is consumed
    as part of the ARN token (one [ARN] marker) rather than leaving a dangling [ACCOUNT_ID] inside
    already-redacted text.
    """
    text = _ARN_PATTERN.sub("[ARN]", text)
    return _ACCOUNT_ID_PATTERN.sub("[ACCOUNT_ID]", text)


def _load_budget() -> Optional[dict]:
    """Load the authority budget table from TF_AUTHORITY_BUDGET or the default path.

    Returns None on any failure (missing file, parse error). A None budget is fail-closed:
    _classify_iam_change treats every IAM change as out-of-budget.
    """
    path_env = os.environ.get("TF_AUTHORITY_BUDGET")
    path = Path(path_env) if path_env else _BUDGET_DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _classify_iam_change(change_entry: dict, budget: Optional[dict]) -> bool:
    """Return True if this IAM change is in-budget (safe to auto-apply without gated-apply).

    In-budget = resource type in in_budget_resource_types, every action in the change is an
    in_budget_action (SUBSET membership, budget v2 -- ["create","update"] admits a lone ["create"]
    or ["update"]; a replace pair such as ["delete","create"] is never a subset and stays gated),
    and the target role is a managed boundary-carrying role. The managed-role test reads the
    budget-v2 in_budget_managed_role_prefix (a boundary-carrying agent-platform-* prefix) and falls
    back to the v1 enumerated in_budget_managed_roles when no prefix is present (fail-safe on a
    rollback to a v1 budget). The apply role itself is ALWAYS self-excluded -- the widened prefix
    matches agent-platform-github-ci-apply, so an inline-policy write on it routes to gated-apply
    (guard-side counterpart to github_ci_apply.tf's DenySelfInlinePolicyWrite). Missing budget or
    missing role attribute returns False (fail-closed). This widens the in-budget predicate to read
    the v2 budget shape; it adds no new classification stage and retains fail-closed control theory.
    """
    if budget is None:
        return False
    rtype = change_entry.get("type", "")
    if rtype not in budget.get("in_budget_resource_types", []):
        return False
    change = change_entry.get("change") or {}
    actions = change.get("actions") or []
    if not actions or not set(actions).issubset(set(budget.get("in_budget_actions", []))):
        return False
    after = change.get("after") or {}
    before = change.get("before") or {}
    role = after.get("role") or before.get("role")
    if not role:
        return False
    # Apply-role self-exclusion: the widened agent-platform-* prefix matches the apply role's own
    # name, so a write on it must gate (guard-side counterpart to DenySelfInlinePolicyWrite).
    if role == _APPLY_ROLE_NAME:
        return False
    prefix = budget.get("in_budget_managed_role_prefix")
    if prefix:
        return role.startswith(prefix)
    return role in budget.get("in_budget_managed_roles", [])


def _normalise_policy(value: Any) -> Any:
    """Return a comparable representation of a policy value.

    terraform serialises assume_role_policy as a JSON-encoded string; parse it so two
    structurally-equal policies that differ only in key order / whitespace compare equal. Falls
    back to the raw value when it is not a parseable JSON string.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _trust_changed(before: Any, after: Any) -> bool:
    """True if any trust attribute differs between the before and after resource states."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    for attr in TRUST_ATTRIBUTES:
        if attr in before or attr in after:
            if _normalise_policy(before.get(attr)) != _normalise_policy(after.get(attr)):
                return True
    return False


def _resource_policy_finding(change_entry: dict) -> Optional[dict]:
    """Return a blocking finding for a resource-based-policy change, or None if safe.

    Caller guarantees change_entry["type"] is in RESOURCE_POLICY_TYPES. Inert (no-op/read)
    changes are always safe. aws_lambda_permission is safe ONLY when the known EventBridge
    allow-shape holds conjunctively (principal, action, AND function_name-prefix all match);
    every other aws_lambda_permission shape blocks. The other five types have no auto-apply
    shape at all, so any non-inert change on them blocks unconditionally.
    """
    change = change_entry.get("change") or {}
    actions = change.get("actions") or []
    if actions in _INERT_ACTIONS:
        return None

    address = change_entry.get("address", "<unknown>")
    rtype = change_entry.get("type", "<unknown>")

    if rtype == "aws_lambda_permission":
        after = change.get("after") or {}
        before = change.get("before") or {}
        principal = after.get("principal", before.get("principal"))
        action = after.get("action", before.get("action"))
        function_name = after.get("function_name", before.get("function_name"))
        if (
            principal == _EVENTBRIDGE_SAFE_PRINCIPAL
            and action == _EVENTBRIDGE_SAFE_ACTION
            and isinstance(function_name, str)
            and function_name.startswith(_AGENT_PLATFORM_FUNCTION_PREFIX)
        ):
            return None
        redacted_principal = _redact(str(principal))
        return {
            "address": address,
            "type": rtype,
            "actions": actions,
            "reason": f"aws_lambda_permission is not the known-safe EventBridge invoke shape (principal={redacted_principal})",
        }

    return {
        "address": address,
        "type": rtype,
        "actions": actions,
        "reason": f"{rtype} is a resource-based policy with no auto-apply shape",
    }


def iter_resource_changes(plan: dict) -> list[dict]:
    """The one resource_changes traversal, shared by the verdict and the reviewer digest."""
    return plan.get("resource_changes") or []


def evaluate_plan(plan: dict, budget: Optional[dict] = None) -> list[dict]:
    """Return a list of blocking findings. An empty list means the plan is safe to auto-apply.

    Each finding is a dict with keys: address, type, actions, reason.

    Pass the loaded authority budget (from _load_budget()) to enable in-budget IAM classification.
    A None budget is fail-closed: all IAM changes are treated as out-of-budget and blocked.

    Evaluation order (T2.25 / T2.45): delete -> neon -> trust-diff -> IAM (in-budget pass /
    out-of-budget block) -> resource-policy (EventBridge safe-shape pass / block). Trust check
    runs before IAM so a trust diff on a managed role is always gated. The resource-policy stage
    runs LAST so a delete or trust diff on one of its six types is still caught by an earlier rule.
    """
    findings: list[dict] = []
    for change_entry in iter_resource_changes(plan):
        change = change_entry.get("change") or {}
        actions = change.get("actions") or []
        address = change_entry.get("address", "<unknown>")
        rtype = change_entry.get("type", "<unknown>")

        if "delete" in actions:
            findings.append({"address": address, "type": rtype, "actions": actions, "reason": "destroy or replacement"})
            continue

        if rtype.startswith(NEON_PROVIDER_PREFIX) and actions not in _NEON_SAFE_ACTIONS:
            findings.append(
                {
                    "address": address,
                    "type": rtype,
                    "actions": actions,
                    "reason": "non-create neon_* change (allow-list / credential / project-setting mutation)",
                }
            )
            continue

        if _trust_changed(change.get("before"), change.get("after")):
            findings.append(
                {"address": address, "type": rtype, "actions": actions, "reason": "trust-policy (assume_role_policy) diff"}
            )
            continue

        if rtype in IAM_SENSITIVE_TYPES and actions not in _INERT_ACTIONS:
            if _classify_iam_change(change_entry, budget):
                continue  # in-budget inline-policy / attachment update on managed boundary-carrying role
            findings.append(
                {"address": address, "type": rtype, "actions": actions, "reason": "IAM-sensitive change (out-of-budget)"}
            )
            continue

        if rtype in RESOURCE_POLICY_TYPES:
            finding = _resource_policy_finding(change_entry)
            if finding is not None:
                findings.append(finding)
            continue

    return findings


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns the process exit code (0 safe, 2 blocked, 1 error)."""
    args = list(sys.argv[1:] if argv is None else argv)

    if len(args) != 1:
        print("usage: terraform_apply_guard.py <plan.json>", file=sys.stderr)
        return 1

    path = args[0]
    try:
        with open(path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"terraform_apply_guard: cannot read or parse {path!r}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(plan, dict):
        print(f"terraform_apply_guard: expected a JSON object at the top level, got {type(plan).__name__}", file=sys.stderr)
        return 1

    budget = _load_budget()
    findings = evaluate_plan(plan, budget)
    if findings:
        print("terraform_apply_guard: BLOCKED -- this plan requires a manual admin apply or gated-apply approval:")
        for finding in findings:
            print(f"  - {finding['address']} ({finding['type']}) actions={finding['actions']}: {finding['reason']}")
        return 2

    print("terraform_apply_guard: OK -- safe to auto-apply (non-IAM or in-budget IAM, no trust diffs).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
