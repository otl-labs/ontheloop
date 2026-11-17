"""Mirror test for scripts/checks/iam_tf/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib

import pytest

from scripts.checks import registry
from scripts.checks.iam_tf import _manifest


class TestIamTfManifest:
    """Every iam_tf Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    def test_no_entry_is_product_coupled(self) -> None:
        """Platform-only repository: no iam_tf check carries product-coupled ownership metadata."""
        assert [entry.name for entry in _manifest.ENTRIES if entry.product_coupled] == []

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.iam_tf.")

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


class TestEnvironmentTaxonomyPromotedUngated:
    """Promoted into --pre WITHOUT globs, deliberately. The check reads only the .md/.yaml/.yml
    members of _common.get_changed_files(), so it is already diff-scoped by construction: a
    pre_globs tuple could only restate the diff it computes, and every candidate spelling
    ("**/*.md") silently drops repo-root files. Ungated is the recall-safe direction here, and it
    examines nothing on a code-only diff.
    """

    @staticmethod
    def _entry():
        return next(e for e in _manifest.ENTRIES if e.name == "validate_environment_taxonomy")

    def test_is_dispatched_in_pre(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        assert "validate_environment_taxonomy" in pre_names

    def test_declares_no_pre_globs(self) -> None:
        assert self._entry().pre_globs is None


class TestTelemetryWriterPrincipalsPreGlobs:
    """validate_telemetry_writer_principals is a fast-tier check selected by any terraform root change."""

    def _entry(self):
        return next(e for e in _manifest.ENTRIES if e.name == "validate_telemetry_writer_principals")

    def test_in_pre_sequence(self) -> None:
        assert "validate_telemetry_writer_principals" in [e.name for e in registry.pre_sequence()]

    def test_pre_globs_cover_both_terraform_roots(self) -> None:
        entry = self._entry()
        assert entry.pre
        assert "terraform/personal/**" in entry.pre_globs
        assert "terraform/bootstrap/**" in entry.pre_globs
