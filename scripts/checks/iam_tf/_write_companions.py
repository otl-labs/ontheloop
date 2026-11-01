"""Lifecycle-companion submodule -- design (b), MANDATORY-DECLARATION (PLAN-iam-write-surface-completion).

THE DEFECT CLASS THIS CLOSES: "headline verb granted, structurally-required companion verb missing
or narrower". Three instances shipped to production before this table existed --
  rec-2831  lambda:CreateFunction granted, iam:PassRole on the execution role missing;
  rec-2842  iam:CreateRole at role/agent-platform-*, iam:TagRole only at two enumerated ARNs;
  rec-2882  iam:DeleteRole granted, iam:ListInstanceProfilesForRole missing -- which killed a LIVE
            gated destroy, because the provider's deleteRole() unconditionally calls
            deleteRoleInstanceProfiles() first.
Each was previously patched by adding one hard-coded, CREATE-only checker per production failure.
This module replaces those two checkers with ONE phase-keyed table.

WHY MANDATORY-DECLARATION (and not another opt-in list): LIFECYCLE_COMPANIONS must carry a
create / update / delete entry for EVERY key of _write_coverage.WRITE_COVERAGE. A phase entry MAY
declare an empty companion tuple, but ONLY with a collocated one-line justification; a type with NO
entry for a phase FAILS LOUD. So adding a type to WRITE_COVERAGE (design (a)'s discovery output)
now forces the author to answer "what else does its delete phase call?" at review time. Silence is
no longer expressible. That is discovery-driven at the PHASE axis, which is where this class lives.

Honest limit (recorded so nobody over-claims): a table a human writes still cannot enumerate a verb
nobody knows the provider calls. The only mechanism that closes THAT is empirical -- read-only
CloudTrail for PlatformAdmin, filed as the High class-closing follow-on. This module is that item's
precondition, not its substitute.

Credential-free (pure text parsing) -- eligible for --pre and full tiers. Stays < 500 SLOC.
"""

from __future__ import annotations

from scripts.checks import _common
from scripts.checks.iam_tf._read_coverage import (
    _BOOTSTRAP_DIR_REL,
    _action_matches,
    _parse_boundary_dataplane_statement,
    _read_root_text,
)
from scripts.checks.iam_tf._write_coverage import (
    _ALARM_PREFIX,
    _COUNTERS_TABLE_ARN,
    _DATA_LAKE_BUCKET_ARN,
    _FUNCTION_PREFIX,
    _INSTANCE_PROFILE_PREFIX,
    _LOG_GROUP_PREFIX,
    _OIDC_PROVIDER_ARN,
    _PARAMETER_PREFIX,
    _ROLE_PREFIX,
    _RULE_PREFIX,
    _SECRET_PREFIX,
    _TOPIC_ARN,
    WRITE_COVERAGE,
)

PHASES = ("create", "update", "delete")

_IAM_ACTION_PREFIX = "iam:"
# Whole-file substring check (mirrors the plan's own VP step 2 rigor level -- a simple presence
# check, not a statement-scoped parse): the PassedToService=lambda.amazonaws.com condition text
# must be present somewhere in the bootstrap HCL, or the identity iam:PassRole grant is an
# unconditioned over-grant (any service, any pass). Decision 143 worst-verb scoping.
_PASSROLE_ACTION = "iam:PassRole"
_PASSROLE_CONDITION_MARKERS = ("iam:PassedToService", "lambda.amazonaws.com")

_DEFAULT_TAGS_WHY = "default_tags forces a tag call on every taggable resource's create"
_TAG_DIFF_WHY = "a tags diff is applied as an untag + tag pair at the same scope"
_SINGLE_CALL = "a single API call -- no companion verb"
_UPSERT_WHY = "the same upsert call as create -- no companion verb"
_NO_TAG_CALL = "default_tags forces no companion call"
_OIDC_BY_DESIGN = "DENIED BY DESIGN -- creating or destroying the provider would break the pipeline executing the apply"
_UNGRANTED_DESTROY = (
    "destroy calls this verb, which is deliberately NOT granted ({what} is admin-tier) -- the row is inert until it ever is"
)
_SNS_SUBSCRIPTION_BY_DESIGN = (
    "sns:SetSubscriptionAttributes and sns:Unsubscribe are DENIED BY DESIGN and therefore ungranted -- neither has a "
    'resource type in SNS\'s IAM model (live-simulate proof: a Resource ["*"] grant is allowed only against a `*` '
    "target and implicitDeny against the topic ARN or a subscription ARN), so authorizing them would take an "
    "account-wide wildcard letting one CD apply reconfigure ANY subscription in the account (Decision 143). Accepted "
    "consequence: destroying or REPLACING aws_sns_topic_subscription.alerts_email through CD AccessDenies and needs "
    "an admin apply -- see the by_design entries in docs/contracts/iam-simulate-fixture.yaml"
)


