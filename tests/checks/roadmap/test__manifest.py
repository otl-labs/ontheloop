"""Mirror test for scripts/checks/roadmap/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib
from fnmatch import fnmatch

import pytest

from scripts.checks import registry
from scripts.checks.roadmap import _manifest


class TestRoadmapManifest:
    """Every roadmap Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.roadmap.")

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_resolves_to_a_callable_named_its_own_attr(self, entry) -> None:
        module = importlib.import_module(entry.module)
        fn = getattr(module, entry.attr)
        assert callable(fn)
        assert fn.__name__ == entry.attr

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_registry_resolve_matches_the_manifest_entry(self, entry) -> None:
        module = importlib.import_module(entry.module)
        assert registry.resolve(entry.name) is getattr(module, entry.attr)

    def test_roadmap_liveness_entry_covers_its_contract_and_baseline(self) -> None:
        """Not the parametrized test_entry_resolves_to_a_callable_named_its_own_attr above: that
        one iterates _manifest.ENTRIES, so an absent Entry simply fails to parametrize and the
        suite stays green before this Entry existed -- a vacuous red/green claim. This test
        resolves the Entry by name so it is genuinely red before the Entry exists."""
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_roadmap_liveness")
        assert entry.pre is True
        assert entry.full_segment == "full_after_lint"
        assert {
            "docs/contracts/roadmap-liveness.yaml",
            "config/roadmap_liveness_baseline.yaml",
            "scripts/platform_roadmap_liveness.py",
        } <= set(entry.pre_globs or ())


class TestGatedEntryInputClosures:
    """A gated check's pre_globs must cover EVERY path its implementation reads, not just its
    headline corpus. Under-inclusion is a recall bug: a diff that touches an uncovered input
    silently skips the check in --pre and only reddens post-merge."""

    @staticmethod
    def _globs(name: str) -> set[str]:
        return set(next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ())

    @pytest.mark.parametrize(
        "name",
        [
            "validate_platform_roadmap",
            "validate_plan_documents",
            "validate_fallback_reevaluation",
            "validate_plan_scope_closure",
        ],
    )
    def test_pydantic_schema_package_is_covered(self, name: str) -> None:
        """All four validate documents against schemas reached through scripts/roadmap/ --
        tightening a schema without editing a plan/roadmap document must not skip the gate."""
        assert {"scripts/roadmap/**", "scripts/checks/roadmap/**"} <= self._globs(name)

    @pytest.mark.parametrize(
        "name",
        [
            "validate_platform_roadmap",
            "validate_candidate_decision_ratification",
            "validate_fallback_reevaluation",
        ],
    )
    def test_platform_schema_triple_is_covered(self, name: str) -> None:
        """scripts/roadmap/platform_roadmap.py is a Decision-124 FACADE: ExitCriterion, TierItem
        and CandidateDecision are defined in scripts/platform_roadmap_models.py, the state machine
        in _state.py and the gate-rule grammar in _gate_rules.py -- all one level up, so
        "scripts/roadmap/**" does not reach any of them."""
        assert {
            "scripts/platform_roadmap_models.py",
            "scripts/platform_roadmap_state.py",
            "scripts/platform_roadmap_gate_rules.py",
        } <= self._globs(name)

    def test_plan_scope_closure_covers_its_obligation_map(self) -> None:
        assert "docs/contracts/plan-obligations.yaml" in self._globs("validate_plan_scope_closure")

    def test_tier_floor_covers_lambda_artifact_state(self) -> None:
        """_lambda_code_files() reads every src/lambdas/*/manifest.yaml through
        scripts.lambda_manifest: a stub -> active status flip changes the floor for an EXISTING
        plan, with no plan/roadmap file in the diff."""
        assert {
            "src/lambdas/**",
            "scripts/lambda_manifest.py",
            "scripts/checks/roadmap/**",
        } <= self._globs("validate_tier_floor")

    def test_candidate_decision_ratification_is_gated_on_its_full_closure(self) -> None:
        assert {
            "docs/ROADMAP-*",
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "scripts/decisions_md.py",
            "scripts/platform_roadmap_models.py",
            "scripts/platform_roadmap_state.py",
            "scripts/roadmap/**",
            "scripts/checks/roadmap/**",
        } <= self._globs("validate_candidate_decision_ratification")


# One row per gated Entry: repo-relative paths in that check's transitive first-party import
# closure (module-scope AND the deferred imports its body always executes -- every roadmap check
# loads its Pydantic schema through a function-scope import).
_CLOSURE_INPUTS: dict[str, tuple[str, ...]] = {
    "validate_platform_roadmap": (
        "scripts/roadmap/platform_roadmap.py",
        "scripts/platform_roadmap_models.py",
        "scripts/platform_roadmap_state.py",
        "scripts/platform_roadmap_gate_rules.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_candidate_decision_ratification": (
        "scripts/decisions_md.py",
        "scripts/roadmap/platform_roadmap.py",
        "scripts/platform_roadmap_models.py",
        "scripts/platform_roadmap_state.py",
        "scripts/platform_roadmap_gate_rules.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_plan_documents": (
        "scripts/roadmap/plan_document.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_fallback_reevaluation": (
        "scripts/roadmap/platform_roadmap.py",
        "scripts/roadmap/plan_document.py",
        "scripts/platform_roadmap_models.py",
        "scripts/platform_roadmap_state.py",
        "scripts/platform_roadmap_gate_rules.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_tier_floor": (
        "scripts/lambda_manifest.py",
        "scripts/contract_rules.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_plan_scope_closure": (
        "docs/contracts/plan-obligations.yaml",
        "scripts/roadmap/plan_obligations.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_roadmap_liveness": (
        "docs/contracts/roadmap-liveness.yaml",
        "config/roadmap_liveness_baseline.yaml",
        "scripts/platform_roadmap_liveness.py",
        "scripts/platform_roadmap_models.py",
        "scripts/platform_roadmap_state.py",
        "scripts/platform_roadmap_gate_rules.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
        "scripts/checks/_marker_guard.py",
    ),
}


class TestClosureMembersAreCovered:
    """Each closure member is asserted MATCHED by the entry's patterns, not present in them as a
    literal -- so rewriting a glob (or moving an input behind a wider one) keeps the row green
    while a member falling out of coverage reddens it.

    Bare fnmatch, not scripts.validate._pre_glob_match: an import edge from tests/checks/** into
    the driver widens the affected-test graph pinned by tests/checks/registry/
    test_manifest_contracts.py. The substitution is sound in the safe direction -- the production
    matcher is fnmatch PLUS a leading-'**/' retry that can only ADD matches, so anything green
    here is green there too.
    """

    @staticmethod
    def _covered(name: str, path: str) -> bool:
        globs = next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ()
        return any(fnmatch(path, glob) for glob in globs)

    @pytest.mark.parametrize(
        ("name", "path"),
        [(name, path) for name, paths in _CLOSURE_INPUTS.items() for path in paths],
        ids=[f"{name}-{path}" for name, paths in _CLOSURE_INPUTS.items() for path in paths],
    )
    def test_a_diff_touching_only_this_closure_member_still_matches_the_gate(self, name: str, path: str) -> None:
        assert self._covered(name, path)

    @pytest.mark.parametrize("name", sorted(_CLOSURE_INPUTS))
    def test_an_unrelated_path_is_not_matched(self, name: str) -> None:
        """Anti-vacuity: the rows above would also pass against a catch-all pattern."""
        assert not self._covered(name, "README.md")
