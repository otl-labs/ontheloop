"""Read-coverage submodule (Decision 128 decomposition of validate_ci_refresh_read_coverage).

Holds the READ classification maps (CHECKED_TYPES / ENUMERATED_IAM_TYPES / TRANSITIVE_TYPES /
NON_AWS_TYPES / NO_GRANT_TYPES) and the shared terraform-HCL parsing / scanning / matching
primitives used by BOTH the read-coverage and the new write-coverage checks. The registered check
validate_ci_refresh_read_coverage lives in the facade module of the same name and orchestrates this
submodule plus _write_coverage. Extracted byte-equivalent from the pre-decomposition module (no
behaviour change); the facade keeps the registry entry + check name unchanged (Decision 128 / T2.48
c5). Every file in this package stays < 500 SLOC (decompose, not raise).
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir
from scripts.checks.iam_tf._secure_string_coverage import secure_string_findings, type_attr

# ---------------------------------------------------------------------------
# Coverage map (rec-2702 anti-recurrence, PLAN-ci-apply-grant-coupling).
#
# Every managed terraform/personal resource TYPE must be classified into exactly one of:
#   (i)   CHECKED_TYPES     -- needs an independent refresh-read grant. "read_actions" names the
#         marker action(s) that grant covering it must contain; "name_attrs" (if not None) names
#         the HCL attribute(s) whose resolved value must be matched by a literal ARN, an
#         agent-platform-* (or other) prefix, OR a bare/interpolated Terraform resource reference
#         in the Resource list of a statement granting one of those actions. A None name_attrs
#         means the type is covered purely by a Resource:"*" grant (no per-instance name to check).
#
#         "read_actions" is ANY-OF: one marker action on a covering Resource satisfies the type.
#         The OPTIONAL "read_actions_all_of" key overrides that with an EXACT, ALL-OF requirement
#         -- every listed action must be granted (literally, or by a wildcard grant that covers
#         it) on a Resource covering the instance. It exists for the types whose refresh-read set
#         is genuinely a conjunction of two DIFFERENT capability classes, where an ANY-OF marker
#         list would let the cheap half stand in for the expensive one. Concretely: after the
#         Secrets Manager read split (value-free metadata prefixed, GetSecretValue enumerated), a
#         wide `secretsmanager:Describe*` statement would ANY-OF-satisfy
#         data:aws_secretsmanager_secret_version -- a green for the wrong reason that lets a data
#         source on a non-enumerated secret AccessDeny live at plan, which is exactly the silent
#         deferred failure this gate exists to kill. Types WITHOUT the key are matched exactly as
#         before (_action_matches, unchanged).
#   (ii)  ENUMERATED_IAM_TYPES -- iam: role reads. MUST be a literal enumerated ARN (Decision
#         35/98) -- a wildcard/prefix match never counts, unlike CHECKED_TYPES.
#   (iii) TRANSITIVE_TYPES  -- covered by a parent/sibling resource's own grant (e.g.
#         aws_lambda_permission via the function's lambda:Get* grant, aws_iam_role_policy via the
#         owning role's iam: read). No independent assertion.
#   (iv)  NON_AWS_TYPES / NO_GRANT_TYPES -- not AWS-IAM-gated at all (a third-party provider
#         resource, or a resource with no AWS refresh-read API call).
#
# A resource type present in terraform/personal/*.tf that appears in NONE of these sets fails
# loud ("unmapped resource type") -- see validate_ci_refresh_read_coverage() below.
# ---------------------------------------------------------------------------

CHECKED_TYPES: dict[str, dict] = {
    "aws_lambda_function": {"read_actions": ("lambda:Get*", "lambda:List*"), "name_attrs": ("function_name",)},
    "aws_lambda_layer_version": {"read_actions": ("lambda:Get*", "lambda:List*"), "name_attrs": ("layer_name",)},
    "aws_cloudwatch_event_rule": {"read_actions": ("events:Describe*", "events:List*"), "name_attrs": ("name",)},
    # The two Secrets Manager types carry EXACT, ALL-OF refresh-read sets rather than the shared
    # Describe*/Get* marker pair they used before the read split. hashicorp/aws v5.100.0:
    # aws_secretsmanager_secret's refresh calls DescribeSecret + GetResourcePolicy and NEVER
    # GetSecretValue, while the value read is issued only by aws_secretsmanager_secret_version
    # (resource + data source). Keeping them on one shared ANY-OF pair would mean a metadata-only
    # `secretsmanager:Describe*` grant satisfies the data source's genuine GetSecretValue need.
    "aws_secretsmanager_secret": {
        # ANY-OF markers, retained for the scope-parity rule (_write_symmetry) which compares read
        # and write SCOPES, not capability classes; the coverage assertion uses the all_of set.
        "read_actions": ("secretsmanager:Describe*", "secretsmanager:GetResourcePolicy"),
        "read_actions_all_of": ("secretsmanager:DescribeSecret", "secretsmanager:GetResourcePolicy"),
        "name_attrs": ("name",),
    },
    "data:aws_secretsmanager_secret_version": {
        "read_actions": ("secretsmanager:GetSecretValue",),
        "read_actions_all_of": ("secretsmanager:GetSecretValue",),
        "name_attrs": ("secret_id",),
    },
    "aws_sns_topic": {"read_actions": ("sns:Get*", "sns:List*"), "name_attrs": ("name",)},
    "aws_dynamodb_table": {"read_actions": ("dynamodb:DescribeTable",), "name_attrs": ("name",)},
    "aws_ssm_parameter": {"read_actions": ("ssm:Get*", "ssm:Describe*", "ssm:List*"), "name_attrs": ("name",)},
    "aws_iam_openid_connect_provider": {"read_actions": ("iam:GetOpenIDConnectProvider",), "name_attrs": ("url",)},
    "aws_s3_bucket": {"read_actions": ("s3:GetBucketLocation",), "name_attrs": ("bucket",)},
    # Resource:"*"-only types -- coverage does not depend on a per-instance name.
    "aws_cloudwatch_log_group": {"read_actions": ("logs:Describe*", "logs:List*"), "name_attrs": None},
    "aws_cloudwatch_metric_alarm": {"read_actions": ("cloudwatch:Describe*", "cloudwatch:List*"), "name_attrs": None},
    "aws_sns_topic_subscription": {"read_actions": ("sns:GetSubscriptionAttributes",), "name_attrs": None},
}

ENUMERATED_IAM_TYPES: dict[str, dict] = {
    "aws_iam_role": {"read_actions": ("iam:GetRole",), "name_attrs": ("name",)},
}

# Covered transitively via a parent/sibling resource's own grant -- no independent assertion.
TRANSITIVE_TYPES = {
    "aws_lambda_permission",  # lambda:GetPolicy is a lambda:Get* action on the same function ARN
    "aws_cloudwatch_event_target",  # events:List* on the same rule ARN
    "aws_lambda_function_url",  # lambda:Get* on the same function ARN
    "aws_iam_role_policy",  # the owning role's iam:GetRolePolicy/ListRolePolicies grant
    "aws_secretsmanager_secret_version",  # the same secret's Describe*/Get* grant
    "aws_s3_bucket_versioning",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_policy",
    "aws_s3_bucket_notification",
    "aws_s3_bucket_lifecycle_configuration",
}

# Third-party (non-AWS) provider resources -- not IAM-gated at all (Neon auth is an API key secret).
NON_AWS_TYPES = {"neon_project", "neon_role", "neon_database"}

# AWS-adjacent resources with no refresh-read AWS API call to gate (local-exec only).
NO_GRANT_TYPES = {"null_resource"}

_PERSONAL_DIR_REL = Path("terraform") / "personal"
_BOOTSTRAP_DIR_REL = Path("terraform") / "bootstrap"


def _read_root_text(root_dir: Path) -> str:
    """Concatenate every *.tf file's text in a terraform root, sorted by filename.

    Root-wide parsing widens ONLY the resolution pool (a `local.<name>` or
    `data.aws_iam_policy_document.<name>` indirection can now resolve across sibling files in the
    same root) -- every statement/local stays keyed to its own named resource, never to its
    position within this concatenation. A missing/empty root (no *.tf files) returns "" rather
    than raising, so every caller can fail loud on that one shared condition.
    """
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root_dir.glob("*.tf")))


ROLE_APPLY = "apply"
ROLE_PLANNER = "planner"
ROLE_TRUSTED_PLAN = "trusted_plan"
# T2.49 / DEP-12 (Decision 144): github_ci_plan + github_ci_drift merged into the single
# dual-sub github_ci_planner role (c2) -- the plan-capable role set goes 3 (apply/plan/drift)
# -> 2 (apply/planner). The coverage-parity contract is unchanged, only the role identity is.
_PLANNER_ROLE_POLICY_NAME = "github_ci_planner"
_TRUSTED_PLAN_ROLE_POLICY_NAME = "github_ci_trusted_plan"

# ---------------------------------------------------------------------------
# Bootstrap (jsonencode, capitalized-key) statement parsing.
#
# terraform/bootstrap/github_ci_apply.tf's inline policy is `policy = jsonencode({ Statement = [
# {Sid=..., Effect=..., Action=[...] or "...", Resource=[...] or "..."}, ... ] })` -- a different
# textual shape from oidc.tf's native `data "aws_iam_policy_document"` `statement {}` blocks, so it
# needs its own (small) parser. Reuses _vir._extract_block (generic brace-depth matcher) and
# _vir._QUOTED_RE for the leaf-level primitives.
# ---------------------------------------------------------------------------

_SID_CAP_RE = re.compile(r'Sid\s*=\s*"([^"]*)"')
# rec-2793 hoist pattern (github_ci_apply.tf): `policy = local.<name>` referencing a
# `locals { <name> = jsonencode({ ... Statement = [...] ... }) }` block elsewhere in the file --
# the lifecycle-precondition-cannot-reference-self workaround. _parse_bootstrap_statements falls
# back to resolving this indirection when the Statement array is not inline in the resource body.
_POLICY_LOCAL_REF_RE = re.compile(r"policy\s*=\s*local\.(\w+)")


def _extract_bracket_block(text: str, open_idx: int) -> str:
    """Return the body between the '[' at open_idx and its matching ']' (depth-counted)."""
    depth = 0
    i = open_idx
    start = open_idx + 1
    while i < len(text):
        c = text[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    raise ValueError(f"Unbalanced brackets starting at index {open_idx}")


def _split_top_level_objects(text: str) -> list[str]:
    """Split a `{...}, {...}, ...` array body into each top-level object's body text."""
    objects = []
    depth = 0
    start = None
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start:i])
                start = None
    return objects


