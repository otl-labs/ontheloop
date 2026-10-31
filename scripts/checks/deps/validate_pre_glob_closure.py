"""BLOCKING auditor: does each glob-gated --pre check's pre_globs cover its own code closure?

Motivating defect class (D2-3 Finding 4, rec-3289): a `--pre` Entry declares `pre_globs`, the
check's implementation then grows a transitive first-party import that none of those globs match,
and from that moment a diff touching the new input silently SKIPS the check in the fast tier.
Under-inclusion in a gate input is fail-OPEN, which Decision 135's posture forbids. Waves 1 and 2
found 12 such defective globs BY HAND; this check finds them mechanically.

What it does NOT do (the rejected framing): it does not replace `pre_globs` with a runtime
derivation. The manifest stays the single greppable, statically-auditable declaration -- Decision
169's audit surface is untouched and no gate input becomes a runtime computation. Measured reason:
over the FULL graph `nx.descendants()` returns an identical closure for every check module in the
repo, because Decision 169's contractual manifest -> check bare-string-literal edge collapses every
gated check into one strongly-connected component. A derived glob would therefore say "every .py
under src/ and scripts/" for all of them -- selection-identical to deleting `pre_globs`.

So the closure is taken over IMPORT-KIND EDGES ONLY (scripts.dependency_graph.import_subgraph),
which leaves the driver edge in the graph for the selection derivation that needs it while giving
this auditor the code-level view it needs. Honest accounting of the trade: this substitutes one
hand-maintained surface (`_PRUNED_EDGES`) for part of another. It is justified because that list
is a tested declaration carrying a stated reason per entry, and because a wrong entry reddens a
build rather than silently narrowing selection.

Staging (D2-3 migration path, now COMPLETE -- LSA-06). Wave 4a was ADVISORY on both channels
(printed findings, declared Decision 170 accounting, never appended to `failed`). Wave 4b paid the
backlog down: a genuinely missing glob got added, an Entry.module naming no graph node got
corrected, a hub artefact got a reviewed `_PRUNED_EDGES` entry, and a derived structural floor
(see `_module_body_is_trivial`) excluded modules with nothing to declare. Wave 4c -- THIS module,
now -- is the flip: an uncovered closure path APPENDS to `failed`, the same as any other --pre
gate. The genuine backlog measured zero across all seven affected domain manifests before this
flip landed (see the seven `scripts/checks/*/_manifest.py` paydowns in the same diff).

_PRUNED_EDGES additions are themselves change-controlled (rec-3558): a brand-new row, or an
existing row's target count growing, requires a `# pruned-edge-approved: dec-NNN <reason>` marker
on the row's own line span, checked by `_marker_guard.check_diff` against a `RegistrySpec` declared
in THIS module (never `_marker_guard.py` itself -- a new binding needs no edit there). Removing a
row, or shrinking one, is always free -- only WIDENING the auditor's blind spot is gated.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping

from scripts.checks import _common, _marker_guard, registry
from scripts.checks._schema import Entry

if TYPE_CHECKING:  # networkx is imported only by the deferred scripts.dependency_graph import
    import networkx as nx

# Reviewed hub-artefact exclusions for the pay-down (wave 4b). Maps an importing module to the
# import targets that are DROPPED from this auditor's traversal -- the pruning wave 2's prototype
# performed silently, promoted to an explicit, tested, per-edge declaration.
#
# WAVE 4b DISCHARGE: wave 4a landed advisory precisely so the real backlog was MEASURED before any
# edge was excluded (930 findings across 40 of 47 gated checks), and the two reviewed hub rows
# below are the one-time allowance that measurement authorised -- never the pay-down method, which
# is ADDING GLOBS to the checks that under-declare them. Add an entry only with an inline comment
# stating why the edge has no behavioural bearing on the importing check, and naming the condition
# that would remove the row again. A NEW row, or an existing row's target count growing, now needs
# a `# pruned-edge-approved: dec-NNN <reason>` marker on the row's own line span (rec-3558) --
# removing a row, or shrinking one, stays free.
_PRUNED_EDGES: dict[str, tuple[str, ...]] = {
    # 17 _manifest targets: Decision 169 registration fan-out -- registry imports each only to assemble _ALL_ENTRIES.
    # The _schema target is a DISTINCT argument: a pure declaration module (Entry, SEGMENT_TOKENS) with no first-party
    # imports, so it drops one path and nothing behind it; 0 of the 45 checks that lose it import it directly.
    # REMOVE this row if a check is shown to need _schema or a manifest in its closure, or at the wave-4c flip.
    # COUPLING: the mirror test pins 17 targets EXACTLY, so a PR adding an 18th check domain must extend this row too.
    "scripts.checks.registry": (
        "scripts.checks._schema",
        "scripts.checks.ci_guards._manifest",
        "scripts.checks.contracts._manifest",
        "scripts.checks.decisions._manifest",
        "scripts.checks.deps._manifest",
        "scripts.checks.executor._manifest",
        "scripts.checks.hygiene._manifest",
        "scripts.checks.iam_tf._manifest",
        "scripts.checks.lambda_pkg._manifest",
        "scripts.checks.misc._manifest",
        "scripts.checks.ops_governance._manifest",
        "scripts.checks.prompts._manifest",
        "scripts.checks.prose._manifest",
        "scripts.checks.roadmap._manifest",
        "scripts.checks.sloc._manifest",
        "scripts.checks.structural._manifest",
        "scripts.checks.typing._manifest",
        "scripts.checks.verification._manifest",
    ),
    # A deferred, PLC0415-exempt function-scope import in _common.load_plan. Safe by COVERAGE, not reachability:
    # 3 of its 5 callers declare no globs at all and so always run; the other 2 glob plan_document's whole closure themselves.
    "scripts.checks._common": ("scripts.roadmap.plan_document",),
    # RETIRED (rec-3291 / rec-3563): the former "scripts.checks._budget_recs": ("scripts.ops_data_portal",)
    # row pruned _budget_recs's own direct edge to the rec-filing portal hub. The migration onto
    # scripts.rec_episode's shared find_rec/run_episode primitive gave _budget_recs (and
    # scripts.convergence_health's escalate.py / code_drift.py) a SECOND, independent module-scope
    # path to the same hub (via scripts.rec_episode's own function-scope portal imports inside
    # run_episode). Pruning either edge alone no longer shrinks any gated check's closure -- the
    # other, unpruned edge keeps the hub reachable regardless -- and pruning BOTH simultaneously
    # (as two rows) fails the roster's own per-row inertness pin (rec-3553's
    # test_every_row_key_is_reachable_from_a_gated_closure), which judges each row ALONE and
    # deliberately rejects a pair of rows that are only JOINTLY sufficient. Retired rather than
    # replaced: the checks that lose this pruning benefit simply show scripts.ops_data_portal in
    # their closure again, same as any other unreviewed hub edge in the backlog the wave-4b/4c
    # program paid down.
}

# Per-check cap on printed paths; the count is always reported in full.
_MAX_PRINTED_PATHS = 8

# Rendered in place of a path when an Entry.module names no node in the import graph -- a finding
# in its own right (see _unmatched_paths), not a silently empty closure.
_UNRESOLVABLE_TEMPLATE = "<module not in the import graph: {module}>"

# The marker token this module's own _PRUNED_EDGES row-addition gate binds to (rec-3558) --
# field semantics live in docs/contracts/marker-grammar.yaml, not restated here.
_PRUNED_EDGE_TOKEN = "pruned-edge-approved"
_PRUNED_EDGE_MARKER_RE = re.compile(rf"#\s*{re.escape(_PRUNED_EDGE_TOKEN)}:\s*dec-(\d+)\s*(.*)$")
_SELF_REL_PATH = "scripts/checks/deps/validate_pre_glob_closure.py"


def _glob_match(path: str, glob: str) -> bool:
    """Behavioural replica of scripts/validate.py::_pre_glob_match, INCLUDING its leading-`**/`
    retry, so the gate and this auditor can never disagree on matching semantics.

    Replicated rather than imported for two reasons: scripts/checks/_common.py's module docstring
    pins the no-scripts.validate-dependency rule (validate.py imports scripts.checks.*, so the
    reverse edge is a cycle); and an import edge to scripts.validate would drag validate.py's
    entire closure into THIS module's closure, which this very check then audits. Equivalence is
    pinned by tests/checks/deps/validate_pre_glob_closure/test_closure_and_floor.py::
    TestGlobMatcherEquivalence.
    """
    if fnmatch.fnmatch(path, glob):
        return True
    return glob.startswith("**/") and fnmatch.fnmatch(path, glob[3:])


def _module_to_repo_path(module: str, root: Path) -> str | None:
    """Inverse of scripts.dependency_graph._file_to_module for the two node shapes build_graph
    produces: a plain module, and a package node whose file is its `__init__.py`."""
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return None


def _module_body_is_trivial(repo_path: str, root: Path) -> bool:
    """The derived structural floor (wave 4b pay-down): True iff the module at `repo_path` has an
    AST body that is EMPTY, or contains ONLY a single docstring `Expr` statement -- no other
    executable statement of any kind.

    Keyed on the AST body alone, NEVER on the filename: a non-`__init__.py` module with the same
    trivial shape is excluded too, and a `__init__.py` facade carrying real code (even a single
    `from __future__ import annotations`, itself an executable Import statement) is NOT excluded.
    A module whose sole statement is ONE executable statement with no docstring (`len(body) == 1`
    but that one statement is not a docstring `Expr`) is deliberately NOT trivial -- this is what
    separates the rule from a naive `len(tree.body) <= 1` heuristic, which would wrongly exclude
    it. Never raises: an unreadable or unparseable module is not trivial (so it stays reported,
    matching the fail-loud posture of everything else in this module)."""
    try:
        tree = ast.parse((root / repo_path).read_text(encoding="utf-8"), filename=repo_path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    body = tree.body
    if not body:
        return True
    if len(body) != 1:
        return False
    stmt = body[0]
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _closure_view(root: Path) -> nx.DiGraph:
    """The import-kind-only view of the first-party graph rooted at `root`.

    Reuses build_graph, which is memoized in-process (lru_cache on the resolved root), and layers
    an O(1) subgraph_view on top -- so this never builds a second graph even though
    validate_check_manifests and the affected-set derivation have usually already built the first.
    """
    from scripts.dependency_graph import build_graph, import_subgraph  # noqa: PLC0415

    return import_subgraph(build_graph(repo_root=root))


def _closure_modules(view: nx.DiGraph, module: str) -> set[str]:
    """REFLEXIVE transitive closure of `module` over `view`, minus `_PRUNED_EDGES`.

    Reflexive on purpose: a check whose own defining file is not matched by its own `pre_globs`
    is the most basic recall bug this auditor exists to catch, so the module counts as part of
    the surface it must declare. Returns the empty set for a module absent from the view.
    """
    if module not in view:
        return set()
    seen = {module}
    stack = [module]
    while stack:
        current = stack.pop()
        pruned = _PRUNED_EDGES.get(current, ())
        for successor in view.successors(current):
            if successor in pruned or successor in seen:
                continue
            seen.add(successor)
            stack.append(successor)
    return seen


def _gated_entries() -> list[Entry]:
    """Every manifest Entry that is in --pre AND declares globs, by name. An ungated entry
    (pre_globs is None) always runs, so it has no under-coverage to audit."""
    return sorted(
        (entry for entry in registry._ALL_ENTRIES.values() if entry.pre and entry.pre_globs is not None),
        key=lambda entry: entry.name,
    )


def _uncovered_and_suppressed(entry: Entry, view: nx.DiGraph, root: Path) -> tuple[list[str], list[str]]:
    """Repo-relative closure paths of `entry.module` that none of `entry.pre_globs` matches,
    split by the derived inert-module floor into (reported, suppressed). `reported` is what the
    gate acts on; `suppressed` is PRINTED as a count only, never asserted -- the floor narrows the
    glob-coverage OBLIGATION, it never removes a node from the import graph itself (Decision 135
    point 2(i) keeps __init__.py facades as graph nodes).

    A module ABSENT from the view (a typo'd or relocated `Entry.module`) has NO computable
    closure, so no glob can be shown to cover it -- and the reflexivity guarantee `_closure_modules`
    documents is exactly what a wrong module identity forfeits. Reporting the module itself as the
    single unresolvable finding keeps that case out of the "clean" bucket, and it is never
    eligible for the inert-module floor (an unresolvable identity is not a trivial module).
    """
    if entry.module not in view:
        return [_UNRESOLVABLE_TEMPLATE.format(module=entry.module)], []
    globs = entry.pre_globs or ()
    paths = sorted(filter(None, (_module_to_repo_path(m, root) for m in _closure_modules(view, entry.module))))
    uncovered = [path for path in paths if not any(_glob_match(path, glob) for glob in globs)]
    reported = [path for path in uncovered if not _module_body_is_trivial(path, root)]
    suppressed = [path for path in uncovered if _module_body_is_trivial(path, root)]
    return reported, suppressed


def _unmatched_paths(entry: Entry, view: nx.DiGraph, root: Path) -> list[str]:
    """Post-floor reported closure paths -- see `_uncovered_and_suppressed`. Kept as its own
    function (rather than inlined at each call site) because it is the direct target of VP step
    10's per-domain residual measurement and several test call sites."""
    reported, _suppressed = _uncovered_and_suppressed(entry, view, root)
    return reported


def uncovered_after_edges(edges: Iterable[tuple[str, str]], new_paths: Mapping[str, str], root: Path) -> dict[str, list[str]]:
    """Plan-time seam: per gated Entry name, the closure paths no pre_glob matches that the unmodified view does
    not already report, once each declared (importer, imported) edge is added to a copy of the import view.

    `new_paths` maps a dotted module name absent from the tree to the repo path a plan will create for it
    (`_module_to_repo_path` returns None for a missing file and would drop it). Read-only; the registered check's
    behaviour is unchanged.
    """
    import networkx as nx  # noqa: PLC0415

    base = _closure_view(root)
    view = nx.DiGraph(base)
    view.add_edges_from(edges)
    found: dict[str, list[str]] = {}
    for entry in _gated_entries():
        if entry.module not in view:
            continue
        known = set(_unmatched_paths(entry, base, root))
        globs = entry.pre_globs or ()
        paths = sorted(
            {p for p in (_module_to_repo_path(m, root) or new_paths.get(m) for m in _closure_modules(view, entry.module)) if p}
        )
        new = [
            p
            for p in paths
            if p not in known and not any(_glob_match(p, g) for g in globs) and not _module_body_is_trivial(p, root)
        ]
        if new:
            found[entry.name] = new
    return found


def _pruned_edges_node(tree: ast.Module) -> ast.AnnAssign | None:
    return next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "_PRUNED_EDGES"
        ),
        None,
    )


