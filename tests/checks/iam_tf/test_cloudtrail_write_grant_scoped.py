"""CloudTrail write authority is exactly six verbs on one trail, held only by PlatformAdmin (Decision 202).

Replaces the retired no-CloudTrail-write check, whose claim Decision 202 deliberately narrowed.
Path-independent -- it sweeps every *.tf under terraform/ and keys on the Sid, so the
PLAN-platform-iam-bootstrap-adopt move of the grant into terraform/bootstrap needed no repoint here:
  (a) quoted "cloudtrail:LookupEvents" is still granted somewhere;
  (b) every quoted cloudtrail verb that is not Get*/Describe*/List*/LookupEvents sits in exactly one
      statement, Sid PlatformSecurityTrailManage, whose Action set EQUALS the six trail-management
      verbs and whose Resource is the single platform-security-trail ARN template;
  (c) that statement's policy is attached only to PlatformAdmin;
  (d) cloudtrail:StopLogging, cloudtrail:DeleteTrail, cloudtrail:* and any cloudtrail
      Delete*/Stop*/Put*/Update*/Create* wildcard appear nowhere as a grant.
Read per account until the organisation trail (Decision 202 note, PLAN-sit-organization): a quoted verb
inside a statement whose Effect is Deny is not a grant (an SCP names StopLogging to forbid it), and
terraform/organization's policy templates are read as JSON, admitting exactly one second write
statement, Sid OrgSecurityTrailManage, on the management trail, read only by OrganizationAdmin's inline policy.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Callable

import pytest

from scripts.checks.iam_tf import _read_coverage as rc

_REPO_ROOT = Path(__file__).resolve().parents[3]


# --- String- and comment-aware HCL helpers (patterns are string literals full of braces, so a
# naive brace count would split mid-string; comments are blanked so a commented header never matches).


def _scan(text: str, blank_strings: bool) -> str:
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#" or (c == "/" and text[i + 1 : i + 2] == "/"):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                step = 2 if text[i] == "\\" else 1
                if blank_strings:
                    for j in range(i, min(i + step, n)):
                        if text[j] != "\n":
                            out[j] = " "
                i += step
            i += 1
        else:
            i += 1
    return "".join(out)


@lru_cache(maxsize=256)
def _mask(text: str) -> str:
    """Equal-length copy with string contents and #/// comments blanked (quotes and newlines kept)."""
    return _scan(text, blank_strings=True)


@lru_cache(maxsize=256)
def _strip_comments(text: str) -> str:
    """Equal-length copy with only comments blanked; string contents intact."""
    return _scan(text, blank_strings=False)


def _block_body(text: str, open_brace_idx: int) -> str:
    """Body between the '{' at open_brace_idx and its match, computed on masked text."""
    masked = _mask(text)
    depth = 0
    for i in range(open_brace_idx, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : i]
    raise ValueError(f"unbalanced braces at {open_brace_idx}")


def _resource_body(text: str, rtype: str, rname: str) -> str | None:
    m = re.search(rf'resource\s+"{re.escape(rtype)}"\s+"{re.escape(rname)}"\s*\{{', _strip_comments(text))
    return None if m is None else _block_body(text, m.end() - 1)


def _resources(text: str) -> list[tuple[str, str, str]]:
    """Every (kind, type, name) of a resource/data block, comments ignored."""
    return [
        (m.group(1), m.group(2), m.group(3))
        for m in re.finditer(r'\b(resource|data)\s+"([\w-]+)"\s+"([\w-]+)"\s*\{', _strip_comments(text))
    ]


def _attr(body: str, name: str) -> str | None:
    """Raw right-hand side of a top-level `name = ...` line inside a block body (first match)."""
    m = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", _strip_comments(body), re.M)
    return None if m is None else m.group(1)