def _at(marker: str, *actions: str) -> tuple[tuple[str, str], ...]:
    """Bind each companion action to the Resource marker its grant must carry."""
    return tuple((action, marker) for action in actions)


def _row(trigger: str | None, companions: tuple[tuple[str, str], ...], why: str) -> dict:
    """One phase declaration: the verb that makes the phase live, what else it calls, and why.

    `trigger=None` declares that the phase issues NO AWS API call at all (a state-only destroy, for
    instance) -- the row is inert by declaration rather than by the absence of a grant.
    """
    return {"trigger": trigger, "companions": companions, "why": why}


def _tag_pair(service: str, suffix: str = "Resource") -> tuple[str, str]:
    """The (untag, tag) verb pair for a service, in the order a tags diff issues them."""
    return (f"{service}:Untag{suffix}", f"{service}:Tag{suffix}")


# ---------------------------------------------------------------------------
# The table. One row per (WRITE_COVERAGE type, phase) -- no exceptions, no silence.
# ---------------------------------------------------------------------------

LIFECYCLE_COMPANIONS: dict[str, dict[str, dict]] = {
    "aws_lambda_function": {
        "create": _row(
            "lambda:CreateFunction",
            _at(_FUNCTION_PREFIX, "lambda:TagResource") + _at(_ROLE_PREFIX, _PASSROLE_ACTION),
            f"{_DEFAULT_TAGS_WHY}; AWS also requires iam:PassRole on the execution role for CreateFunction (rec-2831)",
        ),
        "update": _row("lambda:UpdateFunctionConfiguration", _at(_FUNCTION_PREFIX, *_tag_pair("lambda")), _TAG_DIFF_WHY),
        "delete": _row("lambda:DeleteFunction", (), f"{_SINGLE_CALL}; the URL config and permissions have their own rows"),
    },
    "aws_cloudwatch_log_group": {
        "create": _row(
            "logs:CreateLogGroup",
            _at(_LOG_GROUP_PREFIX, "logs:TagLogGroup", "logs:TagResource", "logs:PutRetentionPolicy"),
            f"{_DEFAULT_TAGS_WHY}; BOTH tagging spellings, because which family provider 5.100 calls could not be "
            "determined offline (P0-4), and retention is set in the same create",
        ),
        "update": _row(
            "logs:PutRetentionPolicy",
            _at(_LOG_GROUP_PREFIX, "logs:UntagLogGroup", "logs:UntagResource"),
            f"{_TAG_DIFF_WHY}; both spellings, same P0-4 uncertainty as the create row",
        ),
        "delete": _row("logs:DeleteLogGroup", (), _SINGLE_CALL),
    },
    "aws_cloudwatch_metric_alarm": {
        "create": _row("cloudwatch:PutMetricAlarm", _at(_ALARM_PREFIX, "cloudwatch:TagResource"), _DEFAULT_TAGS_WHY),
        "update": _row(
            "cloudwatch:PutMetricAlarm", _at(_ALARM_PREFIX, *_tag_pair("cloudwatch")), f"{_UPSERT_WHY}; {_TAG_DIFF_WHY}"
        ),
        "delete": _row("cloudwatch:DeleteAlarms", (), _SINGLE_CALL),
    },
    "aws_cloudwatch_event_rule": {
        "create": _row("events:PutRule", _at(_RULE_PREFIX, "events:TagResource"), _DEFAULT_TAGS_WHY),
        "update": _row(
            "events:PutRule",
            _at(_RULE_PREFIX, *_tag_pair("events"), "events:EnableRule", "events:DisableRule"),
            f"{_UPSERT_WHY}; {_TAG_DIFF_WHY}, and a state flip calls EnableRule / DisableRule rather than PutRule",
        ),
        "delete": _row(
            "events:DeleteRule",
            _at(_RULE_PREFIX, "events:RemoveTargets"),
            "DeleteRule fails while targets remain, so the destroy path removes the rule's targets first",
        ),
    },
    "aws_iam_role": {
        "create": _row(
            "iam:CreateRole",
            _at(_ROLE_PREFIX, "iam:TagRole", "iam:UntagRole", "iam:UpdateRole"),
            f"{_DEFAULT_TAGS_WHY}; rec-2842's actual bug was these companions granted at a NARROWER Resource than "
            "CreateRole's prefix, so the marker is asserted, not just presence",
        ),
        "update": _row(
            "iam:UpdateRole",
            _at(_ROLE_PREFIX, "iam:TagRole", "iam:UntagRole", "iam:UpdateRoleDescription"),
            f"{_TAG_DIFF_WHY}; a description diff routes to UpdateRoleDescription in provider 5.100",
        ),
        "delete": _row(
            "iam:DeleteRole",
            _at(_ROLE_PREFIX, "iam:ListInstanceProfilesForRole")
            + _at(_INSTANCE_PROFILE_PREFIX, "iam:RemoveRoleFromInstanceProfile"),
            "provider deleteRole() unconditionally calls deleteRoleInstanceProfiles() (rec-2882 -- the missing List "
            "verb killed a live gated destroy); the two loop verbs carry DIFFERENT markers because "
            "RemoveRoleFromInstanceProfile authorizes on the instance-profile resource type -- at the role prefix it "
            "is inert, which only the post-apply live simulate could see",
        ),
    },
    "aws_iam_role_policy": {
        "create": _row(
            "iam:PutRolePolicy", (), f"PutRolePolicy is an upsert; inline policies are not taggable, so {_NO_TAG_CALL}"
        ),
        "update": _row("iam:PutRolePolicy", (), _UPSERT_WHY),
        "delete": _row("iam:DeleteRolePolicy", (), f"{_SINGLE_CALL}; the owning role's delete row covers role-level teardown"),
    },
    "aws_iam_openid_connect_provider": {
        "create": _row(
            "iam:CreateOpenIDConnectProvider", (), f"{_OIDC_BY_DESIGN}; the row is inert because the verb is ungranted"
        ),
        "update": _row(
            "iam:UpdateOpenIDConnectProviderThumbprint",
            _at(_OIDC_PROVIDER_ARN, *_tag_pair("iam", "OpenIDConnectProvider"), "iam:AddClientIDToOpenIDConnectProvider"),
            f"{_TAG_DIFF_WHY}; a client-id diff routes to AddClientIDToOpenIDConnectProvider (P1-4)",
        ),
        "delete": _row(
            "iam:DeleteOpenIDConnectProvider", (), f"{_OIDC_BY_DESIGN}; the row is inert because the verb is ungranted"
        ),
    },
    "aws_lambda_function_url": {
        "create": _row("lambda:CreateFunctionUrlConfig", (), f"{_SINGLE_CALL}; the invoke permission is its own resource"),
        "update": _row("lambda:UpdateFunctionUrlConfig", (), _SINGLE_CALL),
        "delete": _row("lambda:DeleteFunctionUrlConfig", (), _SINGLE_CALL),
    },
    "aws_lambda_permission": {
        "create": _row("lambda:AddPermission", (), f"{_SINGLE_CALL}; a resource-policy statement is not taggable"),
        "update": _row(
            "lambda:AddPermission",
            _at(_FUNCTION_PREFIX, "lambda:RemovePermission"),
            "statement ids are immutable, so a permission change is applied as RemovePermission + AddPermission",
        ),
        "delete": _row("lambda:RemovePermission", (), _SINGLE_CALL),
    },
    "aws_lambda_layer_version": {
        "create": _row("lambda:PublishLayerVersion", (), f"layer versions carry no tags in provider 5.100, so {_NO_TAG_CALL}"),
        "update": _row(
            "lambda:PublishLayerVersion",
            (),
            "a layer version cannot be updated in place -- an update publishes a new version and deletes the old, "
            "both already covered by the create and delete rows",
        ),
        "delete": _row("lambda:DeleteLayerVersion", (), _SINGLE_CALL),
    },
    "aws_cloudwatch_event_target": {
        "create": _row(
            "events:PutTargets",
            (),
            f"{_SINGLE_CALL}; a target carrying a role_arn would also need iam:PassRole, already granted at "
            "role/agent-platform-* for lambda:CreateFunction",
        ),
        "update": _row("events:PutTargets", (), _UPSERT_WHY),
        "delete": _row("events:RemoveTargets", (), _SINGLE_CALL),
    },
    "aws_sns_topic": {
        "create": _row(
            "sns:CreateTopic",
            _at(_TOPIC_ARN, "sns:TagResource", "sns:SetTopicAttributes"),
            f"{_DEFAULT_TAGS_WHY}; non-default topic attributes are applied with SetTopicAttributes right after create",
        ),
        "update": _row("sns:SetTopicAttributes", _at(_TOPIC_ARN, *_tag_pair("sns")), _TAG_DIFF_WHY),
        "delete": _row("sns:DeleteTopic", (), _SINGLE_CALL),
    },
    "aws_sns_topic_subscription": {
        "create": _row(
            "sns:Subscribe",
            (),
            f"sns:Subscribe is topic-scoped and creates the subscription on its own; {_SNS_SUBSCRIPTION_BY_DESIGN}, so "
            "the raw-message-delivery / filter-policy follow-up call is NOT declared as a companion -- a non-default "
            "attribute on create AccessDenies and needs an admin apply",
        ),
        "update": _row("sns:SetSubscriptionAttributes", (), f"{_SINGLE_CALL}; {_SNS_SUBSCRIPTION_BY_DESIGN}"),
        "delete": _row("sns:Unsubscribe", (), f"{_SINGLE_CALL}; {_SNS_SUBSCRIPTION_BY_DESIGN}"),
    },
    "aws_ssm_parameter": {
        "create": _row(
            "ssm:PutParameter",
            _at(_PARAMETER_PREFIX, "ssm:AddTagsToResource"),
            f"{_DEFAULT_TAGS_WHY} -- and SSM spells the pair AddTagsToResource / RemoveTagsFromResource, not "
            "Tag*/Untag* (the ALIAS_TAG_PAIRS case in _write_symmetry)",
        ),
        "update": _row(
            "ssm:PutParameter",
            _at(_PARAMETER_PREFIX, "ssm:RemoveTagsFromResource", "ssm:AddTagsToResource"),
            f"PutParameter with Overwrite is the update call; {_TAG_DIFF_WHY}",
        ),
        "delete": _row("ssm:DeleteParameter", (), "the SINGULAR DeleteParameter is what provider 5.100 calls"),
    },
    "aws_secretsmanager_secret": {
        "create": _row("secretsmanager:CreateSecret", _at(_SECRET_PREFIX, "secretsmanager:TagResource"), _DEFAULT_TAGS_WHY),
        "update": _row(
            "secretsmanager:UpdateSecret",
            _at(_SECRET_PREFIX, *_tag_pair("secretsmanager")),
            f"{_TAG_DIFF_WHY}; UpdateSecret itself is deliberately ENUMERATED at two ARNs (a worst verb under "
            "Decision 143 -- value-capable, no metadata-only variant), so only the metadata companions sit at the prefix",
        ),
        "delete": _row("secretsmanager:DeleteSecret", (), f"{_SINGLE_CALL}, with a 7-30 day recovery window"),
    },
    "aws_secretsmanager_secret_version": {
        "create": _row("secretsmanager:PutSecretValue", (), f"{_SINGLE_CALL}; a version carries no tags of its own"),
        "update": _row("secretsmanager:PutSecretValue", (), "a value change publishes a new version via the same call"),
        "delete": _row(
            None,
            (),
            "destroying an aws_secretsmanager_secret_version issues NO AWS call in provider 5.100 -- the version is "
            "retained until the owning secret is deleted, which the secret's own row covers",
        ),
    },
    "aws_dynamodb_table": {
        "create": _row("dynamodb:CreateTable", _at(_COUNTERS_TABLE_ARN, "dynamodb:TagResource"), _DEFAULT_TAGS_WHY),
        "update": _row("dynamodb:UpdateTable", _at(_COUNTERS_TABLE_ARN, *_tag_pair("dynamodb")), _TAG_DIFF_WHY),
        "delete": _row("dynamodb:DeleteTable", (), _SINGLE_CALL),
    },
    "aws_s3_bucket": {
        "create": _row("s3:CreateBucket", _at(_DATA_LAKE_BUCKET_ARN, "s3:PutBucketTagging"), _DEFAULT_TAGS_WHY),
        "update": _row(
            "s3:PutBucketTagging",
            (),
            "the only in-place bucket update this module performs is the tag set itself -- every other bucket "
            "setting is its own aws_s3_bucket_* resource with its own rows",
        ),
        "delete": _row(
            None,
            (),
            "DENIED BY DESIGN -- s3:DeleteBucket is granted nowhere: the data lake holds tfstate, the tfplan "
            "artifacts AND the convergence record, so a CD-executable bucket delete could destroy the platform's "
            "own integrity anchor; bucket destruction stays admin-tier",
        ),
    },
    "aws_s3_bucket_versioning": {
        "create": _row("s3:PutBucketVersioning", (), _SINGLE_CALL),
        "update": _row("s3:PutBucketVersioning", (), _UPSERT_WHY),
        "delete": _row(None, (), "bucket versioning cannot be un-set -- the destroy is state-only and issues no AWS call"),
    },
    "aws_s3_bucket_server_side_encryption_configuration": {
        "create": _row("s3:PutEncryptionConfiguration", (), _SINGLE_CALL),
        "update": _row("s3:PutEncryptionConfiguration", (), _UPSERT_WHY),
        "delete": _row("s3:DeleteEncryptionConfiguration", (), _UNGRANTED_DESTROY.format(what="removing bucket encryption")),
    },
    "aws_s3_bucket_public_access_block": {
        "create": _row("s3:PutBucketPublicAccessBlock", (), _SINGLE_CALL),
        "update": _row("s3:PutBucketPublicAccessBlock", (), _UPSERT_WHY),
        "delete": _row("s3:DeletePublicAccessBlock", (), _UNGRANTED_DESTROY.format(what="removing the public-access block")),
    },
    "aws_s3_bucket_policy": {
        "create": _row("s3:PutBucketPolicy", (), f"{_SINGLE_CALL}; a bucket policy is not taggable"),
        "update": _row("s3:PutBucketPolicy", (), _UPSERT_WHY),
        "delete": _row("s3:DeleteBucketPolicy", (), _SINGLE_CALL),
    },
    "aws_s3_bucket_notification": {
        "create": _row("s3:PutBucketNotification", (), _SINGLE_CALL),
        "update": _row("s3:PutBucketNotification", (), _UPSERT_WHY),
        "delete": _row("s3:PutBucketNotification", (), "the destroy is a PutBucketNotification with an empty configuration"),
    },
    "aws_s3_bucket_lifecycle_configuration": {
        "create": _row("s3:PutLifecycleConfiguration", (), _SINGLE_CALL),
        "update": _row("s3:PutLifecycleConfiguration", (), _UPSERT_WHY),
        "delete": _row("s3:DeleteLifecycleConfiguration", (), _UNGRANTED_DESTROY.format(what="dropping the expiry rules")),
    },
}


