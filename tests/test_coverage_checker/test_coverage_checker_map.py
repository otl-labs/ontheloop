"""Tests for test_coverage_checker.map_source_to_test() and the post-retirement mirror rule.

Split from the former tests/test_coverage_checker.py monolith (rec-2709 Wave 6b -- SLOC governance
per Decision 128, not a mirror-roster retirement: scripts/test_coverage_checker.py isn't one of the
24 _ALL_MIRROR_TARGET_HOMES roster entries -- it resolves via the direct
_CONCERN_SPLIT_TEST_PACKAGES membership check instead). See tests/fixtures/coverage_checker_module.py
for the shared module-under-test singleton.
"""

from pathlib import Path

import pytest

from tests.fixtures.coverage_checker_module import _ALL_MIRROR_TARGET_HOMES, _RETIRING_GRANDFATHER_HOMES, ROOT
from tests.fixtures.coverage_checker_module import checker as _checker

check_test_file_exists = _checker.check_test_file_exists
map_source_to_test = _checker.map_source_to_test


class TestMapSourceToTest:
    """Tests for map_source_to_test()."""

    def test_maps_src_nested_to_test(self) -> None:
        """src/common/config.py maps to tests/test_config.py."""
        source = ROOT / "src" / "common" / "config.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "test_config.py"

    def test_maps_scripts_to_test(self) -> None:
        """scripts/validate.py maps to the tests/validate/ concern-split package (rec-2709
        Wave 1: "test_validate.py" retired from _RETIRING_GRANDFATHER_HOMES, and
        scripts/validate.py is a declared _CONCERN_SPLIT_TEST_PACKAGES entry)."""
        source = ROOT / "scripts" / "validate.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "validate"

    def test_maps_scripts_session_preflight_to_concern_split_package(self) -> None:
        """scripts/session/preflight.py maps to the tests/session/preflight/ concern-split
        package (rec-2709 Wave 4: "test_session_preflight.py" retired from
        _RETIRING_GRANDFATHER_HOMES, and scripts/session/preflight.py is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry)."""
        source = ROOT / "scripts" / "session" / "preflight.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "session" / "preflight"

    def test_maps_scripts_sync_ops_to_concern_split_package(self) -> None:
        """scripts/sync/ops.py maps to the tests/sync/ops/ concern-split package (rec-2709
        Wave 10: "test_sync_ops.py" retired from _RETIRING_GRANDFATHER_HOMES, and
        scripts/sync/ops.py is a declared _CONCERN_SPLIT_TEST_PACKAGES entry)."""
        source = ROOT / "scripts" / "sync" / "ops.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "sync" / "ops"

    def test_maps_scripts_session_postflight_to_concern_split_package(self) -> None:
        """scripts/session/postflight.py maps to the tests/session/postflight/ concern-split
        package (rec-2709 Wave 10: "test_session_postflight.py" retired from
        _RETIRING_GRANDFATHER_HOMES, and scripts/session/postflight.py is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry)."""
        source = ROOT / "scripts" / "session" / "postflight.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "session" / "postflight"

    def test_maps_scripts_ci_rca_taxonomy_to_concern_split_package(self) -> None:
        """scripts/ci_rca/taxonomy.py maps to the tests/ci_rca/taxonomy/ concern-split package
        (ci-rca-evidence-fidelity: scripts/ci_rca/taxonomy.py is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry, needed once its test file's Priority-0/refusal
        coverage pushed tests/test_ci_rca_taxonomy.py past the 500-SLOC budget). Note:
        scripts/ci_rca/evidence.py is no longer a real source path (it became the
        scripts/ci_rca/evidence/ package, whose members are excluded from or unmapped by this
        registry -- __init__.py is excluded upstream by get_changed_source_files; its submodules
        are covered end to end by the existing tests/ci_rca/evidence/ suite regardless)."""
        source = ROOT / "scripts" / "ci_rca" / "taxonomy.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "ci_rca" / "taxonomy"

    def test_maps_scripts_checks_verification_validate_scope_boundary_to_concern_split_package(self) -> None:
        """scripts/checks/verification/validate_scope_boundary.py maps to the
        tests/checks/verification/validate_scope_boundary/ concern-split package
        (PLAN-secrets-baseline-sanction-row: the flat test_validate_scope_boundary.py monolith was
        decomposed per Decision 128 SLOC governance, and the source is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry so map_source_to_test resolves to the package directory
        instead of the deleted flat file)."""
        source = ROOT / "scripts" / "checks" / "verification" / "validate_scope_boundary.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "verification" / "validate_scope_boundary"

    def test_maps_still_grandfathered_sync_sibling_to_flat_home(self) -> None:
        """scripts/sync/ducklake_version.py (never on the 24-roster, never concern-split) still
        resolves to its flat grandfathered home via _NESTED_SUBPACKAGE_TEST_PREFIX -- proves
        Wave 10's retirement of "test_sync_ops.py" did not perturb the family-sibling prefix
        rule. (PLAN-coverage-paydown-ops-writer-sync-ops: this case used to run against
        scripts/sync/recommendations.py, but that source is now itself a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry -- see test_maps_scripts_sync_recommendations_to_concern_split_package
        below -- so it moved to a sibling that stays flat.)"""
        source = ROOT / "scripts" / "sync" / "ducklake_version.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "test_sync_ducklake_version.py"

    def test_maps_scripts_sync_recommendations_to_concern_split_package(self) -> None:
        """scripts/sync/recommendations.py maps to the tests/sync/recommendations/ concern-split
        package (PLAN-coverage-paydown-ops-writer-sync-ops: Option D registers it in
        _CONCERN_SPLIT_TEST_PACKAGES to recover the coverage its former flat mapping was
        discarding -- see tests/sync/recommendations/test_sync_recommendations_decisions.py)."""
        source = ROOT / "scripts" / "sync" / "recommendations.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "sync" / "recommendations"

    def test_maps_still_grandfathered_session_sibling_to_flat_home(self) -> None:
        """scripts/session/metrics.py (never on the 24-roster) still resolves to its flat
        grandfathered home via _NESTED_SUBPACKAGE_TEST_PREFIX -- proves Wave 10's retirement of
        "test_session_postflight.py" did not perturb the family-sibling prefix rule."""
        source = ROOT / "scripts" / "session" / "metrics.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "test_session_metrics.py"

    def test_maps_still_grandfathered_ci_rca_sibling_to_flat_home(self) -> None:
        """scripts/ci_rca/filing.py (never on the 24-roster) still resolves to its flat
        grandfathered home via _NESTED_SUBPACKAGE_TEST_PREFIX -- proves Wave 10's retirement of
        "test_ci_rca_evidence.py" did not perturb the family-sibling prefix rule."""
        source = ROOT / "scripts" / "ci_rca" / "filing.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "test_ci_rca_filing.py"

    def test_maps_scripts_roadmap_plan_document_to_concern_split_package(self) -> None:
        """scripts/roadmap/plan_document.py maps to the tests/roadmap/plan_document/ concern-split
        package (PLAN-decompose-test-plan-document: registers it in _CONCERN_SPLIT_TEST_PACKAGES
        so the former tests/test_plan_document.py monolith's decomposition resolves to a test
        PACKAGE DIRECTORY via rule 3, DIRECT CONCERN-SPLIT)."""
        source = ROOT / "scripts" / "roadmap" / "plan_document.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "roadmap" / "plan_document"

    def test_maps_still_grandfathered_roadmap_sibling_to_flat_home(self) -> None:
        """scripts/roadmap/plan_audit.py and scripts/roadmap/find_plan.py (never on the 24-roster,
        never concern-split) still resolve to their flat grandfathered homes -- proves registering
        scripts/roadmap/plan_document.py as a direct concern-split entry did not perturb its
        roadmap-family siblings."""
        assert map_source_to_test(ROOT / "scripts" / "roadmap" / "plan_audit.py") == ROOT / "tests" / "test_plan_audit.py"
        assert map_source_to_test(ROOT / "scripts" / "roadmap" / "find_plan.py") == ROOT / "tests" / "test_find_plan.py"

    def test_maps_backlog_health_package_to_flat_homes(self) -> None:
        """scripts/backlog_health/<stem>.py (a new nested subpackage, PLAN-backlog-health-detection)
        resolves to tests/test_backlog_health_<stem>.py via _NESTED_SUBPACKAGE_TEST_PREFIX -- the
        FLAT-HOME family map, not the _ALL_MIRROR_TARGET_HOMES mirror-package route (that
        membership is a frozenset documented as never mutated)."""
        assert (
            map_source_to_test(ROOT / "scripts" / "backlog_health" / "census.py")
            == ROOT / "tests" / "test_backlog_health_census.py"
        )
        assert (
            map_source_to_test(ROOT / "scripts" / "backlog_health" / "probe.py")
            == ROOT / "tests" / "test_backlog_health_probe.py"
        )

    def test_returns_none_for_unmapped_path(self, tmp_path: Path) -> None:
        """Paths not under src/ or scripts/ return None."""
        source = tmp_path / "docs" / "README.py"
        result = map_source_to_test(source)
        assert result is None

    def test_returns_none_for_tests_dir(self) -> None:
        """Paths under tests/ return None (not mapped to themselves)."""
        source = ROOT / "tests" / "test_config.py"
        result = map_source_to_test(source)
        assert result is None

    def test_maps_src_flat_to_test(self) -> None:
        """src/common/ducklake_runtime.py maps to tests/test_ducklake_runtime.py."""
        source = ROOT / "src" / "data" / "pipeline.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "test_pipeline.py"

    def test_maps_scripts_checks_nested_module_to_mirror(self) -> None:
        """scripts/checks/<domain>/<module>.py maps to its per-check mirror test
        (tests/checks/<domain>/test_<module>.py) post rec-2709 Wave 1 retirement.

        Closes the coverage-gate hole: the pre-extension rule (len(parts) == 2) silently
        skipped every nested scripts/checks/** module.
        """
        source = ROOT / "scripts" / "checks" / "sloc" / "sloc_limits.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "sloc" / "test_sloc_limits.py"

    def test_maps_scripts_checks_domain_helper_to_mirror(self) -> None:
        """A domain-package helper module (e.g. contracts/_shared.py) mirrors to
        tests/checks/<domain>/test__shared.py post rec-2709 Wave 1 (no test file actually
        exists at that path -- contracts/_shared.py has no public defs -- this assertion is
        about map_source_to_test's computed path, not file presence; see
        PLAN-sloc-test-validate.yaml's LATENT OBLIGATIONS context note for the domain
        _shared.py helpers)."""
        source = ROOT / "scripts" / "checks" / "contracts" / "_shared.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "contracts" / "test__shared.py"

    def test_maps_scripts_checks_registry_to_its_concern_split_package(self) -> None:
        """scripts/checks/registry.py is a declared concern-split monolith (Decision 169) and
        maps to its own test package directory, not test_validate.py nor the retired
        tests/test_checks_registry.py."""
        source = ROOT / "scripts" / "checks" / "registry.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "registry"

    def test_maps_scripts_checks_common_to_its_concern_split_package(self) -> None:
        """scripts/checks/_common.py is a declared concern-split monolith (Decision 169) and
        maps to its own test package directory, not test_validate.py nor the retired
        tests/test_checks_registry.py."""
        source = ROOT / "scripts" / "checks" / "_common.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "_common"

    @pytest.mark.parametrize(
        "stem",
        ["ducklake_writes", "ducklake_tables", "ducklake_reads", "ducklake_metrics"],
    )
    def test_maps_ducklake_runtime_split_modules_resolve_to_their_common_mirror(self, stem: str) -> None:
        """The four ducklake_runtime split-out src/common modules map to their own
        tests/common/test_ducklake_<stem>.py mirror (rec-2709 Wave 7: "test_ducklake_runtime.py"
        retired from _RETIRING_GRANDFATHER_HOMES). Proves the crux: _DUCKLAKE_RUNTIME_SPLIT_MODULES
        is KEPT (not removed) -- it still routes these four to the ducklake_runtime grandfather
        home, and once that home retires, the mirror branch (drop-root, non-concern-split) resolves
        each to its real per-module test home instead of the flat tests/test_ducklake_<stem>.py a
        removed special-case would wrongly produce."""
        source = ROOT / "src" / "common" / f"{stem}.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "common" / f"test_{stem}.py"

    def test_maps_ducklake_neon_smoke_test_to_concern_split_package(self) -> None:
        """scripts/ducklake_neon_smoke_test.py maps to the tests/ducklake_neon_smoke_test/
        concern-split package (rec-2709 Wave 7: "test_ducklake_neon_smoke_test.py" retired from
        _RETIRING_GRANDFATHER_HOMES, and scripts/ducklake_neon_smoke_test.py is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry, already seeded before this wave)."""
        source = ROOT / "scripts" / "ducklake_neon_smoke_test.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "ducklake_neon_smoke_test"

    def test_maps_ducklake_writer_smoke_actions_to_handler_concern_split_package(self) -> None:
        """src/lambdas/ducklake_writer/smoke_actions.py maps to the tests/lambdas/ducklake_writer/handler/
        concern-split package -- it shares handler.py's home (Edit C / rec-2709 Wave 8, mirroring the
        _ORCHESTRATION_SCAFFOLDING_FILES precedent: "test_ducklake_writer_handler.py" retired from
        _RETIRING_GRANDFATHER_HOMES, and src/lambdas/ducklake_writer/handler.py is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry)."""
        source = ROOT / "src" / "lambdas" / "ducklake_writer" / "smoke_actions.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "lambdas" / "ducklake_writer" / "handler"

    def test_maps_ducklake_writer_handler_to_concern_split_package(self) -> None:
        """src/lambdas/ducklake_writer/handler.py maps to the tests/lambdas/ducklake_writer/handler/
        concern-split package (rec-2709 Wave 8: "test_ducklake_writer_handler.py" retired from
        _RETIRING_GRANDFATHER_HOMES, and src/lambdas/ducklake_writer/handler.py is a declared
        _CONCERN_SPLIT_TEST_PACKAGES entry) -- keyed off the parent lambda-slug directory (RS-08)
        rather than the handler.py stem, so it no longer collides with the other lambdas' handler.py
        files on the retired tests/test_handler.py shim."""
        source = ROOT / "src" / "lambdas" / "ducklake_writer" / "handler.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "lambdas" / "ducklake_writer" / "handler"

    def test_maps_ducklake_maintenance_handler_to_concern_split_package(self) -> None:
        """src/lambdas/ducklake_maintenance/handler.py maps to the tests/lambdas/ducklake_maintenance/
        handler/ concern-split package (rec-2709 Wave 8: "test_ducklake_maintenance_handler.py"
        retired from _RETIRING_GRANDFATHER_HOMES, and src/lambdas/ducklake_maintenance/handler.py is
        a declared _CONCERN_SPLIT_TEST_PACKAGES entry)."""
        source = ROOT / "src" / "lambdas" / "ducklake_maintenance" / "handler.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "lambdas" / "ducklake_maintenance" / "handler"

    def test_other_lambda_dirs_get_their_own_parent_qualified_test(self) -> None:
        """A non-ducklake_writer lambda dir resolves to its OWN distinct test home -- the RS-08
        parent-qualified rule applies uniformly to every src/lambdas/<slug>/ directory, not just
        ducklake_writer (the pre-generalization special case)."""
        source = ROOT / "src" / "lambdas" / "ducklake_reader" / "handler.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "test_ducklake_reader_handler.py"

    def test_all_lambda_handlers_map_to_distinct_existing_parent_qualified_tests(self) -> None:
        """RS-08, growth-safe (rec-2709 Wave 8): every src/lambdas/*/handler.py resolves to its own
        distinct, EXISTING test home -- a concern-split package dir (tests/lambdas/<slug>/handler/)
        once its flat home has retired from _RETIRING_GRANDFATHER_HOMES, else the still-grandfathered
        flat tests/test_{slug}_handler.py. smoke_actions.py shares ducklake_writer's home either way;
        none collides on the retired tests/test_handler.py shim."""
        # Derive the slug set from disk (growth-safe: a future lambda is covered automatically, and
        # one added without a parent-qualified test home fails the checks below). Do NOT hardcode
        # a list of a collection that grows by addition -- tests/CLAUDE.md test-count-coupling rule.
        handler_paths = sorted((ROOT / "src" / "lambdas").glob("*/handler.py"))
        assert handler_paths, "no src/lambdas/*/handler.py found -- glob is wrong"
        handler_results = {p.parent.name: map_source_to_test(p) for p in handler_paths}

        for slug, result in handler_results.items():
            flat_home = f"test_{slug}_handler.py"
            if flat_home in _ALL_MIRROR_TARGET_HOMES and flat_home not in _RETIRING_GRANDFATHER_HOMES:
                assert result == ROOT / "tests" / "lambdas" / slug / "handler", (slug, result)
                assert result.is_dir() and any(result.glob("test_*.py")), f"empty/missing package for {slug}: {result}"
            else:
                flat = ROOT / "tests" / flat_home
                mirror = ROOT / "tests" / "lambdas" / slug / "test_handler.py"
                assert flat.is_file() != mirror.is_file(), f"exactly one of the flat and mirror homes must exist for {slug}"
                expected = flat if flat.is_file() else mirror
                assert result == expected, (slug, result)
                assert result.is_file(), f"missing test home for {slug}: {result}"

        # Every handler's home is distinct from every other handler's home (no collision).
        assert len({str(r) for r in handler_results.values()}) == len(handler_results)

        # None resolves to the retired shim.
        assert all(r != ROOT / "tests" / "test_handler.py" for r in handler_results.values())

        # smoke_actions.py (split-out from ducklake_writer/handler.py) shares that lambda's home.
        smoke_actions_result = map_source_to_test(ROOT / "src" / "lambdas" / "ducklake_writer" / "smoke_actions.py")
        assert smoke_actions_result == handler_results["ducklake_writer"]

    def test_telemetry_writer_modules_map_to_their_own_mirror_tests(self) -> None:
        """With no flat home, each telemetry writer module is measured by its own mirror file (rule 4, rec-3809)."""
        base = ROOT / "src" / "lambdas" / "ducklake_telemetry_writer"
        tests = ROOT / "tests" / "lambdas" / "ducklake_telemetry_writer"
        assert map_source_to_test(base / "handler.py") == tests / "test_handler.py"
        assert map_source_to_test(base / "telemetry_actions.py") == tests / "test_telemetry_actions.py"
        assert (tests / "test_handler.py").is_file() and (tests / "test_telemetry_actions.py").is_file()
        assert not (ROOT / "tests" / "test_ducklake_telemetry_writer_handler.py").exists()

    @pytest.mark.parametrize(
        ("stem", "expected_test_name"),
        [
            ("record", "test_record.py"),
            ("approvals", "test_approvals.py"),
            ("assess", "test_assess.py"),
            ("escalate", "test_escalate.py"),
            ("__main__", "test___main__.py"),
        ],
    )
    def test_maps_convergence_health_submodules_to_their_own_mirror(self, stem: str, expected_test_name: str) -> None:
        # rec-2709 Wave 6 PACKAGE-MIRROR: each submodule maps 1:1 to its own mirror file.
        # code_drift.py is EXCLUDED from this parametrize set -- it is now a direct
        # _CONCERN_SPLIT_TEST_PACKAGES entry (PLAN-convergence-health-prod-drift-red) and maps to
        # a test PACKAGE DIRECTORY instead; see TestCodeDriftConcernSplitRegistration below.
        source = ROOT / "scripts" / "convergence_health" / f"{stem}.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "convergence_health" / expected_test_name


