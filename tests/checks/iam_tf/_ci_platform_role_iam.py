"""Predicate + parsers for Decision 225 (rec-4049): no CI identity holds an iam: Allow, write or
read, whose Resource matches PlatformDev or PlatformAdmin.

Parses BOTH HCL dialects used by the scanned files: jsonencode Statement lists
(terraform/bootstrap's two apply-role policy files) and `statement {}` blocks
(terraform/personal's aws_iam_policy_document data sources). Reuses the comment/string masking
primitives from _platform_security_hcl.py rather than a third masker (Decision 128 precedent).
"""

from __future__ import annotations

import fnmatch
import re

from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_PLATFORM_ROLE_NAMES = ("PlatformDev", "PlatformAdmin")


def _substitute(raw: str) -> str:
    return raw.replace("${var.account_id}", "123456789012").replace("${var.aws_region}", "eu-west-2")


def _is_resolved_literal(raw: str) -> bool:
    """True once var.account_id / var.aws_region substitution leaves no interpolation behind."""
    return "${" not in _substitute(raw)


def _list_or_scalar(raw: str) -> list[str]:
    """A quoted scalar or a `[...]` list of quoted strings -> its literal string elements."""
    raw = raw.strip()
    m = re.fullmatch(r"\[(.*)\]", raw, re.S)
    body = m.group(1) if m is not None else raw
    return re.findall(r'"((?:[^"\\]|\\.)*)"', body)


def _case_fold_covers_iam(patterns: list[str]) -> bool:
    """True if any action pattern's case-folded fnmatch covers at least one iam: verb shape.

    Namespace-based: a pattern with a colon covers iam: actions iff its namespace fragment
    (case-folded fnmatch) matches "iam"; a bare pattern with no colon (e.g. "*", "i*") is tested
    against a representative iam: action string directly.
    """
    for p in patterns:
        pl = p.lower()
        if ":" in pl:
            ns = pl.split(":", 1)[0]
            if fnmatch.fnmatch("iam", ns):
                return True
        elif fnmatch.fnmatch("iam:getrole", pl):
            return True
    return False


class _Statement:
    __slots__ = ("sid", "effect", "actions", "not_actions", "resources", "not_resources", "resources_unresolved")

    def __init__(self) -> None:
        self.sid: str | None = None
        self.effect: str = "Allow"
        self.actions: list[str] = []
        self.not_actions: list[str] = []
        self.resources: list[str] = []
        self.not_resources: list[str] = []
        self.resources_unresolved: bool = False


def _jsonencode_statement_blocks(text: str) -> list[str]:
    """Raw text spans of every top-level object inside every `Statement = [...]` list."""
    out: list[str] = []
    masked = _mask(text)
    for m in re.finditer(r"\bStatement\s*=\s*\[", masked):
        depth = 1
        i = m.end()
        list_start = i
        while depth > 0 and i < len(masked):
            if masked[i] == "[":
                depth += 1
            elif masked[i] == "]":
                depth -= 1
            i += 1
        list_body = text[list_start : i - 1]
        masked_body = masked[list_start : i - 1]
        bd = 0
        obj_start = None
        for j, ch in enumerate(masked_body):
            if ch == "{":
                if bd == 0:
                    obj_start = j
                bd += 1
            elif ch == "}":
                bd -= 1
                if bd == 0 and obj_start is not None:
                    out.append(list_body[obj_start : j + 1])
                    obj_start = None
    return out


def _parse_jsonencode_statement(block: str) -> _Statement:
    s = _Statement()
    stripped = _strip_comments(block)
    sm = re.search(r'Sid\s*=\s*"([^"]*)"', stripped)
    s.sid = sm.group(1) if sm is not None else None
    em = re.search(r'Effect\s*=\s*"([^"]*)"', stripped)
    s.effect = em.group(1) if em is not None else "Allow"
    for attr, target in (
        ("Action", "actions"),
        ("NotAction", "not_actions"),
        ("Resource", "resources"),
        ("NotResource", "not_resources"),
    ):
        am = re.search(rf"{attr}\s*=\s*(\[(?:[^][]|\[[^]]*\])*\]|\"(?:[^\"\\\\]|\\\\.)*\")", stripped, re.S)
        if am is not None:
            setattr(s, target, _list_or_scalar(am.group(1)))
    if any(not _is_resolved_literal(r) for r in (s.resources + s.not_resources)):
        s.resources_unresolved = True
    return s


def _hcl_statement_blocks(text: str) -> list[str]:
    masked = _mask(text)
    out: list[str] = []
    for m in re.finditer(r"\bstatement\s*\{", masked):
        out.append(_block_body(text, m.end() - 1))
    return out