def _allow_statements(apply_statements: list[dict]) -> list[dict]:
    """Effect-aware filter: a statement with no parsed "effect" (synthetic fixtures) counts as Allow."""
    return [s for s in apply_statements if s.get("effect") in (None, "Allow")]


def _action_granted(action: str, statements: list[dict]) -> bool:
    """True if some statement grants `action` (or a covering pattern) anywhere."""
    return any(_action_matches((action,), stmt["actions"]) for stmt in statements)


def _action_granted_on_marker(action: str, marker: str, statements: list[dict]) -> bool:
    """True if some statement grants `action` on a Resource list containing `marker`."""
    return any(_action_matches((action,), stmt["actions"]) and marker in stmt["resources_raw"] for stmt in statements)


def _load_boundary_actions(failed: list[str], key: str) -> tuple[str, ...] | None:
    """Parse the github_ci_apply_boundary DataPlaneAllow ceiling. Appends + returns None on failure.

    The orchestrator facade passes only the identity/reads apply_statements, never the boundary
    document, so every boundary assertion re-reads the whole terraform/bootstrap root. That re-read
    goes through _read_coverage._parse_boundary_dataplane_statement, which is built on the shared
    policy locator and therefore follows the rec-2793 hoisted-local indirection AND the split across
    sibling .tf files in the same root.
    """
    bootstrap_dir = _common.ROOT / _BOOTSTRAP_DIR_REL
    bootstrap_text = _read_root_text(bootstrap_dir)
    if not bootstrap_text:
        failed.append(f"{key} cannot re-read any *.tf file under {bootstrap_dir} for the boundary-ceiling check")
        return None
    boundary_stmt = _parse_boundary_dataplane_statement(bootstrap_text)
    if boundary_stmt is None:
        failed.append(
            f"{key} could not locate the github_ci_apply_boundary DataPlaneAllow statement under "
            f"{bootstrap_dir} -- has the boundary HCL shape changed?"
        )
        return None
    return tuple(boundary_stmt["actions"])


