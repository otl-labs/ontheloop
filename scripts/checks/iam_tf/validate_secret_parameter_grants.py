"""No pattern grant covers the /secret/ SSM parameter family (Decision 237, secret parameters).

STATIC-TEXT STANDING CHECK: parses every IAM Allow statement in terraform/personal and terraform/bootstrap (both the
jsonencode `Effect`/`Action`/`Resource` style and the `aws_iam_policy_document` `statement {}` style) and fails on any
that grants an SSM value verb (ssm:GetParameter, GetParameters, GetParametersByPath, GetParameterHistory,
PutParameter, or an action wildcard covering one) on a Resource pattern reaching a parameter/secret/ ARN. A /secret/
parameter is granted only by its own literal ARN to a declared consumer. The two exceptions the Decision names are
matched by file and Sid. A resources expression that is not a string literal or a list of string literals fails closed.

Credential-free: pure text parsing.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from scripts.checks import _common, registry
from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir

VALUE_VERBS = (
    "ssm:getparameter",
    "ssm:getparameters",
    "ssm:getparametersbypath",
    "ssm:getparameterhistory",
    "ssm:putparameter",
)
BY_PATH_VERB = "ssm:getparametersbypath"
_ARN_PREFIX = "arn:aws:ssm:region:account:parameter/secret/"
PROBE = _ARN_PREFIX + "probe"
_SECRET_NAMES = (
    "probe",
    "neon-api-key",
    "agent-platform-terraform-personal-tfvars",
    "agent-platform-deepseek-api-key",
    "agent-platform-anthropic-api-key",
    "agent-platform-ducklake-telemetry-writer-dsn",
    "agent-platform-ducklake-telemetry-writer-prod-dsn",
    "agent-platform-ducklake-telemetry-reader-dsn",
    "agent-platform-ducklake-smoke-maintainer-dsn",
    "ducklake-neon-catalog-dsn",
)
_ANCESTOR_SUFFIXES = (":parameter", ":parameter/", ":parameter/secret", ":parameter/secret/")
_EXCEPTIONS = (
    ("github_ci_apply_boundary.tf", "DataPlaneAllow"),
    ("platform_admin_policies.tf", "SecretParameterAdmin"),
)
_INTERP_RE = re.compile(r"\$\{[^}]*\}")
_QUOTED_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_JSON_STATEMENT_WRAPPER_RE = re.compile(r"\bStatement\s*=\s*\[")
_ROOTS = (Path("terraform") / "personal", Path("terraform") / "bootstrap")


def covers_value_verb(actions: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(v, a.lower()) for a in actions for v in VALUE_VERBS)


def covers_by_path(actions: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(BY_PATH_VERB, a.lower()) for a in actions)


def normalise(resource: str) -> str:
    return _INTERP_RE.sub("*", resource)


def reaches_secret(resource: str, by_path: bool) -> bool:
    """True when the resource string can name a parameter/secret/ ARN (each ${...} counts as *)."""
    norm = normalise(resource)
    if any(fnmatch.fnmatchcase(_ARN_PREFIX + n, norm) for n in _SECRET_NAMES) or ":parameter/secret/" in norm:
        return True
    return by_path and norm.endswith(_ANCESTOR_SUFFIXES)


def string_list(raw: str) -> list[str] | None:
    """Quoted strings of a `[...]` body or one quoted string; None when any non-string expression is present."""
    values = _QUOTED_STR_RE.findall(raw)
    leftover = _QUOTED_STR_RE.sub("", raw)
    leftover = re.sub(r"#[^\n]*", "", leftover)
    return values if not leftover.strip(" \t\r\n,") else None


def read_field(body: str, name: str) -> tuple[str, list[str] | None]:
    """('absent', None), ('literal', [strings]) or ('nonliteral', None) for `name = ...` in a statement body."""
    m = re.search(rf"\b{name}\s*=\s*", body)
    if not m:
        return "absent", None
    rest = body[m.end() :]
    if rest.startswith('"'):
        q = _QUOTED_STR_RE.match(rest)
        return ("literal", [q.group(1)]) if q else ("nonliteral", None)
    if rest.startswith("["):
        depth = 0
        for i, c in enumerate(rest):
            depth += c == "["
            depth -= c == "]"
            if depth == 0:
                values = string_list(rest[1:i])
                return ("literal", values) if values is not None else ("nonliteral", None)
    return "nonliteral", None


def _strip_comments(text: str) -> str:
    lines = (ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "//")))
    return "\n".join(re.sub(r'\s+#[^"\n]*$', "", ln) for ln in lines)


def _statement(body: str, style: str) -> dict:
    cap = style == "json"
    names = (
        ("Sid", "Effect", "Action", "NotAction", "Resource", "NotResource")
        if cap
        else ("sid", "effect", "actions", "not_actions", "resources", "not_resources")
    )
    sid_k, eff_k, act_k, nact_k, res_k, nres_k = names
    effect = read_field(body, eff_k)[1]
    sid = read_field(body, sid_k)[1]
    action_kind, actions = read_field(body, act_k)
    return {
        "sid": sid[0] if sid else None,
        "effect": effect[0] if effect else "Allow",
        "actions": actions or [],
        "actions_nonliteral": action_kind == "nonliteral",
        "not_action": read_field(body, nact_k)[0] != "absent",
        "resource": read_field(body, res_k),
        "not_resource": read_field(body, nres_k)[0] != "absent",
    }


def parse_statements(text: str) -> list[dict]:
    text = _strip_comments(text)
    found: list[dict] = []
    for m in re.finditer(r"statement\s*\{", text):
        found.append(_statement(_vir._extract_block(text, m.end() - 1), "hcl"))
    for m in re.finditer(r"\{", text):
        body = _vir._extract_block(text, m.start())
        if re.search(r"\bEffect\s*=", body) and not _JSON_STATEMENT_WRAPPER_RE.search(body):
            found.append(_statement(body, "json"))
    return found


def _finding(stmt: dict) -> str | None:
    if stmt["effect"] != "Allow":
        return None
    if stmt["actions_nonliteral"]:
        return "an Action expression that is not a string literal or list of literals"
    if stmt["not_action"] or stmt["not_resource"]:
        return "NotAction or NotResource on an Allow statement"
    if not covers_value_verb(stmt["actions"]):
        return None
    kind, resources = stmt["resource"]
    if kind != "literal" or resources is None:
        return "a Resource expression that is not a string literal or list of literals (fail closed)"
    by_path = covers_by_path(stmt["actions"])
    for r in resources:
        if reaches_secret(r, by_path) and ("*" in r or "?" in r or (by_path and normalise(r).endswith(_ANCESTOR_SUFFIXES))):
            return f"an SSM value verb on a pattern reaching parameter/secret/ ({r})"
    return None


def _excepted(file_name: str, sid: str | None) -> bool:
    return any(file_name == f and (s is None or s == sid) for f, s in _EXCEPTIONS)


@registry.register("validate_secret_parameter_grants", owner="platform")
def validate_secret_parameter_grants(failed: list[str], root: Path | None = None) -> None:
    """No Allow statement grants an SSM value verb on a pattern covering parameter/secret/ (Decision 237 cl.2)."""
    print("\n=== Secret parameter grants (no pattern value grant covers /secret/) ===")
    key = "secret-parameter-grants:"
    root = root if root is not None else _common.ROOT
    files = [p for rel in _ROOTS if (root / rel).is_dir() for p in sorted((root / rel).glob("*.tf"))]
    if not files:
        reason = "neither terraform/personal nor terraform/bootstrap exists"
        print(f"  SKIP: {reason}")
        registry.skipped(reason)
        return
    examined = 0
    before = len(failed)
    for path in files:
        try:
            statements = parse_statements(path.read_text(encoding="utf-8"))
        except ValueError:
            failed.append(f"{key} {path.parent.name}/{path.name} has unbalanced braces -- cannot be parsed (fail closed)")
            continue
        for stmt in statements:
            examined += 1
            problem = _finding(stmt)
            if problem and not _excepted(path.name, stmt["sid"]):
                failed.append(
                    f"{key} {path.parent.name}/{path.name} Sid {stmt['sid'] or '<none>'}: {problem} -- grant a /secret/ "
                    "parameter only by its own literal ARN to a declared consumer (Decision 237 cl.2)"
                )
    for finding in failed[before:]:
        print(f"  FAIL: {finding}")
    if len(failed) == before:
        print(f"  PASS: {examined} statement(s) examined; none grants a value verb on a pattern covering /secret/.")
    registry.examined(examined, unit="iam_statements")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_secret_parameter_grants(_failed)
    for _f in _failed:
        print(f"  - {_f}")
    raise SystemExit(1 if _failed else 0)