def _attr_raw(body: str, name: str) -> str | None:
    """Raw RHS of `name = ...`: a bracket-matched `[...]` list (may span lines, comments
    included), a quoted scalar, or a bare token to end of line."""
    stripped = _strip_comments(body)
    m = re.search(rf"^\s*{name}\s*=\s*", stripped, re.M)
    if m is None:
        return None
    i = m.end()
    if i < len(stripped) and stripped[i] == "[":
        depth = 0
        j = i
        while j < len(stripped):
            if stripped[j] == "[":
                depth += 1
            elif stripped[j] == "]":
                depth -= 1
                if depth == 0:
                    return stripped[i : j + 1]
            j += 1
        return None
    lm = re.match(r'"(?:[^"\\]|\\.)*"', stripped[i:])
    if lm is not None:
        return lm.group(0)
    em = re.match(r"[^\n]*", stripped[i:])
    return em.group(0).strip() if em is not None else None


def _parse_hcl_statement(block: str) -> _Statement:
    s = _Statement()
    sm = re.search(r'^\s*sid\s*=\s*"([^"]*)"\s*$', _strip_comments(block), re.M)
    s.sid = sm.group(1) if sm is not None else None
    em = re.search(r'^\s*effect\s*=\s*"([^"]*)"\s*$', _strip_comments(block), re.M)
    s.effect = em.group(1) if em is not None else "Allow"
    for attr, target in (
        ("actions", "actions"),
        ("not_actions", "not_actions"),
        ("resources", "resources"),
        ("not_resources", "not_resources"),
    ):
        raw = _attr_raw(block, attr)
        if raw is not None:
            setattr(s, target, _list_or_scalar(raw))
    if any(not _is_resolved_literal(r) for r in (s.resources + s.not_resources)):
        s.resources_unresolved = True
    return s


_TEST_ACCOUNT_ID = "123456789012"


def _role_matches(pattern: str, role_name: str) -> bool:
    concrete = f"arn:aws:iam::{_TEST_ACCOUNT_ID}:role/{role_name}"
    translated = _substitute(pattern)
    return fnmatch.fnmatch(concrete.lower(), translated.lower())


def statement_leaks(stmt: _Statement) -> list[str]:
    """Role names (PlatformDev / PlatformAdmin) this single Allow statement leaks an iam: grant on."""
    if stmt.effect != "Allow":
        return []
    is_iam = bool(stmt.not_actions) or _case_fold_covers_iam(stmt.actions)
    if not is_iam:
        return []
    if stmt.not_resources:
        # Any NotResource on an iam-granting Allow is itself the leak shape -- it cannot be
        # proven to exclude either platform role.
        return list(_PLATFORM_ROLE_NAMES)
    if stmt.resources_unresolved:
        return list(_PLATFORM_ROLE_NAMES)
    hits: list[str] = []
    for role_name in _PLATFORM_ROLE_NAMES:
        for pattern in stmt.resources:
            if _role_matches(pattern, role_name):
                hits.append(role_name)
                break
    return hits


def ci_platform_role_iam_leaks(texts: dict[str, str]) -> list[tuple[str, str | None, str]]:
    """(file, sid, role_name) for every Allow statement across both dialects that carries an
    iam: action (or NotAction) and whose Resource matches a concrete PlatformDev or PlatformAdmin
    ARN, over every scanned file. Fails closed: an unresolvable Resource or a NotResource on an
    iam-granting Allow counts as a leak on BOTH role names, never a pass."""
    out: list[tuple[str, str | None, str]] = []
    for fname, text in texts.items():
        if not text.strip():
            raise ValueError(f"{fname}: scanned file is empty -- fail closed")
        for block in _jsonencode_statement_blocks(text):
            stmt = _parse_jsonencode_statement(block)
            for role in statement_leaks(stmt):
                out.append((fname, stmt.sid, role))
        for block in _hcl_statement_blocks(text):
            stmt = _parse_hcl_statement(block)
            for role in statement_leaks(stmt):
                out.append((fname, stmt.sid, role))
    return out


def independent_statement_counts(text: str) -> tuple[int, int]:
    """(parsed statement{} blocks, raw `statement {` openings) and
    (parsed jsonencode statements, raw `Effect =` keys) must each agree -- see
    independent_jsonencode_counts for the second pair."""
    masked = _mask(text)
    raw_hcl = len(re.findall(r"\bstatement\s*\{", masked))
    parsed_hcl = len(_hcl_statement_blocks(text))
    return parsed_hcl, raw_hcl


def independent_jsonencode_counts(text: str) -> tuple[int, int]:
    masked = _mask(text)
    raw_json = len(re.findall(r"\bEffect\s*=", masked))
    parsed_json = len(_jsonencode_statement_blocks(text))
    return parsed_json, raw_json