def _extract_capitalized_field(body: str, field: str) -> tuple[list[str], str]:
    """Return (quoted values, raw body text) for a capitalized `Field = [...]` or `Field = "..."`."""
    m = re.search(rf"{field}\s*=\s*\[(.*?)\]", body, re.DOTALL)
    if m:
        raw = m.group(1)
        return _vir._QUOTED_RE.findall(raw), raw
    m = re.search(rf'{field}\s*=\s*"([^"]*)"', body)
    if m:
        return [m.group(1)], f'"{m.group(1)}"'
    return [], ""


def _locate_policy_statement_array(text: str, resource_type: str, resource_name: str) -> str | None:
    """Return the raw `Statement = [...]` body for a named IAM policy resource, or None.

    ONE shared locator for every policy document in terraform/bootstrap. It resolves BOTH textual
    shapes a policy attribute can take, so callers never depend on where in the file a document's
    JSON physically lives:

      inline   `policy = jsonencode({ ... Statement = [...] ... })`
      hoisted  `policy = local.<name>` -> `locals { <name> = jsonencode({ ... Statement = [...] }) }`

    The hoist is the rec-2793 lifecycle-precondition workaround (a precondition cannot reference
    `self`, so the rendered document must exist as a local the precondition can re-render). It is NOT
    identity-policy-specific: any document that grows a size precondition acquires the same shape.
    Resolving the indirection here -- rather than forward-searching from a resource match -- is what
    makes the locator position-independent, so hoisting a document no longer silently strips its
    statements from every consumer.
    """
    block_re = re.compile(rf'resource\s+"{re.escape(resource_type)}"\s+"{re.escape(resource_name)}"\s*\{{')
    m = block_re.search(text)
    if not m:
        return None
    body = _vir._extract_block(text, m.end() - 1)
    stmt_m = re.search(r"Statement\s*=\s*\[", body)
    if stmt_m:
        return _extract_bracket_block(body, stmt_m.end() - 1)
    local_m = _POLICY_LOCAL_REF_RE.search(body)
    if not local_m:
        return None
    assign_m = re.search(rf"\b{re.escape(local_m.group(1))}\s*=\s*jsonencode\s*\(\s*\{{", text)
    if not assign_m:
        return None
    local_body = _vir._extract_block(text, assign_m.end() - 1)
    local_stmt_m = re.search(r"Statement\s*=\s*\[", local_body)
    if not local_stmt_m:
        return None
    return _extract_bracket_block(local_body, local_stmt_m.end() - 1)


