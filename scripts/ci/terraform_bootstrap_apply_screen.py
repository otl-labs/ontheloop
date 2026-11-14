#!/usr/bin/env python3
"""Static screen of the terraform/bootstrap root, run before any credential exists (PLAN-bootstrap-ci-gated-apply).

Stdlib only; run through terraform_bootstrap_apply_gate.py's `screen` verb. Two layers: the trusted-plan screen's
tree rules (file shapes) with a bootstrap Policy, then a block scanner over each .tf file. The scanner skips
comments, reads strings with their interpolations as code, and skips a heredoc body to its terminator without
counting its braces (a heredoc holding ${ or %{ is refused). It prints rules and file names, never a value.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ROOT = "terraform/bootstrap"
MAIN_ONLY_FILES = frozenset({".terraform.lock.hcl", "backend-sit.hcl", "authority_budget.json"})
FILE_FUNCTIONS = frozenset(
    {"file", "fileexists", "fileset", "filebase64", "filemd5", "filesha1", "filesha256", "filesha512", "templatefile"}
)
BAD_BLOCKS = frozenset({"provisioner", "connection", "module"})
PROVIDER_ARGS = frozenset({"region", "profile", "default_tags"})
BACKEND_ARGS = frozenset({"bucket", "key", "encrypt", "use_lockfile", "region"})
Finding = tuple[str, str, int]  # rule, file, line


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Refuse(Exception):
    def __init__(self, rule: str, line: int) -> None:
        super().__init__(rule)
        self.rule = rule
        self.line = line


Tok = tuple[str, str, int]  # kind (ident, str, punct), text, line


class _Scanner:
    def __init__(self, text: str) -> None:
        self.t = text
        self.i = 0
        self.line = 1
        self.out: list[Tok] = []

    def run(self) -> list[Tok]:
        self._code(False)
        return self.out

    def _peek(self, k: int = 0) -> str:
        return self.t[self.i + k] if self.i + k < len(self.t) else ""

    def _code(self, in_interp: bool) -> None:
        depth = 0
        while self.i < len(self.t):
            ch = self._peek()
            if ch == "\n":
                self.line += 1
                self.i += 1
            elif ch in " \t\r":
                self.i += 1
            elif self._comment(ch):
                continue
            elif ch == '"':
                self._string()
            elif ch == "<" and self._peek(1) == "<" and self._heredoc():
                continue
            elif ch.isalpha() or ch == "_":
                self._ident()
            else:
                if in_interp:
                    depth, done = self._interp_brace(ch, depth)
                    if done:
                        return
                self.out.append(("punct", ch, self.line))
                self.i += 1

    def _comment(self, ch: str) -> bool:
        if ch == "#" or (ch == "/" and self._peek(1) == "/"):
            while self.i < len(self.t) and self._peek() != "\n":
                self.i += 1
            return True
        if ch == "/" and self._peek(1) == "*":
            end = self.t.find("*/", self.i + 2)
            if end < 0:
                raise _Refuse("unterminated_comment", self.line)
            self.line += self.t.count("\n", self.i, end)
            self.i = end + 2
            return True
        return False

    def _ident(self) -> None:
        j = self.i
        while j < len(self.t) and (self.t[j].isalnum() or self.t[j] in "_-"):
            j += 1
        self.out.append(("ident", self.t[self.i : j], self.line))
        self.i = j

    def _interp_brace(self, ch: str, depth: int) -> tuple[int, bool]:
        if ch == "{":
            return depth + 1, False
        if ch == "}":
            if depth == 0:
                self.i += 1
                return depth, True
            return depth - 1, False
        return depth, False

    def _string(self) -> None:
        start = self.line
        self.i += 1
        buf: list[str] = []
        while self.i < len(self.t):
            ch = self._peek()
            if ch == "\\":
                buf.append(self._peek(1))
                self.i += 2
            elif ch == '"':
                self.i += 1
                self.out.append(("str", "".join(buf), start))
                return
            elif ch == "\n":
                raise _Refuse("newline_in_string", self.line)
            elif ch in "$%" and self._peek(1) == "{" and self._peek(-1) != ch:
                self.i += 2
                self.out.append(("punct", "${", self.line))
                self._code(True)
                self.out.append(("punct", "$}", self.line))
            elif ch in "$%" and self._peek(1) == ch and self._peek(2) == "{":
                buf.append("${")
                self.i += 3
            else:
                buf.append(ch)
                self.i += 1
        raise _Refuse("unterminated_string", start)

    def _heredoc(self) -> bool:
        m = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\r?\n").match(self.t, self.i)
        if not m:
            return False
        ident = m.group(1)
        body_start = m.end()
        pos = body_start
        while True:
            nl = self.t.find("\n", pos)
            line_text = self.t[pos : nl if nl >= 0 else len(self.t)]
            if line_text.strip() == ident:
                body = self.t[body_start:pos]
                if "${" in body or "%{" in body:
                    raise _Refuse("heredoc_interpolation", self.line)
                self.line += self.t.count("\n", self.i, pos)
                self.i = pos + len(line_text)
                self.out.append(("str", "<heredoc>", self.line))
                return True
            if nl < 0:
                raise _Refuse("unterminated_heredoc", self.line)
            pos = nl + 1


def _header(toks: list[Tok], idx: int) -> tuple[str, list[str]] | None:
    """The block header ending at the '{' at toks[idx], or None for an object literal."""
    j = idx - 1
    labels: list[str] = []
    while j >= 0 and toks[j][0] == "str":
        labels.append(toks[j][1])
        j -= 1
    if j >= 0 and toks[j][0] == "ident" and (j == 0 or toks[j - 1][1] not in (".", "=")):
        return toks[j][1], labels[::-1]
    return None


def scan_file(name: str, text: str) -> list[Finding]:
    return _scan(name, text)[0]


def _scan(name: str, text: str) -> tuple[list[Finding], int]:
    """(findings, count of top-level provider blocks) of one file."""
    try:
        toks = _Scanner(text).run()
    except _Refuse as exc:
        return [(exc.rule, name, exc.line)], 0
    out: list[Finding] = []
    stack: list[tuple[str, list[str]]] = []
    providers_seen = 0
    for idx, (kind, val, line) in enumerate(toks):
        if kind == "punct" and val == "{":
            hdr = _header(toks, idx)
            stack.append(hdr if hdr else ("", []))
            found, is_provider = _open_block(name, hdr, len(stack) == 1, toks[idx - 1][1] if idx else "", line)
            out += found
            providers_seen += is_provider
        elif kind == "punct" and val == "}":
            if stack:
                stack.pop()
        elif kind == "ident":
            out += _ident_rules(name, toks, idx, tuple(s[0] for s in stack))
    return out, providers_seen


def _open_block(name: str, hdr: tuple[str, list[str]] | None, top: bool, prev: str, line: int) -> tuple[list[Finding], int]:
    if hdr is None:
        return [], 0
    out: list[Finding] = []
    is_provider = 0
    if top:
        out += _top_block(name, hdr, line)
        if hdr[0] == "provider":
            is_provider = 1
            if hdr[1] != ["aws"]:
                out.append(("provider_block", name, line))
    if hdr[0] in BAD_BLOCKS and prev != ".":
        out.append((f"{hdr[0]}_block", name, line))
    return out, is_provider


def _ident_rules(name: str, toks: list[Tok], idx: int, names: tuple[str, ...]) -> list[Finding]:
    _kind, val, line = toks[idx]
    prev = toks[idx - 1][1] if idx else ""
    nxt = toks[idx + 1] if idx + 1 < len(toks) else ("", "", 0)
    out: list[Finding] = []
    if val in FILE_FUNCTIONS and nxt[1] == "(" and prev != ".":
        out.append(("file_function", name, line))
    if names == ("provider",) and nxt[1] in ("=", "{") and val not in PROVIDER_ARGS:
        out.append(("provider_argument", name, line))
    if names == ("terraform", "required_providers") and nxt[1] == "=" and (val != "aws" or not _aws_source(toks, idx)):
        out.append(("required_providers_entry", name, line))
    if names == ("terraform",) and val == "backend" and not (nxt[0] == "str" and nxt[1] == "s3"):
        out.append(("backend", name, line))
    if names == ("terraform", "backend") and nxt[1] in ("=", "{") and val not in BACKEND_ARGS:
        out.append(("backend_argument", name, line))
    return out


def _top_block(name: str, hdr: tuple[str, list[str]], line: int) -> list[Finding]:
    block, labels = hdr
    if block == "data" and not (labels and labels[0].startswith("aws_")):
        return [("data_source", name, line)]
    return []


def _aws_source(toks: list[Tok], idx: int) -> bool:
    j = idx + 2
    if not (j < len(toks) and toks[j][1] == "{"):
        return False
    depth = 0
    while j < len(toks):
        kind, val, _ = toks[j]
        if val == "{":
            depth += 1
        elif val == "}":
            depth -= 1
            if depth == 0:
                return False
        elif kind == "ident" and val == "source" and depth == 1:
            return j + 2 < len(toks) and toks[j + 1][1] == "=" and toks[j + 2] == ("str", "hashicorp/aws", toks[j + 2][2])
        j += 1
    return False


def lock_findings(name: str, text: str) -> list[Finding]:
    providers = re.findall(r'^provider\s+"([^"]+)"', text, flags=re.MULTILINE)
    return [("lock_provider", name, 0)] if any(p != "registry.terraform.io/hashicorp/aws" for p in providers) else []


def screen_files(files: dict[str, str], extra: list[Finding] | None = None) -> list[Finding]:
    hcl = _load("terraform_hcl_screen")
    policy = dataclasses.replace(hcl.policies.default(), root=ROOT, main_only_files=MAIN_ONLY_FILES)
    found: list[Finding] = list(extra or []) + hcl.tree_rules(files, policy)
    providers: list[tuple[str, int]] = []
    for name in sorted(files):
        if name.endswith(".tf"):
            file_findings, count = _scan(name, files[name])
            found += file_findings
            providers += [(name, count)] if count else []
        elif name == ".terraform.lock.hcl":
            found += lock_findings(name, files[name])
    if sum(c for _, c in providers) > 1:
        found += [("provider_block", n, 0) for n, _ in providers]
    return sorted(set(found), key=lambda f: (f[1], f[2], f[0]))


def screen_root(root: Path) -> list[Finding]:
    hcl = _load("terraform_hcl_screen")
    files, findings = hcl.read_root(root)
    return screen_files(files, findings)