# ---------------------------------------------------------------------------
# ATTACHMENTS, POLICY SOURCES, APPLY ROLE UNIVERSE (plan scope: the three structural leak
# classes beyond direct Allow-statement scanning -- an AWS-managed attachment or an unsanctioned
# policy source can re-grant the read quartet through the boundary's iam:Get* ceiling without
# ever appearing as a literal Allow statement in the scanned files).
# ---------------------------------------------------------------------------

_APPLY_ROLE_ADDR = "aws_iam_role.github_ci_apply"
_APPLY_ROLE_ALLOWED_TARGETS = frozenset(
    {"aws_iam_role_policy.github_ci_apply", "aws_iam_role_policy_attachment.github_ci_apply_reads"}
)


def _all_resource_blocks(text: str) -> list[tuple[str, str, str, str]]:
    """Every (kind, type, name, body) of a resource/data block, comments ignored."""
    out: list[tuple[str, str, str, str]] = []
    for m in re.finditer(r'\b(resource|data)\s+"([\w-]+)"\s+"([\w-]+)"\s*\{', _strip_comments(text)):
        out.append((m.group(1), m.group(2), m.group(3), _block_body(text, m.end() - 1)))
    return out


def _list_elements(raw: str) -> list[str]:
    """Top-level comma-separated elements of a `[...]` list literal; [] if not a bracketed list."""
    m = re.fullmatch(r"\[(.*)\]", raw, re.S)
    if m is None:
        return []
    inner = m.group(1)
    depth = 0
    elems: list[str] = []
    cur = ""
    for ch in inner:
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        if ch == "," and depth == 0:
            elems.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        elems.append(cur)
    return [e.strip() for e in elems]


def _declared_iam_policy_names(joined: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r'resource\s+"aws_iam_policy"\s+"(\w+)"', _strip_comments(joined))}


def _resolves_to_declared_policy(expr: str, declared: set[str]) -> bool:
    expr = expr.strip()
    m = re.fullmatch(r'"\$\{\s*aws_iam_policy\.(\w+)\.arn\s*\}"', expr) or re.fullmatch(r"aws_iam_policy\.(\w+)\.arn", expr)
    return m is not None and m.group(1) in declared


def attachment_problems(texts: dict[str, str]) -> list[str]:
    """ATTACHMENTS (fail closed): every policy-attachment-shaped resource in the scanned files
    must point at an aws_iam_policy declared in one of those same scanned files -- an AWS-managed
    ARN (e.g. IAMReadOnlyAccess) or any other unscanned policy is a problem."""
    joined = "\n\n".join(texts.values())
    declared = _declared_iam_policy_names(joined)
    problems: list[str] = []
    for kind, rtype, rname, body in _all_resource_blocks(joined):
        if kind != "resource":
            continue
        addr = f"{rtype}.{rname}"
        if rtype in ("aws_iam_role_policy_attachment", "aws_iam_policy_attachment"):
            raw = _attr_raw(body, "policy_arn")
            if raw is not None and not _resolves_to_declared_policy(raw, declared):
                problems.append(f"{addr}: policy_arn {raw!r} does not resolve to a scanned aws_iam_policy")
        elif rtype == "aws_iam_role_policy_attachments_exclusive":
            for elem in _list_elements(_attr_raw(body, "policy_arns") or "[]"):
                if not _resolves_to_declared_policy(elem, declared):
                    problems.append(f"{addr}: policy_arns element {elem!r} does not resolve to a scanned aws_iam_policy")
        elif rtype == "aws_iam_role":
            for elem in _list_elements(_attr_raw(body, "managed_policy_arns") or "[]"):
                if not _resolves_to_declared_policy(elem, declared):
                    problems.append(
                        f"{addr}: managed_policy_arns element {elem!r} does not resolve to a scanned aws_iam_policy"
                    )
    return problems


def _declared_policy_document_names(joined: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r'data\s+"aws_iam_policy_document"\s+"(\w+)"', _strip_comments(joined))}


def _locals_holding_jsonencode(joined: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"\blocals\s*\{", _mask(joined)):
        body = _block_body(joined, m.end() - 1)
        out |= {em.group(1) for em in re.finditer(r"^\s*(\w+)\s*=\s*jsonencode\(", _strip_comments(body), re.M)}
    return out


def _is_sanctioned_policy_source(expr: str, declared_docs: set[str], jsonencode_locals: set[str]) -> bool:
    expr = expr.strip()
    if re.match(r"^jsonencode\s*\(", expr):
        return True
    m = re.fullmatch(r"local\.(\w+)", expr) or re.fullmatch(r'"\$\{\s*local\.(\w+)\s*\}"', expr)
    if m is not None:
        return m.group(1) in jsonencode_locals
    m = re.fullmatch(r"data\.aws_iam_policy_document\.(\w+)\.json", expr) or re.fullmatch(
        r'"\$\{\s*data\.aws_iam_policy_document\.(\w+)\.json\s*\}"', expr
    )
    if m is not None:
        return m.group(1) in declared_docs
    return False