def _parse_statement_array(array_text: str) -> list[dict]:
    """Parse a `Statement = [...]` array body into the shared statement dicts."""
    statements = []
    for obj_body in _split_top_level_objects(array_text):
        sid_m = _SID_CAP_RE.search(obj_body)
        actions, _ = _extract_capitalized_field(obj_body, "Action")
        _, resources_raw = _extract_capitalized_field(obj_body, "Resource")
        # DEP-02 (Decision 144, rec-2842 plan): backwards-compatible "effect" key, added for
        # check_identity_iam_actions_subset_of_boundary's Effect-aware subset test (a Deny-only
        # action must never be flagged as "absent from the boundary Allow ceiling" -- it was never
        # granted to begin with). Existing consumers read only sid/actions/resources_raw and never
        # assert exact dict equality, so this addition is non-breaking (verified against
        # tests/checks/iam_tf/test__read_coverage.py this session).
        effect_vals, _ = _extract_capitalized_field(obj_body, "Effect")
        statements.append(
            {
                "sid": sid_m.group(1) if sid_m else None,
                "actions": actions,
                "resources_raw": resources_raw,
                "effect": effect_vals[0] if effect_vals else None,
            }
        )
    return statements


def _parse_bootstrap_statements(text: str, role_policy_resource_name: str) -> list[dict]:
    """Statements of the named inline `aws_iam_role_policy` (the identity policy). [] when absent."""
    array_text = _locate_policy_statement_array(text, "aws_iam_role_policy", role_policy_resource_name)
    if array_text is None:
        return []
    return _parse_statement_array(array_text)


