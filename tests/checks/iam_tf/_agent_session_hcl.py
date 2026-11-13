"""Minimal HCL value parser for the agent-platform-session policy locals (Decision 255).

Handles only the subset `x = jsonencode({...})` uses: objects, lists, quoted strings (interpolation kept raw),
bare references and literals. Comments are blanked first, so a bracket in a comment cannot truncate a parse.
"""

from __future__ import annotations

import re

from tests.checks.iam_tf._platform_security_hcl import _strip_comments

_TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[{}\[\]()=,]|[^\s{}\[\]()=,"]+)')


def _tokens(text: str) -> list[str]:
    return [m.group(1) for m in _TOKEN.finditer(text)]


def _value(toks: list[str], i: int):
    t = toks[i]
    if t == "{":
        out: dict = {}
        i += 1
        while toks[i] != "}":
            if toks[i] == ",":
                i += 1
                continue
            key = toks[i].strip('"')
            assert toks[i + 1] == "=", f"expected = after {key}"
            out[key], i = _value(toks, i + 2)
        return out, i + 1
    if t == "[":
        items: list = []
        i += 1
        while toks[i] != "]":
            if toks[i] == ",":
                i += 1
                continue
            v, i = _value(toks, i)
            items.append(v)
        return items, i + 1
    if t == "jsonencode" and toks[i + 1] == "(":
        v, i = _value(toks, i + 2)
        assert toks[i] == ")"
        return v, i + 1
    if t.startswith('"'):
        return t[1:-1], i + 1
    return t, i + 1  # a bare reference or literal, such as local.x or true


def policy_local(text: str, name: str) -> dict:
    """The jsonencode body assigned to `name = jsonencode(...)`, as nested dicts and lists."""
    clean = _strip_comments(text)
    m = re.search(rf"\b{re.escape(name)}\s*=\s*jsonencode\(", clean)
    assert m is not None, f"local {name} not found"
    toks = _tokens(clean[m.start() :])
    return _value(toks, 2)[0]


def statements(doc: dict) -> list[dict]:
    return list(doc.get("Statement") or [])


def as_list(v) -> list:
    return v if isinstance(v, list) else [v]