SID = "PlatformSecurityTrailManage"
SIX = frozenset(
    {
        "cloudtrail:CreateTrail",
        "cloudtrail:UpdateTrail",
        "cloudtrail:PutEventSelectors",
        "cloudtrail:StartLogging",
        "cloudtrail:AddTags",
        "cloudtrail:RemoveTags",
    }
)
TRAIL_ARN = "arn:aws:cloudtrail:${var.aws_region}:${var.account_id}:trail/platform-security-trail"
_BANNED_EXACT = {"cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:*"}
_BANNED_WILDCARD_RE = re.compile(r"^cloudtrail:(?:Delete|Stop|Put|Update|Create)[A-Za-z]*\*")
_READ_RE = re.compile(r"^cloudtrail:(?:Get|Describe|List)[A-Za-z*]*$|^cloudtrail:LookupEvents$")
_ADMIN_ROLE_REFS = {'"PlatformAdmin"', "aws_iam_role.platform_admin.name", "aws_iam_role.platform_admin.id"}


ORG_SID = "OrgSecurityTrailManage"
ORG_FILE = "terraform/organization/policies/organization_admin.json.tftpl"
ORG_TRAIL_ARN = "arn:aws:cloudtrail:${region}:${account_id}:trail/agent-platform-org-security-trail"
_ORG_TEMPLATE_REF = re.compile(r"policies/organization_admin\.json\.tftpl")


def terraform_texts() -> dict[str, str]:
    root = _REPO_ROOT / "terraform"
    paths = [*root.rglob("*.tf"), *(root / "organization" / "policies").glob("*.json.tftpl")]
    return {
        str(p.relative_to(_REPO_ROOT)): p.read_text(encoding="utf-8") for p in sorted(paths) if ".terraform" not in p.parts
    }


def _is_template(path: str) -> bool:
    return path.endswith(".json.tftpl")


def _object_effect(text: str, open_idx: int) -> str | None:
    """The Effect of the object opened at open_idx (its own top level, nested objects blanked)."""
    body = _block_body(text, open_idx)
    masked = _mask(body)
    depth, top = 0, []
    for i, ch in enumerate(body):
        if masked[i] == "{":
            depth += 1
        top.append(" " if depth else ch)
        if masked[i] == "}":
            depth -= 1
    m = re.search(r'\bEffect\s*=\s*"(\w+)"', _strip_comments("".join(top)))
    return None if m is None else m.group(1)


def _is_deny_only(text: str, offset: int) -> bool:
    """True when every enclosing object that carries an Effect says Deny (and at least one does)."""
    masked = _mask(text)
    stack: list[int] = []
    for i in range(offset):
        if masked[i] == "{":
            stack.append(i)
        elif masked[i] == "}" and stack:
            stack.pop()
    effects = [e for e in (_object_effect(text, i) for i in stack) if e]
    return bool(effects) and all(e == "Deny" for e in effects)


def _container(text: str, offset: int) -> tuple[str, str] | None:
    """(type, name) of the policy resource holding offset: a resource block, or the resource whose
    `policy = local.X` names the locals entry holding it."""
    code = _strip_comments(text)
    for m in re.finditer(r'resource\s+"(\w+)"\s+"(\w+)"\s*\{', code):
        body = _block_body(text, m.end() - 1)
        if m.end() <= offset < m.end() + len(body):
            return m.group(1), m.group(2)
    for m in re.finditer(r"\b(\w+)\s*=\s*jsonencode\s*\(\s*\{", code):
        body = _block_body(text, m.end() - 1)
        if m.end() <= offset < m.end() + len(body):
            ref = re.search(rf'resource\s+"(\w+)"\s+"(\w+)"\s*\{{[^}}]*?policy\s*=\s*local\.{m.group(1)}\b', code, re.S)
            if ref:
                return ref.group(1), ref.group(2)
    return None