def _parse_managed_policy_statements(text: str, policy_resource_name: str) -> list[dict]:
    """Statements of a named customer-managed `aws_iam_policy`. [] when the resource is absent.

    Returning [] (never raising) on an absent resource is load-bearing: the synthetic fixtures under
    tests/checks/iam_tf/validate_ci_refresh_read_coverage/ declare only an inline role policy, and
    they must stay green unmodified. Callers that REQUIRE the policy assert on the parsed length.
    """
    array_text = _locate_policy_statement_array(text, "aws_iam_policy", policy_resource_name)
    if array_text is None:
        return []
    return _parse_statement_array(array_text)


def _parse_boundary_dataplane_statement(bootstrap_text: str) -> dict | None:
    """Locate github_ci_apply_boundary's DataPlaneAllow statement. None on HCL shape drift.

    Lives here (not in _write_coverage) because it is a policy-document parse, and it is expressed on
    the SHARED locator above so it follows the hoisted-local indirection. The previous implementation
    forward-searched for the next `Statement = [` after the boundary resource match and documented an
    assumption -- "the boundary policy is inline, no rec-2793 hoisted-local indirection" -- that a
    boundary size precondition makes false: once the boundary JSON moves into a local ABOVE the
    resource, the forward search finds nothing (the resource is the last thing in the file) and every
    boundary-ceiling check fails closed with "could not locate the DataPlaneAllow statement".
    Re-exported from _write_coverage so existing importers keep working.
    """
    for stmt in _parse_managed_policy_statements(bootstrap_text, "github_ci_apply_boundary"):
        if stmt.get("sid") == "DataPlaneAllow":
            return stmt
    return None


# ---------------------------------------------------------------------------
# terraform/personal/*.tf resource + locals scanning.
# ---------------------------------------------------------------------------

_RESOURCE_BLOCK_RE = re.compile(r'resource\s+"([a-zA-Z0-9_]+)"\s+"([a-zA-Z0-9_]+)"\s*\{')
_DATA_SECRET_VERSION_RE = re.compile(r'data\s+"aws_secretsmanager_secret_version"\s+"([a-zA-Z0-9_]+)"\s*\{')
_LOCALS_BLOCK_RE = re.compile(r"\blocals\s*\{")
_LOCAL_ASSIGN_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')

