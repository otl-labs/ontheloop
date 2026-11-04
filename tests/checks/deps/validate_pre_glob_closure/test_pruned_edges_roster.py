"""Mirror test for the _PRUNED_EDGES roster (scripts/checks/deps/validate_pre_glob_closure.py):
liveness/inertness pins (rec-3553), rec-3558's structural rationale tests, the marker-gated
row-addition leg, and the VP step 12 paydown-direction bound."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import patch

from scripts.checks import _common, _marker_guard
from scripts.checks.deps import validate_pre_glob_closure as vpgc
from scripts.checks.verification.validate_tier_demotion_markers import (
    TierState,
    _base_manifest_paths,
    _batched_base_reader,
    _head_manifest_paths,
    extract_tier_states,
)


class TestPrunedEdgesRoster:
    """The reviewed wave-4b hub roster, pinned against SILENT staleness.

    A row whose key or target no longer exists, or whose target is no longer a SUCCESSOR of its key
    in the live import subgraph, prunes nothing and reads exactly like a correct row. So these
    assertions check resolution AND liveness against the real graph the auditor traverses, carry an
    explicit non-vacuity guard so an emptied roster cannot satisfy them, and are paired with a
    negative control that feeds the detector a deliberately bogus roster. CONTENT and edge liveness
    are pinned here; the live backlog count never is.
    """

    _CAP = 4
    _REGISTRY = "scripts.checks.registry"
    _COMMON = "scripts.checks._common"

    @staticmethod
    def _dead_rows(roster: dict[str, tuple[str, ...]]) -> list[str]:
        """One report per dead row, in the three shapes a stale roster can take: an unresolvable
        key, an unresolvable target, and a declared pair that is not an edge of the live subgraph."""
        root = _common.ROOT
        view = vpgc._closure_view(root)
        reports: list[str] = []
        for key, targets in sorted(roster.items()):
            key_live = vpgc._module_to_repo_path(key, root) is not None and key in view
            if not key_live:
                reports.append(f"key {key} unresolvable")
            for target in targets:
                if vpgc._module_to_repo_path(target, root) is None or target not in view:
                    reports.append(f"target {target} unresolvable")
                elif not key_live or not view.has_edge(key, target):
                    reports.append(f"edge {key} -> {target} not live")
        return reports

    @staticmethod
    def _inert_rows(roster: dict[str, tuple[str, ...]]) -> list[str]:
        """One report per row that prunes nothing the auditor actually walks -- the two vacuous
        shapes a perfectly LIVE edge can still take (rec-3553): a key no gated check's closure
        reaches, and a row whose targets stay reachable by another route.

        Each row is judged ALONE against the UNPRUNED closure -- the roster patched to that single
        row versus the roster patched to empty -- never against the live roster minus the row, so
        one row can never excuse another by having already pruned its targets.
        """
        view = vpgc._closure_view(_common.ROOT)
        modules = [entry.module for entry in vpgc._gated_entries()]
        with patch.object(vpgc, "_PRUNED_EDGES", {}):
            unpruned = {module: vpgc._closure_modules(view, module) for module in modules}
        reports: list[str] = []
        for key, targets in sorted(roster.items()):
            if not any(key in closure for closure in unpruned.values()):
                reports.append(f"key {key} is in no gated check's closure")
                continue
            with patch.object(vpgc, "_PRUNED_EDGES", {key: targets}):
                shrinks = any(vpgc._closure_modules(view, module) < unpruned[module] for module in modules)
            if not shrinks:
                reports.append(f"row {key} shrinks no gated closure")
        return reports

    @staticmethod
    def _literal_block() -> list[str]:
        """Source lines of the _PRUNED_EDGES literal, ast-located rather than offset-guessed."""
        source = Path(vpgc.__file__).read_text(encoding="utf-8").splitlines()
        node = next(
            n
            for n in ast.walk(ast.parse("\n".join(source)))
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "_PRUNED_EDGES"
        )
        return source[node.lineno - 1 : node.end_lineno]

    def test_the_roster_is_the_two_reviewed_hub_rows(self) -> None:
        """rec-3291 / rec-3563: the former scripts.checks._budget_recs row is RETIRED, not
        replaced -- see the module's own _PRUNED_EDGES comment. Also the NON-VACUITY guard the two
        staleness assertions below lean on -- they iterate the roster and would pass trivially
        against an emptied one."""
        roster = vpgc._PRUNED_EDGES
        assert sorted(roster) == [self._COMMON, self._REGISTRY]
        assert roster[self._COMMON] == ("scripts.roadmap.plan_document",)
        targets = roster[self._REGISTRY]
        assert "scripts.checks._schema" in targets
        assert len([t for t in targets if t.endswith("._manifest")]) == 17
        assert len(targets) == 18

    def test_row_count_is_exactly_the_reviewed_two_and_within_the_cap(self) -> None:
        """An EXACT pin, never an inequality or a range: the cap records the reviewed wave ceiling
        and is not permission to fill it."""
        assert len(vpgc._PRUNED_EDGES) == 2
        assert len(vpgc._PRUNED_EDGES) <= self._CAP

    def test_every_declared_edge_is_live_in_the_import_subgraph(self) -> None:
        assert vpgc._PRUNED_EDGES, "an emptied roster must not satisfy this assertion vacuously"
        assert self._dead_rows(vpgc._PRUNED_EDGES) == []

    def test_the_detector_rejects_all_three_dead_row_shapes(self) -> None:
        """Negative control, green in BOTH states by construction: FOUR findings, not three -- a
        bogus KEY also invalidates every edge declared under it."""
        bogus = {
            "scripts.checks.no_such_hub": ("scripts.checks._schema",),
            self._REGISTRY: ("scripts.checks.no_such_target", "scripts.dependency_graph"),
        }
        reports = self._dead_rows(bogus)
        assert reports == [
            "key scripts.checks.no_such_hub unresolvable",
            "edge scripts.checks.no_such_hub -> scripts.checks._schema not live",
            "target scripts.checks.no_such_target unresolvable",
            f"edge {self._REGISTRY} -> scripts.dependency_graph not live",
        ]
        assert len(reports) == 4

    def test_every_row_key_is_reachable_from_a_gated_closure(self) -> None:
        """rec-3553: the liveness pin above accepts a row that is a genuinely live edge and still
        prunes nothing the auditor walks. LOAD-BEARING is the third property, alongside resolvable
        and live -- the key must sit in some gated check's unpruned closure AND the row must
        strictly shrink at least one such closure."""
        assert vpgc._PRUNED_EDGES, "an emptied roster must not satisfy this assertion vacuously"
        assert self._inert_rows(vpgc._PRUNED_EDGES) == []

    def test_the_inertness_detector_rejects_both_vacuous_row_shapes(self) -> None:
        bogus: dict[str, tuple[str, ...]] = {
            "scripts.build_lambda": ("scripts.build_lambda_config",),
            "scripts.checks._scaffolding": (self._COMMON,),
        }
        assert self._dead_rows(bogus) == []
        reports = self._inert_rows(bogus)
        assert reports == [
            "key scripts.build_lambda is in no gated check's closure",
            "row scripts.checks._scaffolding shrinks no gated closure",
        ]
        assert len(reports) == 2

    def test_every_row_is_preceded_by_an_inline_rationale_comment(self) -> None:
        """The module's own rule for adding an entry, enforced rather than trusted. Together with
        the cap test this is the anti-gaming pin: a row cannot be added silently or unexplained."""
        assert vpgc._PRUNED_EDGES, "an emptied roster must not satisfy this assertion vacuously"
        block = self._literal_block()
        for key in vpgc._PRUNED_EDGES:
            index = next(i for i, line in enumerate(block) if line.strip().startswith(f'"{key}":'))
            preceding = next(block[i] for i in range(index - 1, -1, -1) if block[i].strip())
            assert preceding.strip().startswith("#"), f"row {key} carries no inline rationale comment"


_REGISTRY_SEQUENCE_SYMBOLS = frozenset({"_ALL_ENTRIES", "pre_sequence", "full_sequence"})
_MANIFEST_PROBE_PATH = "scripts/checks/ci_guards/_manifest.py"
_PLAN_DOCUMENT = "scripts.roadmap.plan_document"
_PLAN_DOCUMENT_PROBE_PATH = "scripts/roadmap/plan_document.py"


def _references_registry_sequence_or_all_entries(module_source: str) -> bool:
    tree = ast.parse(module_source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return bool((names | attrs) & _REGISTRY_SEQUENCE_SYMBOLS)


def _calls_load_plan(module_source: str) -> bool:
    tree = ast.parse(module_source)
    return any(
        (isinstance(n, ast.Attribute) and n.attr == "load_plan") or (isinstance(n, ast.Name) and n.id == "load_plan")
        for n in ast.walk(tree)
    )


def _globs_cover(pre_globs: tuple[str, ...] | None, probe: str) -> bool:
    if not pre_globs:
        return False
    return any(vpgc._glob_match(probe, g) for g in pre_globs)


class TestRowRationaleIsStructural:
    """rec-3558: each _PRUNED_EDGES row's prose rationale becomes an EXECUTED structural test,
    never a comment trusted on faith. Both land green on the live tree today."""

    def test_no_registry_referencing_gated_check_lacks_manifest_coverage(self) -> None:
        """The scripts.checks.registry row's own rationale: registry imports each manifest only
        to assemble _ALL_ENTRIES, so a check reaching registry in its closure needs no manifest
        glob UNLESS it actually reads registry._ALL_ENTRIES/pre_sequence/full_sequence itself --
        in which case its behaviour genuinely depends on manifest content and the row's rationale
        no longer covers it."""
        root = _common.ROOT
        view = vpgc._closure_view(root)
        violations = []
        for entry in vpgc._gated_entries():
            closure = vpgc._closure_modules(view, entry.module)
            if "scripts.checks.registry" not in closure:
                continue
            module_path = vpgc._module_to_repo_path(entry.module, root)
            if module_path is None:
                continue
            source = (root / module_path).read_text(encoding="utf-8")
            if _references_registry_sequence_or_all_entries(source) and not _globs_cover(
                entry.pre_globs, _MANIFEST_PROBE_PATH
            ):
                violations.append(entry.name)
        assert violations == []

    def test_any_gated_check_calling_load_plan_globs_plan_document(self) -> None:
        """The scripts.checks._common row's own rationale: 3 of 5 load_plan callers declare no
        globs (always run), the other 2 glob plan_document's whole closure themselves -- so a NEW
        caller, or a new plan_document import outside scripts/roadmap/, that leaves a closure path
        unglobbed would silently under-declare the caller's dependency."""
        root = _common.ROOT
        view = vpgc._closure_view(root)
        plan_document_paths = sorted(
            path
            for path in filter(None, (vpgc._module_to_repo_path(m, root) for m in vpgc._closure_modules(view, _PLAN_DOCUMENT)))
            if not vpgc._module_body_is_trivial(path, root)
        )
        assert _PLAN_DOCUMENT_PROBE_PATH in plan_document_paths
        violations = []
        for entry in vpgc._gated_entries():
            module_path = vpgc._module_to_repo_path(entry.module, root)
            if module_path is None:
                continue
            source = (root / module_path).read_text(encoding="utf-8")
            if _calls_load_plan(source):
                violations.extend(
                    f"{entry.name}: {path}" for path in plan_document_paths if not _globs_cover(entry.pre_globs, path)
                )
        assert violations == []

    def test_references_registry_sequence_or_all_entries_detects_each_symbol(self) -> None:
        """Negative control: the detector fires on each of the three symbols individually and
        never on unrelated code, so the structural test above is not vacuously green."""
        assert _references_registry_sequence_or_all_entries("from scripts.checks import registry\nX = registry._ALL_ENTRIES\n")
        assert _references_registry_sequence_or_all_entries(
            "from scripts.checks.registry import pre_sequence\npre_sequence()\n"
        )
        assert _references_registry_sequence_or_all_entries("from scripts.checks import registry\nregistry.full_sequence()\n")
        assert not _references_registry_sequence_or_all_entries(
            "from scripts.checks import registry\nregistry.register('x')\n"
        )

    def test_calls_load_plan_detects_attribute_and_bare_forms(self) -> None:
        assert _calls_load_plan("from scripts.checks import _common\n_common.load_plan('x', root)\n")
        assert _calls_load_plan("from scripts.checks._common import load_plan\nload_plan('x', root)\n")
        assert not _calls_load_plan("from scripts.checks import _common\n_common.run(['git'])\n")

    def test_globs_cover_helper(self) -> None:
        assert _globs_cover(("scripts/checks/**",), _MANIFEST_PROBE_PATH) is True
        assert _globs_cover(("scripts/checks/*/_manifest.py",), _MANIFEST_PROBE_PATH) is True
        assert _globs_cover(("scripts/dependency_graph.py",), _MANIFEST_PROBE_PATH) is False
        assert _globs_cover(None, _MANIFEST_PROBE_PATH) is False


