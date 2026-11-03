"""Agent-identity observability read-coverage gate (Lever B, T2.48, rec-2851/2906/3763).

_read_coverage.py's validate_ci_refresh_read_coverage governs a DIFFERENT axis: whether the
plan-capable CI roles (github_ci_apply / github_ci_planner) can REFRESH-read every managed
terraform/personal resource during `terraform plan`. This check governs whether the AGENT
IDENTITIES (PlatformDev "dev", PlatformAdmin "admin") can READ every managed observability
resource at runtime -- the axis PLAN-agent-observability-read-axis closes.

It CONSUMES _read_coverage.py's existing resource-type census (CHECKED_TYPES / ENUMERATED_IAM_TYPES
/ TRANSITIVE_TYPES / NON_AWS_TYPES / NO_GRANT_TYPES) as the single classification table -- a second
independent census would be the two-surfaces-one-subject anti-pattern that census already exists to
prevent (Decision 129 pt4). This module adds only the per-agent-identity OBSERVABILITY_READ_MAP on
top of it: every managed terraform/personal resource type must resolve to EITHER an entry in that
map (an observability type, with its per-instance coverage asserted) OR membership in _read_coverage's
census (a type this check has nothing to say about) -- a type in neither FAILS LOUD.

Deliberately partial (PLAN-agent-observability-read-axis context note): the account-wide
Resource:"*" verbs this plan grants (cloudwatch metric reads, cloudtrail:LookupEvents,
s3:ListAllMyBuckets, cloudwatch:DescribeAlarms/DescribeAlarmHistory) have no managed resource type
to hang a per-instance coverage row on -- VP step 17's live invocation proof carries those. This
map covers only the prefix-scoped residue: aws_s3_bucket (Lever A's s3:ListBucket /
s3:ListBucketVersions on arn:aws:s3:::agent-platform-*) and aws_cloudwatch_log_group (PlatformDev's
logs:DescribeLogStreams/GetLogEvents/FilterLogEvents on /aws/lambda/agent-platform-*).
"""

from __future__ import annotations

import re

from scripts.checks import _common, registry
from scripts.checks.iam_tf._read_coverage import (
    _BOOTSTRAP_DIR_REL,
    _PERSONAL_DIR_REL,
    CHECKED_TYPES,
    ENUMERATED_IAM_TYPES,
    NO_GRANT_TYPES,
    NON_AWS_TYPES,
    TRANSITIVE_TYPES,
    _parse_bootstrap_statements,
    _read_root_text,
    _resolve_resource_name,
    _resolve_value,
    _resource_covered,
    _scan_resources,
)

# Per-agent-identity observability READ-GRANT map (Lever B). "role" keys into the role_statements
# dict this module builds from the two agent-identity inline policies (dev = PlatformDev's
# DailyOps, admin = PlatformAdmin's AdminOps).
OBSERVABILITY_READ_MAP: dict[str, dict] = {
    "aws_s3_bucket": {
        "role": "admin",
        "role_label": "PlatformAdmin",
        "read_actions": ("s3:ListBucket", "s3:ListBucketVersions"),
    },
    "aws_cloudwatch_log_group": {
        "role": "dev",
        "role_label": "PlatformDev",
        "read_actions": ("logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents"),
    },
}

# A log group's `name` attribute is a literal string with ONE embedded interpolation
# (e.g. "/aws/lambda/${local.ducklake_writer_function}") -- a shape _read_coverage's generic
# _resolve_value does not handle (it resolves a WHOLE-value bare `local.X` reference or a
# whole literal string, not one token embedded inside a larger string).
_EMBEDDED_LOCAL_RE = re.compile(r"\$\{local\.(\w+)\}")


def _resolve_log_group_name(raw: str | None, locals_map: dict[str, str]) -> str | None:
    """Resolve an `aws_cloudwatch_log_group.name` raw attribute to its agent-platform-* suffix.

    Returns the bare embedded local's value (e.g. "agent-platform-ducklake-writer"), NOT the
    reassembled "/aws/lambda/..." path -- _literal_or_prefix_match's prefix matching requires the
    resolved name to START WITH the grant's name-prefix tail, exactly as aws_s3_bucket's `bucket`
    attribute already resolves to a bare, unprefixed name.
    """
    if raw is None:
        return None
    unresolved = _resolve_value(raw, locals_map, {})
    if unresolved is None:
        return None
    m = _EMBEDDED_LOCAL_RE.search(unresolved)
    if not m:
        return unresolved
    return locals_map.get(m.group(1), unresolved)