def _check_declarations(failed: list[str], key: str) -> None:
    """MANDATORY-DECLARATION: every WRITE_COVERAGE type answers all three phases, or fails loud."""
    for rtype in WRITE_COVERAGE:
        rows = LIFECYCLE_COMPANIONS.get(rtype)
        if rows is None:
            failed.append(
                f"{key} write-managed type {rtype!r} has NO lifecycle-companion declaration -- add "
                f"create/update/delete rows to LIFECYCLE_COMPANIONS in "
                f"scripts/checks/iam_tf/_write_companions.py (an empty tuple is legal WITH a justification)"
            )
            continue
        for phase in PHASES:
            row = rows.get(phase)
            if row is None:
                failed.append(
                    f"{key} write-managed type {rtype!r} declares no {phase!r} lifecycle-companion row -- "
                    "every WRITE_COVERAGE type must answer create/update/delete; silence is not expressible"
                )
                continue
            if not row["companions"] and not (row["why"] or "").strip():
                failed.append(
                    f"{key} the {phase!r} lifecycle-companion row for {rtype!r} declares an EMPTY companion "
                    "tuple with no justification -- an empty answer is legal only with a collocated one-line "
                    "why string saying what the phase calls instead"
                )


def check_lifecycle_companions(apply_statements: list[dict], failed: list[str], key: str) -> None:
    """Design (b): assert every LIVE phase's declared companion verbs are granted at their marker.

    Three obligations, in order:
      1. MANDATORY DECLARATION (statement-independent): every WRITE_COVERAGE type declares a
         create / update / delete row; an empty companion tuple requires a justification string.
      2. INERT ROWS ARE SKIPPED: a row whose trigger verb is granted nowhere (or is declared None)
         imposes nothing -- the pipeline cannot reach that phase, so its companions are not required.
      3. LIVE ROWS ARE ASSERTED: for a row whose trigger IS granted, every declared companion must be
         granted on a Resource containing the companion's own marker (a narrower Resource is the exact
         rec-2842 bug), and every `iam:` companion must ALSO be present in the boundary DataPlaneAllow
         ceiling -- a grant absent from the ceiling is silently denied by the identity/boundary
         intersection (the rec-2831 class).
    """
    _check_declarations(failed, key)

    statements = _allow_statements(apply_statements)
    live: list[tuple[str, str, dict]] = []
    for rtype, rows in LIFECYCLE_COMPANIONS.items():
        for phase in PHASES:
            row = rows.get(phase)
            if row is None or not row["companions"]:
                continue
            trigger = row["trigger"]
            if trigger is None or not _action_granted(trigger, statements):
                continue  # inert: the pipeline cannot reach this phase, so its companions are not required
            live.append((rtype, phase, row))

    boundary_actions: tuple[str, ...] | None = None
    if any(action.startswith(_IAM_ACTION_PREFIX) for _, _, row in live for action, _ in row["companions"]):
        boundary_actions = _load_boundary_actions(failed, key)

    for rtype, phase, row in live:
        for action, marker in row["companions"]:
            if not _action_granted_on_marker(action, marker, statements):
                failed.append(
                    f"{key} {rtype} {phase} grants {row['trigger']!r} but its declared companion {action!r} "
                    f"is missing, or granted only on a Resource narrower than {marker!r} -- {row['why']}"
                )
            if action.startswith(_IAM_ACTION_PREFIX) and boundary_actions is not None:
                if not _action_matches(boundary_actions, [action]):
                    failed.append(
                        f"{key} {rtype} {phase} declares the companion {action!r}, but the "
                        "github_ci_apply_boundary DataPlaneAllow ceiling does not grant it (or a covering "
                        "pattern) -- a grant absent from the ceiling is silently denied by the "
                        "identity/boundary intersection"
                    )

    if any(action == _PASSROLE_ACTION for _, _, row in live for action, _ in row["companions"]):
        _check_passrole_condition(failed, key)


