"""Path-aware coverage for SecureString aws_ssm_parameter resources (Decision 237, secret parameters).

A SecureString parameter's refresh decrypts it, so its read is a value read and its write is a value write: both
must be granted on a Resource that covers the parameter's OWN name, not on the shared parameter/agent-platform/*
marker or a Describe*/List* grant. Pure functions, no I/O; shared by _read_coverage (read side) and the facade
(write side).
"""

from __future__ import annotations

import fnmatch
import re

from scripts.checks.iam_tf.validate_invoke_implies_resolve import _QUOTED_RE
from scripts.checks.iam_tf.validate_secret_parameter_grants import normalise

READ_ACTIONS = ("ssm:GetParameter", "ssm:ListTagsForResource")
WRITE_ACTIONS = ("ssm:PutParameter", "ssm:DeleteParameter", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource")
_PLAIN_TYPES = ('"String"', '"StringList"')


def _action_granted(required: str, granted: list[str]) -> bool:
    return any(g == required or (g.endswith("*") and required.startswith(g[:-1])) for g in granted)


def is_secure_string(attrs: dict[str, str] | None) -> bool:
    """True unless the type attribute is a plain String or StringList literal (a non-literal type is treated as secure)."""
    return (attrs or {}).get("type") not in _PLAIN_TYPES


def covers_own_name(rname: str, resolved_name: str | None, resources_raw: str) -> bool:
    """A quoted ARN entry fnmatching arn:aws:ssm:R:A:parameter<name> (each ${...} read as *), or a resource reference."""
    if f"aws_ssm_parameter.{rname}." in resources_raw:
        return True
    if not resolved_name:
        return False
    target = f"arn:aws:ssm:region:account:parameter{resolved_name}"
    secret = resolved_name.startswith("/secret/")
    return any(
        fnmatch.fnmatchcase(target, normalise(entry)) and not (secret and ("*" in entry or "?" in entry))
        for entry in _QUOTED_RE.findall(resources_raw)
    )


def _all_of_covered(actions: tuple[str, ...], statements: list[dict], rname: str, resolved_name: str | None) -> list[str]:
    return [
        a
        for a in actions
        if not any(
            _action_granted(a, s["actions"]) and covers_own_name(rname, resolved_name, s["resources_raw"]) for s in statements
        )
    ]


def secure_string_read_missing(rname: str, resolved_name: str | None, statements: list[dict]) -> list[str]:
    """Read actions not granted on a Resource covering the parameter's own name."""
    return _all_of_covered(READ_ACTIONS, statements, rname, resolved_name)


def top_level_attr(body: str, attr: str) -> str | None:
    """The raw value text of `attr = ...` at brace depth 0 of a block body (a nested map's same-named key never matches)."""
    depth = 0
    for line in body.splitlines():
        m = re.match(rf"\s*{attr}\s*=\s*(.+?)\s*$", line)
        if depth == 0 and m:
            return m.group(1)
        depth += line.count("{") - line.count("}")
    return None


def type_attr(rtype: str, body: str) -> dict[str, str]:
    """The raw top-level type attribute of an aws_ssm_parameter body ({} for other types or when it is absent)."""
    value = top_level_attr(body, "type") if rtype == "aws_ssm_parameter" else None
    return {"type": value} if value else {}


def secure_string_findings(
    rtype: str,
    rname: str,
    fname: str,
    resolved_name: str | None,
    attrs: dict[str, str] | None,
    role_statements: dict[str, list[dict]],
    key: str,
) -> list[str] | None:
    """Read-side findings for a SecureString aws_ssm_parameter (one per role lacking cover); None for any other resource."""
    if rtype != "aws_ssm_parameter" or not is_secure_string(attrs):
        return None
    findings = []
    for role_key, statements in role_statements.items():
        missing = secure_string_read_missing(rname, resolved_name, statements)
        if missing:
            findings.append(
                f"{key} SecureString {rtype} {rname!r} in {fname} lacks {missing} on a Resource covering its own name in "
                f"the {role_key} role policy (Decision 237: its refresh decrypts, so ssm:Describe* or List* cannot cover it)"
            )
    return findings


def check_secure_string_write_coverage(
    apply_statements: list[dict],
    resources: list[tuple[str, str, str]],
    locals_map: dict,
    attr_index: dict,
    failed: list[str],
    key: str,
) -> int:
    """Each SecureString aws_ssm_parameter needs the four write verbs on its own name. Returns the count asserted."""
    from scripts.checks.iam_tf._read_coverage import _resolve_value  # deferred: _read_coverage imports this module

    asserted = 0
    for rtype, rname, fname in resources:
        if rtype != "aws_ssm_parameter" or not is_secure_string(attr_index.get((rtype, rname))):
            continue
        asserted += 1
        resolved = _resolve_value(attr_index.get((rtype, rname), {}).get("name"), locals_map, attr_index)
        missing = _all_of_covered(WRITE_ACTIONS, apply_statements, rname, resolved)
        if missing:
            failed.append(
                f"{key} SecureString aws_ssm_parameter {rname!r} in {fname} lacks {missing} on a Resource covering its own "
                "name in the apply role (Decision 237: a /secret/ parameter is covered only by its own literal ARN)"
            )
    return asserted