def policy_source_problems(texts: dict[str, str]) -> list[str]:
    """POLICY SOURCES (fail closed): every policy source in the scanned files must resolve to a
    jsonencode literal, a local holding one, or a declared aws_iam_policy_document -- a
    data "aws_iam_policy", a heredoc, file()/templatefile(), or a dynamic "statement" block is a
    problem."""
    joined = "\n\n".join(texts.values())
    declared_docs = _declared_policy_document_names(joined)
    jsonencode_locals = _locals_holding_jsonencode(joined)
    problems: list[str] = []
    if re.search(r'\bdynamic\s+"statement"', _strip_comments(joined)):
        problems.append('a dynamic "statement" block exists in a scanned file')
    for kind, rtype, rname, body in _all_resource_blocks(joined):
        addr = f"{kind}.{rtype}.{rname}"
        if kind == "resource" and rtype in ("aws_iam_role_policy", "aws_iam_policy"):
            raw = _attr_raw(body, "policy")
            if raw is not None and not _is_sanctioned_policy_source(raw, declared_docs, jsonencode_locals):
                problems.append(f"{addr}: policy source {raw[:80]!r} is not a sanctioned form")
        if kind == "resource" and rtype == "aws_iam_role":
            if re.search(r"^\s*inline_policy\s*\{", _strip_comments(body), re.M):
                problems.append(f"{addr}: declares an inline_policy block")
        if kind == "data" and rtype == "aws_iam_policy_document":
            for attr in ("source_policy_documents", "override_policy_documents"):
                raw = _attr_raw(body, attr)
                if raw is None:
                    continue
                for elem in _list_elements(raw):
                    if not _is_sanctioned_policy_source(elem, declared_docs, jsonencode_locals):
                        problems.append(f"{addr}.{attr}: element {elem!r} is not a sanctioned form")
    return problems


def _is_apply_role_ref(expr: str) -> bool:
    expr = expr.strip()
    if re.fullmatch(r'"agent-platform-github-ci-apply"', expr):
        return True
    return re.fullmatch(r'"\$\{\s*aws_iam_role\.github_ci_apply\.(name|id|arn)\s*\}"', expr) is not None or (
        re.fullmatch(r"aws_iam_role\.github_ci_apply\.(name|id|arn)", expr) is not None
    )


def apply_role_universe_problems(bootstrap_dir_texts: dict[str, str]) -> list[str]:
    """APPLY ROLE UNIVERSE (fail closed): no aws_iam_role_policy, attachment or *_exclusive
    resource anywhere in terraform/bootstrap targets the apply role other than the two allowed
    addresses, and the apply role's own declaration carries no inline_policy block or
    managed_policy_arns argument."""
    joined = "\n\n".join(bootstrap_dir_texts.values())
    problems: list[str] = []
    for kind, rtype, rname, body in _all_resource_blocks(joined):
        if kind != "resource":
            continue
        addr = f"{rtype}.{rname}"
        if rtype in ("aws_iam_role_policy", "aws_iam_role_policy_attachment"):
            raw = _attr_raw(body, "role")
            if raw is not None and _is_apply_role_ref(raw) and addr not in _APPLY_ROLE_ALLOWED_TARGETS:
                problems.append(f"{addr}: targets the apply role outside the allowed pair")
        elif rtype in ("aws_iam_role_policies_exclusive", "aws_iam_role_policy_attachments_exclusive"):
            raw = _attr_raw(body, "role_name")
            if raw is not None and _is_apply_role_ref(raw):
                problems.append(f"{addr} ({rtype}): targets the apply role")
        elif rtype == "aws_iam_role" and addr == _APPLY_ROLE_ADDR:
            if re.search(r"^\s*inline_policy\s*\{", _strip_comments(body), re.M):
                problems.append(f"{addr}: declares an inline_policy block on the apply role")
            if _attr_raw(body, "managed_policy_arns") is not None:
                problems.append(f"{addr}: declares managed_policy_arns on the apply role")
    return problems


def structural_grant_surface_problems(scanned_texts: dict[str, str], bootstrap_dir_texts: dict[str, str]) -> list[str]:
    """ATTACHMENTS + POLICY SOURCES over the scanned files, plus APPLY ROLE UNIVERSE over the
    whole terraform/bootstrap directory."""
    return [
        *attachment_problems(scanned_texts),
        *policy_source_problems(scanned_texts),
        *apply_role_universe_problems(bootstrap_dir_texts),
    ]
