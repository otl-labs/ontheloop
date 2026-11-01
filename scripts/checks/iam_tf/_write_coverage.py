"""Write-coverage submodule -- DISCOVERY-DRIVEN (design (a), PLAN-iam-write-surface-completion).

The enumerated-model recurrence (rec-2703 / rec-2757) was a resource whose refresh-READ was covered
but whose WRITE verb was missing from github_ci_apply's grant surface, so a real apply AccessDenied.
The read-coverage gate (_read_coverage) closes the read half; this submodule closes the write half.

WHY THIS MODULE WAS REWRITTEN (the ROOT CAUSE this plan exists to fix): the previous version
declared `APPLY_WRITTEN_TYPES = frozenset(WRITE_COVERAGE)` and then branched on
`rtype in APPLY_WRITTEN_TYPES and rtype not in WRITE_COVERAGE` -- UNSATISFIABLE by construction, so
the "an apply-role-written type with no write-coverage entry FAILS LOUD" claim in its docstring was
false. Its only test coverage reached that branch by monkeypatching the frozenset, which is proof
the production path was unreachable. Meanwhile WRITE_COVERAGE hand-listed 5 types against the 31
distinct resource types actually present in terraform/personal.

The write side is now discovery-driven, mirroring the read side:
  1. Every DISCOVERED terraform/personal resource type must be either WRITE_COVERAGE-mapped or
     WRITE_EXEMPT_TYPES-exempt (each exemption carrying its own one-line justification). A type that
     is neither FAILS LOUD -- this is the branch that used to be dead code.
  2. Every discovered WRITE_COVERAGE-mapped type's required write verbs must be granted on a
     Resource containing its marker. A removed / narrowed write grant fails the PR (DEP-01
     write-surface gap). Gating direction 2 on discovery is what makes the whole check
     discovery-driven: a type that has left terraform/personal stops being asserted, exactly as the
     read side behaves, and re-arms the moment a resource of that type reappears.

HONEST SCOPE LIMIT (recorded so nobody over-claims): design (a) alone would have caught NONE of the
three historical instances of the "headline verb granted, companion verb missing" class (rec-2831,
rec-2842, rec-2882) -- aws_iam_role was already WRITE_COVERAGE-mapped in all three. (a) closes the
"type never mapped at all" hole. The phase-keyed mandatory-declaration table in _write_companions
closes the "type mapped but its companion verbs were never enumerated" hole that actually recurred,
and _write_symmetry adds the two mechanical scope rules. Neither can enumerate a verb nobody knows
the provider calls -- only CloudTrail can, which is the filed class-closing follow-on.

Credential-free (pure text parsing) -- eligible for --pre and full tiers. Stays < 500 SLOC.
"""

from __future__ import annotations

# _parse_boundary_dataplane_statement / _parse_managed_policy_statements MOVED to _read_coverage so
# they can be expressed on the shared, hoist-resolving policy locator (see their docstrings there).
# Imported -- and thus still importable FROM this module -- so existing importers keep working.
from scripts.checks.iam_tf._read_coverage import (
    _action_matches,
    _parse_boundary_dataplane_statement,  # noqa: F401 -- re-exported for existing importers
    _parse_managed_policy_statements,  # noqa: F401 -- re-exported for existing importers
)

# ---------------------------------------------------------------------------
# Resource markers. A marker is a substring the granting statement's Resource list must contain --
# the broadened agent-platform-* / ducklake-* prefix family from the DEP-01 write-surface inversion.
# ---------------------------------------------------------------------------

