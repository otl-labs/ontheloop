"""Ad-hoc Decision-entry reads in real-log test readers never rise against the base (rec-5638).

A test that isolates one entry of the real docs/DECISIONS.md by hand (split on a titled heading, a regex over
'## Decision N', a filtered iter_decision_sections loop) breaks when a chain consolidation turns that entry into a
stub. tests.fixtures.decision_entry carries the sanctioned read: decision_entry for a test's own Decision,
live_block or resolve_entry for another one. This check freezes today's count per changed test file and converts
nothing. The per-chain probe (docs/contracts/decision-compaction.yaml procedure step 1) stays the real guard; this
check guards the reading discipline only.

Known blind spots: the real-log gate misses os.path.join with open(), a relative open('docs/DECISIONS.md') and a
root imported at function scope; the shapes miss aliased re functions (from re import search, import re as regex)
and an aliased iter_decision_sections. Reverting a PR that converted reads to the fixture is a rise (no marker
route, Decision 187; known_red_shrinks has the same property), so a revert author re-converts. A rename that
rewrites more than half the file falls below git's -M similarity and reads as a delete plus an add, so its reads
count as new.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from scripts.checks import _common, registry

LOG = "DECISIONS.md"
MARKER = "## Decision"
FIXTURE_REL = "tests/fixtures/decision_entry.py"
STR_METHODS = frozenset(
    {"index", "rindex", "find", "rfind", "split", "rsplit", "count", "partition", "rpartition", "startswith", "endswith"}
)
RE_FUNCS = frozenset({"compile", "search", "match", "fullmatch", "findall", "finditer", "split", "sub", "subn"})
SECTIONS = frozenset({"iter_decision_sections", "_iter_decision_sections"})
FIX = (
    "read the entry through tests.fixtures.decision_entry: decision_entry for this test's own Decision, live_block or "
    "resolve_entry for another entry; live_block returns None once the entry is a stub, so assert that branch, never "
    "skip on it; a whole-log walk iterates iter_decision_sections without a number filter"
)
_ESCAPE = re.compile(r"\\.")
_GROUP_SYNTAX = re.compile(r"\(\?P?<[^>]*>|\(\?:")


def _str_const(node: ast.AST, pred: Callable[[str], bool]) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and pred(node.value)


def _names_one_entry(value: str) -> bool:
    tail = _GROUP_SYNTAX.sub("", _ESCAPE.sub("", value.split(MARKER, 1)[1]))
    return bool(re.match(r"\s*\d", tail)) or bool(re.search(r"[A-Za-z]{2,}", tail))


def _interpolates(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant))
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"


def _has_entry_marker(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and _str_const(sub, lambda v: MARKER in v) and _names_one_entry(str(sub.value)):
            return True
        if _interpolates(sub) and any(_str_const(c, lambda v: MARKER in v) for c in ast.walk(sub)):
            return True
    return False


class _Module:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.globals: set[str] = set()
        self.imported: set[str] = set()
        self.log_names: set[str] = set()
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        self._bind(target.id, stmt.value)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                self._bind(stmt.target.id, stmt.value)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.globals.add(stmt.name)
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    name = alias.asname or alias.name.split(".")[0]
                    self.globals.add(name)
                    self.imported.add(name)

    def _bind(self, name: str, value: ast.AST | None) -> None:
        self.globals.add(name)
        if value is not None and _str_const(value, lambda v: v.endswith(LOG)):
            self.log_names.add(name)

    def is_log(self, node: ast.AST) -> bool:
        return _str_const(node, lambda v: v.endswith(LOG)) or (isinstance(node, ast.Name) and node.id in self.log_names)

    def holds_log(self, node: ast.AST) -> bool:
        return any(self.is_log(sub) for sub in ast.walk(node))

    @staticmethod
    def _leftmost(node: ast.AST) -> ast.AST:
        while True:
            if isinstance(node, ast.BinOp):
                node = node.left
            elif isinstance(node, (ast.Attribute, ast.Subscript)):
                node = node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                node = node.func.value
            else:
                return node

    def _rooted(self, base: ast.AST) -> bool:
        if isinstance(base, ast.Name):
            return base.id in self.globals and base.id != "Path"
        if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "Path":
            return not base.args or all(isinstance(a, ast.Constant) for a in base.args) or "__file__" in ast.unparse(base)
        return False

    def _real_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "joinpath":
            return any(self.is_log(a) for a in node.args) and self._rooted(self._leftmost(func.value))
        if isinstance(func, ast.Name) and node.args:
            if func.id == "Path":
                return self.is_log(node.args[0])
            plain = all(isinstance(a, ast.Constant) or (isinstance(a, ast.Name) and a.id in self.log_names) for a in node.args)
            return func.id in self.globals and plain and any(self.is_log(a) for a in node.args)
        return False

    def reads_real_log(self) -> bool:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                if self.holds_log(node.right) and self._rooted(self._leftmost(node.left)):
                    return True
            elif isinstance(node, ast.Call) and self._real_call(node):
                return True
        return False


def _has_group_call(node: ast.AST) -> bool:
    return any(
        isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute) and s.func.attr == "group" for s in ast.walk(node)
    )


def _filters_one_entry(nodes: Sequence[ast.AST]) -> bool:
    return any(
        isinstance(s, ast.Compare) and any(isinstance(o, (ast.Eq, ast.In)) for o in s.ops) and _has_group_call(s)
        for root in nodes
        for s in ast.walk(root)
    )


def _is_sections_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    return name in SECTIONS


def _marked_names(tree: ast.Module) -> set[str]:
    marked: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and _has_entry_marker(node.value):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    marked.add(target.id)
    return marked


def ad_hoc_sites(text: str) -> tuple[list[tuple[int, str]], bool]:
    """(counted (line, shape) sites, whether the module reads the real log). Raises SyntaxError/ValueError on bad source."""
    tree = ast.parse(text)
    mod = _Module(tree)
    marked = _marked_names(tree)

    def hit(arg: ast.AST) -> bool:
        return _has_entry_marker(arg) or (isinstance(arg, ast.Name) and arg.id in marked)

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                keyed = isinstance(node, ast.DictComp) and _has_group_call(node.key)
                if _is_sections_call(gen.iter) and (keyed or _filters_one_entry(gen.ifs)):
                    out.append((gen.iter.lineno, "sections"))
        elif isinstance(node, ast.For):
            if _is_sections_call(node.iter) and _filters_one_entry(node.body):
                out.append((node.iter.lineno, "sections"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            recv = func.value.id if isinstance(func.value, ast.Name) else None
            if func.attr in RE_FUNCS and recv == "re" and node.args and hit(node.args[0]):
                out.append((node.lineno, "regex"))
            elif func.attr in STR_METHODS and recv not in mod.imported:
                if any(hit(a) for a in node.args):
                    out.append((node.lineno, "strmethod"))
    return sorted(out), mod.reads_real_log()


def _safe_sites(text: str) -> tuple[list[tuple[int, str]], bool] | None:
    try:
        return ad_hoc_sites(text)
    except (SyntaxError, ValueError):
        return None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _common.run(["git", *args], cwd=str(root), capture_output=True)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _changed(root: Path, base: str, failed: list[str]) -> list[tuple[str, str]] | None:
    """(old_path, new_path) for each added, modified or renamed tests/*.py plus untracked ones; deletions are ignored."""
    diff = _git(root, "diff", "-z", "--name-status", "-M", base, "--", "tests/*.py")
    if diff.returncode != 0:
        failed.append(f"decision-reads ratchet: git diff against {base} failed: {_decode(diff.stderr).strip()}")
        return None
    tokens = [t for t in _decode(diff.stdout).split("\0") if t]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        status = tokens[i][0]
        if status in ("R", "C"):
            old, new = tokens[i + 1], tokens[i + 2]
            i += 3
        else:
            old = new = tokens[i + 1]
            i += 2
        if status != "D":
            pairs.append((old, new))
    listing = _git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", "tests/*.py")
    if listing.returncode != 0:
        failed.append(f"decision-reads ratchet: untracked listing failed: {_decode(listing.stderr).strip()}")
        return None
    pairs.extend((p, p) for p in _decode(listing.stdout).split("\0") if p)
    return [(old, new) for old, new in pairs if new != FIXTURE_REL]


@registry.register("validate_decision_reads_through_fixture", owner="platform")
def validate_decision_reads_through_fixture(failed: list[str], root: Path | None = None) -> None:
    """Fail when a changed test file that reads the real DECISIONS.md gains ad-hoc single-entry reads over the base."""
    tree = Path(root) if root is not None else _common.ROOT
    try:
        base = _common.status_diff_base(root)
    except RuntimeError as exc:
        failed.append(f"decision-reads ratchet: no usable base: {exc}")
        registry.examined(0, unit="test_files")
        return
    if base is None:
        registry.skipped("no base ref resolves (neither the push-context base nor the merge base with origin/main)")
        return
    pairs = _changed(tree, base, failed)
    if pairs is None:
        registry.examined(0, unit="test_files")
        return
    for old, new in pairs:
        path = tree / new
        head = _safe_sites(_decode(path.read_bytes()))
        if head is None:
            failed.append(f"decision-reads ratchet: {new} does not parse; fix its syntax")
            continue
        shown = _git(tree, "show", f"{base}:{old}")
        base_res = _safe_sites(_decode(shown.stdout)) if shown.returncode == 0 else None
        base_sites, base_gate = base_res if base_res is not None else ([], False)
        if not (head[1] or base_gate):
            continue
        if len(head[0]) > len(base_sites):
            lines = ", ".join(f"{line} ({shape})" for line, shape in head[0])
            failed.append(
                f"{new}: ad-hoc Decision-entry reads rose from {len(base_sites)} to {len(head[0])} (sites at lines "
                f"{lines}); {FIX}"
            )
    registry.examined(len(pairs), unit="test_files")
