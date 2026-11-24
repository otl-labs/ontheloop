"""Static lint for hermetic authoring rule (v) (docs/contracts/vp-red-before.yaml ``rule_v_lint``, rec-5547).

Private sibling of validate_vp_replay.py (Decision 128 decompose-by-default). Refuses, on the plan PR, a
``hermetic: true`` pre-deploy step of a diff-ADDED plan that re-runs a whole test file or directory the plan's own
scope creates or modifies, or runs a ruff check/format phase. Never executes anything.

Governing rule: a segment is examined only when it is PLAIN (see ``_analyse``); every segment naming pytest or ruff
that is not PLAIN is counted EXCLUDED, never silently passed and never refused. The command is split by a
quote-aware tokenizer, so the inside of a quoted string is never a segment, and heredoc bodies are cut on raw lines
before tokenizing.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.checks import _common

_PREFIX = "vp-hermetic-rule-v"
_MENTION_RE = re.compile(r"pytest|ruff")
_CODE_WORD_RE = re.compile(r"\bpytest\b|\bruff\b")
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_ASSIGN_RE = re.compile(r"^[A-Za-z_]\w*=")
_INTERP_RE = re.compile(r"^(python[\d.]*|venv-python)$")
_DURATION_RE = re.compile(r"^\d+(\.\d+)?[smhd]?$")
_REDIRECT_OP_RE = re.compile(r"^(\d*>>?|&>>?)$")
_REDIRECT_JOINED_RE = re.compile(r"^(\d*>>?|&>>?)[^<>&$`(){}]+$")
_REDIRECT_DUP_RE = re.compile(r"^\d*>&\d$")
_FORBIDDEN_CHARS = frozenset("$`(){}<>")
_CWD_TOKENS = frozenset({"cd", "pushd", "{", "("})
_PYTHON_FLAGS = frozenset({"-I", "-u", "-B", "-O", "-E", "-s"})
_PYTHON_VALUE_FLAGS = frozenset({"-X", "-W"})
VALUE_OPTIONS = frozenset(
    {
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "-k",
        "-m",
        "-p",
        "-c",
        "-o",
        "--rootdir",
        "--cov",
        "--cov-report",
        "--basetemp",
        "--confcutdir",
        "--junitxml",
        "--junit-xml",
        "--cov-config",
        "--log-file",
        "--report-log",
        "--html",
        "-n",
    }
)

_PYTEST_FIX = (
    "Fix: rewrite the step to pytest node or class selectors (path::test_name), which keeps hermetic: true and "
    "proves the node exists (Decision 148 point 3); set hermetic: false only for a step that genuinely needs the "
    "whole file or directory."
)
_RUFF_FIX = "Fix: set hermetic: false or drop the step (the tier already runs ruff)."


@dataclass
class ScanResult:
    """One command's outcome: ("pytest", in-scope targets) / ("ruff", []) findings, EXCLUDED and directory counts."""

    findings: list[tuple[str, list[str]]] = field(default_factory=list)
    excluded: int = 0
    dir_targets: int = 0