def _pruned_edge_marker_on_span(lines: list[str], start: int, end: int) -> tuple[str | None, str | None]:
    """Scan a row's own line span [start, end] (1-indexed, inclusive -- the dict key's line
    through its value tuple's closing line, which for a real multi-line tuple is the same
    physical line as the trailing `),`) for a `# pruned-edge-approved: dec-NNN <reason>` marker.
    Mirrors validate_tier_demotion_markers.py's `_marker_on_span` entry-line-span placement rule.
    A marker with no reason text parses as no marker at all (this token's reason_required: true)."""
    for lineno in range(start, end + 1):
        if not (1 <= lineno <= len(lines)):
            continue
        match = _PRUNED_EDGE_MARKER_RE.search(lines[lineno - 1])
        if not match:
            continue
        reason = match.group(2).strip()
        if not reason:
            continue
        return f"dec-{match.group(1)}", reason
    return None, None


def _pruned_edges_entries(text: str) -> dict[str, _marker_guard.MarkerEntry]:
    """spec.extractor for the _PRUNED_EDGES row-addition gate (rec-3558): one MarkerEntry per row
    of the `_PRUNED_EDGES` dict literal found in `text` (this module's own source, at either base
    or head), keyed by the row's key string and valued by its TARGET COUNT. A brand-new row, or an
    existing row's target count growing, is what `_marker_guard.check_diff`'s gated_direction="up"
    comparison catches; a row or a target REMOVED shrinks the count (or removes the key entirely,
    which check_diff never even visits) and is never gated. Never raises: an unparseable `text`
    (base ref content that predates this module, or a malformed fixture) yields an empty map."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}
    node = _pruned_edges_node(tree)
    if node is None or not isinstance(node.value, ast.Dict):
        return {}
    lines = text.splitlines()
    entries: dict[str, _marker_guard.MarkerEntry] = {}
    for key_node, value_node in zip(node.value.keys, node.value.values):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        target_count = len(value_node.elts) if isinstance(value_node, ast.Tuple) else 0
        end = getattr(value_node, "end_lineno", None) or key_node.lineno
        marker, reason = _pruned_edge_marker_on_span(lines, key_node.lineno, end)
        entries[key_node.value] = _marker_guard.MarkerEntry(float(target_count), marker, reason)
    return entries


_PRUNED_EDGE_SPEC = _marker_guard.RegistrySpec(
    rel_path=_SELF_REL_PATH,
    token=_PRUNED_EDGE_TOKEN,
    gated_direction="up",
    extractor=_pruned_edges_entries,
    gates_new_entry=lambda _value: True,
    label="_PRUNED_EDGES row addition (rec-3558)",
    reason_required=True,
)


def _pruned_edges_row_addition_violations() -> list[str]:
    """Violation strings for an unauthorized _PRUNED_EDGES row addition or target-count growth --
    empty when clean. A thin call-site wrapper around `_marker_guard.check_diff` so the audit body
    stays a plain call, matching every other marker-gated registry's own check function shape."""
    return _marker_guard.check_diff(_PRUNED_EDGE_SPEC)