class TestMirrorRule:
    """Once a wave retires a home (removes it from _RETIRING_GRANDFATHER_HOMES), sources that
    grandfather to it resolve via the mirror rule instead."""

    def test_package_source_resolves_to_mirror_path_once_retired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        retired = _ALL_MIRROR_TARGET_HOMES - {"test_validate.py"}
        monkeypatch.setattr(_checker, "_RETIRING_GRANDFATHER_HOMES", retired)

        source = ROOT / "scripts" / "checks" / "hygiene" / "validate_prose_allowlist.py"
        result = map_source_to_test(source)

        assert result == ROOT / "tests" / "checks" / "hygiene" / "test_validate_prose_allowlist.py"

    def test_concern_split_monolith_resolves_to_test_package_directory_once_retired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        retired = _ALL_MIRROR_TARGET_HOMES - {"test_ops_data_portal.py"}
        monkeypatch.setattr(_checker, "_RETIRING_GRANDFATHER_HOMES", retired)

        source = ROOT / "scripts" / "ops_data_portal.py"
        result = map_source_to_test(source)

        assert result == ROOT / "tests" / "ops_data_portal"

    def test_nested_concern_split_monolith_keeps_subdir_once_retired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """src/common/ducklake_reader_client.py (nested under common/) mirrors to
        tests/common/ducklake_reader_client/, not tests/ducklake_reader_client/ -- the
        mirror-subpath is preserved for non-root sources."""
        retired = _ALL_MIRROR_TARGET_HOMES - {"test_ducklake_reader_client.py"}
        monkeypatch.setattr(_checker, "_RETIRING_GRANDFATHER_HOMES", retired)

        source = ROOT / "src" / "common" / "ducklake_reader_client.py"
        result = map_source_to_test(source)

        assert result == ROOT / "tests" / "common" / "ducklake_reader_client"