class TestPrunedEdgeMarkerGate:
    """The _PRUNED_EDGES row-addition marker gate (rec-3558): a brand-new row, or an existing
    row's target count growing, needs an authorized `# pruned-edge-approved: dec-NNN <reason>`
    marker on the row's own line span; removing a row, or shrinking one, stays free."""

    def test_extractor_reads_key_and_target_count(self) -> None:
        text = 'X: dict = {\n    "a.b": (\n        "c.d",\n        "e.f",\n    ),\n}\n'
        text = text.replace("X: dict", "_PRUNED_EDGES: dict[str, tuple[str, ...]]")
        entries = vpgc._pruned_edges_entries(text)
        assert entries["a.b"].value == 2.0
        assert entries["a.b"].marker is None

    def test_extractor_finds_a_marker_on_the_rows_closing_line(self) -> None:
        text = (
            "_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n"
            '    "a.b": (\n'
            '        "c.d",\n'
            "    ),  # pruned-edge-approved: dec-501 authorized for testing\n"
            "}\n"
        )
        entries = vpgc._pruned_edges_entries(text)
        assert entries["a.b"].marker == "dec-501"
        assert entries["a.b"].reason == "authorized for testing"

    def test_extractor_marker_with_empty_reason_does_not_count(self) -> None:
        text = (
            "_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n"
            '    "a.b": (\n'
            '        "c.d",\n'
            "    ),  # pruned-edge-approved: dec-501\n"
            "}\n"
        )
        entries = vpgc._pruned_edges_entries(text)
        assert entries["a.b"].marker is None

    def test_extractor_returns_empty_on_unparseable_text(self) -> None:
        assert vpgc._pruned_edges_entries("def broken(:\n") == {}

    def test_extractor_returns_empty_when_no_pruned_edges_literal_present(self) -> None:
        assert vpgc._pruned_edges_entries("X = 1\n") == {}

    def test_marker_on_span_out_of_range_lineno_is_skipped(self) -> None:
        assert vpgc._pruned_edge_marker_on_span(["a", "b"], 5, 7) == (None, None)

    def test_extractor_skips_non_string_keys_and_non_tuple_values(self) -> None:
        text = '_PRUNED_EDGES: dict = {\n    1: ("a",),\n    "b": ["not-a-tuple"],\n}\n'
        entries = vpgc._pruned_edges_entries(text)
        assert entries["b"].value == 0.0

    def _repo(self, tmp_path: Path, base_text: str, head_text: str) -> Path:
        repo = tmp_path / "repo"
        target = repo / vpgc._SELF_REL_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        (repo / "docs").mkdir(exist_ok=True)
        decisions = repo / "docs" / "DECISIONS.md"
        decisions.write_text(
            "## Decision 501: Authorizes a new pruned edge for testing (Decided)\n\n"
            "**Status:** Decided\n**Date:** 2026-01-01\n"
            "**Decision:** Authorizes the e.f pruned-edge row for testing.\n\n---\n",
            encoding="utf-8",
        )

        def _git(args: list[str]) -> None:
            result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
            assert result.returncode == 0, f"git {args} failed: {result.stderr}"

        repo.mkdir(parents=True, exist_ok=True)
        _git(["init", "-q"])
        _git(["config", "user.email", "test@example.com"])
        _git(["config", "user.name", "Test"])
        target.write_text(base_text, encoding="utf-8")
        _git(["add", "-A"])
        _git(["commit", "-q", "-m", "base"])
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()
        _git(["update-ref", "refs/remotes/origin/main", base_sha])

        target.write_text(head_text, encoding="utf-8")
        _git(["add", "-A"])
        _git(["commit", "-q", "-m", "head"])
        return repo

    _BASE = '_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n    "a.b": (\n        "c.d",\n    ),\n}\n'

    def _run(self, repo: Path) -> list[str]:
        spec = _marker_guard.RegistrySpec(
            rel_path=vpgc._SELF_REL_PATH,
            token=vpgc._PRUNED_EDGE_TOKEN,
            gated_direction="up",
            extractor=vpgc._pruned_edges_entries,
            gates_new_entry=lambda _value: True,
            label="_PRUNED_EDGES row addition (rec-3558)",
            reason_required=True,
        )
        with patch.object(_common, "ROOT", repo):
            return _marker_guard.check_diff(spec)

    def test_a_brand_new_row_without_a_marker_is_gated(self, tmp_path: Path) -> None:
        head = self._BASE.replace("}\n", '    "e.f": (\n        "g.h",\n    ),\n}\n')
        repo = self._repo(tmp_path, self._BASE, head)
        assert self._run(repo) != []

    def test_a_brand_new_row_with_an_authorized_marker_passes(self, tmp_path: Path) -> None:
        head = self._BASE.replace(
            "}\n",
            '    "e.f": (\n        "g.h",\n    ),  # pruned-edge-approved: dec-501 authorized new hub row\n}\n',
        )
        repo = self._repo(tmp_path, self._BASE, head)
        assert self._run(repo) == []

    def test_widening_an_existing_rows_targets_without_a_marker_is_gated(self, tmp_path: Path) -> None:
        head = '_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n    "a.b": (\n        "c.d",\n        "i.j",\n    ),\n}\n'
        repo = self._repo(tmp_path, self._BASE, head)
        assert self._run(repo) != []

    def test_removing_a_row_entirely_is_free(self, tmp_path: Path) -> None:
        head = "_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n}\n"
        repo = self._repo(tmp_path, self._BASE, head)
        assert self._run(repo) == []

    def test_shrinking_an_existing_rows_targets_is_free(self, tmp_path: Path) -> None:
        base = '_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n    "a.b": (\n        "c.d",\n        "e.f",\n    ),\n}\n'
        head = '_PRUNED_EDGES: dict[str, tuple[str, ...]] = {\n    "a.b": (\n        "c.d",\n    ),\n}\n'
        repo = self._repo(tmp_path, base, head)
        assert self._run(repo) == []

    def test_row_addition_violations_wrapper_runs_against_the_live_tree_clean(self) -> None:
        """The live roster's own head==base state (this diff makes no unmarked pruned-edge
        change) must clear the real wrapper end to end."""
        assert vpgc._pruned_edges_row_addition_violations() == []


