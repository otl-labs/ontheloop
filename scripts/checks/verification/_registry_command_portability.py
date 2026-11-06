"""Admission-time portability rule for graduated command-slot records (rec-4889, Decision 226 cl.5).

A command-slot record re-runs on the CI runner at every full tier, where ripgrep is absent and the checkout is a
push-context merge commit. findings(row) reads only the command's own text and flags two classes:

- RG: ripgrep in program position (top-level argv, a python list of string literals, a shell segment head, a
  bash/sh -c body, a $(...) or backtick substitution, a python -c body).
- LIVE_CHECKOUT: a read of the live checkout's branch or push context (git branch --show-current, rev-parse
  --abbrev-ref, symbolic-ref, merge-base; a $GITHUB_ expansion; os.environ/os.getenv of a GITHUB_ key; any
  push_context_base call).

Python bodies are read with the stdlib tokenize module and never executed or parsed, so string contents are never
matched. A body or segment that cannot be tokenized is counted in Findings.unparseable and fails open, never raising.

Residuals (all fail closed with the pytest remedy): a LIVE_CHECKOUT verb against a repository the command built
itself (git -C <dir>, cd <tmp> && git ..., a python git list run with cwd=<tmp>, --git-dir=), git merge-base
--is-ancestor over fixed refs, and an all-string python data list headed 'rg' are refused by design. Reads through
first-party code and string-form shell calls (shell=True, os.system) are rec-4904's. The graduated re-run's rg stub is
present-but-exiting-127 rather than absent, so a command -v probe or an exec-level subprocess.run of rg would differ
from the runner (none exists in scripts/ or src/ today).
"""

from __future__ import annotations

import ast
import io
import keyword
import os
import re
import shlex
import tokenize
from dataclasses import dataclass, field

_COMMAND_SLOTS = frozenset({"command_exit_zero", "command_output_matches", "metric_under_threshold"})
_MAX_DEPTH = 8
_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PY3_VERSIONED_RE = re.compile(r"^python3\.\d+$")
_PYTHON_HEADS = frozenset({"python", "python3", "venv-python"})
_SHELL_HEADS = frozenset({"bash", "sh"})
_SHELL_OPTS_WITH_OPERAND = frozenset({"-o", "+o"})
_PYTHON_OPTS_WITH_OPERAND = frozenset({"-W", "-X"})
_GIT_FLAGS_WITH_OPERAND = frozenset({"-C", "-c"})
_GIT_FLAGS_ALONE = frozenset({"--no-pager"})
_GIT_FLAG_PREFIXES = ("--git-dir=", "--work-tree=")
_RG_MESSAGE = "RG: ripgrep is not on the CI runner; use POSIX grep instead ({detail})"
_LIVE_MESSAGE = (
    "LIVE_CHECKOUT: the verdict depends on the live checkout's branch or push context ({detail}); "
    "assert the claim in a pytest test that builds its own repository"
)
_SKIP_TOKEN_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


@dataclass(frozen=True)
class Findings:
    messages: list[str] = field(default_factory=list)
    unparseable: int = 0


class _Ctx:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.unparseable = 0

    def rg(self, detail: str) -> None:
        self.messages.append(_RG_MESSAGE.format(detail=detail))

    def live(self, detail: str) -> None:
        self.messages.append(_LIVE_MESSAGE.format(detail=detail))

    def result(self) -> Findings:
        return Findings(messages=list(dict.fromkeys(self.messages)), unparseable=self.unparseable)


def _strip_wrappers(tokens: list[str], shell: bool) -> list[str]:
    toks = list(tokens)
    while toks:
        head = toks[0]
        if head == "timeout" and len(toks) >= 2:
            toks = toks[2:]
        elif head == "env" or _VAR_ASSIGN_RE.match(head):
            toks = toks[1:]
        elif shell and head in {"!", "command", "exec"}:
            toks = toks[1:]
        else:
            break
    return toks


def _git_rules(rest: list[str], ctx: _Ctx) -> None:
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in _GIT_FLAGS_WITH_OPERAND:
            i += 2
        elif tok in _GIT_FLAGS_ALONE or tok.startswith(_GIT_FLAG_PREFIXES):
            i += 1
        else:
            break
    if i >= len(rest):
        return
    sub, args = rest[i], rest[i + 1 :]
    if sub == "branch" and "--show-current" in args:
        ctx.live("git branch --show-current")
    elif sub == "rev-parse" and "--abbrev-ref" in args:
        ctx.live("git rev-parse --abbrev-ref")
    elif sub in {"symbolic-ref", "merge-base"}:
        ctx.live(f"git {sub}")


def _shell_c_body(tokens: list[str]) -> str | None:
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith(("-", "+")) or tok in {"-", "+"}:
            return None
        if tok in _SHELL_OPTS_WITH_OPERAND:
            i += 2
            continue
        if not tok.startswith("--") and tok.startswith("-") and "c" in tok[1:]:
            return tokens[i + 1] if i + 1 < len(tokens) else None
        i += 1
    return None