class TestContractDriftConcernSplitRegistration:
    """scripts/checks/contracts/validate_contract_drift.py (T2.56 / contracts-first-class-
    migration) is registered directly in _CONCERN_SPLIT_TEST_PACKAGES -- independent of any
    _RETIRING_GRANDFATHER_HOMES membership (its grandfathered home, tests/test_validate.py, is
    itself already retired), exercising map_source_to_test's item-3 direct-membership branch."""

    def test_maps_validate_contract_drift_to_concern_split_package_directory(self) -> None:
        source = ROOT / "scripts" / "checks" / "contracts" / "validate_contract_drift.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "contracts" / "validate_contract_drift"

    def test_contract_drift_pre_decomposition_single_file_module_is_gone(self) -> None:
        assert not (ROOT / "tests" / "checks" / "contracts" / "test_validate_contract_drift.py").exists()


class TestCodeDriftConcernSplitRegistration:
    """scripts/convergence_health/code_drift.py (PLAN-convergence-health-prod-drift-red) is
    registered directly in _CONCERN_SPLIT_TEST_PACKAGES so the concern-split test decomposition
    (tests/convergence_health/code_drift/) keeps measuring 100% coverage of the source module --
    without this registration map_source_to_test would resolve the single retired flat mirror
    file that no longer exists post-split, dropping the module out of the coverage roster
    entirely (see the plan's coverage-measurement note). The retired file's non-existence is
    swept authoritatively by the plan's own VP step 10 (a repo-wide grep for dangling
    references), not re-asserted here."""

    def test_maps_code_drift_to_concern_split_package_directory(self) -> None:
        source = ROOT / "scripts" / "convergence_health" / "code_drift.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "convergence_health" / "code_drift"