_NAME_ATTR_CANDIDATES = ("function_name", "layer_name", "name", "url", "secret_id", "bucket")
_ATTR_VALUE_RE_TMPL = r'\b{attr}\s*=\s*("(?:[^"\\]|\\.)*"|local\.\w+|aws_\w+\.\w+\.\w+)'


def _parse_locals(text: str) -> dict[str, str]:
    locals_map: dict[str, str] = {}
    for m in _LOCALS_BLOCK_RE.finditer(text):
        body = _vir._extract_block(text, m.end() - 1)
        for am in _LOCAL_ASSIGN_RE.finditer(body):
            locals_map[am.group(1)] = am.group(2)
    return locals_map


def _scan_resources(personal_dir: Path):
    """Scan every terraform/personal/*.tf file for resource blocks + locals.

    Returns (resources: list[(type, name, filename)], locals_map, attr_index[(type, name)] -> {attr: raw}).
    """
    resources: list[tuple[str, str, str]] = []
    locals_map: dict[str, str] = {}
    attr_index: dict[tuple[str, str], dict[str, str]] = {}
    for tf_path in sorted(personal_dir.glob("*.tf")):
        text = tf_path.read_text(encoding="utf-8")
        locals_map.update(_parse_locals(text))
        for m in _RESOURCE_BLOCK_RE.finditer(text):
            rtype, rname = m.group(1), m.group(2)
            body = _vir._extract_block(text, m.end() - 1)
            resources.append((rtype, rname, tf_path.name))
            attrs = {}
            for attr in _NAME_ATTR_CANDIDATES:
                am = re.search(_ATTR_VALUE_RE_TMPL.format(attr=attr), body)
                if am:
                    attrs[attr] = am.group(1)
            attrs.update(type_attr(rtype, body))
            attr_index[(rtype, rname)] = attrs
        for m in _DATA_SECRET_VERSION_RE.finditer(text):
            rname = m.group(1)
            body = _vir._extract_block(text, m.end() - 1)
            rtype = "data:aws_secretsmanager_secret_version"
            resources.append((rtype, rname, tf_path.name))
            attrs = {}
            am = re.search(_ATTR_VALUE_RE_TMPL.format(attr="secret_id"), body)
            if am:
                attrs["secret_id"] = am.group(1)
            attr_index[(rtype, rname)] = attrs
    return resources, locals_map, attr_index


_LOCAL_REF_RE = re.compile(r"^local\.(\w+)$")
_RESOURCE_REF_RE = re.compile(r"^(aws_\w+)\.(\w+)\.(\w+)$")


def _resolve_value(raw: str | None, locals_map: dict, attr_index: dict, _depth: int = 0) -> str | None:
    """Resolve a raw HCL attribute value (literal / local.X / aws_type.name.attr) to a string."""
    if raw is None or _depth > 6:
        return None
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    m = _LOCAL_REF_RE.match(raw)
    if m:
        return locals_map.get(m.group(1))
    m = _RESOURCE_REF_RE.match(raw)
    if m:
        rtype, rname, attr = m.groups()
        sub_raw = attr_index.get((rtype, rname), {}).get(attr)
        return _resolve_value(sub_raw, locals_map, attr_index, _depth + 1) if sub_raw else None
    return None


# ---------------------------------------------------------------------------
# Coverage matching.
# ---------------------------------------------------------------------------