_FUNCTION_PREFIX = "function:agent-platform-*"
_LAYER_PREFIX = "layer:agent-platform-*"
_LOG_GROUP_PREFIX = "log-group:/aws/lambda/agent-platform-*"
_ROLE_PREFIX = "role/agent-platform-*"
# iam:RemoveRoleFromInstanceProfile authorizes on the INSTANCE-PROFILE resource type, never on the
# role, so its grant carries this marker and NOT _ROLE_PREFIX. A role-scoped grant of that verb is
# INERT -- present in the policy, structurally unable to authorize -- which every static check
# passed for, because "the verb is present" was literally true; only the post-apply live simulate
# saw it. See the IAMInstanceProfileDetach Sid in terraform/bootstrap/github_ci_apply.tf.
_INSTANCE_PROFILE_PREFIX = "instance-profile/agent-platform-*"
_RULE_PREFIX = "rule/agent-platform-*"
_ALARM_PREFIX = "alarm:"
_TOPIC_ARN = "agent-platform-alerts"
_PARAMETER_PREFIX = "parameter/agent-platform/*"
_SECRET_PREFIX = "secret:agent-platform-*"  # pragma: allowlist secret -- ARN shape, not a value
_DSN_SECRET_ARN = "secret:ducklake-neon-catalog-dsn-*"  # pragma: allowlist secret -- ARN shape, not a value
_OIDC_PROVIDER_ARN = "oidc-provider/token.actions.githubusercontent.com"
_COUNTERS_TABLE_ARN = "table/agent-platform-counters"
_DATA_LAKE_BUCKET_ARN = "arn:aws:s3:::agent-platform-data-lake"

# ---------------------------------------------------------------------------
# WRITE-coverage map: managed resource type -> the write actions github_ci_apply's grant surface
# (inline identity policy UNION the attached customer-managed reads policy) MUST grant + the marker
# its granting statement's Resource list must contain. A required action is covered when SOME apply
# statement grants it on a Resource containing the marker.
#
# Scope note: an entry lists the verbs that MUST be granted for the pipeline to manage the type at
# all; it is not an exhaustive list of every verb the provider may call. The per-phase companion
# obligations (create / update / delete) live in _write_companions.LIFECYCLE_COMPANIONS, which is
# MANDATORY-DECLARATION over exactly the key set of this table.
# ---------------------------------------------------------------------------