class TestEventReadsConcernSplitRegistration:
    """src/common/ducklake_event_reads.py is registered in _CONCERN_SPLIT_TEST_PACKAGES so its coverage target is the
    concern-split package tests/common/ducklake_event_reads/ (Decision 128) rather than a retired flat mirror file."""

    def test_maps_ducklake_event_reads_to_concern_split_package_directory(self) -> None:
        source = ROOT / "src" / "common" / "ducklake_event_reads.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "common" / "ducklake_event_reads"


class TestBudgetIngestConcernSplitRegistration:
    """scripts/convergence_health/budget_ingest.py (rec-3288 wave-4 fixups) is registered directly
    in _CONCERN_SPLIT_TEST_PACKAGES for the same reason code_drift.py above is: the wave-4 review
    fixes pushed its single flat mirror file past the 500-SLOC limit, so the tests decomposed by
    concern into tests/convergence_health/budget_ingest/ (Decision 128 -- decompose, never raise).
    Without this registration map_source_to_test resolves the retired flat mirror file that no
    longer exists, dropping the module out of the coverage roster entirely."""

    def test_maps_budget_ingest_to_concern_split_package_directory(self) -> None:
        source = ROOT / "scripts" / "convergence_health" / "budget_ingest.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "convergence_health" / "budget_ingest"

    def test_budget_ingest_pre_decomposition_single_file_module_is_gone(self) -> None:
        assert not (ROOT / "tests" / "convergence_health" / "test_budget_ingest.py").exists()


