"""Decision 226 cl.1: the repair track, the derived priority and the conditional preventive_action
(PLAN-red-main-recovery-policy). Bundles are built in this file (no cross-test imports)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ops_portal import ci_rca_schema as _schema
from scripts.ops_portal.ci_rca_lifecycle import ChainRecord
from scripts.ops_portal.ci_rca_runtime import _run_ci_rca_cross_check, back_validate_ci_rca
from scripts.ops_portal.ci_rca_schema import (
    CiRcaContext,
    derive_ci_rca_priority,
    is_track_a,
    preventive_deficiency,
)
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS
from tests.fixtures.ops_rec_rows import reader_rec_row

_FP = "a" * 64


def _ctx(**extra: object) -> dict:
    ctx: dict = {
        "schema_version": 2,
        "proximate_cause": "x" * 100,
        "why_chain": ["a " * 25, "b " * 25, "c " * 25 + "a gap in scripts/validate.py:1"],
        "detection_gap": {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": (
                "A tests-only gap explanation that cites scripts/validate.py:1 "
                "and runs comfortably past the 120 character floor of the field."
            ),
        },
        "recurrence_class": "novel",
        "corrective_action": "x" * 100,
    }
    ctx.update(extra)
    return ctx


def _write_bundle(root: Path, **overrides: object) -> str:
    data: dict = {
        "earliest_viable_gate": "pre",
        "escape_mode": "tier_misplaced",
        "vacuous_pass": False,
        "fingerprint": _FP,
        "failure_category": "pytest_regression",
    }
    data.update(overrides)
    sha = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    pending = root / "logs" / ".ci-rca-evidence-pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / f"{sha}.json").write_bytes(
        json.dumps({**data, "sha256": sha}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    )
    return sha


def _with_bundle(ctx: dict, sha: str) -> dict:
    ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "upload_failed"}
    return ctx


class TestRepairTrack:
    def test_track_fields_accept_two_values_and_reject_others(self) -> None:
        for field in ("repair_track", "repair_track_confirmed"):
            for value in ("test_defect", "product_regression"):
                assert getattr(CiRcaContext.model_validate(_ctx(**{field: value})), field) == value
            with pytest.raises(ValueError):
                CiRcaContext.model_validate(_ctx(**{field: "other"}))

    def test_missing_preventive_action_validates(self) -> None:
        assert CiRcaContext.model_validate(_ctx()).preventive_action is None

    def test_preventive_deficiency(self) -> None:
        track_a = {"repair_track": "test_defect", "workflow_tier": "CI"}
        assert preventive_deficiency(track_a) == []
        assert preventive_deficiency({"preventive_action": "x" * 100}) == []
        assert preventive_deficiency({"repair_track": "test_defect", "workflow_tier": "unknown"})
        assert preventive_deficiency({**track_a, "regression_of": "rec-1"})
        assert preventive_deficiency({})

    def test_is_track_a_and_priority(self) -> None:
        assert is_track_a({"repair_track": "test_defect", "workflow_tier": "CI"}) is True
        assert derive_ci_rca_priority({"repair_track": "test_defect", "workflow_tier": "CI"}) == "High"
        for ctx in (
            {"repair_track": "product_regression", "workflow_tier": "CI"},
            {"workflow_tier": "CI"},
            {"repair_track": "test_defect", "workflow_tier": "unknown"},
            {"repair_track": "test_defect"},
            {"repair_track": "test_defect", "workflow_tier": "CI", "regression_of": "rec-2"},
        ):
            assert is_track_a(ctx) is False
            assert derive_ci_rca_priority(ctx) == "Critical"


class TestWorkflowTierStamp:
    @pytest.mark.parametrize("resolution", ["CI", "unknown"])
    def test_verified_bundle_stamps_resolution(self, tmp_path: Path, resolution: str) -> None:
        sha = _write_bundle(tmp_path, workflow_to_tier_resolution=resolution)
        ctx = _with_bundle(_ctx(), sha)
        with patch.object(_schema, "ROOT", tmp_path):
            _run_ci_rca_cross_check(ctx)
        assert ctx["workflow_tier"] == resolution

    def test_absent_bundle_stamps_nothing(self, tmp_path: Path) -> None:
        ctx = _ctx(rca_confidence="undetermined")
        with patch.object(_schema, "ROOT", tmp_path):
            _run_ci_rca_cross_check(ctx)
        assert "workflow_tier" not in ctx


class TestCiRcaPortalDerivations:
    def _file(self, tmp_path: Path, ctx: dict, *, mode: str = "warn", head: ChainRecord | None = None, passed: str = "Low"):
        import scripts.ops_data_portal as p

        with (
            patch.object(_schema, "ROOT", tmp_path),
            patch.object(p, "get_ci_rca_strict_mode", return_value=mode),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value=None),
            patch.object(p, "closed_head_of_chain", return_value=head),
            patch.object(p, "current_commit_sha", return_value="failing_sha"),
            patch.object(p, "classify_closed_head", return_value="regression"),
            patch.object(p, "_ducklake_write", return_value={"key": "rec-950"}) as mock_write,
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            p.file_rec({**_VALID_FIELDS, "source": "ci_rca", "priority": passed}, context_v2_json=ctx)
        written = mock_write.call_args[0][1]
        return written, json.loads(written["context_v2_json"])

    def _bundle_ctx(self, tmp_path: Path, tier: str | None = "CI", **extra: object) -> dict:
        overrides = {"workflow_to_tier_resolution": tier} if tier else {}
        return _with_bundle(_ctx(**extra), _write_bundle(tmp_path, **overrides))

    def test_track_a_files_high_without_preventive(self, tmp_path: Path) -> None:
        written, ctx = self._file(tmp_path, self._bundle_ctx(tmp_path, repair_track="test_defect"))
        assert written["priority"] == "High"
        assert ctx["workflow_tier"] == "CI"
        assert "warn_mode_reject" not in ctx or "preventive_required" not in ctx["warn_mode_reject"]["reasons"]

    @pytest.mark.parametrize(
        "extra,tier",
        [
            ({"repair_track": "product_regression"}, "CI"),
            ({}, "CI"),
            ({"repair_track": "test_defect"}, "unknown"),
            ({"repair_track": "test_defect"}, None),
        ],
    )
    def test_other_reds_file_critical_whatever_was_passed(self, tmp_path: Path, extra: dict, tier: str | None) -> None:
        ctx = self._bundle_ctx(tmp_path, tier, preventive_action="p" * 100, **extra)
        written, _ = self._file(tmp_path, ctx, passed="Low")
        assert written["priority"] == "Critical"

    def test_caller_supplied_workflow_tier_is_stripped(self, tmp_path: Path) -> None:
        ctx = _ctx(repair_track="test_defect", workflow_tier="CI", repair_track_confirmed="test_defect")
        written, stored = self._file(tmp_path, ctx)
        assert written["priority"] == "Critical"
        assert "workflow_tier" not in stored
        assert "repair_track_confirmed" not in stored

    def test_critical_without_preventive_warns_in_warn_mode(self, tmp_path: Path) -> None:
        _, ctx = self._file(tmp_path, self._bundle_ctx(tmp_path, repair_track="product_regression"))
        assert "preventive_required" in ctx["warn_mode_reject"]["reasons"]

    def test_critical_without_preventive_raises_in_strict_mode(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="preventive_action"):
            self._file(tmp_path, self._bundle_ctx(tmp_path, repair_track="product_regression"), mode="strict")

    def test_portal_found_regression_never_raises_in_strict_mode(self, tmp_path: Path) -> None:
        head = ChainRecord(rec_id="rec-777", status="closed", fixed_by_sha="fixed_sha", last_touched="")
        written, ctx = self._file(tmp_path, self._bundle_ctx(tmp_path, repair_track="test_defect"), mode="strict", head=head)
        assert written["priority"] == "Critical"
        assert ctx["regression_of"] == "rec-777"
        assert ctx["warn_mode_reject"]["mode_at_write"] == "strict"
        assert "preventive_required" in ctx["warn_mode_reject"]["reasons"]

    def test_back_validate_classifies_preventive_gaps(self) -> None:
        def row(rec_id: str, ctx: dict) -> dict:
            return {"id": rec_id, "source": "ci_rca", "created_timestamp": "2026-10-10", "context_v2_json": json.dumps(ctx)}

        tagged = _ctx(
            repair_track="product_regression", warn_mode_reject={"reasons": ["preventive_required"], "mode_at_write": "warn"}
        )
        result = back_validate_ci_rca(
            cache_rows=[row("rec-1", tagged), row("rec-2", _ctx()), row("rec-3", _ctx(preventive_action="p" * 100))],
            since="2026-01-01",
        )
        bad = {e["id"] for e in result["non_conformant"]}
        assert bad == {"rec-1", "rec-2"}

    def test_update_rec_closure_fix_sha_stamps_track_for_ci_rca_only(self, tmp_path: Path) -> None:
        import scripts.ops_data_portal as p

        blob = json.dumps({"fingerprint": _FP})
        for source, expected in (("ci_rca", "test_defect"), ("planning", None)):
            existing = reader_rec_row(source=source, context_v2_json=blob)
            with (
                patch.object(p, "_fetch_rec_from_reader", return_value=existing),
                patch.object(p, "_ducklake_write", return_value={"ok": True}) as mock_write,
                patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
                patch.object(p, "_sync_table"),
                patch("scripts.ops_portal.closure_gate.confirmed_repair_track", return_value="test_defect"),
            ):
                p.update_rec("rec-4001", {"title": existing["title"]}, closure_fix_sha="cafef00dcafef00d")
            stored = json.loads(mock_write.call_args[0][1]["context_v2_json"])
            assert stored.get("repair_track_confirmed") == expected

    def test_legacy_write_without_context_keeps_passed_priority(self, tmp_path: Path) -> None:
        import scripts.ops_data_portal as p

        with (
            patch.object(p, "get_ci_rca_strict_mode", return_value="warn"),
            patch.object(p, "_ducklake_write", return_value={"key": "rec-951"}) as mock_write,
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            p.file_rec({**_VALID_FIELDS, "source": "ci_rca", "priority": "Medium"})
        assert mock_write.call_args[0][1]["priority"] == "Medium"

    def test_non_ci_rca_source_keeps_context_untouched(self, tmp_path: Path) -> None:
        import scripts.ops_data_portal as p

        with (
            patch.object(p, "_ducklake_write", return_value={"key": "rec-952"}) as mock_write,
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            p.file_rec({**_VALID_FIELDS, "priority": "Medium"}, context_v2_json={"workflow_tier": "CI"})
        assert json.loads(mock_write.call_args[0][1]["context_v2_json"]) == {"workflow_tier": "CI"}
        assert mock_write.call_args[0][1]["priority"] == "Medium"