def _check_passrole_condition(failed: list[str], key: str) -> None:
    """Decision 143 worst-verb scoping: an unconditioned iam:PassRole over-grants (any service, any pass)."""
    bootstrap_dir = _common.ROOT / _BOOTSTRAP_DIR_REL
    bootstrap_text = _read_root_text(bootstrap_dir)
    if not bootstrap_text:
        failed.append(f"{key} cannot re-read any *.tf file under {bootstrap_dir} for the PassRole-condition check")
        return
    if not all(marker in bootstrap_text for marker in _PASSROLE_CONDITION_MARKERS):
        failed.append(
            f"{key} github_ci_apply's iam:PassRole grant is missing the iam:PassedToService="
            "lambda.amazonaws.com condition (Decision 143 worst-verb scoping) -- an unconditioned "
            "PassRole over-grants (any service, any pass)"
        )


def _identity_allow_iam_actions(apply_statements: list[dict]) -> set[str]:
    """Every distinct iam: action GRANTED (Effect=Allow) somewhere in the identity/reads surface.

    Effect-aware: a statement with no parsed "effect" (older callers / synthetic fixtures that never
    set the key) is treated as Allow -- none of those fixtures is a Deny statement.
    """
    return {
        action
        for stmt in _allow_statements(apply_statements)
        for action in stmt["actions"]
        if action.startswith(_IAM_ACTION_PREFIX)
    }


