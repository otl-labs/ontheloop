"""Flags a module that filters a `named("<verb>")` RESULT on a key that verb does not project
(rec-3291 / rec-3563 class guard, Decision 170).

Root cause this guards against: a call site binds `X = reader.named("<verb>")` and later filters
an element of `X` on a key (`elem.get("key")` / `elem["key"]`) the verb's declared projection
(src.common.ducklake_reader_client.VERB_FIELDS) does not include -- the exact shape that made
scripts.convergence_health.escalate's pre-migration dedup lookup filter `source`/`status` against
`open_recs`, a verb that projects neither, so the filter always matched nothing.

DATA-FLOW linked, not module-level co-occurrence: the scan is per-function-scope. It tracks a
local `name = X.named("<verb>", ...)` assignment (or the `X.named(...) or []` fallback idiom),
then looks ONLY at accesses on names bound to an ELEMENT of that list within the SAME function
(a `for elem in name:` loop, or a `[... for elem in name ...]` comprehension) -- never at
accesses anywhere else in the module or file. Two real consequences of that scoping choice
(verified against this repo's tree, not assumed):

  * scripts/preflight/recs_cache.py's `_derive_followon_recs` and `_derive_open_critical_recs` filter a
    `rows` FUNCTION PARAMETER (the bounded signal rows) that is never itself assigned from a
    `named()` call in that function -- a data path the per-function scan never connects.
  * scripts/ci/reconcile_target.py's `validate_rec_id_open` reads `rows[0].get("status")` from
    `rows = reader(rec_id)`, where `reader` is an injected PARAMETER, not a `named()` call in
    that function -- the closure that actually calls `.named("rec_by_id", ...)` lives in a
    DIFFERENT function (`_default_reader`'s nested `_call`). The scan's function-scope boundary
    means it never traces into that closure; it simply finds no verb bound to `rows` in
    `validate_rec_id_open`'s own scope and moves on. Tracing through such an indirection is
    deliberately out of scope for this guard -- a documented blind spot, not a claimed strength.

Four live input classes, each independently verified against the tree at authoring time:
  (a) SENTINEL verbs (`VERB_FIELDS[verb] is ALL_TABLE_COLUMNS`, e.g. `rec_by_id`) project every
      column -- checked via identity BEFORE any `in` membership test, so a sentinel entry never
      raises TypeError and is never flagged.
  (b) A verb absent from VERB_FIELDS entirely (an unregistered verb name) is
      recorded as unresolved and never flagged -- the guard cannot judge a verb it cannot
      resolve, and refusing to guess is safer than a false negative OR a false positive.
  (c) A result reached only through a callable indirection (reconcile_target.py above) is never
      connected, by construction of the per-function scan -- not because of anything special
      about the verb it happens to use.
  (d) The migrated tree itself (post scripts.rec_episode.find_rec) filters only via
      `current_state`, which this guard does not model at all (VERB_FIELDS is `named()`-verb
      projection only) -- so the migrated call sites are invisible to this scan, correctly.

Declares examined()/skipped() on every reachable exit path (Decision 170): failed / skipped (only
unresolved verbs encountered) / vacuous (nothing found) / enforced (clean pass).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, Optional

from scripts.checks import _common, registry
from src.common.ducklake_reader_client import ALL_TABLE_COLUMNS, VERB_FIELDS

_SCAN_ROOTS: tuple[str, ...] = ("scripts",)
_EXCLUDE_DIR_PARTS = frozenset({"tests", "__pycache__"})


def _iter_source_files(root: Path) -> Iterator[Path]:
    for base in _SCAN_ROOTS:
        base_dir = root / base
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.rglob("*.py")):
            rel_parts = path.relative_to(root).parts
            if any(part in _EXCLUDE_DIR_PARTS for part in rel_parts):
                continue
            yield path


def _literal_str(node: Optional[ast.expr]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _named_verb_call(node: ast.expr) -> Optional[str]:
    """If *node* is (or reduces to) `<expr>.named("<verb>", ...)`, return the verb literal.

    Handles the `reader.named("verb") or []` fallback idiom used throughout this repo
    (e.g. scripts/convergence_health/escalate.py's pre-migration `_fetch_open_recs`) by
    unwrapping a top-level `or` BoolOp and checking each operand.
    """
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        for value in node.values:
            verb = _named_verb_call(value)
            if verb is not None:
                return verb
        return None
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "named"):
        return None
    if not node.args:
        return None
    return _literal_str(node.args[0])


def _own_scope_nodes(func_node: ast.AST) -> Iterator[ast.AST]:
    """Yield every node within *func_node*'s own scope, not descending into a nested def's or
    lambda's body (each such nested scope is analyzed separately when the top-level walk reaches
    it as its own FunctionDef/AsyncFunctionDef)."""
    stack = list(ast.iter_child_nodes(func_node))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _key_accesses_on(node: ast.AST, target_name: str) -> list[tuple[int, str]]:
    """Every `.get("key")` / `["key"]` literal-key access on Name(target_name) inside *node*."""
    found: list[tuple[int, str]] = []
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "get"
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == target_name
            and sub.args
        ):
            key = _literal_str(sub.args[0])
            if key is not None:
                found.append((sub.lineno, key))
        elif isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id == target_name:
            key = _literal_str(sub.slice)
            if key is not None:
                found.append((sub.lineno, key))
    return found


class _ScanResult:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.unresolved_verbs: set[str] = set()
        self.examined = 0


def _check_target(target: str, verb: str, scope_node: ast.AST, rel: str, out: _ScanResult) -> None:
    fields = VERB_FIELDS.get(verb)
    if fields is None:
        out.unresolved_verbs.add(verb)
        return
    for lineno, key in _key_accesses_on(scope_node, target):
        out.examined += 1
        if fields is ALL_TABLE_COLUMNS or not isinstance(fields, tuple):
            continue  # sentinel verb: full projection, never out of bounds.
        if key not in fields:
            out.violations.append(
                f"{rel}:{lineno}: filters named({verb!r}) result on key {key!r}, which that verb "
                f"does not project (VERB_FIELDS[{verb!r}] = {fields!r})"
            )


def _collect_verb_vars(func_node: ast.AST) -> dict[str, str]:
    """Every local `name = X.named("<verb>", ...)` binding in *func_node*'s own scope."""
    verb_vars: dict[str, str] = {}
    for node in _own_scope_nodes(func_node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            verb = _named_verb_call(node.value)
            if verb is not None:
                verb_vars[node.targets[0].id] = verb
    return verb_vars


def _scan_for_loop(node: ast.For | ast.AsyncFor, verb_vars: dict[str, str], rel: str, out: _ScanResult) -> None:
    if not (isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Name)):
        return
    verb = verb_vars.get(node.iter.id)
    if verb is None:
        return
    for stmt in (*node.body, *node.orelse):
        _check_target(node.target.id, verb, stmt, rel, out)


