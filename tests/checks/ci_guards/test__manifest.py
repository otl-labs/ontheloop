"""Mirror test for scripts/checks/ci_guards/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib
from fnmatch import fnmatch

import pytest

from scripts.checks import registry
from scripts.checks.ci_guards import _manifest


class TestCiGuardsManifest:
    """Every ci_guards Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.ci_guards.")

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


class TestGatedEntryInputClosures:
    """A gated check's pre_globs must cover EVERY path its implementation reads. Under-inclusion
    is a recall bug: a diff that touches an uncovered input silently skips the check in --pre."""

    @staticmethod
    def _globs(name: str) -> set[str]:
        return set(next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ())

    def test_ops_portal_patch_targets_is_gated_on_its_full_closure(self) -> None:
        """It AST-scans tests/**/*.py against the _MOVED_CALLERS roster hardcoded in its own
        module; the facade/submodule namespaces that roster names are covered too, so a caller
        move plus roster edit can never land unscanned."""
        assert {
            "tests/**",
            "scripts/checks/ci_guards/**",
            "scripts/ops_data_portal.py",
            "scripts/ops_portal/**",
        } <= self._globs("validate_ops_portal_patch_targets")

    def test_ci_rca_trigger_is_gated_on_its_full_closure(self) -> None:
        """Promoted into --pre: it is the odd one out of the three-check ci-rca family (taxonomy
        and adjudication were already there) and its own docstring claims presubmit membership
        per Decision 60. _check_ci_rca_filter reads main-canary.yml AND ci-rca.yml AND the
        scheduled-agent doc carrying the FILED: marker contract -- all three are inputs."""
        assert {
            ".github/workflows/**",
            ".claude/agents/scheduled/ci-rca.md",
            "scripts/verify_ci_workflow/**",
            "scripts/checks/ci_guards/**",
        } <= self._globs("validate_ci_rca_trigger")

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/ci-rca.yml",
            ".github/workflows/main-canary.yml",
            ".claude/agents/scheduled/ci-rca.md",
            "scripts/verify_ci_workflow/_shared.py",
            "scripts/checks/ci_guards/validate_ci_rca_trigger.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ],
    )
    def test_a_diff_touching_only_a_ci_rca_trigger_input_still_matches_the_gate(self, path: str) -> None:
        globs = self._globs("validate_ci_rca_trigger")
        assert any(fnmatch(path, glob) for glob in globs)

    def test_an_unrelated_path_does_not_match_the_ci_rca_trigger_gate(self) -> None:
        """Anti-vacuity: the rows above would also pass against a catch-all pattern."""
        globs = self._globs("validate_ci_rca_trigger")
        assert not any(fnmatch("README.md", glob) for glob in globs)

    def test_github_settings_apply_entry_closes_over_its_render_inputs(self) -> None:
        """The guard reads Terraform through _account_names.render, so an edit to the renderer or to any file it
        parses must dispatch the guard in --pre (validate_pre_glob_closure follows import edges only)."""
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_github_settings_apply")
        assert entry.pre is True
        assert {
            "scripts/checks/iam_tf/_account_names.py",
            "terraform/bootstrap/account_names.tf",
            "terraform/bootstrap/variables.tf",
            "terraform/personal/account_names.tf",
            "terraform/personal/variables.tf",
        } <= self._globs("validate_github_settings_apply")

    def test_dependabot_config_entry_closes_over_its_inputs(self) -> None:
        """A config-only or checker-only PR must still dispatch the guard in --pre: every path
        validate_dependabot_config.py reads (the dependabot config, the denylist delegate it
        derives from, its own module, and the shared _common/registry primitives) must be in its
        pre_globs closure."""
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_dependabot_config")
        assert entry.pre is True
        assert {
            ".github/dependabot.yml",
            "scripts/ci/dependabot_auto_merge.sh",
            "scripts/checks/ci_guards/validate_dependabot_config.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        } <= self._globs("validate_dependabot_config")