def _cut_heredocs(command: str) -> tuple[str, bool]:
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = _HEREDOC_RE.search(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        out.append(lines[i][: match.start()])
        word = match.group(2)
        j = i + 1
        while j < len(lines) and lines[j].lstrip("\t").rstrip() != word:
            j += 1
        if j >= len(lines):
            return "\n".join(out), True
        i = j + 1
    return "\n".join(out), False


def _quoted_end(text: str, start: int, quote: str) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\" and quote == '"':
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    raise ValueError("unterminated quote")


def _segments(text: str) -> list[list[tuple[str, bool]]]:
    """Quote-aware split into segments of (token, was_quoted); &&, ||, ;, |, a bare & and newline separate segments."""
    segments: list[list[tuple[str, bool]]] = []
    cur: list[tuple[str, bool]] = []
    buf: list[str] = []
    state = {"has": False, "quoted": False}

    def flush() -> None:
        if state["has"]:
            cur.append(("".join(buf), state["quoted"]))
        buf.clear()
        state["has"] = state["quoted"] = False

    def end_segment() -> None:
        flush()
        if cur:
            segments.append(list(cur))
            cur.clear()

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c in "'\"":
            j = _quoted_end(text, i, c)
            body = text[i + 1 : j]
            buf.append(re.sub(r"\\(.)", r"\1", body) if c == '"' else body)
            state["has"] = state["quoted"] = True
            i = j + 1
            continue
        if c == "\\":
            if nxt and nxt != "\n":
                buf.append(nxt)
                state["has"] = True
            i += 2
            continue
        if c in " \t\r":
            flush()
        elif c == "#" and not buf and not state["has"]:
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c in "\n;":
            end_segment()
        elif c == "|":
            end_segment()
            i += 1 if nxt == "|" else 0
        elif c == "&":
            if nxt == "&":
                end_segment()
                i += 1
            elif (buf and buf[-1] == ">") or nxt == ">":
                buf.append(c)
                state["has"] = True
            else:
                end_segment()
        elif c in "()":
            flush()
            cur.append((c, False))
        else:
            buf.append(c)
            state["has"] = True
        i += 1
    end_segment()
    return segments


def _strip_redirects(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _REDIRECT_OP_RE.match(tok):
            i += 2
        elif _REDIRECT_JOINED_RE.match(tok) or _REDIRECT_DUP_RE.match(tok):
            i += 1
        else:
            out.append(tok)
            i += 1
    return out


def _skip_env(prefix: list[str], i: int) -> int | None:
    """Index after an ``env`` wrapper's options and assignments, or None for any other option."""
    while i < len(prefix):
        cur = prefix[i]
        if cur in ("-i", "--ignore-environment"):
            i += 1
        elif cur in ("-u", "--unset"):
            i += 2
        elif _ASSIGN_RE.match(cur):
            if cur.startswith("PYTEST_ADDOPTS="):
                return None
            i += 1
        elif cur.startswith("-"):
            return None
        else:
            break
    return i


def _skip_timeout(prefix: list[str], i: int) -> int | None:
    while i < len(prefix) and prefix[i].startswith("-"):
        i += 2 if prefix[i] in ("-k", "-s", "--kill-after", "--signal") else 1
    if i >= len(prefix) or not _DURATION_RE.match(prefix[i]):
        return None
    return i + 1


def _skip_interpreter(prefix: list[str], i: int) -> int:
    while i < len(prefix):
        cur = prefix[i]
        if cur in _PYTHON_FLAGS:
            i += 1
        elif cur in _PYTHON_VALUE_FLAGS:
            i += 2
        else:
            break
    return i + 1 if i < len(prefix) and prefix[i] == "-m" else i


def _prefix_ok(prefix: list[str]) -> bool:
    """True when every token before the marker is from the recognised prefix set."""
    i: int | None = 0
    while i is not None and i < len(prefix):
        tok = prefix[i]
        if tok == "!":
            i += 1
        elif _ASSIGN_RE.match(tok):
            i = None if tok.startswith("PYTEST_ADDOPTS=") else i + 1
        elif tok == "env":
            i = _skip_env(prefix, i + 1)
        elif tok == "timeout":
            i = _skip_timeout(prefix, i + 1)
        elif _INTERP_RE.match(posixpath.basename(tok)):
            i = _skip_interpreter(prefix, i + 1)
        else:
            i = None
    return i is not None


def _find_marker(tokens: list[str]) -> tuple[str, int] | None:
    for i, tok in enumerate(tokens):
        base = posixpath.basename(tok)
        if base == "pytest":
            return "pytest", i
        if base == "ruff" and i + 1 < len(tokens) and tokens[i + 1] in ("check", "format"):
            return "ruff", i
    return None


def _has_forbidden(tokens: list[str]) -> bool:
    return any(_FORBIDDEN_CHARS & set(tok) for tok in tokens)


def _analyse(seg: list[tuple[str, bool]], cwd_unknown: bool) -> tuple[str, list[str]] | None:
    """("pytest", positional tokens) or ("ruff", []) for a PLAIN segment, else None (counted EXCLUDED by the caller)."""
    if cwd_unknown or any(quoted and _CODE_WORD_RE.search(tok) for tok, quoted in seg):
        return None
    toks = [tok for tok, _ in seg]
    marker = _find_marker(toks)
    if marker is None:
        return None
    arm, idx = marker
    prefix = _strip_redirects(toks[:idx])
    if not _prefix_ok(prefix) or _has_forbidden(prefix):
        return None
    if arm == "ruff":
        return "ruff", []
    rest = _strip_redirects(toks[idx + 1 :])
    if _has_forbidden(rest) or any(t == "--pyargs" or t.startswith(("@", "PYTEST_ADDOPTS=")) for t in rest):
        return None
    positionals: list[str] = []
    skip_value = False
    for tok in rest:
        if skip_value:
            skip_value = False
        elif tok.startswith("-"):
            skip_value = tok in VALUE_OPTIONS
        else:
            positionals.append(tok)
    return "pytest", positionals


def _is_test_file(path: str) -> bool:
    base = posixpath.basename(path)
    return (base.startswith("test_") and base.endswith(".py")) or base.endswith("_test.py") or base == "conftest.py"


def _resolve_target(target: str, scope_files: set[str], root_abs: str) -> list[str]:
    """Repo-relative candidates for one pytest target; a glob matches the disk and the scope set."""
    path = target
    if os.path.isabs(path):
        path = os.path.relpath(path, root_abs)
    norm = os.path.normpath(path).replace(os.sep, "/")
    if norm == ".." or norm.startswith("../"):
        return []
    if not any(ch in norm for ch in "*?["):
        return [norm]
    matches = glob.glob(os.path.join(root_abs, norm), recursive=True)
    found = {os.path.relpath(m, root_abs).replace(os.sep, "/") for m in matches}
    found |= {f for f in scope_files if fnmatch.fnmatch(f, norm)}
    return sorted(found)


def _in_scope(cand: str, scope_files: set[str]) -> bool:
    if cand in scope_files:
        return True
    prefix = "" if cand == "." else cand + "/"
    return any(f.startswith(prefix) and _is_test_file(f) for f in scope_files)


def step_rule_v_scan(command: str, scope_files: set[str], root: Path) -> ScanResult:
    """Scan one command; scope_files is the set of Create/Modify scope paths."""
    result = ScanResult()
    text, unterminated = _cut_heredocs(command or "")
    result.excluded += int(unterminated)
    try:
        segments = _segments(text)
    except ValueError:
        result.excluded += int(bool(_MENTION_RE.search(text)))
        return result
    root_abs = os.path.abspath(root)
    cwd_unknown = False
    for seg in segments:
        raw = " ".join(tok for tok, _ in seg)
        if _MENTION_RE.search(raw):
            outcome = _analyse(seg, cwd_unknown)
            if outcome is None:
                result.excluded += 1
            elif outcome[0] == "ruff":
                result.findings.append(("ruff", []))
            else:
                _pytest_findings(outcome[1], scope_files, root_abs, result)
        cwd_unknown = cwd_unknown or any(tok in _CWD_TOKENS for tok, _ in seg)
    return result


def _pytest_findings(positionals: list[str], scope_files: set[str], root_abs: str, result: ScanResult) -> None:
    targets = [p for p in positionals if "::" not in p] if positionals else ["."]
    hits: list[str] = []
    for target in targets:
        for cand in _resolve_target(target, scope_files, root_abs):
            if cand == "." or os.path.isdir(os.path.join(root_abs, cand)):
                result.dir_targets += 1
            if _in_scope(cand, scope_files):
                hits.append(cand)
    if hits:
        result.findings.append(("pytest", sorted(set(hits))))


def _scan_plan(doc, root: Path) -> tuple[list[tuple[int, str]], int, list[int]]:
    scope = {posixpath.normpath(s.file) for s in doc.scope if s.action in ("Create", "Modify")}
    findings: list[tuple[int, str]] = []
    excluded = 0
    warned: list[int] = []
    for step in doc.verification_plan:
        if step.phase != "pre-deploy" or not step.hermetic:
            continue
        res = step_rule_v_scan(step.command, scope, root)
        excluded += res.excluded
        if res.dir_targets >= 2:
            warned.append(step.step)
        for kind, targets in res.findings:
            if kind == "ruff":
                reason = f"hermetic: true step runs a ruff check/format phase (rule (v)). {_RUFF_FIX}"
            else:
                reason = (
                    f"hermetic: true step re-runs whole in-scope test target(s) {', '.join(targets)} (rule (v); a -k "
                    f"filter does not exempt a whole target). {_PYTEST_FIX}"
                )
            findings.append((step.step, reason))
    return findings, excluded, warned


def plan_rule_v_findings(doc, root: Path) -> list[tuple[int, str]]:
    """(step id, reason) for each hermetic pre-deploy step of a loaded plan that rule (v) refuses."""
    return _scan_plan(doc, root)[0]


def rule_v_pre_pass(plan_files: list[str], root: Path, added: set[str], resolved: set[str], failed: list[str]) -> None:
    """Refuse rule (v) steps of ADDED plans; advise on resolved-not-added plans; count everything else.

    A resolved-not-added plan never fails (an implementer cannot flip hermetic, implement-scope-boundary.yaml
    implementing_plan_bookkeeping). The ``vp-hermetic-rule-v`` prefix is load-bearing: ``vp-replay`` suppresses the
    implement leg's PASS line and ``vp-red-before-lint`` is asserted absent by negated-sweep tests.
    """
    skipped = excluded = 0
    advisory: list[str] = []
    for plan_rel in plan_files:
        if plan_rel not in added and plan_rel not in resolved:
            skipped += 1
            continue
        try:
            doc = _common.load_plan(plan_rel, root)
        except ImportError as exc:
            failed.append(f"{_PREFIX} {plan_rel}: could not import scripts.roadmap.plan_document: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 -- schema validity is validate_plan_documents' concern
            print(f"  SKIP: {plan_rel}: load error ({exc}) -- not double-reported here")
            continue
        findings, plan_excluded, warned = _scan_plan(doc, root)
        excluded += plan_excluded
        if plan_rel in added:
            for step, reason in findings:
                failed.append(f"{_PREFIX} {plan_rel}:{step}: {reason}")
            for step in warned:
                print(f"  WARN: {plan_rel}:{step}: hermetic step names two or more directory targets (rule (v)).")
        elif findings:
            advisory.append(f"{plan_rel}:{','.join(str(s) for s, _ in findings)}")
    if skipped:
        print(f"  EXCLUDED: {skipped} diff-present plan(s) neither added nor resolved -- rule (v) lint not applied.")
    if excluded:
        print(f"  EXCLUDED: {excluded} not-plain pytest/ruff segment(s) -- rule (v) lint did not examine them.")
    if advisory:
        print(f"  ADVISORY: {len(advisory)} resolved plan(s) carry a rule (v) step (never refused): {'; '.join(advisory)}")