def _audit() -> tuple[list[str], int]:
    """The audit proper. Split out so the registered check can wrap it in the Decision 55
    loud-skip guard, and so the banner still prints when that guard fires. Returns
    (findings, suppressed_count): `findings` is what the caller appends to `failed` (BLOCKING,
    LSA-06); `suppressed_count` is printed as pure telemetry, never asserted."""
    entries = _gated_entries()
    findings: list[str] = []
    if not entries:
        print("  PASS: no --pre entry declares pre_globs.")
        registry.examined(0, unit="gated_pre_checks")
        return findings, 0

    root = _common.ROOT
    view = _closure_view(root)

    total_reported = 0
    total_suppressed = 0
    with_findings = 0
    for entry in entries:
        reported, suppressed = _uncovered_and_suppressed(entry, view, root)
        total_suppressed += len(suppressed)
        if not reported:
            continue
        with_findings += 1
        total_reported += len(reported)
        findings.append(f"{entry.name}: {len(reported)} closure path(s)/module(s) not matched by its declared pre_globs")
        print(f"  {entry.name}: {len(reported)} closure path(s)/module(s) not matched by its declared pre_globs")
        for path in reported[:_MAX_PRINTED_PATHS]:
            print(f"      - {path}")
        if len(reported) > _MAX_PRINTED_PATHS:
            print(f"      ... and {len(reported) - _MAX_PRINTED_PATHS} more")

    if with_findings:
        print(
            f"  FAIL: {with_findings} of {len(entries)} glob-gated --pre check(s) have at least one uncovered "
            f"closure path or unresolvable module ({total_reported} finding(s) total). Add the missing glob to the "
            "check's Entry, fix the Entry.module that names no graph node, or record a reviewed hub-artefact edge in "
            "validate_pre_glob_closure._PRUNED_EDGES (row/target additions are themselves marker-gated)."
        )
    else:
        print(f"  PASS: all {len(entries)} glob-gated --pre check(s) declare globs covering their own import closure.")

    print(
        f"  {total_suppressed} closure path(s) suppressed by the derived inert-module floor (informational, never asserted)."
    )

    row_violations = _pruned_edges_row_addition_violations()
    if row_violations:
        print("  _PRUNED_EDGES row-addition violations:")
        for v in row_violations:
            print(f"    - {v}")
        findings.extend(row_violations)

    registry.examined(len(entries), unit="gated_pre_checks")
    return findings, total_suppressed