WRITE_COVERAGE: dict[str, dict] = {
    "aws_lambda_function": {
        "write_actions": ("lambda:CreateFunction", "lambda:UpdateFunctionConfiguration"),
        "resource_marker": _FUNCTION_PREFIX,
    },
    "aws_cloudwatch_log_group": {
        "write_actions": ("logs:CreateLogGroup", "logs:PutRetentionPolicy"),
        "resource_marker": _LOG_GROUP_PREFIX,
    },
    "aws_cloudwatch_metric_alarm": {
        "write_actions": ("cloudwatch:PutMetricAlarm",),
        "resource_marker": _ALARM_PREFIX,
    },
    "aws_cloudwatch_event_rule": {
        "write_actions": ("events:PutRule",),
        "resource_marker": _RULE_PREFIX,
    },
    "aws_iam_role": {
        # Role CREATE routes to gated-apply (guard), but the apply role still needs the VERB to execute
        # the approved create; IAMRoleCreateBounded grants iam:CreateRole on role/agent-platform-* under
        # the boundary-propagation condition (DEP-02 / Decision 144).
        "write_actions": ("iam:CreateRole",),
        "resource_marker": _ROLE_PREFIX,
    },
    "aws_iam_role_policy": {
        "write_actions": ("iam:PutRolePolicy", "iam:DeleteRolePolicy"),
        "resource_marker": _ROLE_PREFIX,
    },
    "aws_iam_openid_connect_provider": {
        # Provider CREATE/DELETE are denied BY DESIGN (destroying or replacing the provider breaks the
        # pipeline executing the apply) -- see the by_design record in docs/contracts/iam-simulate-fixture.yaml.
        # Only the in-place reconcile verbs are required.
        "write_actions": (
            "iam:UpdateOpenIDConnectProviderThumbprint",
            "iam:TagOpenIDConnectProvider",
            "iam:UntagOpenIDConnectProvider",
        ),
        "resource_marker": _OIDC_PROVIDER_ARN,
    },
    "aws_lambda_function_url": {
        "write_actions": (
            "lambda:CreateFunctionUrlConfig",
            "lambda:UpdateFunctionUrlConfig",
            "lambda:DeleteFunctionUrlConfig",
        ),
        "resource_marker": _FUNCTION_PREFIX,
    },
    "aws_lambda_permission": {
        "write_actions": ("lambda:AddPermission", "lambda:RemovePermission"),
        "resource_marker": _FUNCTION_PREFIX,
    },
    "aws_lambda_layer_version": {
        "write_actions": ("lambda:PublishLayerVersion", "lambda:DeleteLayerVersion"),
        "resource_marker": _LAYER_PREFIX,
    },
    "aws_cloudwatch_event_target": {
        "write_actions": ("events:PutTargets", "events:RemoveTargets"),
        "resource_marker": _RULE_PREFIX,
    },
    "aws_sns_topic": {
        "write_actions": ("sns:CreateTopic", "sns:DeleteTopic", "sns:SetTopicAttributes"),
        "resource_marker": _TOPIC_ARN,
    },
    "aws_sns_topic_subscription": {
        # sns:Subscribe ONLY. sns:Unsubscribe and sns:SetSubscriptionAttributes have NO resource type
        # in SNS's IAM model (live-simulate proof: a Resource ["*"] grant is `allowed` only against a
        # `*` target and implicitDeny against the topic ARN or a subscription ARN), so they are
        # unscopeable and are DENIED BY DESIGN rather than granted account-wide -- see the
        # SNSTopicWrite comment and the by_design entries in docs/contracts/iam-simulate-fixture.yaml.
        # Accepted consequence: destroying or replacing the subscription needs an admin apply.
        "write_actions": ("sns:Subscribe",),
        "resource_marker": _TOPIC_ARN,
    },
    "aws_ssm_parameter": {
        "write_actions": ("ssm:PutParameter", "ssm:DeleteParameter"),
        "resource_marker": _PARAMETER_PREFIX,
    },
    "aws_secretsmanager_secret": {
        # SecretsManagerMetadataWrite -- none of these verbs can read or overwrite a VALUE
        # (Decision 129 pt 2). secretsmanager:UpdateSecret is granted separately, ENUMERATED at two
        # ARNs, and PutSecretValue nowhere new.
        "write_actions": ("secretsmanager:CreateSecret", "secretsmanager:DeleteSecret"),
        "resource_marker": _SECRET_PREFIX,
    },
    "aws_secretsmanager_secret_version": {
        # The single terraform/personal secret VERSION is the DuckLake Neon DSN, whose value IS
        # terraform-managed -- so PutSecretValue is required, and only on that one enumerated ARN.
        "write_actions": ("secretsmanager:PutSecretValue",),
        "resource_marker": _DSN_SECRET_ARN,
    },
    "aws_dynamodb_table": {
        "write_actions": ("dynamodb:CreateTable", "dynamodb:UpdateTable", "dynamodb:DeleteTable"),
        "resource_marker": _COUNTERS_TABLE_ARN,
    },
    "aws_s3_bucket": {
        # s3:DeleteBucket is denied BY DESIGN: the data lake holds tfstate, the tfplan artifacts AND
        # the convergence record, so a CD-executable bucket delete would let one approved apply
        # destroy the platform's own integrity anchor. Bucket destruction stays admin-tier.
        "write_actions": ("s3:CreateBucket",),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
    "aws_s3_bucket_versioning": {
        "write_actions": ("s3:PutBucketVersioning",),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
    "aws_s3_bucket_server_side_encryption_configuration": {
        "write_actions": ("s3:PutEncryptionConfiguration",),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
    "aws_s3_bucket_public_access_block": {
        "write_actions": ("s3:PutBucketPublicAccessBlock",),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
    "aws_s3_bucket_policy": {
        "write_actions": ("s3:PutBucketPolicy", "s3:DeleteBucketPolicy"),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
    "aws_s3_bucket_notification": {
        "write_actions": ("s3:PutBucketNotification",),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
    "aws_s3_bucket_lifecycle_configuration": {
        "write_actions": ("s3:PutLifecycleConfiguration",),
        "resource_marker": _DATA_LAKE_BUCKET_ARN,
    },
}

# Discovered types that require NO write grant, each with its one-line justification. An exemption
# is a deliberate, reviewable statement that the type is not AWS-IAM-write-gated -- never a way to
# silence a real gap (that is what a WRITE_COVERAGE entry is for).
WRITE_EXEMPT_TYPES: dict[str, str] = {
    "neon_project": "third-party (Neon) provider resource -- authenticated by an API key, not AWS IAM",
    "neon_role": "third-party (Neon) provider resource -- authenticated by an API key, not AWS IAM",
    "neon_database": "third-party (Neon) provider resource -- authenticated by an API key, not AWS IAM",
    "null_resource": "local-exec only -- provisions nothing in AWS, so there is no write API call to grant",
}

# `data "..." "..."` blocks are read-only by construction: a data source is refreshed, never
# written, so it has a READ-coverage obligation (_read_coverage.CHECKED_TYPES) and no write one.
_DATA_SOURCE_PREFIX = "data:"
_DATA_SOURCE_JUSTIFICATION = "terraform data source -- read-only by construction; its obligation is read coverage, not write"


def _write_exemption(rtype: str) -> str | None:
    """Return the justification exempting `rtype` from write coverage, or None if it is not exempt."""
    if rtype.startswith(_DATA_SOURCE_PREFIX):
        return _DATA_SOURCE_JUSTIFICATION
    return WRITE_EXEMPT_TYPES.get(rtype)


def _write_grant_present(apply_statements: list[dict], spec: dict) -> bool:
    """True if each required write action is granted by some apply statement on a matching Resource."""
    marker = spec["resource_marker"]
    for action in spec["write_actions"]:
        covered = False
        for stmt in apply_statements:
            if _action_matches((action,), stmt["actions"]) and marker in stmt["resources_raw"]:
                covered = True
                break
        if not covered:
            return False
    return True


def check_write_coverage(
    apply_statements: list[dict], resources: list[tuple[str, str, str]], failed: list[str], key: str
) -> int:
    """Assert the apply role write-covers every DISCOVERED write-managed type (design (a), c5).

    Two loud-fail directions, both driven by the terraform/personal resource sweep (never by a
    hand-maintained "types we think we write" set -- that set was the dead branch this replaces):

      1. A discovered resource type that is neither WRITE_COVERAGE-mapped nor WRITE_EXEMPT_TYPES-
         exempt fails loud. A new write-managed resource class must declare its write grant (or its
         exemption, with a justification) at review time instead of AccessDenying at a real apply.
      2. A discovered WRITE_COVERAGE-mapped type whose required write verbs are not granted on a
         Resource containing its marker fails loud (DEP-01 write-surface gap, rec-2703/rec-2757).

    The per-phase companion obligations (_write_companions) and the mechanical scope rules
    (_write_symmetry) are orchestrated by the facade alongside this function, not from inside it.

    Returns the count of write-managed types asserted (for the PASS summary). Appends to `failed`.
    """
    discovered = {rtype for rtype, _, _ in resources}

    for rtype in sorted(discovered):
        if rtype in WRITE_COVERAGE or _write_exemption(rtype) is not None:
            continue
        example = next(f"{rname} in {fname}" for rt, rname, fname in resources if rt == rtype)
        failed.append(
            f"{key} discovered terraform/personal resource type {rtype!r} (resource {example}) is "
            "neither WRITE_COVERAGE-mapped nor WRITE_EXEMPT_TYPES-exempt -- add a write-coverage "
            "entry (or a justified exemption) to scripts/checks/iam_tf/_write_coverage.py"
        )

    asserted = sorted(discovered & set(WRITE_COVERAGE))
    for rtype in asserted:
        spec = WRITE_COVERAGE[rtype]
        if not _write_grant_present(apply_statements, spec):
            failed.append(
                f"{key} apply-role write-managed type {rtype!r} has no covering write grant under "
                f"terraform/bootstrap/ (expected {spec['write_actions']} on a Resource matching "
                f"{spec['resource_marker']!r}) -- DEP-01 write-surface gap (rec-2703/rec-2757)"
            )

    return len(asserted)