def _python_c_body(tokens: list[str]) -> str | None:
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-c":
            return tokens[i + 1] if i + 1 < len(tokens) else None
        if tok in _PYTHON_OPTS_WITH_OPERAND:
            i += 2
            continue
        if not tok.startswith("-") or tok == "-m":
            return None
        i += 1
    return None


def _argv_rules(tokens: list[str], ctx: _Ctx, depth: int, shell: bool) -> None:
    toks = _strip_wrappers(tokens, shell)
    if not toks:
        return
    head = os.path.basename(toks[0])
    if _PY3_VERSIONED_RE.match(head):
        head = "python3"
    if head == "rg":
        ctx.rg("rg invoked")
    elif head == "git":
        _git_rules(toks[1:], ctx)
    elif head in _SHELL_HEADS:
        body = _shell_c_body(toks)
        if body is not None:
            _shell_body(body, ctx, depth + 1)
    elif head in _PYTHON_HEADS:
        body = _python_c_body(toks)
        if body is not None:
            _python_body(body, ctx, depth + 1)


def _matching(text: str, start: int, opener: str, closer: str) -> int | None:
    """Index of the closer matching the opener just before `start`, quote-aware, else None."""
    depth, i, quote = 1, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch == "\\":
            i += 1
        elif ch in "'\"":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


class _Scanner:
    """Quote-, comment- and substitution-aware split of a shell body into command segments."""

    def __init__(self, body: str, ctx: _Ctx, depth: int) -> None:
        self.body = body
        self.ctx = ctx
        self.depth = depth
        self.segs: list[str] = []
        self.cur: list[str] = []
        self.i = 0

    def flush(self) -> None:
        self.segs.append("".join(self.cur))
        self.cur.clear()

    def expansion(self, at: int) -> None:
        if self.body.startswith(("GITHUB_", "{GITHUB_"), at + 1):
            self.ctx.live("$GITHUB_ expansion")

    def substitution(self, at: int) -> int | None:
        """Recurse into the body of the $(...) or backtick substitution opening at `at`; its closing index."""
        if self.body[at] == "`":
            close = self.body.find("`", at + 1)
            start = at + 1
        else:
            found = _matching(self.body, at + 2, "(", ")")
            close = -1 if found is None else found
            start = at + 2
        if close < 0:
            return None
        _shell_body(self.body[start:close], self.ctx, self.depth + 1)
        return close

    def is_substitution(self, at: int) -> bool:
        return self.body[at] == "`" or (self.body[at] == "$" and self.body.startswith("(", at + 1))

    def double_quoted(self) -> bool:
        body, j = self.body, self.i + 1
        while j < len(body) and body[j] != '"':
            if body[j] == "\\":
                j += 1
            elif self.is_substitution(j):
                close = self.substitution(j)
                if close is None:
                    return False
                j = close
            elif body[j] == "$":
                self.expansion(j)
            j += 1
        if j >= len(body):
            return False
        self.cur.append(body[self.i : j + 1])
        self.i = j + 1
        return True

    def single_quoted(self) -> bool:
        end = self.body.find("'", self.i + 1)
        if end < 0:
            return False
        self.cur.append(self.body[self.i : end + 1])
        self.i = end + 1
        return True

    def substituted(self) -> bool:
        close = self.substitution(self.i)
        if close is None:
            return False
        self.cur.append(self.body[self.i : close + 1])
        self.i = close + 1
        return True

    def comment_starts(self) -> bool:
        return not self.cur or self.cur[-1][-1:].isspace()

    def skip_comment(self) -> None:
        end = self.body.find("\n", self.i)
        self.i = len(self.body) if end < 0 else end

    def ampersand(self) -> None:
        if self.body.startswith("&&", self.i):
            self.flush()
            self.i += 2
        elif (self.cur and self.cur[-1][-1:] in "<>") or self.body.startswith(">", self.i + 1):
            self.cur.append("&")
            self.i += 1
        else:
            self.flush()
            self.i += 1

    def step(self) -> bool:
        ch = self.body[self.i]
        if ch == "\\":
            self.cur.append(self.body[self.i : self.i + 2])
            self.i += 2
        elif ch == "'":
            return self.single_quoted()
        elif ch == '"':
            return self.double_quoted()
        elif self.is_substitution(self.i):
            return self.substituted()
        elif ch == "#" and self.comment_starts():
            self.skip_comment()
        elif ch in "\n;()":
            self.flush()
            self.i += 1
        elif ch == "|":
            self.flush()
            self.i += 2 if self.body.startswith("||", self.i) else 1
        elif ch == "&":
            self.ampersand()
        else:
            if ch == "$":
                self.expansion(self.i)
            self.cur.append(ch)
            self.i += 1
        return True

    def run(self) -> bool:
        while self.i < len(self.body):
            if not self.step():
                return False
        self.flush()
        return True


def _segments(body: str, ctx: _Ctx, depth: int) -> tuple[list[str], bool]:
    """(segments, ok): ok is False when a quote or substitution is left open."""
    scanner = _Scanner(body, ctx, depth)
    ok = scanner.run()
    return scanner.segs, ok


