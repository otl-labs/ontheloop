"""Tests for the scripts/ops_portal/ci_rca_schema.py enforcement surface: the ci-rca
source-file gate in file_rec(), the CI_RCA_STRICT_MODE feature flag, the CiRcaContext schema,
and reconcile_table_columns() rerun-safety.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).

_CI_RCA_FIELDS and _VALID_CONTEXT_V2 are duplicated verbatim from the monolith (also used by
test_ci_rca_dispute.py / test_ci_rca_runtime.py / test_ci_rca_propose_close.py) rather than
hoisted to tests/fixtures/ -- the plan hoists ONLY VALID_FIELDS / VALID_DECISION_FIELDS there;
these two stay private per-module duplicates, mirroring the sanctioned fingerprint-split
duplication pattern (no cross-test imports).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb")

from src.common import ducklake_runtime as rt  # noqa: E402
from src.common.ducklake_scd2_schema import load_field_semantics, resolve_table_spec  # noqa: E402
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402

_CI_RCA_FIELDS = {
    **_VALID_FIELDS,
    "source": "ci_rca",
    "context": ("CI RCA test rec with sufficient length to satisfy the 80-char minimum for the legacy context column field."),
}

_VALID_CONTEXT_V2 = {
    "schema_version": 1,
    "proximate_cause": (
        "validate_sloc_limits() raised: scripts/roadmap/platform_roadmap.py is 810 SLOC, exceeds 500 limit "
        "(Decision 43, no complexity-waiver header found in first 10 lines)."
    ),
    "why_chain": [
        "The file was committed at over 500 SLOC in a single PR with no incremental breakpoint.",
        "No local --pre check fired because validate_sloc_limits() is presubmit-tier only.",
        (
            "The validate_sloc_limits() check was placed in the presubmit tier not --pre despite being O(lines); "
            "this tier placement defect is the gap at scripts/validate.py:2294."
        ),
    ],
    "detection_gap": {
        "earliest_viable_gate": "pre",
        "actual_gate_that_caught_it": "CI",
        "gap_explanation": (
            "validate_sloc_limits() gates on scope=='all' at scripts/validate.py:2294, unreachable from "
            "--pre (exits at scripts/validate.py:2284). Gap is tier-placement, not logic."
        ),
    },
    "recurrence_class": "instance_of_known_pattern",
    "corrective_action": (
        "Add a complexity-waiver header OR refactor the module below 500 SLOC to satisfy the "
        "validate_sloc_limits() check in scripts/validate.py and unblock CI."
    ),
    "preventive_action": (
        "Promote validate_sloc_limits() to the --pre tier at scripts/validate.py so the check fires "
        "during local development and prevents the same tier-placement failure mode in future PRs. "
        "Additionally gate new check additions: require a documented tier-placement rationale."
    ),
}


class TestCiRcaSourceFileGate:
    """Tests for the ci-rca source-file gate in file_rec()."""

    _MINIMAL_CI_RCA_FIELDS = {
        "title": "CI broken: IAM gap in runner policy",
        "file": "terraform/ec2_runner.tf",
        "context": (
            "Runner IAM denied s3:GetObject on agent-logs/tmp/* during the upload-artifacts CI step. "
            "Error: AccessDeniedException was thrown. Fix: add s3:GetObject to the runner policy resource block."
        ),
        "acceptance": "grep -q 'GetObject' terraform/ec2_runner.tf && grep -q 's3' terraform/ec2_runner.tf",
        "effort": "S",
        "priority": "Critical",
        "source": "ci_rca",
        "risk": "low",
        "status": "open",
        "automatable": True,
    }

    def test_rejects_empty_file(self) -> None:
        fields = dict(self._MINIMAL_CI_RCA_FIELDS)
        fields["file"] = ""
        from scripts.ops_data_portal import file_rec

        with pytest.raises(ValueError) as exc_info:
            file_rec(fields)
        assert "source_file" in str(exc_info.value)

    def test_rejects_missing_file_key(self) -> None:
        fields = dict(self._MINIMAL_CI_RCA_FIELDS)
        fields.pop("file")
        from scripts.ops_data_portal import file_rec

        with pytest.raises(ValueError) as exc_info:
            file_rec(fields)
        assert "source_file" in str(exc_info.value)

    def test_accepts_populated_file(self, tmp_path: Path) -> None:
        fields = dict(self._MINIMAL_CI_RCA_FIELDS)
        from scripts.ops_data_portal import file_rec

        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-1234"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", tmp_path / "recs.jsonl"),
        ):
            result = file_rec(fields)
        assert result == "rec-1234"


class TestCiRcaSchemaEnforcement:
    """Tests for the CI_RCA_STRICT_MODE feature flag, CiRcaContext schema, and file_rec warn-mode validation."""

    def setup_method(self) -> None:
        import scripts.ops_data_portal as p

        p._ci_rca_strict_mode_cache = None  # reset the module-level cache before each test

    def test_flag_default_is_warn(self, tmp_path: Path) -> None:
        """get_ci_rca_strict_mode() returns 'warn' when the key is absent or file is missing."""
        import scripts.ops_data_portal as p

        with patch.object(p, "_FEATURE_FLAGS_YAML", tmp_path / "nonexistent.yaml"):
            result = p.get_ci_rca_strict_mode()
        assert result == "warn"

    def test_flag_reads_yaml(self, tmp_path: Path) -> None:
        """get_ci_rca_strict_mode() reads CI_RCA_STRICT_MODE from the feature flags YAML."""
        import scripts.ops_data_portal as p

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: warn\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            result = p.get_ci_rca_strict_mode()
        assert result == "warn"

    def test_flag_rejects_unknown_value(self, tmp_path: Path) -> None:
        """get_ci_rca_strict_mode() raises ValueError for unrecognised flag values."""
        import scripts.ops_data_portal as p

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: debug\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="CI_RCA_STRICT_MODE"):
                p.get_ci_rca_strict_mode()

    def test_guidance_returns_schema(self) -> None:
        """get_rec_write_guidance('ci_rca') returns all six CiRcaContext schema fields."""
        from scripts.executor.rec_write_guidance import get_rec_write_guidance

        guidance = get_rec_write_guidance(source="ci_rca")
        assert "context_v2_json" in guidance
        schema_fields = guidance["context_v2_json"]["schema_fields"]
        for field in [
            "proximate_cause",
            "why_chain",
            "detection_gap",
            "recurrence_class",
            "corrective_action",
            "preventive_action",
        ]:
            assert field in schema_fields, f"schema_fields missing {field!r}"

    def test_valid_context_v2_passes(self, tmp_path: Path) -> None:
        """file_rec(source=ci_rca, context_v2_json=<valid>) succeeds and persists context_v2_json."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9001"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            rec_id = p.file_rec(dict(_CI_RCA_FIELDS), context_v2_json=dict(_VALID_CONTEXT_V2))
        assert rec_id == "rec-9001"
        entry = mock_write.call_args.args[1]
        assert "context_v2_json" in entry
        stored = json.loads(entry["context_v2_json"])
        assert stored["schema_version"] == 1

    def test_deficient_why_chain_warns_not_raises(self, tmp_path: Path, caplog) -> None:
        """In warn mode a deficient why_chain logs a warning and does NOT raise."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        deficient_ctx = {**_VALID_CONTEXT_V2, "why_chain": ["short", "also short", "still short"]}
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: warn\n", encoding="utf-8")
        with (
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9002"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            rec_id = p.file_rec(dict(_CI_RCA_FIELDS), context_v2_json=deficient_ctx)
        assert rec_id == "rec-9002"
        assert any("CI_RCA_STRICT_MODE=warn" in r.message for r in caplog.records)

    def test_deficient_context_v2_with_empty_context_builds_short_fallback_summary(self, tmp_path: Path) -> None:
        """Warn mode continues past a context_v2_json missing all three summary-source fields
        (proximate_cause/corrective_action/preventive_action) -- the composed summary is then
        empty, the short-summary fallback text is appended, and (since fields["context"] is
        also empty) that fallback overwrites it. The fallback text itself stays under 80 chars,
        so the row-rule gate raises downstream -- this test's purpose is coverage of that
        composition path, not a successful file."""
        import scripts.ops_data_portal as p

        deficient_ctx = {"schema_version": 1}
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: warn\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="at least 80 stripped characters"):
                p.file_rec({**_CI_RCA_FIELDS, "context": ""}, context_v2_json=deficient_ctx)

    def test_deficient_context_v2_with_short_context_overwrites_via_elif_branch(self, tmp_path: Path) -> None:
        """Same deficient-summary composition as above, but fields["context"] is present and
        non-empty though under 80 chars -- exercises the elif branch (as opposed to the
        not-fields.get("context") branch)."""
        import scripts.ops_data_portal as p

        deficient_ctx = {"schema_version": 1}
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: warn\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="at least 80 stripped characters"):
                p.file_rec({**_CI_RCA_FIELDS, "context": "too short"}, context_v2_json=deficient_ctx)

    def test_legacy_free_text_passes_with_deprecation_warning(self, tmp_path: Path, caplog) -> None:
        """file_rec(source=ci_rca) with no context_v2_json logs a deprecation warning and passes."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9003"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            rec_id = p.file_rec(dict(_CI_RCA_FIELDS))
        assert rec_id == "rec-9003"
        assert any("legacy free-text" in r.message for r in caplog.records)

    def test_strict_mode_raises_on_deficiency(self, tmp_path: Path) -> None:
        """In strict mode a deficient why_chain raises ValueError (branch exists but inert by default)."""
        import scripts.ops_data_portal as p

        deficient_ctx = {**_VALID_CONTEXT_V2, "why_chain": ["short", "also short", "still short"]}
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="CI_RCA_STRICT_MODE=strict"):
                p.file_rec(dict(_CI_RCA_FIELDS), context_v2_json=deficient_ctx)

    def test_reconcile_table_columns_rerun_safety(self) -> None:
        """reconcile_table_columns on a table that already has context_v2_json is a no-op.

        Uses real duckdb in-memory tables with CATALOG_ALIAS patched to 'memory' so that
        information_schema queries and ALTER TABLE work without a DuckLake catalog.
        Proves introspection-based idempotency: a second call adds nothing.
        """
        semantics = load_field_semantics()
        spec = resolve_table_spec("ops_recommendations", semantics)

        con = duckdb.connect(":memory:")

        # Create both tables with all spec columns (including context_v2_json) up front.
        # Note: BIGINT[] and VARCHAR[] must be written as DuckDB array types.
        def _safe_ddl_type(sql_type: str) -> str:
            return sql_type.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")

        cols_ddl = ", ".join(f"{col} {_safe_ddl_type(spec.fields[col].get('sql_type', 'VARCHAR'))}" for col in spec.fields)
        con.execute(f"CREATE TABLE ops_recommendations_history ({cols_ddl})")
        con.execute(f"CREATE TABLE ops_recommendations_current ({cols_ddl})")

        # Patch CATALOG_ALIAS to 'memory' so reconcile_table_columns resolves table_fq
        # as 'memory.ops_recommendations_*' -- valid in a plain DuckDB in-memory connection.
        # reconcile_table_columns moved to ducklake_tables (PLAN-sloc-ducklake-layer); it imports
        # CATALOG_ALIAS directly from there, so the patch target must move with it -- a facade-namespace
        # patch would silently no-op (the moved function no longer reads rt.CATALOG_ALIAS).
        with patch("src.common.ducklake_tables.CATALOG_ALIAS", "memory"):
            result1 = rt.reconcile_table_columns(con, table="ops_recommendations")
            result2 = rt.reconcile_table_columns(con, table="ops_recommendations")

        # First call: context_v2_json is already present -- nothing to add.
        assert result1["added_history"] == []
        assert result1["added_current"] == []
        # Second call: idempotent no-op.
        assert result2["added_history"] == []
        assert result2["added_current"] == []
        con.close()

    def test_guidance_source_ci_rca_returns_schema(self, capsys: pytest.CaptureFixture) -> None:
        """CLI --guidance --source ci_rca emits the context_v2_json schema_fields block."""
        from scripts.ops_data_portal import main

        rc = main(["--guidance", "--source", "ci_rca"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "context_v2_json" in out
        assert "schema_fields" in out
        assert "proximate_cause" in out

    def test_file_rec_context_v2_json_valid_routes_to_file_rec(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """CLI --file-rec --context-v2-json with valid JSON parses and routes to file_rec(context_v2_json=...)."""
        import json as _json

        from scripts.ops_data_portal import main

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-cv2-1"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            rc = main(
                [
                    "--file-rec",
                    "--source",
                    "ci_rca",
                    "--priority",
                    "Critical",
                    "--risk",
                    "medium",
                    "--effort",
                    "S",
                    "--file",
                    "scripts/validate.py",
                    "--title",
                    "validate_sloc_limits missed in pre tier",
                    "--context",
                    (
                        "validate_sloc_limits() raised on scripts/roadmap/platform_roadmap.py: 810 SLOC exceeds 500 limit. "
                        "CI step 'validate' failed; resource: scripts/roadmap/platform_roadmap.py."
                    ),
                    "--acceptance",
                    "grep -q validate_sloc_limits scripts/validate.py && grep -q main scripts/validate.py",
                    "--context-v2-json",
                    _json.dumps(_VALID_CONTEXT_V2),
                ]
            )
        assert rc == 0
        captured = capsys.readouterr()
        assert "rec-cv2-1" in captured.out
        entry = mock_write.call_args.args[1]
        assert "context_v2_json" in entry
        stored = _json.loads(entry["context_v2_json"])
        assert stored["schema_version"] == 1

    def test_file_rec_context_v2_json_invalid_json_exits_nonzero(self, capsys: pytest.CaptureFixture) -> None:
        """CLI --file-rec --context-v2-json with malformed JSON exits 1 and files nothing."""
        from scripts.ops_data_portal import main

        rc = main(
            [
                "--file-rec",
                "--source",
                "ci_rca",
                "--priority",
                "Critical",
                "--risk",
                "low",
                "--effort",
                "XS",
                "--file",
                "scripts/validate.py",
                "--title",
                "Test invalid JSON path",
                "--context",
                "validate_sloc_limits() raised: file is over limit. CI step failed; resource: scripts/foo.py.",
                "--acceptance",
                "grep -q validate scripts/validate.py && grep -q main scripts/validate.py",
                "--context-v2-json",
                "{not: valid json",
            ]
        )
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err
        assert "not valid JSON" in captured.err

    def test_file_rec_ci_rca_legacy_path_warns_and_files(self, tmp_path: Path, caplog, capsys: pytest.CaptureFixture) -> None:
        """CLI --file-rec source=ci_rca without --context-v2-json still files in warn mode."""
        import logging

        from scripts.ops_data_portal import main

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-legacy-1"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            rc = main(
                [
                    "--file-rec",
                    "--source",
                    "ci_rca",
                    "--priority",
                    "Critical",
                    "--risk",
                    "low",
                    "--effort",
                    "XS",
                    "--file",
                    "scripts/validate.py",
                    "--title",
                    "Legacy ci_rca path test",
                    "--context",
                    "validate_sloc_limits() raised: file is over 500 SLOC. CI step failed; resource: scripts/foo.py.",
                    "--acceptance",
                    "grep -q validate scripts/validate.py && grep -q main scripts/validate.py",
                ]
            )
        assert rc == 0
        captured = capsys.readouterr()
        assert "rec-legacy-1" in captured.out
        assert any("legacy free-text" in r.message for r in caplog.records)

    @pytest.mark.integration
    @pytest.mark.enable_socket()
    def test_context_v2_roundtrip_live(self) -> None:
        """Live roundtrip: file a ci_rca rec with context_v2_json, read back, assert persisted, close.

        Skipped unless RUN_LIVE_DUCKLAKE=1 (requires portal connectivity + production DuckLake).
        """
        if not os.environ.get("RUN_LIVE_DUCKLAKE"):
            pytest.skip("set RUN_LIVE_DUCKLAKE=1 to run live DuckLake roundtrip")

        import scripts.ops_data_portal as p

        live_fields = {
            **_CI_RCA_FIELDS,
            "title": "test_context_v2_roundtrip_live (T1.13 VP10)",
            "context": (
                "Live roundtrip test for context_v2_json persistence filed by TestCiRcaSchemaEnforcement. "
                "This rec will be immediately closed via update_rec (self-cleaning, Decision 70)."
            ),
        }
        rec_id = p.file_rec(live_fields, context_v2_json=dict(_VALID_CONTEXT_V2))
        assert rec_id.startswith("rec-"), f"Expected rec-NNN, got {rec_id!r}"

        # Read back via reader and assert context_v2_json was persisted.
        row = p._fetch_rec_from_reader(rec_id)
        assert row is not None, f"{rec_id} not found after filing"
        ctx_v2 = row.get("context_v2_json")
        assert ctx_v2 is not None, f"context_v2_json not persisted for {rec_id}: {row}"
        if isinstance(ctx_v2, str):
            ctx_v2 = json.loads(ctx_v2)
        assert ctx_v2.get("schema_version") == 1
        legacy_ctx = row.get("context", "")
        assert len((legacy_ctx or "").strip()) >= 80, f"legacy context too short: {legacy_ctx!r}"

        # Close the test rec (self-cleaning, Decision 70).
        closed = p.update_rec(
            rec_id,
            {
                "status": "closed",
                "resolution": "test_context_v2_roundtrip_live self-cleaning close (T1.13 VP10)",
            },
        )
        assert closed is True, f"update_rec failed for {rec_id}"


class TestCiRcaClosureFields:
    """Decision 186: closure_artifact / closure_waiver_category / closure_waiver_reason are
    Optional[...]=None additions -- backward-compatible (no historical row is retro-rejected)
    and validated when present."""

    def test_closure_fields_are_optional_and_validated(self) -> None:
        """A context omitting all three still validates; well-formed values accept; malformed
        ones (including an off-vocabulary category) reject."""
        from pydantic import ValidationError

        from scripts.ops_portal.ci_rca_schema import CiRcaContext

        CiRcaContext.model_validate(_VALID_CONTEXT_V2)  # no closure_* fields at all -- must validate

        well_formed = {
            **_VALID_CONTEXT_V2,
            "closure_artifact": "shard:some-shard",
            "closure_waiver_category": "environment_only",
            "closure_waiver_reason": "sandbox-only failure with no premerge signal",
        }
        CiRcaContext.model_validate(well_formed)

        with pytest.raises(ValidationError):
            CiRcaContext.model_validate({**_VALID_CONTEXT_V2, "closure_waiver_category": "made_up_category"})

        with pytest.raises(ValidationError):
            CiRcaContext.model_validate({**_VALID_CONTEXT_V2, "closure_artifact": "not-a-kind-shaped-token"})

    def test_closure_waiver_category_duplicate_of_rejected_by_field_pattern(self) -> None:
        """duplicate_of is not in the field's own alternation pattern -- rejected at the schema
        layer too, not merely by the gate's membership check."""
        from pydantic import ValidationError

        from scripts.ops_portal.ci_rca_schema import CiRcaContext

        with pytest.raises(ValidationError):
            CiRcaContext.model_validate({**_VALID_CONTEXT_V2, "closure_waiver_category": "duplicate_of"})