def _literal_or_prefix_match(name: str, raw: str, literal_only: bool = False) -> bool:
    """Does `name` match an ARN entry in `raw` -- as a boundary-anchored `/`- or `:`-delimited
    segment, or (unless literal_only) via an `agent-platform-*`-style prefix or a Secrets-Manager
    `<name>-*` suffix?

    Boundary-anchored throughout (H-finding, code-review 2026-07-15): a raw `name in entry`
    substring test let a short enumerated role name (agent-platform-github-ci-pr) spuriously match
    a longer enumerated ARN (.../agent-platform-github-ci-prod-deploy), silently defeating the
    enumerated-IAM fail-loud invariant this verifier exists to guarantee (Decision 35/98/55). The
    exact match now requires `name` to be a whole segment; the prefix/suffix matches require a
    real separator at the name boundary.
    """
    name_clean = name.strip("/")
    for entry in _vir._QUOTED_RE.findall(raw):
        if entry == "*":
            continue  # the bare-wildcard case is handled by the caller's wildcard branch
        # Exact: `name` (or its slash-stripped form) is a whole `:`/`/`-delimited segment of the ARN.
        segments = re.split(r"[:/]", entry)
        if name in segments or (name_clean and name_clean in segments):
            return True
        if literal_only or "*" not in entry:
            continue
        tail = re.split(r"[:/]", entry.split("*", 1)[0].rstrip("/:"))[-1]
        if not tail:
            continue
        # Broadening: the resource name falls under a static account-scoped prefix
        # (function:agent-platform-* covers agent-platform-ducklake-writer; parameter/agent-platform/*
        # covers /agent-platform/ducklake/reader_url).
        if name.lstrip("/").startswith(tail):
            return True
        # Secrets-Manager suffix: the enumerated ARN is exactly `<name><sep>*` (the wildcard stands
        # in for SM's random 6-char suffix), so `tail` is precisely the full name plus one separator.
        if len(tail) == len(name) + 1 and tail.startswith(name) and tail[len(name)] in "-/:":
            return True
    return False


def _action_matches(read_actions: tuple[str, ...], stmt_actions: list[str]) -> bool:
    """Does `stmt_actions` grant one of `read_actions`?

    A `service:Verb*`-suffixed pattern also matches a literal same-verb action (e.g.
    `secretsmanager:Describe*` matches a statement that lists the specific
    `secretsmanager:DescribeSecret` rather than the wildcard form -- both are the same class of
    refresh read; a role is free to spell it either way). Patterns without a trailing `*` require
    an exact match (no prefix-matching risk of pulling in an unrelated same-service action).
    """
    for pattern in read_actions:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if any(a == pattern or a.startswith(prefix) for a in stmt_actions):
                return True
        elif pattern in stmt_actions:
            return True
    return False


def _action_granted(required: str, stmt_actions: list[str]) -> bool:
    """Does `stmt_actions` grant the EXACT `required` action?

    Deliberately the MIRROR IMAGE of _action_matches, and kept separate from it so no existing
    type's behaviour shifts. _action_matches generalises on the REQUIREMENT side (a
    `service:Verb*` marker matches a literal grant); this generalises on the GRANT side (a
    literal requirement is satisfied by a wildcard grant that covers it). That direction is what
    an exact ALL-OF set needs: `secretsmanager:Get*` genuinely does grant
    `secretsmanager:GetSecretValue`, while `secretsmanager:Describe*` genuinely does not -- which
    is precisely the distinction the shared Describe*/Get* marker pair could not draw.
    """
    for granted in stmt_actions:
        if granted == required:
            return True
        if granted.endswith("*") and required.startswith(granted[:-1]):
            return True
    return False


def _statement_covers_resource(
    rtype: str,
    rname: str,
    resolved_name: str | None,
    stmt: dict,
    literal_only: bool,
) -> bool:
    """Does one statement's Resource list reach this resource instance? (Action is not consulted.)"""
    raw = stmt["resources_raw"]
    if not literal_only and "*" in _vir._QUOTED_RE.findall(raw):
        return True
    # Bare or interpolated Terraform resource reference (oidc.tf style), e.g.
    # `aws_sns_topic.alerts.arn` or `${aws_ssm_parameter.reader_url.name}`.
    if f"{rtype}.{rname}." in raw:
        return True
    return bool(resolved_name and _literal_or_prefix_match(resolved_name, raw, literal_only=literal_only))


def _resource_covered(
    rtype: str,
    rname: str,
    resolved_name: str | None,
    read_actions: tuple[str, ...],
    statements: list[dict],
    literal_only: bool = False,
    all_of: bool = False,
) -> bool:
    """ANY-OF by default (unchanged); ALL-OF when `all_of` -- every action needs its own cover.

    In ALL-OF mode each required action must be granted on a covering Resource by SOME statement,
    not necessarily the same one: two statements each covering the instance, one granting
    DescribeSecret and the other GetResourcePolicy, is a correct grant surface.
    """
    if all_of:
        return all(
            any(
                _action_granted(required, stmt["actions"])
                and _statement_covers_resource(rtype, rname, resolved_name, stmt, literal_only)
                for stmt in statements
            )
            for required in read_actions
        )
    for stmt in statements:
        if not _action_matches(read_actions, stmt["actions"]):
            continue
        if _statement_covers_resource(rtype, rname, resolved_name, stmt, literal_only):
            return True
    return False