def statements(texts: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in texts.items():
        code = _strip_comments(text)
        for m in re.finditer(r"\bStatement\s*=\s*\[", code):
            array = rc._extract_bracket_block(code, m.end() - 1)
            for stmt in rc._parse_statement_array(array):
                stmt.update(path=path, container=_container(text, m.start()))
                out.append(stmt)
    return out


def cloudtrail_occurrences(texts: dict[str, str]) -> list[tuple[str, str]]:
    """Every quoted cloudtrail verb in the .tf texts that sits in a grant (a Deny statement grants nothing)."""
    out: list[tuple[str, str]] = []
    for path, text in texts.items():
        if _is_template(path):
            continue
        for m in re.finditer(r'"(cloudtrail:[^"]*)"', _strip_comments(text)):
            if not _is_deny_only(text, m.start()):
                out.append((path, m.group(1)))
    return out


def _attached_roles(texts: dict[str, str], container: tuple[str, str]) -> list[str]:
    rtype, rname = container
    if rtype == "aws_iam_role_policy":
        body = next((b for t in texts.values() if (b := _resource_body(t, rtype, rname))), "")
        return [_attr(body, "role") or "<none>"]
    roles: list[str] = []
    for text in texts.values():
        for kind, atype, aname in _resources(text):
            body = _resource_body(text, atype, aname) or "" if kind == "resource" else ""
            if f"aws_iam_policy.{rname}.arn" not in body:
                continue
            roles.append(_attr(body, "role") or "<none>" if atype == "aws_iam_role_policy_attachment" else f"<{atype}>")
    return roles


def _as_list(value: object) -> list[str]:
    return [value] if isinstance(value, str) else [str(v) for v in value or []]  # type: ignore[union-attr]


def _template_problems(texts: dict[str, str]) -> list[str]:
    """Allow statements in terraform/organization's policy templates, read as JSON."""
    problems: list[str] = []
    writes: list[tuple[str, dict]] = []
    for path, text in sorted(texts.items()):
        if not _is_template(path):
            continue
        try:
            doc = json.loads(text)
        except ValueError:
            problems.append(f"{path}: not valid JSON")
            continue
        stmts = doc.get("Statement", [])
        for stmt in [stmts] if isinstance(stmts, dict) else stmts:
            if stmt.get("Effect") != "Allow":
                continue
            verbs = [a for a in _as_list(stmt.get("Action")) if a.startswith("cloudtrail:")]
            problems += [f"{path}: banned {a}" for a in verbs if a in _BANNED_EXACT or _BANNED_WILDCARD_RE.match(a)]
            if any(not _READ_RE.match(a) for a in verbs):
                writes.append((path, stmt))
    if not writes:
        return problems
    if len(writes) != 1:
        return problems + [f"expected at most one template write statement, found {len(writes)}"]
    path, stmt = writes[0]
    if path != ORG_FILE or stmt.get("Sid") != ORG_SID:
        problems.append(f"{path}: template write statement {stmt.get('Sid')} is not {ORG_SID} in {ORG_FILE}")
    actions = _as_list(stmt.get("Action"))
    if set(actions) != SIX or len(actions) != len(SIX):
        problems.append(f"{ORG_SID} actions {sorted(actions)} != the six trail-management verbs")
    if _as_list(stmt.get("Resource")) != [ORG_TRAIL_ARN]:
        problems.append(f"{ORG_SID} Resource {stmt.get('Resource')!r} is not the single management trail ARN template")
    readers = [
        (atype, _attr(_resource_body(t, atype, aname) or "", "role"))
        for t in texts.values()
        if not _is_template(t)
        for _, atype, aname in _resources(t)
        if _ORG_TEMPLATE_REF.search(_resource_body(t, atype, aname) or "")
    ]
    if not readers or any(r != ("aws_iam_role_policy", "aws_iam_role.organization_admin.name") for r in readers):
        problems.append(f"{ORG_SID} template is read by {readers or 'nothing'}, not only OrganizationAdmin's inline policy")
    return problems


def grant_problems(texts: dict[str, str]) -> list[str]:
    problems = _tf_grant_problems({p: t for p, t in texts.items() if not _is_template(p)})
    return problems + _template_problems(texts)


def _tf_grant_problems(texts: dict[str, str]) -> list[str]:
    occurrences = cloudtrail_occurrences(texts)
    problems = (
        [] if any(a == "cloudtrail:LookupEvents" for _, a in occurrences) else ["cloudtrail:LookupEvents is granted nowhere"]
    )
    problems += [f"{p}: banned {a}" for p, a in occurrences if a in _BANNED_EXACT or _BANNED_WILDCARD_RE.match(a)]
    stmts = statements(texts)
    manage = [s for s in stmts if s["sid"] == SID]
    writes_in_text = sorted(a for _, a in occurrences if not _READ_RE.match(a))
    writes_in_manage = sorted(a for s in manage for a in s["actions"] if a.startswith("cloudtrail:") and not _READ_RE.match(a))
    if writes_in_text != writes_in_manage:
        problems.append(
            f"cloudtrail write verbs outside {SID}: {sorted(set(writes_in_text) - set(writes_in_manage)) or writes_in_text}"
        )
    if len(manage) != 1:
        return problems + [f"expected exactly one {SID} statement, found {len(manage)}"]
    stmt = manage[0]
    if set(stmt["actions"]) != SIX or len(stmt["actions"]) != len(SIX):
        problems.append(f"{SID} actions {sorted(stmt['actions'])} != the six trail-management verbs")
    if re.findall(r'"([^"]*)"', stmt["resources_raw"]) != [TRAIL_ARN]:
        problems.append(f"{SID} Resource {stmt['resources_raw'].strip()} is not the single trail ARN template")
    if stmt.get("container") is None:
        return problems + [f"{SID} is not inside a recognisable policy resource"]
    roles = _attached_roles(texts, stmt["container"])
    if not roles or any(r not in _ADMIN_ROLE_REFS for r in roles):
        problems.append(f"{SID} policy is attached to {roles or 'nothing'}, not only PlatformAdmin")
    return problems


_ADMIN_FILE = "terraform/bootstrap/platform_security_admin_policy.tf"
_SECOND_CREATE_TRAIL = """locals {
  p = jsonencode({
    Statement = [
      {
        Sid = "Other"
        Effect = "Allow"
        Action = ["cloudtrail:CreateTrail"]
        Resource = "*"
      },
    ]
  })
}
"""
_ALLOW_NESTING_DENY = """locals {
  p = jsonencode({
    Statement = [
      {
        Sid      = "Other"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "*"
        Nested = {
          Effect = "Deny"
          Action = ["cloudtrail:StopLogging"]
        }
      },
    ]
  })
}
"""
_DENY_ONLY = """locals {
  p = {
    deny_stop = {
      Effect = "Deny"
      Action = ["cloudtrail:StopLogging", "cloudtrail:DeleteTrail"]
    }
  }
}
"""
_DEV_ATTACHMENT = """resource "aws_iam_role_policy_attachment" "dev" {
  role       = "PlatformDev"
  policy_arn = aws_iam_policy.platform_security_admin.arn
}
"""
_REDS: dict[str, Callable[[dict[str, str]], dict[str, str]]] = {
    "extra verb in the Sid": lambda t: {
        **t,
        _ADMIN_FILE: t[_ADMIN_FILE].replace(
            '"cloudtrail:RemoveTags",', '"cloudtrail:RemoveTags",\n          "cloudtrail:PutInsightSelectors",'
        ),
    },
    "second statement granting CreateTrail": lambda t: {
        **t,
        "terraform/personal/x.tf": _SECOND_CREATE_TRAIL,
    },
    "StopLogging anywhere": lambda t: {**t, "terraform/personal/x.tf": 'locals {\n  a = ["cloudtrail:StopLogging"]\n}\n'},
    "cloudtrail:* on PlatformAdmin": lambda t: {
        **t,
        _ADMIN_FILE: t[_ADMIN_FILE].replace('"cloudtrail:AddTags",', '"cloudtrail:*",'),
    },
    "policy attached to PlatformDev": lambda t: {
        **t,
        _ADMIN_FILE: t[_ADMIN_FILE].replace(
            "role       = aws_iam_role.platform_admin.name", "role       = aws_iam_role.platform_dev.name"
        ),
    },
    "second attachment to PlatformDev": lambda t: {
        **t,
        "terraform/personal/x.tf": _DEV_ATTACHMENT,
    },
    "Resource of star": lambda t: {**t, _ADMIN_FILE: t[_ADMIN_FILE].replace(f'Resource = "{TRAIL_ARN}"', 'Resource = "*"')},
    "LookupEvents removed": lambda t: {k: v.replace('"cloudtrail:LookupEvents",', "") for k, v in t.items()},
    "Allow statement nesting a Deny-shaped map": lambda t: {**t, "terraform/personal/x.tf": _ALLOW_NESTING_DENY},
    "StopLogging allowed in the org template": lambda t: {
        **t,
        ORG_FILE: t[ORG_FILE].replace('"cloudtrail:AddTags"', '"cloudtrail:StopLogging", "cloudtrail:AddTags"'),
    },
    "seventh verb in OrgSecurityTrailManage": lambda t: {
        **t,
        ORG_FILE: t[ORG_FILE].replace('"cloudtrail:AddTags"', '"cloudtrail:PutInsightSelectors", "cloudtrail:AddTags"'),
    },
    "OrgSecurityTrailManage on another trail": lambda t: {
        **t,
        ORG_FILE: t[ORG_FILE].replace("trail/agent-platform-org-security-trail", "trail/other-trail"),
    },
    "third write statement": lambda t: {
        **t,
        ORG_FILE: t[ORG_FILE].replace(
            '"Statement": [',
            '"Statement": [\n    {"Sid": "Extra", "Effect": "Allow", "Action": ["cloudtrail:CreateTrail"], "Resource": "*"},',
        ),
    },
    "org template read by another role": lambda t: {
        **t,
        "terraform/organization/management_admin.tf": t["terraform/organization/management_admin.tf"].replace(
            "role   = aws_iam_role.organization_admin.name", 'role   = "PlatformDev"'
        ),
    },
    "wildcard Delete verb": lambda t: {**t, "terraform/personal/x.tf": 'locals {\n  a = ["cloudtrail:Delete*"]\n}\n'},
}


class TestCloudTrailWriteGrantScoped:
    def test_grant_is_scoped(self) -> None:
        assert grant_problems(terraform_texts()) == []

    def test_manage_statement_is_parsed(self) -> None:
        manage = [s for s in statements(terraform_texts()) if s["sid"] == SID]
        assert [s["container"] for s in manage] == [("aws_iam_policy", "platform_security_admin")]

    def test_deny_statements_are_not_grants(self) -> None:
        base = terraform_texts()
        assert grant_problems({**base, "terraform/personal/x.tf": _DENY_ONLY}) == []
        denied = json.loads(base[ORG_FILE])
        denied["Statement"].append({"Effect": "Deny", "Action": ["cloudtrail:StopLogging"], "Resource": "*"})
        assert grant_problems({**base, ORG_FILE: json.dumps(denied)}) == []

    def test_org_write_statement_is_parsed(self) -> None:
        doc = json.loads(terraform_texts()[ORG_FILE])
        assert [s["Sid"] for s in doc["Statement"] if s["Sid"] == ORG_SID] == [ORG_SID]

    @pytest.mark.parametrize("label", sorted(_REDS))
    def test_red_case(self, label: str) -> None:
        base = terraform_texts()
        mutated = _REDS[label](base)
        assert mutated != base, f"red case {label!r} did not mutate anything"
        assert grant_problems(mutated) != [], f"red case {label!r} was not detected"
