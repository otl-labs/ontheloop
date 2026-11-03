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
      Delete*/Stop*/Put*/Update*/Create* wildcard appear nowhere.
"""

from __future__ import annotations

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


def terraform_texts() -> dict[str, str]:
    root = _REPO_ROOT / "terraform"
    return {
        str(p.relative_to(_REPO_ROOT)): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.tf"))
        if ".terraform" not in p.parts
    }


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
    return [(p, a) for p, t in texts.items() for a in re.findall(r'"(cloudtrail:[^"]*)"', _strip_comments(t))]


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


def grant_problems(texts: dict[str, str]) -> list[str]:
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
    "wildcard Delete verb": lambda t: {**t, "terraform/personal/x.tf": 'locals {\n  a = ["cloudtrail:Delete*"]\n}\n'},
}


class TestCloudTrailWriteGrantScoped:
    def test_grant_is_scoped(self) -> None:
        assert grant_problems(terraform_texts()) == []

    def test_manage_statement_is_parsed(self) -> None:
        manage = [s for s in statements(terraform_texts()) if s["sid"] == SID]
        assert [s["container"] for s in manage] == [("aws_iam_policy", "platform_security_admin")]

    @pytest.mark.parametrize("label", sorted(_REDS))
    def test_red_case(self, label: str) -> None:
        base = terraform_texts()
        mutated = _REDS[label](base)
        assert mutated != base, f"red case {label!r} did not mutate anything"
        assert grant_problems(mutated) != [], f"red case {label!r} was not detected"
