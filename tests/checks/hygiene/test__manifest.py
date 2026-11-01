"""Mirror test for scripts/checks/hygiene/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib
from fnmatch import fnmatch

import pytest

from scripts.checks import registry
from scripts.checks.hygiene import _manifest


class TestHygieneManifest:
    """Every hygiene Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.hygiene.")

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


class TestValidateCheckAccountingDispatchedInBothTiers:
    """VP step 7: the new check is actually dispatched in both tiers, not merely defined."""

    def test_registered_in_both_pre_and_full_sequence(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_check_accounting" in pre_names
        assert "validate_check_accounting" in full_names


class TestValidateRootScopedDiffBaseDispatchedInBothTiers:
    """rec-3166: the new guard is actually dispatched in both tiers, not merely defined."""

    def test_registered_in_both_pre_and_full_sequence(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_root_scoped_diff_base" in pre_names
        assert "validate_root_scoped_diff_base" in full_names


class TestValidateVacuityJustifiedDispatched:
    """VP step 3: validate_vacuity_justified (rec-3163) is dispatched full-tier only -- it
    adjudicates a full-tier check's own declaration, so it belongs in that check's segment, not
    --pre (promoting coverage enforcement pre-merge is rec-3221's territory, not this plan's)."""

    def test_registered_in_full_sequence_only(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_vacuity_justified" in full_names
        assert "validate_vacuity_justified" not in pre_names


class TestGatedEntryInputClosures:
    """A gated check's pre_globs must cover EVERY path its implementation reads. Under-inclusion
    is a recall bug: a diff that touches an uncovered input silently skips the check in --pre."""

    @staticmethod
    def _globs(name: str) -> set[str]:
        return set(next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ())

    def test_test_count_coupling_covers_its_own_curated_roster(self) -> None:
        """_CURATED_TOKENS lives in the check's own module: adding a token retroactively makes
        existing `assert len(X) == N` sites violations, with no tests/** file in the diff."""
        assert {"tests/**", "scripts/checks/hygiene/**"} <= self._globs("validate_test_count_coupling")

    def test_sys_executable_gate_covers_its_scan_scope_and_its_own_module(self) -> None:
        """Promoted into --pre. It regex-scans scripts/**/*.py, and its own module (which holds
        the pattern) lives under the same root -- so one glob is the whole closure."""
        assert {"scripts/**"} <= self._globs("validate_sys_executable")


class TestPromotedGateClosures:
    """Each promoted check's declared closure member is asserted MATCHED by its patterns, not
    present in them as a literal -- so widening a glob keeps the row green while a member falling
    out of coverage reddens it.

    Bare fnmatch, not scripts.validate._pre_glob_match, for the reason
    tests/checks/ops_governance/test__manifest.py::TestClosureMembersAreCovered states: the
    production matcher is fnmatch PLUS a leading-'**/' retry that can only ADD matches.
    """

    _CLOSURE_INPUTS: dict[str, tuple[str, ...]] = {
        "validate_sys_executable": (
            "scripts/checks/hygiene/validate_sys_executable.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
            "scripts/session/postflight.py",
        ),
    }

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


class TestStatusLineEmittersRegistered:
    """validate_status_line_emitters is dispatched in both tiers and runs on every edit."""

    @pytest.mark.parametrize("tier", ["pre", "full"])
    def test_dispatched(self, tier: str) -> None:
        sequence = registry.pre_sequence() if tier == "pre" else registry.full_sequence()
        assert "validate_status_line_emitters" in {step.name for step in sequence if step.kind == "check"}

    def test_ungated_so_every_edit_runs_it(self) -> None:
        """No pre_globs: the check scans scripts/, src/ and .claude/hooks, which a repo-wide glob may not gain."""
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_status_line_emitters")
        assert entry.pre and not entry.pre_globs