class TestPlatformRoadmapModelsConcernSplitRegistration:
    """scripts/platform_roadmap_models.py (PLAN-roadmap-blocking-edge-semantics) is registered
    directly in _CONCERN_SPLIT_TEST_PACKAGES, alongside its sibling scripts/platform_roadmap_state.py
    entry. Without this registration map_source_to_test falls through _grandfathered_source_to_test's
    generic scripts-root rule to tests/test_platform_roadmap_models.py -- a file this plan deletes in
    favor of the tests/platform_roadmap_models/ mirror package -- dropping the module out of the
    coverage roster entirely and breaking check_test_file_exists / validate_diff_coverage for a file
    that is itself in scope."""

    def test_maps_platform_roadmap_models_to_concern_split_package(self) -> None:
        source = ROOT / "scripts" / "platform_roadmap_models.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "platform_roadmap_models"

    def test_platform_roadmap_models_pre_decomposition_single_file_module_is_gone(self) -> None:
        assert not (ROOT / "tests" / "test_platform_roadmap_models.py").exists()


class TestValidatePlacementConcernSplitRegistration:
    """scripts/checks/hygiene/validate_placement.py's tests were concern-split into
    tests/checks/hygiene/validate_placement/ (three test modules) at #1037 without registering
    the split, so map_source_to_test computed the non-existent FLAT file
    tests/checks/hygiene/test_validate_placement.py and check_test_file_exists returned
    (False, "missing test file") for it -- a live latent false negative for validate_test_coverage
    (PLAN-red-case-floor's precondition fix: this is the one Entry of 123 the red-case floor's
    canonical-map dependency could not resolve)."""

    def test_maps_validate_placement_to_concern_split_package_directory(self) -> None:
        source = ROOT / "scripts" / "checks" / "hygiene" / "validate_placement.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "hygiene" / "validate_placement"

    def test_check_test_file_exists_finds_the_package(self) -> None:
        source = ROOT / "scripts" / "checks" / "hygiene" / "validate_placement.py"
        ok, why = check_test_file_exists(source)
        assert ok is True
        assert why == "test package found"


