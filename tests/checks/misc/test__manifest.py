"""Mirror test for scripts/checks/misc/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib
from fnmatch import fnmatch

import pytest

from scripts.checks import registry
from scripts.checks.misc import _manifest


class TestMiscManifest:
    """Every misc Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.misc.")

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


class TestDiffCoveragePreTierOnlyDisposition:
    """validate_diff_coverage (PLAN-premerge-diff-coverage-gate) is registered pre=True with NO
    full_segment -- a DECLARED unsequenced-from-full disposition (misc sits in full_after_lint,
    which the full-tier skeleton dispatches BEFORE the unit_tests scaffold, so a full-tier leg
    would have no coverage artifact to read). This must be asserted, not assumed -- it fails
    loudly if anyone later adds a full_segment to this Entry."""

    def test_registered_pre_true(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_diff_coverage")
        assert entry.pre is True

    def test_declares_no_full_segment(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_diff_coverage")
        assert entry.full_segment is None

    def test_present_in_pre_sequence(self) -> None:
        names = {step.name for step in registry.pre_sequence()}
        assert "validate_diff_coverage" in names

    def test_absent_from_full_sequence(self) -> None:
        names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_diff_coverage" not in names


class TestPromotedGateClosures:
    """validate_invariants and validate_scheduled_agent_logs are promoted into --pre.

    validate_invariants' second invariant compares the subprocess.run count inside
    cleanup_after_merge() against the mock side_effect lengths in one specific test module, so
    that test file is a real INPUT, not merely a scan target.
    validate_scheduled_agent_logs engages only when EVERY changed file is under logs/, so a
    logs/**-gated skip is exactly the branch the check would take anyway.
    """

    _CLOSURE_INPUTS: dict[str, tuple[str, ...]] = {
        "validate_invariants": (
            "scripts/executor/postflight.py",
            "tests/test_execute_recommendation.py",
            "scripts/checks/misc/validate_invariants.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        "validate_persona_verdict_egress": (
            "scripts/checks/ci_guards/validate_project_region.py",
            "docs/contracts/inference-provider.yaml",
            "scripts/checks/misc/validate_persona_verdict_egress.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        "validate_scheduled_agent_logs": (
            "logs/.recommendations-log.jsonl",
            "logs/agents/20260101T000000Z.jsonl",
            "scripts/checks/misc/validate_scheduled_agent_logs.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
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


class TestExpensiveMiscChecksStayFullOnly:
    """The two misc checks the promotion wave deliberately left behind, each for a recorded
    reason -- asserted so a later wave has to argue with a red test rather than a comment."""

    def test_test_coverage_stays_full_only(self) -> None:
        """Decision 159 / Decision 163: one pytest+coverage subprocess PER changed source file
        (measured 39s on a 3-file working-tree diff) against the 300s fast-tier budget, plus the
        vacuous-pass hazard rec-2970 records. The pre tier carries the report-only
        validate_diff_coverage instead."""
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_test_coverage" in full_names
        assert "validate_test_coverage" not in pre_names

    def test_ghas_probe_stays_full_only(self) -> None:
        """Live GitHub API probe that SKIPS whenever GHAS_PROBE_TOKEN is unset -- the pr-validate
        default -- so promoting it would add a permanently vacuous step to every --pre run."""
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        assert "validate_ghas_probe" not in pre_names