class TestPaydownDirection:
    """VP step 12: DIRECTION bound on the paydown. Step 10's zero is satisfiable the wrong way --
    declaring a repo-wide glob on all 19 Entries would turn every obligation green while
    destroying fast-tier selectivity. Base-vs-head is necessary: SEVENTEEN gated Entries already
    declare a repo-wide glob at base (measured against THIS forbidden set), so an absolute ban
    would flag all of them -- only a NEWLY GAINED one, other than the auditor's own widened Entry,
    is forbidden."""

    _FORBIDDEN = frozenset({"**", "**/*.py", "scripts/**", "scripts/checks/**", "src/**", "tests/**"})
    _SELF_NAME = "validate_pre_glob_closure"

    @staticmethod
    def _newly_gained(
        base_globs: tuple[str, ...] | None, head_globs: tuple[str, ...] | None, forbidden: frozenset[str]
    ) -> set[str]:
        base_hit = set(base_globs or ()) & forbidden
        head_hit = set(head_globs or ()) & forbidden
        return head_hit - base_hit

    def test_newly_gained_helper(self) -> None:
        forbidden = frozenset({"scripts/**"})
        assert self._newly_gained(None, ("scripts/**",), forbidden) == {"scripts/**"}
        assert self._newly_gained(("scripts/**",), ("scripts/**",), forbidden) == set()
        assert self._newly_gained(("scripts/**",), None, forbidden) == set()
        assert self._newly_gained(("a/**",), ("b/**",), forbidden) == set()

    def test_no_entry_except_the_auditor_newly_gains_a_repo_wide_glob(self) -> None:
        root = _common.ROOT
        all_paths = sorted(set(_head_manifest_paths(root)) | set(_base_manifest_paths(root)))
        base_reader = _batched_base_reader(root, all_paths)
        assert base_reader is not None, "git cat-file --batch failed -- cannot measure the paydown direction bound"

        base_states = {}
        head_states = {}
        for rel in all_paths:
            base_states.update(extract_tier_states(base_reader(rel)))
            head_path = root / rel
            if head_path.exists():
                head_states.update(extract_tier_states(head_path.read_text(encoding="utf-8", errors="replace")))

        violations = []
        for name, head_state in head_states.items():
            if name == self._SELF_NAME:
                continue
            base_state = base_states.get(name)
            base_globs = cast(TierState, base_state.state).pre_globs if base_state is not None else None
            gained = self._newly_gained(base_globs, cast(TierState, head_state.state).pre_globs, self._FORBIDDEN)
            if gained:
                violations.append((name, sorted(gained)))
        assert violations == [], violations