class TestVpReplayConcernSplitRegistration:
    """scripts/checks/verification/validate_vp_replay.py (PLAN-vp-replay-mirror-decomposition) is
    registered directly in _CONCERN_SPLIT_TEST_PACKAGES: its tests were concern-split into
    tests/checks/verification/validate_vp_replay/ (four test modules) alongside the deletion of
    the former flat single-file mirror. Without this registration map_source_to_test resolves the
    deleted flat file, and validate_red_case_floor fails on an unresolvable mirror rather than a
    skip."""

    def test_maps_vp_replay_to_concern_split_package(self) -> None:
        source = ROOT / "scripts" / "checks" / "verification" / "validate_vp_replay.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "verification" / "validate_vp_replay"


class TestRedCaseFloorConcernSplitRegistration:
    """scripts/checks/verification/validate_red_case_floor.py (PLAN-loop-spec-audit-tail, rec-3727)
    is registered directly in _CONCERN_SPLIT_TEST_PACKAGES: its tests were concern-split into
    tests/checks/verification/validate_red_case_floor/ (test_red_case_signal.py, relocated
    unchanged, plus the new test_within_package_file_deletion.py) alongside the deletion of the
    former flat single-file mirror. Without this registration map_source_to_test resolves the
    deleted flat file, and the red-case floor's own OWN Entry fails on an unresolvable mirror
    rather than clearing itself -- the exact self-hosting property TestSelfApplication pins."""

    def test_maps_red_case_floor_to_concern_split_package(self) -> None:
        source = ROOT / "scripts" / "checks" / "verification" / "validate_red_case_floor.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "verification" / "validate_red_case_floor"

    def test_check_test_file_exists_finds_the_package(self) -> None:
        source = ROOT / "scripts" / "checks" / "verification" / "validate_red_case_floor.py"
        ok, why = check_test_file_exists(source)
        assert ok is True
        assert why == "test package found"