def _scan_comprehension(node: ast.AST, verb_vars: dict[str, str], rel: str, out: _ScanResult) -> None:
    generators = getattr(node, "generators", ())
    value_exprs = (node.key, node.value) if isinstance(node, ast.DictComp) else (getattr(node, "elt", None),)
    for gen in generators:
        if not (isinstance(gen.target, ast.Name) and isinstance(gen.iter, ast.Name)):
            continue
        verb = verb_vars.get(gen.iter.id)
        if verb is None:
            continue
        for expr in (*value_exprs, *gen.ifs):
            if expr is not None:
                _check_target(gen.target.id, verb, expr, rel, out)


def _scan_function(func_node: ast.AST, rel: str, out: _ScanResult) -> None:
    verb_vars = _collect_verb_vars(func_node)
    if not verb_vars:
        return

    for node in _own_scope_nodes(func_node):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            _scan_for_loop(node, verb_vars, rel, out)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            _scan_comprehension(node, verb_vars, rel, out)


def scan_paths(paths: list[Path], root: Path) -> _ScanResult:
    """Scan *paths* (relative to *root*) for the defect shape. Exposed for the mirror test, which
    calls this directly against a curated file list rather than the full registered check's
    repo-wide walk (`_iter_source_files`)."""
    out = _ScanResult()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix() if path.is_absolute() else str(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _scan_function(node, rel, out)
    return out


@registry.register(name="validate_episode_lookup_projection")
def validate_episode_lookup_projection(failed: list[str]) -> None:
    """Fail when a module filters a named() verb's result on a key that verb does not project."""
    print("\n=== Episode-lookup named() projection guard (rec-3291 / rec-3563) ===")
    root = _common.ROOT
    result = scan_paths(list(_iter_source_files(root)), root)

    if result.violations:
        for v in sorted(result.violations):
            print(f"  - {v}")
        failed.append("Episode-lookup named() projection guard")
        registry.examined(result.examined, unit="named()_key_accesses")
        return

    if result.examined == 0:
        if result.unresolved_verbs:
            reason = (
                f"no resolvable named() key-filter found; unresolved verb(s) referenced: {sorted(result.unresolved_verbs)}"
            )
            registry.skipped(reason)
            print(f"  SKIPPED: {reason}")
        else:
            registry.examined(0, unit="named()_key_accesses")
            print("  PASS (vacuous): no named() result key-filter found under scripts/.")
        return

    if result.unresolved_verbs:
        print(
            f"  NOTE: unresolved verb(s) referenced elsewhere in this scan (not judged, never "
            f"silently dropped): {sorted(result.unresolved_verbs)}"
        )
    registry.examined(result.examined, unit="named()_key_accesses")
    print(f"  PASS: {result.examined} named()-result key access(es) examined, all within projection.")
