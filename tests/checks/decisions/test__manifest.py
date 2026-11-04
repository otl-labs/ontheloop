"""Mirror test for scripts/checks/decisions/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib
from fnmatch import fnmatch

import pytest

from scripts.checks import registry
from scripts.checks.decisions import _manifest


class TestDecisionsManifest:
    """Every decisions Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.decisions.")

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


# Membership FLOOR, not an exhaustive roster (tests/CLAUDE.md count-coupling rules): naming the
# checks this domain must not silently lose keeps the parametrized tests above honest -- they
# assert properties of whatever ENTRIES happens to contain, so an accidentally-dropped Entry
# would leave them green with nothing to iterate over. A NEW check may be added freely without
# touching this list; removing one named here must be a deliberate edit.
_REQUIRED_ENTRY_NAMES = frozenset(
    {
        "validate_decisions_size",
        "validate_decisions_index_freshness",
        "validate_decision_entry_conformance",
        "validate_supersession_annotations",
        "validate_decision_currency",
    }
)


class TestRequiredEntryMembership:
    def test_every_required_check_is_registered_in_this_manifest(self) -> None:
        present = {entry.name for entry in _manifest.ENTRIES}
        assert _REQUIRED_ENTRY_NAMES <= present, f"missing from ENTRIES: {sorted(_REQUIRED_ENTRY_NAMES - present)}"

    def test_entry_conformance_runs_in_both_tiers(self) -> None:
        """Corpus conformance is a both-tier gate: a --pre-only registration would let a
        nonconforming edit reach main through any path that skips the fast tier."""
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_decision_entry_conformance")
        assert entry.pre is True
        assert entry.full_segment == "full_after_lint"

    def test_entry_conformance_pre_globs_cover_both_corpus_files(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_decision_entry_conformance")
        assert {"docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"} <= set(entry.pre_globs)


class TestGatedEntryInputClosures:
    """A gated check's pre_globs must cover EVERY path its implementation reads, not just its
    headline corpus. Under-inclusion is a recall bug: a diff that touches an uncovered input
    silently skips the check in --pre and only reddens post-merge."""

    @staticmethod
    def _globs(name: str) -> set[str]:
        return set(next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ())

    def test_decision_entry_conformance_covers_its_contract_and_grammar_sources(self) -> None:
        """It parses docs/contracts/decision-entry.yaml for required_markers and the significance
        vocabulary, and imports scripts.decisions_md plus this package's _baseline helper."""
        assert {
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "docs/contracts/decision-entry.yaml",
            "scripts/decisions_md.py",
            "scripts/checks/decisions/**",
        } <= self._globs("validate_decision_entry_conformance")

    def test_supersession_annotations_covers_its_waiver_file(self) -> None:
        """Promoted into --pre. The waiver roster is the third input alongside the two corpus
        files: deleting a waiver turns an already-committed unannotated edge into a violation
        with neither DECISIONS file in the diff."""
        assert {
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "config/decision_supersession_waivers.yaml",
            "scripts/decisions_md.py",
            "scripts/checks/decisions/**",
        } <= self._globs("validate_supersession_annotations")

    def test_supersession_annotations_runs_in_both_tiers(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_supersession_annotations" in pre_names
        assert "validate_supersession_annotations" in full_names


# One row per gated Entry: repo-relative paths in that check's transitive first-party import
# closure (module-scope AND the deferred imports its body always executes).
_CLOSURE_INPUTS: dict[str, tuple[str, ...]] = {
    "validate_decision_entry_conformance": (
        "docs/contracts/decision-entry.yaml",
        "docs/contracts/decision-kind.yaml",
        "docs/contracts/north-star.yaml",
        "scripts/decisions_md.py",
        "scripts/north_star_models.py",
        "scripts/checks/decisions/envelope_rules.py",
        "scripts/checks/decisions/_baseline.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    "validate_decision_currency": (
        "docs/decisions-index.json",
        "scripts/decisions_md.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    # Promoted out of the full-only tier. Its module docstring ruled --pre out on "heavier
    # both-file parse" grounds, but the sibling conformance/immutability checks already parse the
    # SAME two files in --pre, and the measured body cost is ~0.07s -- so the stated rationale no
    # longer holds. Third input: the waiver file that decides which unannotated edges are legal.
    "validate_supersession_annotations": (
        "docs/DECISIONS.md",
        "docs/DECISIONS_ARCHIVE.md",
        "config/decision_supersession_waivers.yaml",
        "scripts/decisions_md.py",
        "scripts/checks/decisions/validate_supersession_annotations.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
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