def _identity_allow_actions(apply_statements: list[dict]) -> set[str]:
    """Every distinct action GRANTED (Effect=Allow) anywhere in the identity/reads surface, any
    service -- the general per-action collector check_identity_iam_actions_subset_of_boundary uses.

    Delegates to _identity_allow_iam_actions for the iam: subset rather than re-deriving it, so
    that helper keeps a production caller instead of becoming an orphan retained for tests alone;
    its iam-only contract (pinned by test_collects_allow_iam_actions_only) is unchanged by this
    generalization -- this collector only unions in the non-iam: actions on top.
    """
    return _identity_allow_iam_actions(apply_statements) | {
        action
        for stmt in _allow_statements(apply_statements)
        for action in stmt["actions"]
        if not action.startswith(_IAM_ACTION_PREFIX)
    }


def check_identity_iam_actions_subset_of_boundary(apply_statements: list[dict], failed: list[str], key: str) -> None:
    """Defense-in-depth (same ceiling-assertion family as the companion check above, hence collocated).

    Every identity-policy Allow action -- not just iam: -- must also be granted by the boundary
    DataPlaneAllow ceiling, so a future one-layer restore (an identity grant added without its
    ceiling counterpart, or the reverse) fails loud instead of silently denying at the
    identity/boundary intersection (PLAN-glue-delete-database-grant generalization).

    The iam-only premise of the original, narrower check still HOLDS today: iam is the sole
    ACTION-ENUMERATED service in DataPlaneAllow, and s3:*/lambda:*/glue:*/etc. are already
    service-wide wildcards there, so this generalization is per-action DEFENSE against a FUTURE
    one-layer restore, not a repair of a premise the 7b67e21d cleanse broke -- that sweep removed
    BOTH layers together in one sweep, leaving no single-layer gap for either the old or the new
    check to have caught. "The boundary covers action a" is implemented with the boundary's action
    list as the PATTERNS, so a service-wide ceiling wildcard (iam:*, glue:*, ...) is honored -- not
    literal string-equality membership. Effect-aware: a Deny-only identity action is never
    flagged -- it grants nothing, so it has no boundary-ceiling obligation.
    """
    identity_actions = _identity_allow_actions(apply_statements)
    if not identity_actions:
        return

    boundary_actions = _load_boundary_actions(failed, key)
    if boundary_actions is None:
        return

    for action in sorted(identity_actions):
        if not _action_matches(boundary_actions, [action]):
            failed.append(
                f"{key} identity policy grants {action!r} (Effect=Allow) but the "
                "github_ci_apply_boundary DataPlaneAllow ceiling does not grant it (or a covering "
                "pattern) -- a grant absent from the ceiling is silently denied by the "
                "identity/boundary intersection"
            )
