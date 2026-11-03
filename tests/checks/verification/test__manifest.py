"""Mirror test for scripts/checks/verification/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib

import pytest

from scripts.checks import registry
from scripts.checks.verification import _manifest


class TestVerificationManifest:
    """Every verification Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.verification.")

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

    def test_vp_replay_entry_runs_in_both_tiers(self) -> None:
        """PLAN-vp-red-before-gate: validate_vp_replay's Entry gains full_segment (a tightening,
        free and unmarked under Decision 187 point 3) so the check and its in-dispatch classifier
        self-test also run in the full tier, not --pre alone."""
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_vp_replay" in pre_names
        assert "validate_vp_replay" in full_names


class TestGatedEntryInputClosures:
    """A gated check's pre_globs must cover EVERY path its implementation reads. Under-inclusion
    is a recall bug: a diff that touches an uncovered input silently skips the check in --pre."""

    @staticmethod
    def _globs(name: str) -> set[str]:
        return set(next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ())

    def test_handoff_full_tier_covers_the_plan_document_schema(self) -> None:
        assert {"scripts/roadmap/**", "scripts/checks/verification/**"} <= self._globs("validate_handoff_full_tier")

    def test_verification_registry_is_gated_on_its_full_closure(self) -> None:
        """Reads the entries/ shards and the retired flat file under
        config/agent/verification_registry/, the loader/differential engine in
        scripts/verification_graduation.py, CANONICAL_SLOTS in scripts/verification_checks.py, and
        _common/_scaffolding helpers."""
        assert {
            "config/agent/verification_registry/**",
            "scripts/verification_graduation.py",
            "scripts/verification_checks.py",
            "scripts/checks/verification/**",
            "scripts/checks/_common.py",
            "scripts/checks/_scaffolding.py",
        } <= self._globs("validate_verification_registry")

    def test_verification_registry_covers_the_late_closure_route_inputs(self) -> None:
        """The late-closure route reads the trailer parser, the approved-text sidecar owner and every plan's
        graduation steps and bundled list."""
        assert {
            "scripts/rec_trailer.py",
            "scripts/roadmap/approved_text.py",
            "docs/plans/PLAN-*.yaml",
        } <= self._globs("validate_verification_registry")

    def test_merge_step_covers_the_ops_portal_gate_modules(self) -> None:
        """The route reads trailer_closure_gate.py, closure_gate.py and ops_portal/_common.py."""
        assert "scripts/ops_portal/**" in self._globs("validate_merge_step")

    def test_hermeticity_flags_covers_both_tier_command_builders(self) -> None:
        """Promoted into --pre -- it guards the FAST TIER'S OWN flags, so a full-only placement
        meant the fast tier could drop --disable-socket or the fixed seed and only learn about it
        post-merge. It reads pyproject's addopts, _build_unit_test_cmd() in _scaffolding, and
        _PYTEST_FLAGS/_PYTEST_RANDOMLY_SEED, which are defined in _pytest_diff."""
        assert {
            "pyproject.toml",
            "scripts/checks/_pytest_diff.py",
            "scripts/checks/_scaffolding.py",
            "scripts/checks/verification/**",
        } <= self._globs("validate_hermeticity_flags")

    def test_verifier_hermeticity_covers_its_scan_dir_and_rule_module(self) -> None:
        """AST-scans scripts/verifiers/*.py against clock/randomness/network rosters that live in
        the check's own module."""
        assert {"scripts/verifiers/**", "scripts/checks/verification/**"} <= self._globs("validate_verifier_hermeticity")

    def test_differential_gate_baseline_covers_the_kernel_it_self_tests(self) -> None:
        """It imports is_admitted/CANONICAL_SLOTS from scripts/verification_checks.py and then
        grep-counts that same file."""
        assert {"scripts/verification_checks.py", "scripts/checks/verification/**"} <= self._globs(
            "validate_differential_gate_baseline"
        )


class TestVerificationHarnessStaysFullOnly:
    """validate_verification_harness must NOT be promoted: it runs after, and depends on, the
    full tier's ensure_fresh_dq scaffold (AWS credentials + logs/debug/dq-latest.json). The
    pr-validate job has no OIDC and no AWS by design, so a promoted run would be structurally
    degraded rather than a gate. A hermetic tier_filter subset is the promotable slice, and that
    is a change to the harness, not to this manifest."""

    def test_verification_harness_is_absent_from_pre(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_verification_harness" in full_names
        assert "validate_verification_harness" not in pre_names
