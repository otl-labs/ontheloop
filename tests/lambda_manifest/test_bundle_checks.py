"""Tests for scripts/lambda_manifest.py -- bundle-integrity checks + pattern derivation concern
(VERBATIM split from tests/test_lambda_manifest.py, rec-2709 Wave 12).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.lambda_manifest import (
    LambdaManifest,
    check_assets_present,
    check_handler_imports,
    compute_affected_artifacts,
    derive_lambda_file_patterns,
    load_all,
    stage_bundle,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# tests for check_handler_imports
# ---------------------------------------------------------------------------


class TestCheckHandlerImports:
    def test_missing_handler_file(self, tmp_path):
        m = LambdaManifest(artifact="a.zip", handlers=["src/missing.py"])
        errors = check_handler_imports(m, tmp_path)
        assert any("not found" in e for e in errors)

    def test_successful_import(self, tmp_path):
        handler = tmp_path / "src" / "h.py"
        handler.parent.mkdir(parents=True)
        handler.write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
        m = LambdaManifest(artifact="a.zip", handlers=["src/h.py"])
        errors = check_handler_imports(m, tmp_path)
        assert errors == []

    def test_missing_module_reported(self, tmp_path):
        handler = tmp_path / "src" / "h.py"
        handler.parent.mkdir(parents=True)
        handler.write_text("import nonexistent_module_xyz\n", encoding="utf-8")
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
        m = LambdaManifest(artifact="a.zip", handlers=["src/h.py"])
        errors = check_handler_imports(m, tmp_path)
        assert len(errors) == 1
        assert "nonexistent_module_xyz" in errors[0]

    def test_import_failure_non_module_error(self, tmp_path):
        handler = tmp_path / "src" / "h.py"
        handler.parent.mkdir(parents=True)
        handler.write_text("raise RuntimeError('bad import')\n", encoding="utf-8")
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
        m = LambdaManifest(artifact="a.zip", handlers=["src/h.py"])
        errors = check_handler_imports(m, tmp_path)
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# check_assets_present
# ---------------------------------------------------------------------------


class TestCheckAssetsPresent:
    def test_passes_when_all_present(self, tmp_path):
        asset = tmp_path / "data" / "file.yaml"
        asset.parent.mkdir()
        asset.write_text("x: 1", encoding="utf-8")
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()
        (stage_dir / "data").mkdir()
        (stage_dir / "data" / "file.yaml").write_text("x: 1", encoding="utf-8")
        m = LambdaManifest(artifact="a.zip", assets=["data/file.yaml"])
        with patch("scripts.lambda_manifest.ROOT", tmp_path):
            errors = check_assets_present(m, stage_dir)
        assert errors == []

    def test_fails_when_staged_missing(self, tmp_path):
        asset = tmp_path / "data" / "file.yaml"
        asset.parent.mkdir()
        asset.write_text("x: 1", encoding="utf-8")
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()
        m = LambdaManifest(artifact="a.zip", assets=["data/file.yaml"])
        with patch("scripts.lambda_manifest.ROOT", tmp_path):
            errors = check_assets_present(m, stage_dir)
        assert len(errors) == 1
        assert "data/file.yaml" in errors[0]

    def test_skips_optional_files(self, tmp_path):
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()
        m = LambdaManifest(artifact="a.zip", assets=["config/config.yaml"])
        with patch("scripts.lambda_manifest.ROOT", tmp_path):
            errors = check_assets_present(m, stage_dir)
        assert errors == []  # file doesn't exist in repo root -> skip

    def test_checks_config_paths(self, tmp_path):
        config_dir = tmp_path / "config" / "lambda" / "app"
        config_dir.mkdir(parents=True)
        (config_dir / "env.yaml").write_text("", encoding="utf-8")
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()
        m = LambdaManifest(artifact="a.zip", config=["config/lambda/app/"])
        with patch("scripts.lambda_manifest.ROOT", tmp_path):
            errors = check_assets_present(m, stage_dir)
        assert len(errors) == 1


# ---------------------------------------------------------------------------
# LAMBDA_FILE_PATTERNS derivation
# ---------------------------------------------------------------------------


class TestLambdaFilePatterns:
    def test_derives_patterns_from_active_manifest(self, tmp_path):
        func_dir = tmp_path / "myfunc"
        func_dir.mkdir()
        (func_dir / "manifest.yaml").write_text(
            "artifact: myfunc.zip\nhandlers: [src/data/handlers/h.py]\n"
            "includes: [src/]\nassets: [.github/agents/schedule.yaml]\n"
            "config: [config/lambda/myfunc/]\n",
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        (tmp_path / ".github" / "agents" / "schedule.yaml").write_text("", encoding="utf-8")
        (tmp_path / "config" / "lambda" / "myfunc").mkdir(parents=True)
        with patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path):
            with patch("scripts.lambda_manifest.ROOT", tmp_path):
                patterns = derive_lambda_file_patterns()
        assert any("src/" in p for p in patterns)
        assert any("schedule.yaml" in p for p in patterns)

    def test_stub_manifests_excluded(self, tmp_path):
        func_dir = tmp_path / "stubfunc"
        func_dir.mkdir()
        (func_dir / "manifest.yaml").write_text(
            "artifact: stubfunc.zip\nstatus: stub\nhandlers: [src/h.py]\n",
            encoding="utf-8",
        )
        with patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path):
            with patch("scripts.lambda_manifest.ROOT", tmp_path):
                patterns = derive_lambda_file_patterns()
        assert patterns == []

    def test_returns_empty_on_load_failure(self, tmp_path):
        with patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path / "nonexistent"):
            patterns = derive_lambda_file_patterns()
        assert patterns == []


# ---------------------------------------------------------------------------
# compute_affected_artifacts (deploy gating)
# ---------------------------------------------------------------------------


class TestComputeAffectedArtifacts:
    def _make_manifest(self, tmp_path: Path, slug: str, handlers: list[str]) -> None:
        func_dir = tmp_path / slug
        func_dir.mkdir(exist_ok=True)
        content = {
            "artifact": f"{slug}.zip",
            "handlers": handlers,
            "includes": ["src/"],
            "status": "active",
        }
        (func_dir / "manifest.yaml").write_text(yaml.dump(content), encoding="utf-8")
        (tmp_path / "src").mkdir(exist_ok=True)

    def test_returns_matching_artifact(self, tmp_path):
        self._make_manifest(tmp_path, "myfunc", ["src/data/handlers/h.py"])
        with (
            patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path),
            patch("scripts.lambda_manifest.ROOT", tmp_path),
        ):
            result = compute_affected_artifacts(["src/data/handlers/h.py"])
        assert "myfunc" in result
        assert "src/data/handlers/h.py" in result["myfunc"]

    def test_returns_empty_for_unrelated_changes(self, tmp_path):
        self._make_manifest(tmp_path, "myfunc", ["src/data/handlers/h.py"])
        with (
            patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path),
            patch("scripts.lambda_manifest.ROOT", tmp_path),
        ):
            result = compute_affected_artifacts(["docs/README.md"])
        assert result == {}

    def test_stub_manifest_excluded(self, tmp_path):
        func_dir = tmp_path / "stubfunc"
        func_dir.mkdir()
        content = {"artifact": "stubfunc.zip", "handlers": ["src/h.py"], "status": "stub"}
        (func_dir / "manifest.yaml").write_text(yaml.dump(content), encoding="utf-8")
        with (
            patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path),
            patch("scripts.lambda_manifest.ROOT", tmp_path),
        ):
            result = compute_affected_artifacts(["src/h.py"])
        assert result == {}

    def test_manifest_yaml_change_matches(self, tmp_path):
        self._make_manifest(tmp_path, "myfunc", [])
        with (
            patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path),
            patch("scripts.lambda_manifest.ROOT", tmp_path),
        ):
            result = compute_affected_artifacts(["src/lambdas/myfunc/manifest.yaml"])
        assert "myfunc" in result

    def test_returns_empty_on_load_failure(self, tmp_path):
        with patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path / "nonexistent"):
            result = compute_affected_artifacts(["any/file.py"])
        assert result == {}

    def test_manifest_yaml_change_matches_when_declared_paths_do_not_cover_it(self, tmp_path):
        """The not-already-matched append arm.

        test_manifest_yaml_change_matches cannot reach it: its fixture declares includes of
        src/, so src/lambdas/<slug>/manifest.yaml is already appended by the prefix match and
        the inner not-in-matches guard is False. A manifest whose declared paths do not cover
        its own manifest.yaml is the only shape that reaches the append.
        """
        func_dir = tmp_path / "myfunc"
        func_dir.mkdir()
        content = {"artifact": "myfunc.zip", "handlers": [], "includes": ["docs/"], "status": "active"}
        (func_dir / "manifest.yaml").write_text(yaml.dump(content), encoding="utf-8")
        with (
            patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path),
            patch("scripts.lambda_manifest.ROOT", tmp_path),
        ):
            result = compute_affected_artifacts(["src/lambdas/myfunc/manifest.yaml"])
        assert result == {"myfunc": ["src/lambdas/myfunc/manifest.yaml"]}

    def test_manifest_yaml_change_is_not_double_counted_when_a_prefix_also_matches(self, tmp_path):
        """The not-already-matched guard itself, in the shape where it is load-bearing.

        _make_manifest declares includes of src/, so the prefix match has ALREADY appended
        src/lambdas/<slug>/manifest.yaml by the time the manifest.yaml arm runs; the guard is
        the only thing stopping a second append. The exact single-element list is what
        discriminates it -- a membership assertion passes just as well against the duplicate.
        """
        self._make_manifest(tmp_path, "myfunc", [])
        with (
            patch("scripts.lambda_manifest._LAMBDAS_DIR", tmp_path),
            patch("scripts.lambda_manifest.ROOT", tmp_path),
        ):
            result = compute_affected_artifacts(["src/lambdas/myfunc/manifest.yaml"])
        assert result == {"myfunc": ["src/lambdas/myfunc/manifest.yaml"]}


# ---------------------------------------------------------------------------
# Two-sided bundling invariant (T2.26 control-table-class-and-counter-conformance): every
# artifact that bundles ducklake_scd2_schema.py also bundles the new control module, AND every
# artifact that excludes it also excludes the control module. Asserted against the REAL manifests
# (not a tmp_path fixture) -- this runs in --pre; --check-bundles (validate_lambda_bundle_completeness)
# does not, which is why this standing assertion is needed rather than relying on the staged-import
# oracle to gate the PR.
# ---------------------------------------------------------------------------


def test_new_ducklake_modules_track_scd2_schema_membership():
    scd2_module = "src/common/ducklake_scd2_schema.py"
    control_module = "src/common/ducklake_control_tables.py"
    split_modules = ("src/common/ducklake_named_reads.py", "src/common/ducklake_write_verbs.py")
    manifests = load_all()
    assert manifests, "expected at least one real src/lambdas/*/manifest.yaml"

    checked_include = 0
    checked_exclude = 0
    for slug, manifest in manifests.items():
        includes = set(manifest.includes)
        excludes = set(manifest.excludes)
        if scd2_module in includes:
            checked_include += 1
            assert control_module in includes, f"{slug}: bundles {scd2_module} but not {control_module} -- add an includes row"
            for mod in split_modules:
                assert mod in includes, f"{slug}: bundles {scd2_module} but not {mod} -- add an includes row"
        if scd2_module in excludes:
            checked_exclude += 1
            assert control_module in excludes, (
                f"{slug}: excludes {scd2_module} but not {control_module} -- add an excludes row"
            )
            for mod in split_modules:
                assert mod in excludes, f"{slug}: excludes {scd2_module} but not {mod} -- add an excludes row"
    # Growth-safe (never a hardcoded count): both sides of the invariant must have been exercised
    # at least once, or this test would vacuously pass if every manifest happened to reference
    # neither module.
    assert checked_include > 0
    assert checked_exclude > 0


# ---------------------------------------------------------------------------
# compaction-scope-policy-matrix (T2.18): the new scope module ships with the maintenance Lambda
# (action_merge_ops depends on it directly) and is explicitly excluded from data-pipeline (the
# same wildcard-includes-src/-plus-explicit-excludes pattern the other ducklake_* modules use).
# ---------------------------------------------------------------------------


def test_maintenance_scope_module_bundled_and_excluded():
    scope_module = "src/common/ducklake_maintenance_scope.py"
    manifests = load_all()
    maintenance = manifests["ducklake_maintenance"]
    data_pipeline = manifests["data-pipeline"]
    assert scope_module in set(maintenance.includes), "ducklake_maintenance manifest must bundle the scope module"
    assert scope_module in set(data_pipeline.excludes), "data-pipeline manifest must exclude the scope module"


# ---------------------------------------------------------------------------
# PLAN-telemetry-kernel-identity-append: src/telemetry stays out of data-pipeline (Decision 79
# runtime-import intent, V2 ground) -- the kernel has no writer/reader Lambda yet.
# ---------------------------------------------------------------------------


def test_telemetry_kernel_excluded_from_data_pipeline(tmp_path):
    manifests = load_all()
    data_pipeline = manifests["data-pipeline"]
    assert "src/telemetry" in set(data_pipeline.excludes), "data-pipeline manifest must exclude src/telemetry"

    changed = [
        "src/telemetry/__init__.py",
        "src/telemetry/identity.py",
        "src/telemetry/timestamps.py",
        "src/telemetry/gate.py",
        "src/telemetry/append.py",
    ]
    affected = compute_affected_artifacts(changed)
    assert "data-pipeline" not in affected, affected

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    stage_bundle(data_pipeline, stage_dir, skip_pip=True)
    staged_telemetry_paths = [p for p in stage_dir.rglob("*") if "telemetry" in p.parts]
    assert staged_telemetry_paths == [], f"src/telemetry leaked into data-pipeline.zip: {staged_telemetry_paths}"


# ---------------------------------------------------------------------------
# PLAN-telemetry-turn-capture-core: src/turn_capture stays out of data-pipeline (Decision 79 V2 ground);
# the plane-neutral transcript producer core has no Lambda in slice 3a.
# ---------------------------------------------------------------------------


def test_turn_capture_excluded_from_data_pipeline(tmp_path):
    manifests = load_all()
    data_pipeline = manifests["data-pipeline"]
    assert "src/turn_capture" in set(data_pipeline.excludes), "data-pipeline manifest must exclude src/turn_capture"

    changed = [
        "src/turn_capture/__init__.py",
        "src/turn_capture/record_turn.py",
        "src/turn_capture/streams.py",
        "src/turn_capture/scrub.py",
    ]
    affected = compute_affected_artifacts(changed)
    assert "data-pipeline" not in affected, affected

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    stage_bundle(data_pipeline, stage_dir, skip_pip=True)
    staged_paths = [p for p in stage_dir.rglob("*") if "turn_capture" in p.parts]
    assert staged_paths == [], f"src/turn_capture leaked into data-pipeline.zip: {staged_paths}"


# ---------------------------------------------------------------------------
# PLAN-ducklake-typed-unmapped-errors: the terminal-answer helper ships with the three URL-only serving functions only.
# The async handlers (maintenance, maintenance_smoke, catalog_dr) keep Lambda's own retry behaviour (follow-on rec-4303).
# ---------------------------------------------------------------------------


def test_handler_errors_module_bundled_by_serving_functions_only(tmp_path):
    helper = "src/common/ducklake_handler_errors.py"
    manifests = load_all()
    for serving in ("ducklake_telemetry_writer", "ducklake_reader", "ducklake_writer"):
        assert helper in set(manifests[serving].includes), f"{serving} must bundle {helper}"
    assert helper in set(manifests["data-pipeline"].excludes), "data-pipeline must exclude the helper"
    for asynchronous in ("ducklake_maintenance", "ducklake_maintenance_smoke", "ducklake_catalog_dr"):
        assert helper not in set(manifests[asynchronous].includes), f"{asynchronous} must not bundle {helper} yet"
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    stage_bundle(manifests["data-pipeline"], stage_dir, skip_pip=True)
    assert list(stage_dir.rglob("ducklake_handler_errors.py")) == []


# ---------------------------------------------------------------------------
# PLAN-telemetry-transcript-content: the reader serves transcript_content, so its bundle carries the content module and the
# blob store adapter with their imports. ducklake_event_content is imported function-locally and --check-bundles does not
# run in --pre, so no pre-merge import check would see a missing include.
# ---------------------------------------------------------------------------


def test_reader_bundle_carries_no_event_read_module():
    manifests = load_all()
    reader = set(manifests["ducklake_reader"].includes)
    for module in (
        "src/common/ducklake_event_reads.py",
        "src/common/ducklake_event_read_sql.py",
        "src/common/ducklake_event_read_verbs.py",
        "src/common/ducklake_event_content.py",
        "src/common/ducklake_read_deadline.py",
        "src/common/ducklake_telemetry_blobstore.py",
        "src/telemetry/blobs.py",
        "src/telemetry/request.py",
    ):
        assert module not in reader, f"ducklake_reader must not bundle {module}"
    assert not [m for m in reader if m.startswith("src/telemetry/")]
    telemetry_reader = set(manifests["ducklake_telemetry_reader"].includes)
    for module in ("src/common/ducklake_event_reads.py", "src/common/ducklake_read_deadline.py"):
        assert module in telemetry_reader, module
    assert "src/common/ducklake_event_content.py" in set(manifests["data-pipeline"].excludes)


def test_reader_bundle_carries_the_dq_check_module():
    manifests = load_all()
    module = "src/common/ducklake_dq_checks.py"
    assert module in set(manifests["ducklake_reader"].includes)
    assert module in set(manifests["data-pipeline"].excludes)


# ---------------------------------------------------------------------------
# PLAN-sensitive-value-detectors: src/sensitive_values stays out of data-pipeline (stdlib leaf, rec-4481 plan 1).
# ---------------------------------------------------------------------------


def test_sensitive_values_excluded_from_data_pipeline(tmp_path):
    manifests = load_all()
    data_pipeline = manifests["data-pipeline"]
    assert "src/sensitive_values" in set(data_pipeline.excludes), "data-pipeline manifest must exclude src/sensitive_values"

    changed = [
        "src/sensitive_values/__init__.py",
        "src/sensitive_values/classes.py",
        "src/sensitive_values/engine.py",
        "src/sensitive_values/profiles.py",
    ]
    affected = compute_affected_artifacts(changed)
    assert "data-pipeline" not in affected, affected

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    stage_bundle(data_pipeline, stage_dir, skip_pip=True)
    staged_paths = [p for p in stage_dir.rglob("*") if "sensitive_values" in p.parts]
    assert staged_paths == [], f"src/sensitive_values leaked into data-pipeline.zip: {staged_paths}"