def _shell_body(body: str, ctx: _Ctx, depth: int) -> None:
    if depth > _MAX_DEPTH:
        ctx.unparseable += 1
        return
    segs, ok = _segments(body, ctx, depth)
    if not ok:
        ctx.unparseable += 1
    for seg in segs:
        if not seg.strip():
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            ctx.unparseable += 1
            continue
        _argv_rules(tokens, ctx, depth, shell=True)


def _string_value(tok: tokenize.TokenInfo) -> str | None:
    if tok.type != tokenize.STRING:
        return None
    try:
        value = ast.literal_eval(tok.string)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _is(tok: tokenize.TokenInfo, kind: int, text: str) -> bool:
    return tok.type == kind and tok.string == text


def _environ_read(toks: list[tokenize.TokenInfo], i: int, ctx: _Ctx) -> None:
    """os.environ[...], os.environ.get(...), os.getenv(...) with a GITHUB_ string key, beginning at toks[i] == os."""
    if not (_is(toks[i], tokenize.NAME, "os") and i + 1 < len(toks) and _is(toks[i + 1], tokenize.OP, ".")):
        return
    rest = toks[i + 2 :]
    names = [t.string for t in rest[:3]]
    if names[:1] == ["getenv"] and len(rest) >= 3 and _is(rest[1], tokenize.OP, "("):
        key = _string_value(rest[2])
    elif names[:3] == ["environ", ".", "get"] and len(rest) >= 5 and _is(rest[3], tokenize.OP, "("):
        key = _string_value(rest[4])
    elif names[:2] == ["environ", "["] and len(rest) >= 3:
        key = _string_value(rest[2])
        after = rest[4] if len(rest) >= 5 and _is(rest[3], tokenize.OP, "]") else None
        if after is not None and _is(after, tokenize.OP, "="):
            return
        if i > 0 and _is(toks[i - 1], tokenize.NAME, "del"):
            return
    else:
        return
    if key is not None and key.startswith("GITHUB_"):
        ctx.live(f"reads {key}")


def _string_lists(toks: list[tokenize.TokenInfo], i: int) -> list[str] | None:
    """The values of an all-string list literal opening at toks[i] == '[', else None."""
    if i > 0 and (
        (toks[i - 1].type == tokenize.NAME and not keyword.iskeyword(toks[i - 1].string)) or toks[i - 1].string in {"]", ")"}
    ):
        return None
    values: list[str] = []
    j = i + 1
    while j < len(toks):
        value = _string_value(toks[j])
        if value is None:
            return None
        values.append(value)
        nxt = toks[j + 1].string if j + 1 < len(toks) else ""
        if nxt == "]":
            return values
        if nxt != ",":
            return None
        j += 2
        if j < len(toks) and toks[j].string == "]":
            return values
    return None


def _python_body(body: str, ctx: _Ctx, depth: int) -> None:
    if depth > _MAX_DEPTH:
        ctx.unparseable += 1
        return
    try:
        raw = list(tokenize.generate_tokens(io.StringIO(body).readline))
    except (tokenize.TokenError, SyntaxError):
        ctx.unparseable += 1
        return
    toks = [t for t in raw if t.type not in _SKIP_TOKEN_TYPES]
    for i, tok in enumerate(toks):
        if tok.type == tokenize.NAME and tok.string == "push_context_base" and i + 1 < len(toks):
            if _is(toks[i + 1], tokenize.OP, "("):
                ctx.live("push_context_base call")
        elif tok.type == tokenize.NAME and tok.string == "os":
            _environ_read(toks, i, ctx)
        elif _is(tok, tokenize.OP, "["):
            values = _string_lists(toks, i)
            if values:
                _argv_rules(values, ctx, depth, shell=False)


def findings(row: dict) -> Findings:
    """Portability findings for one registry row; empty for any non-command slot. Never raises."""
    ctx = _Ctx()
    if not isinstance(row, dict) or row.get("primitive_slot") not in _COMMAND_SLOTS:
        return ctx.result()
    spec = row.get("check_spec")
    command = spec.get("command") if isinstance(spec, dict) else None
    if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
        ctx.unparseable += 1
        return ctx.result()
    _argv_rules(command, ctx, 0, shell=False)
    return ctx.result()


def admission_report(candidates: list[dict]) -> tuple[list[str], str]:
    """(failures, counted line) for the added-or-modified candidates."""
    failures: list[str] = []
    examined = refused = unparseable = 0
    for row in candidates:
        if not isinstance(row, dict) or row.get("primitive_slot") not in _COMMAND_SLOTS:
            continue
        examined += 1
        found = findings(row)
        unparseable += found.unparseable
        if found.messages:
            refused += 1
        cid = row.get("check_id", "?")
        failures.extend(f"verification-registry portability: check_id={cid}: {m}" for m in found.messages)
    line = (
        f"Portability (rec-4889): {examined} added-or-modified command-slot row(s) examined, {refused} refused, "
        f"{unparseable} unparseable segment(s) failed open."
    )
    return failures, line