@registry.register("validate_pre_glob_closure", owner="platform")
def validate_pre_glob_closure(failed: list[str]) -> None:
    """Fail, per glob-gated --pre check, when its pre_globs do not cover its own import closure
    (D2-3, LSA-06). Also gates an unauthorized _PRUNED_EDGES row addition or target-count growth
    (rec-3558).

    BLOCKING on both channels since the wave-4c flip: `failed` gains one entry when any glob-gated
    check under-declares its closure or the _PRUNED_EDGES roster grows without authorization, and
    `registry.failure_detail` carries the itemized list. The Decision 55 loud-skip guard is
    unchanged -- nothing wraps a check body (scripts/checks/validation_result.py's
    dispatch_recording calls fn(failed) bare, and validate.py's --pre loop does not wrap the
    dispatch either), so an internal error here must SKIP rather than raise or silently pass.

    Decision 170 accounting on all three reachable exits -- examined(0) for an empty gated roster
    (an empty DOMAIN, not a skip, per check-accounting.yaml's discrimination rule),
    examined(len(entries)) for a real run, and skipped(reason) when the audit could not complete.
    """
    print("\n=== Pre-glob closure audit (D2-3 / rec-3289, LSA-06) ===")
    try:
        findings, _suppressed = _audit()
    except Exception as exc:  # noqa: BLE001 -- Decision 55: a probe failure must SKIP loudly, never raise or silently pass
        print(
            f"  SKIP (LOUD, Decision 55): the pre-glob closure audit raised {exc!r} and was abandoned. "
            "Nothing was audited this run; the gate is NOT failed and the fast tier continues."
        )
        registry.skipped(f"pre-glob closure audit raised {exc!r}")
        return
    if findings:
        failed.append(f"Pre-glob closure audit: {len(findings)} finding(s)")
        registry.failure_detail(findings)