class TestPreGlobClosureConcernSplitRegistration:
    """scripts/checks/deps/validate_pre_glob_closure.py (PLAN-loop-spec-audit-tail, LSA-06/rec-3558)
    is registered directly in _CONCERN_SPLIT_TEST_PACKAGES: its tests were concern-split into
    tests/checks/deps/validate_pre_glob_closure/ (test_closure_and_floor.py, test_pruned_edges_
    roster.py, test_blocking_contract.py) alongside the deletion of the former flat single-file
    mirror (414 SLOC; new tests would have crossed 500). Without this registration
    map_source_to_test resolves the deleted flat file, and this auditor's own dogfooded Entry
    (TestLiveTreeSmoke::test_its_own_entry_is_one_of_the_audited_ones) would fail on an
    unresolvable mirror the moment the auditor became blocking."""

    def test_maps_pre_glob_closure_to_concern_split_package(self) -> None:
        source = ROOT / "scripts" / "checks" / "deps" / "validate_pre_glob_closure.py"
        result = map_source_to_test(source)
        assert result is not None
        assert result == ROOT / "tests" / "checks" / "deps" / "validate_pre_glob_closure"

    def test_check_test_file_exists_finds_the_package(self) -> None:
        source = ROOT / "scripts" / "checks" / "deps" / "validate_pre_glob_closure.py"
        ok, why = check_test_file_exists(source)
        assert ok is True
        assert why == "test package found"