def _classify(rtype: str) -> tuple[dict | None, bool]:
    """Return (spec, literal_only) for `rtype`, or (None, False) if unmapped."""
    spec = CHECKED_TYPES.get(rtype)
    if spec is not None:
        return spec, False
    spec = ENUMERATED_IAM_TYPES.get(rtype)
    return spec, spec is not None


def _resolve_resource_name(rtype: str, rname: str, spec: dict, locals_map: dict, attr_index: dict) -> str | None:
    """Resolve a CHECKED_TYPES/ENUMERATED_IAM_TYPES resource's name/id attribute, if it has one."""
    if not spec["name_attrs"]:
        return None
    raw_val = None
    for attr in spec["name_attrs"]:
        raw_val = attr_index.get((rtype, rname), {}).get(attr)
        if raw_val:
            break
    resolved = _resolve_value(raw_val, locals_map, attr_index) if raw_val else None
    if rtype == "aws_iam_openid_connect_provider" and resolved:
        resolved = resolved.split("://", 1)[-1]
    return resolved


def _check_resource(
    rtype: str,
    rname: str,
    fname: str,
    locals_map: dict,
    attr_index: dict,
    role_statements: dict[str, list[dict]],
    key: str,
) -> tuple[list[str], bool]:
    """Check one resource's coverage. Returns (findings, was_checked)."""
    if rtype in NON_AWS_TYPES or rtype in NO_GRANT_TYPES or rtype in TRANSITIVE_TYPES:
        return [], False

    spec, literal_only = _classify(rtype)
    if spec is None:
        return [
            (
                f"{key} unmapped resource type {rtype!r} (resource {rname} in {fname}) -- add a coverage rule "
                "to scripts/checks/iam_tf/validate_ci_refresh_read_coverage.py"
            )
        ], False

    resolved_name = _resolve_resource_name(rtype, rname, spec, locals_map, attr_index)
    if spec["name_attrs"] and not resolved_name:
        return [
            (
                f"{key} could not resolve a name/id for {rtype} {rname!r} in {fname} -- "
                "treating as uncovered until the extraction is fixed"
            )
        ], False

    # An entry declaring read_actions_all_of is asserted as an EXACT conjunction; every other entry
    # keeps the historical ANY-OF marker semantics, byte for byte.
    all_of_actions = spec.get("read_actions_all_of")
    required_actions = all_of_actions or spec["read_actions"]
    quantifier = "ALL of" if all_of_actions else "one of"

    secure = secure_string_findings(rtype, rname, fname, resolved_name, attr_index.get((rtype, rname)), role_statements, key)
    findings = list(secure or [])
    for role_key, statements in ({} if secure is not None else role_statements).items():
        if not _resource_covered(
            rtype,
            rname,
            resolved_name,
            required_actions,
            statements,
            literal_only=literal_only,
            all_of=bool(all_of_actions),
        ):
            findings.append(
                f"{key} {rtype} {rname!r}"
                + (f" ({resolved_name!r})" if resolved_name else "")
                + f" in {fname} is not refresh-read-covered in the {role_key} role policy "
                f"(expected {quantifier} {required_actions} on a matching Resource ARN/reference)"
            )
    return findings, True


def _resolve_role_statements(oidc_text: str) -> dict[str, list[dict]] | None:
    docs = _vir._parse_policy_documents(oidc_text)
    role_policy_map = _vir._parse_role_policy_map(oidc_text)
    # The trusted role is optional here (synthetic roots carry none); test__read_coverage_trusted pins it on the real tree.
    result = {
        role_key: _vir._resolve_statements(doc_name, docs)
        for role_key, policy_name in (
            (ROLE_PLANNER, _PLANNER_ROLE_POLICY_NAME),
            (ROLE_TRUSTED_PLAN, _TRUSTED_PLAN_ROLE_POLICY_NAME),
        )
        if (doc_name := role_policy_map.get(policy_name))
    }
    return result if ROLE_PLANNER in result else None