@registry.register("validate_agent_observability_read_coverage", owner="platform")
def validate_agent_observability_read_coverage(failed: list[str]) -> None:
    """Every managed terraform/personal resource type is classified; every observability-managed
    resource instance is read-covered by its agent identity.

    Credential-free (pure text parsing, no boto3/terraform invocation) -- eligible for --pre and
    full tiers. Test isolation: patch `scripts.checks._common.ROOT`, mirroring
    validate_ci_refresh_read_coverage's convention.
    """
    print("\n=== Agent-identity observability read-coverage gate (Lever B, T2.48) ===")
    key = "agent-observability-read-coverage:"

    personal_dir = _common.ROOT / _PERSONAL_DIR_REL
    bootstrap_dir = _common.ROOT / _BOOTSTRAP_DIR_REL
    bootstrap_text = _read_root_text(bootstrap_dir)
    if not bootstrap_text:
        failed.append(f"{key} cannot read any *.tf file under {bootstrap_dir}")
        print(f"  FAIL: cannot read terraform/bootstrap HCL under {bootstrap_dir}")
        return

    # DailyOps and AdminOps are read from terraform/bootstrap (PLAN-platform-iam-bootstrap-adopt,
    # the durable owner) -- _scan_resources below stays on terraform/personal, which still declares
    # the observability-managed resources (aws_s3_bucket, aws_cloudwatch_log_group).
    dev_statements = _parse_bootstrap_statements(bootstrap_text, "platform_dev_runtime")
    admin_statements = _parse_bootstrap_statements(bootstrap_text, "platform_admin_ops")
    if not dev_statements or not admin_statements:
        failed.append(f"{key} could not parse the platform_dev_runtime/platform_admin_ops policy statements")
        print(
            "  FAIL: could not parse PlatformDev/PlatformAdmin inline policy statements in terraform/bootstrap -- "
            "has the HCL shape changed, or are the policies absent from bootstrap?"
        )
        return
    role_statements = {"dev": dev_statements, "admin": admin_statements}

    resources, locals_map, attr_index = _scan_resources(personal_dir)
    if not resources:
        failed.append(f"{key} no terraform resources discovered under {personal_dir}")
        print("  FAIL: no terraform resources discovered -- has the module moved?")
        return

    known_types = set(CHECKED_TYPES) | set(ENUMERATED_IAM_TYPES) | TRANSITIVE_TYPES | NON_AWS_TYPES | NO_GRANT_TYPES

    examined = 0
    for rtype, rname, fname in resources:
        obs_spec = OBSERVABILITY_READ_MAP.get(rtype)
        if obs_spec is None:
            if rtype not in known_types:
                failed.append(
                    f"{key} unmapped resource type {rtype!r} (resource {rname} in {fname}) -- classify it in "
                    "OBSERVABILITY_READ_MAP (scripts/checks/iam_tf/validate_agent_observability_read_coverage.py) "
                    "if an agent identity needs an observability read grant over it, or confirm it is already "
                    "classified in _read_coverage.py's census"
                )
            continue

        if rtype == "aws_cloudwatch_log_group":
            raw_name = attr_index.get((rtype, rname), {}).get("name")
            resolved_name = _resolve_log_group_name(raw_name, locals_map)
        else:
            resolved_name = _resolve_resource_name(rtype, rname, CHECKED_TYPES[rtype], locals_map, attr_index)

        if not resolved_name:
            failed.append(
                f"{key} could not resolve a name for {rtype} {rname!r} in {fname} -- treating as uncovered "
                "until the extraction is fixed"
            )
            continue

        examined += 1
        if not _resource_covered(rtype, rname, resolved_name, obs_spec["read_actions"], role_statements[obs_spec["role"]]):
            failed.append(
                f"{key} {rtype} {rname!r} ({resolved_name!r}) in {fname} is not observability-read-covered by "
                f"{obs_spec['role_label']} (expected one of {obs_spec['read_actions']} on a matching Resource "
                "ARN/prefix)"
            )

    registry.examined(examined, unit="observability_managed_resources")

    if not any(f.startswith(key) for f in failed):
        print(f"  PASS: all {examined} observability-managed resources are read-covered by their agent identity.")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_agent_observability_read_coverage(_failed)
    for _f in _failed:
        print(f"  - {_f}")
    raise SystemExit(1 if _failed else 0)
