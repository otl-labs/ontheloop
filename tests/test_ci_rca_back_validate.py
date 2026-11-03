"""Unit tests for scripts.ops_data_portal.back_validate_ci_rca (PLAN-ci-rca-back-validate).

Tests inject cache_rows directly (TestDefaultRowsFromReader covers the default source-scoped
read, Decision 215) and mock file_rec so the --refile-audit path never performs a real write.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.executor.rec_write_guidance import validate_source
from scripts.ops_data_portal import _print_ci_rca_back_validation_report, back_validate_ci_rca, main

_VALID_CONTEXT_V2 = {
    "schema_version": 1,
    "proximate_cause": (
        "validate_sloc_limits() raised: scripts/ops_data_portal.py is 810 SLOC, exceeds 500 limit "
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


def _rec(rec_id: str, created_timestamp: str, context_v2: dict | None = None, no_context_key: bool = False) -> dict:
    row = {
        "id": rec_id,
        "source": "ci_rca",
        "status": "open",
        "file": "scripts/validate.py",
        "created_timestamp": created_timestamp,
    }
    if not no_context_key:
        row["context_v2_json"] = json.dumps(context_v2) if context_v2 is not None else ""
    return row


class TestBackValidateBucketRouting:
    def test_conformant_with_context_zero_deficiencies(self) -> None:
        rows = [_rec("rec-1", "2026-06-20T00:00:00+00:00", _VALID_CONTEXT_V2)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert [e["id"] for e in result["conformant"]] == ["rec-1"]
        assert result["non_conformant"] == []
        assert result["legacy_no_schema"] == []

    def test_deficient_context_lands_non_conformant_with_failing_checks(self) -> None:
        deficient = dict(_VALID_CONTEXT_V2)
        deficient["recurrence_class"] = "not-a-valid-value"
        rows = [_rec("rec-2", "2026-06-20T00:00:00+00:00", deficient)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert len(result["non_conformant"]) == 1
        entry = result["non_conformant"][0]
        assert entry["id"] == "rec-2"
        assert entry["failing_checks"]  # non-empty list of pydantic error messages

    def test_no_context_v2_json_is_legacy_never_non_conformant(self) -> None:
        """Load-bearing legacy rule: a historical rec with no context_v2_json, filed before
        CIRCA-02 landed, stays grandfathered/conformant here. Strict-mode file_rec() now REJECTS
        a NEW source=ci_rca write with no context_v2_json (CIRCA-02) -- this bucket covers only
        pre-CIRCA-02 rows; back_validate_ci_rca never retro-rejects them. An empty dict is never
        validated for these recs.
        """
        rows = [_rec("rec-3", "2026-06-20T00:00:00+00:00", context_v2=None)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert [e["id"] for e in result["legacy_no_schema"]] == ["rec-3"]
        assert result["legacy_no_schema"][0]["has_context_v2"] is False
        assert result["non_conformant"] == []
        assert result["conformant"] == []

    def test_no_context_v2_json_key_at_all_is_also_legacy(self) -> None:
        rows = [_rec("rec-3b", "2026-06-20T00:00:00+00:00", no_context_key=True)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert [e["id"] for e in result["legacy_no_schema"]] == ["rec-3b"]
        assert result["non_conformant"] == []

    def test_non_schema_warn_mode_reject_reason_surfaces_even_when_schema_passes(self) -> None:
        ctx = dict(_VALID_CONTEXT_V2)
        ctx["warn_mode_reject"] = {"reasons": ["bundle_absent"], "mode_at_write": "warn"}
        rows = [_rec("rec-4", "2026-06-20T00:00:00+00:00", ctx)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert result["conformant"] == []
        assert len(result["non_conformant"]) == 1
        entry = result["non_conformant"][0]
        assert entry["id"] == "rec-4"
        assert entry["failing_checks"] == ["bundle_absent"]

    def test_schema_deficiency_reason_alone_does_not_double_count_but_recompute_catches_it(self) -> None:
        """A stamped schema_deficiency marker (from the original write) is excluded from the
        non-schema reasons list -- the recompute against the CURRENT schema is authoritative,
        not the stamped marker, for the schema axis.
        """
        deficient = dict(_VALID_CONTEXT_V2)
        deficient["recurrence_class"] = "not-a-valid-value"
        deficient["warn_mode_reject"] = {"reasons": ["schema_deficiency"], "mode_at_write": "warn"}
        rows = [_rec("rec-5", "2026-06-20T00:00:00+00:00", deficient)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert len(result["non_conformant"]) == 1
        # failing_checks comes from the recompute (pydantic errors), not the stamped tag.
        assert "schema_deficiency" not in result["non_conformant"][0]["failing_checks"]

    def test_per_rule_schema_tag_excluded_but_genuine_reason_still_surfaces(self) -> None:
        """CIRCA-04: a stamped per-rule schema_<rule> tag (not just the bare "schema_deficiency")
        is excluded from non_schema_reasons, so it is never double-counted alongside the
        recompute -- while a genuine non-schema reason (bundle/S3/cross-check) still surfaces.
        """
        deficient = dict(_VALID_CONTEXT_V2)
        deficient["recurrence_class"] = "not-a-valid-value"
        deficient["warn_mode_reject"] = {
            "reasons": ["schema_why_chain_too_long", "bundle_absent"],
            "mode_at_write": "warn",
        }
        rows = [_rec("rec-5b", "2026-06-20T00:00:00+00:00", deficient)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert len(result["non_conformant"]) == 1
        failing = result["non_conformant"][0]["failing_checks"]
        assert "schema_why_chain_too_long" not in failing
        assert "bundle_absent" in failing

    def test_ignores_non_ci_rca_sources(self) -> None:
        rows = [
            {
                "id": "rec-6",
                "source": "planning",
                "created_timestamp": "2026-06-20T00:00:00+00:00",
                "context_v2_json": "",
            }
        ]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert result["aggregate"]["total"] == 0


class TestDefaultRowsFromReader:
    def test_default_rows_come_from_source_scoped_read(self) -> None:
        reader = MagicMock()
        reader.current_state.return_value = [_rec("rec-1", "2026-06-20T00:00:00+00:00", _VALID_CONTEXT_V2)]
        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            result = back_validate_ci_rca(since="2026-06-15")
        reader.current_state.assert_called_once_with("ops_recommendations", row_filter="source = 'ci_rca'")
        assert [r["id"] for r in result["conformant"]] == ["rec-1"]

    def test_reader_failure_raises_instead_of_empty_report(self) -> None:
        reader = MagicMock()
        reader.current_state.side_effect = RuntimeError("reader down")
        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            with pytest.raises(RuntimeError, match="reader down"):
                back_validate_ci_rca(since="2026-06-15")


class TestSinceBoundary:
    def test_since_boundary_excludes_earlier_recs(self) -> None:
        rows = [
            _rec("rec-old", "2026-06-14T23:59:59+00:00", _VALID_CONTEXT_V2),
            _rec("rec-new", "2026-06-15T00:00:00+00:00", _VALID_CONTEXT_V2),
        ]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        ids = [e["id"] for e in result["conformant"]]
        assert ids == ["rec-new"]

    def test_missing_created_timestamp_excluded(self) -> None:
        rows = [{"id": "rec-7", "source": "ci_rca", "context_v2_json": ""}]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert result["aggregate"]["total"] == 0


class TestAggregateMath:
    def test_non_conformance_rate_over_with_context_subset_only(self) -> None:
        deficient = dict(_VALID_CONTEXT_V2)
        deficient["recurrence_class"] = "bad"
        rows = [
            _rec("rec-legacy-1", "2026-06-20T00:00:00+00:00", context_v2=None),
            _rec("rec-legacy-2", "2026-06-20T00:00:00+00:00", context_v2=None),
            _rec("rec-conf-1", "2026-06-20T00:00:00+00:00", _VALID_CONTEXT_V2),
            _rec("rec-conf-2", "2026-06-20T00:00:00+00:00", _VALID_CONTEXT_V2),
            _rec("rec-conf-3", "2026-06-20T00:00:00+00:00", _VALID_CONTEXT_V2),
            _rec("rec-nc-1", "2026-06-20T00:00:00+00:00", deficient),
        ]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        agg = result["aggregate"]
        assert agg["legacy_no_schema_count"] == 2
        assert agg["conformant_count"] == 3
        assert agg["non_conformant_count"] == 1
        assert agg["with_context_total"] == 4
        assert agg["total"] == 6
        assert agg["non_conformance_rate"] == pytest.approx(0.25)

    def test_zero_with_context_recs_rate_is_zero(self) -> None:
        rows = [_rec("rec-legacy", "2026-06-20T00:00:00+00:00", context_v2=None)]
        result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        assert result["aggregate"]["non_conformance_rate"] == 0.0


class TestRefileAudit:
    def _non_conformant_rows(self, n: int) -> list[dict]:
        deficient = dict(_VALID_CONTEXT_V2)
        deficient["recurrence_class"] = "bad"
        return [_rec(f"rec-nc-{i}", "2026-06-20T00:00:00+00:00", deficient) for i in range(n)]

    def test_default_refile_audit_off_performs_zero_writes(self) -> None:
        rows = self._non_conformant_rows(3)
        with patch("scripts.ops_data_portal.file_rec") as mock_file_rec:
            result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15")
        mock_file_rec.assert_not_called()
        assert result["filed"] == []
        assert result["audit_cap_reached"] is False

    def test_refile_audit_files_each_non_conformant_rec(self) -> None:
        rows = self._non_conformant_rows(2)
        with patch("scripts.ops_data_portal.file_rec", side_effect=["rec-9001", "rec-9002"]) as mock_file_rec:
            result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15", refile_audit=True)
        assert mock_file_rec.call_count == 2
        for call in mock_file_rec.call_args_list:
            fields = call.args[0]
            assert fields["source"] == "ci_rca_warn_period_audit"
            assert fields["priority"] == "Low"
        assert result["filed"] == ["rec-9001", "rec-9002"]
        assert result["audit_cap_reached"] is False

    def test_k_cap_enforced(self) -> None:
        rows = self._non_conformant_rows(5)
        with patch("scripts.ops_data_portal.file_rec", side_effect=[f"rec-{9000 + i}" for i in range(2)]) as mock_file_rec:
            result = back_validate_ci_rca(cache_rows=rows, since="2026-06-15", refile_audit=True, cap=2)
        assert mock_file_rec.call_count == 2
        assert len(result["filed"]) == 2
        assert result["audit_cap_reached"] is True

    def test_filed_rec_carries_parent_id_and_failing_checks(self) -> None:
        rows = self._non_conformant_rows(1)
        with patch("scripts.ops_data_portal.file_rec", return_value="rec-9001") as mock_file_rec:
            back_validate_ci_rca(cache_rows=rows, since="2026-06-15", refile_audit=True)
        fields = mock_file_rec.call_args.args[0]
        assert "rec-nc-0" in fields["title"]
        assert "rec-nc-0" in fields["context"]

        # The composed acceptance string must itself pass the file_rec write-boundary lint
        # (require_discrimination=True) -- code-review finding: this call site's acceptance
        # was widened to a chained assertion specifically to keep clearing that boundary.
        from scripts.executor.acceptance_lint import lint_acceptance_command

        lint_ok, lint_msg = lint_acceptance_command(fields["acceptance"], require_discrimination=True)
        assert lint_ok, lint_msg


class TestSourceRegistration:
    def test_validate_source_admits_ci_rca_warn_period_audit(self) -> None:
        validate_source("ci_rca_warn_period_audit")  # must not raise


class TestCliWiring:
    def test_cli_back_validate_default_text_report(self, capsys: pytest.CaptureFixture) -> None:
        fake_result = {
            "since": "2026-06-15",
            "legacy_no_schema": [{"id": "rec-1", "has_context_v2": False}],
            "non_conformant": [{"id": "rec-2", "has_context_v2": True, "failing_checks": ["bad"]}],
            "conformant": [{"id": "rec-3", "has_context_v2": True}],
            "aggregate": {
                "legacy_no_schema_count": 1,
                "non_conformant_count": 1,
                "conformant_count": 1,
                "with_context_total": 2,
                "total": 3,
                "non_conformance_rate": 0.5,
            },
            "filed": [],
            "audit_cap_reached": False,
            "audit_cap": 20,
        }
        with patch("scripts.ops_data_portal.back_validate_ci_rca", return_value=fake_result) as mock_bv:
            rc = main(["--back-validate", "--since", "2026-06-15"])
        assert rc == 0
        mock_bv.assert_called_once_with(since="2026-06-15", refile_audit=False, profile=None)
        out = capsys.readouterr().out
        assert "NON_CONFORMANT" in out
        assert "rec-2" in out

    def test_cli_back_validate_json_output(self, capsys: pytest.CaptureFixture) -> None:
        fake_result = {
            "since": "2026-06-15",
            "legacy_no_schema": [],
            "non_conformant": [],
            "conformant": [],
            "aggregate": {
                "legacy_no_schema_count": 0,
                "non_conformant_count": 0,
                "conformant_count": 0,
                "with_context_total": 0,
                "total": 0,
                "non_conformance_rate": 0.0,
            },
            "filed": [],
            "audit_cap_reached": False,
            "audit_cap": 20,
        }
        with patch("scripts.ops_data_portal.back_validate_ci_rca", return_value=fake_result):
            rc = main(["--back-validate", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["since"] == "2026-06-15"

    def test_cli_back_validate_refile_audit_flag_forwarded(self) -> None:
        fake_result = {
            "since": "2026-06-15",
            "legacy_no_schema": [],
            "non_conformant": [],
            "conformant": [],
            "aggregate": {
                "legacy_no_schema_count": 0,
                "non_conformant_count": 0,
                "conformant_count": 0,
                "with_context_total": 0,
                "total": 0,
                "non_conformance_rate": 0.0,
            },
            "filed": [],
            "audit_cap_reached": False,
            "audit_cap": 20,
        }
        with patch("scripts.ops_data_portal.back_validate_ci_rca", return_value=fake_result) as mock_bv:
            rc = main(["--back-validate", "--refile-audit"])
        assert rc == 0
        mock_bv.assert_called_once_with(since="2026-06-15", refile_audit=True, profile=None)


class TestPrintReportHelper:
    def test_print_report_covers_filed_and_cap_reached_branches(self, capsys: pytest.CaptureFixture) -> None:
        result = {
            "since": "2026-06-15",
            "legacy_no_schema": [{"id": "rec-1", "has_context_v2": False}],
            "non_conformant": [{"id": "rec-2", "has_context_v2": True, "failing_checks": ["bad"]}],
            "conformant": [{"id": "rec-3", "has_context_v2": True}],
            "aggregate": {
                "legacy_no_schema_count": 1,
                "non_conformant_count": 1,
                "conformant_count": 1,
                "with_context_total": 2,
                "total": 3,
                "non_conformance_rate": 0.5,
            },
            "filed": ["rec-9001"],
            "audit_cap_reached": True,
            "audit_cap": 1,
        }
        _print_ci_rca_back_validation_report(result)
        out = capsys.readouterr().out
        assert "filed audit recs" in out
        assert "audit cap reached" in out
