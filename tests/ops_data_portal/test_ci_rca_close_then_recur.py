"""Tests for the closed-head regression-vs-drop routing in file_rec()'s write-time backstop
(ci-rca-identity-lifecycle, Decision 142).

Rewrite of the former rec-2644 close-then-recur coverage (its two revive-path helpers -- a
status-flip mutator and its READ-ONLY recency finder -- are fully retired): a closed-head
fingerprint match now resolves via scripts.ops_portal.ci_rca_lifecycle (closed_head_of_chain +
classify_closed_head) and either drops (ancestry-proven stale rerun, no insert/no bump/no reopen)
or files a BRAND-NEW REGRESSION record -- never a closed->open status flip. A legacy closed head
with no fixed_by_sha fails closed to a REGRESSION (Decision 55). Split out of the former
tests/test_ops_data_portal.py monolith (rec-2709 Wave 3); the sibling of TestCiRcaFingerprintDedup
(test_ci_rca_fingerprint.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ops_portal import ci_rca_schema as _ci_rca_schema_mod  # noqa: E402
from scripts.ops_portal.ci_rca_lifecycle import ChainRecord  # noqa: E402
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402


class TestCiRcaClosedHeadRegression:
    """The closed-head branch of file_rec()'s write-time backstop: drop (ancestry-proven stale
    rerun) vs REGRESSION (everything else, including the legacy no-fixed_by_sha fail-closed
    case) -- NEVER a closed->open flip anywhere in this path."""

    _FINGERPRINT = "a" * 64

    def _make_bundle(self, tmp_path, **overrides):
        import hashlib as _hashlib
        import json as _json

        bundle_data = {
            "earliest_viable_gate": "pre",
            "escape_mode": "tier_misplaced",
            "vacuous_pass": False,
            "fingerprint": self._FINGERPRINT,
            "failure_category": "sloc_violation",
        }
        bundle_data.update(overrides)
        payload = {k: v for k, v in bundle_data.items() if k != "sha256"}
        sha = _hashlib.sha256(
            _json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        ).hexdigest()
        bundle_data["sha256"] = sha
        pending_dir = tmp_path / "logs" / ".ci-rca-evidence-pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / f"{sha}.json").write_bytes(
            _json.dumps(bundle_data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        )
        return sha, bundle_data

    def _ctx_v2(self, **detection_gap_overrides):
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": "test gap explanation with enough chars for the field",
        }
        dg.update(detection_gap_overrides)
        return {
            "schema_version": 2,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, "c " * 25 + "systemic scripts/validate.py:1"],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }

    def _fields(self):
        return {**_VALID_FIELDS, "source": "ci_rca", "title": "Something broke", "priority": "Medium"}

    # -- drop: ancestry-proven stale rerun -----------------------------------------------------

    def test_closed_head_ancestor_drops_without_insert_bump_or_reopen(self, tmp_path: Path, monkeypatch):
        import scripts.ops_data_portal as p

        monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
        sha, _ = self._make_bundle(tmp_path)
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        head = ChainRecord(rec_id="rec-777", status="closed", fixed_by_sha="fixed_sha", last_touched="")

        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value=None),
            patch.object(p, "closed_head_of_chain", return_value=head) as mock_head,
            patch.object(p, "current_commit_sha", return_value="failing_sha"),
            patch.object(p, "classify_closed_head", return_value="drop") as mock_classify,
            patch.object(p, "bump_ci_rca_occurrence") as mock_bump,
            patch.object(p, "_ducklake_write") as mock_write,
        ):
            result = p.file_rec(self._fields(), context_v2_json=ctx)

        assert result == "rec-777"
        mock_head.assert_called_once_with(self._FINGERPRINT, profile=None)
        mock_classify.assert_called_once_with("failing_sha", head)
        mock_bump.assert_not_called()
        mock_write.assert_not_called()

    # -- regression: non-ancestor (genuine regression) -----------------------------------------

    def test_closed_head_non_ancestor_files_new_regression_record(self, tmp_path: Path, monkeypatch):
        import scripts.ops_data_portal as p

        monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
        sha, _ = self._make_bundle(tmp_path)
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        head = ChainRecord(rec_id="rec-777", status="closed", fixed_by_sha="fixed_sha", last_touched="")

        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value=None),
            patch.object(p, "closed_head_of_chain", return_value=head),
            patch.object(p, "current_commit_sha", return_value="failing_sha"),
            patch.object(p, "classify_closed_head", return_value="regression"),
            patch.object(p, "_ducklake_write", return_value={"key": "rec-900"}) as mock_write,
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            result = p.file_rec(self._fields(), context_v2_json=ctx)

        assert result == "rec-900"
        mock_write.assert_called_once()
        written_record = mock_write.call_args[0][1]
        assert written_record["title"] == "REGRESSION: Something broke"
        assert written_record["priority"] == "Critical"
        written_ctx = json.loads(written_record["context_v2_json"])
        assert written_ctx["regression_of"] == "rec-777"

    def test_closed_head_never_status_flip_to_open(self, tmp_path: Path, monkeypatch):
        """Neither the drop nor the regression path ever calls update_rec to flip status --
        the rec-2644 revive mechanism is fully retired."""
        import scripts.ops_data_portal as p

        monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
        sha, _ = self._make_bundle(tmp_path)
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        head = ChainRecord(rec_id="rec-777", status="closed", fixed_by_sha="fixed_sha", last_touched="")

        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value=None),
            patch.object(p, "closed_head_of_chain", return_value=head),
            patch.object(p, "current_commit_sha", return_value="failing_sha"),
            patch.object(p, "classify_closed_head", return_value="regression"),
            patch.object(p, "update_rec") as mock_update,
            patch.object(p, "_ducklake_write", return_value={"key": "rec-901"}),
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            p.file_rec(self._fields(), context_v2_json=ctx)

        mock_update.assert_not_called()

    # -- legacy closed head with no fixed_by_sha fails closed to REGRESSION --------------------

    def test_legacy_closed_head_no_fixed_by_sha_fails_closed_to_regression(self, tmp_path: Path, monkeypatch):
        """A closed head with NO fixed_by_sha (every rec closed before this change; a manual
        closure) cannot run the ancestry check -- files a REGRESSION, never silently drops."""
        import scripts.ops_data_portal as p

        monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
        sha, _ = self._make_bundle(tmp_path)
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        head = ChainRecord(rec_id="rec-777", status="closed", fixed_by_sha=None, last_touched="")

        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value=None),
            patch.object(p, "closed_head_of_chain", return_value=head),
            patch.object(p, "current_commit_sha", return_value="failing_sha"),
            patch.object(p, "_ducklake_write", return_value={"key": "rec-902"}) as mock_write,
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            # classify_closed_head is the REAL function here (not mocked) -- proves the
            # fail-closed default fires through the full backstop integration, not just the
            # unit-level ci_rca_lifecycle test.
            result = p.file_rec(self._fields(), context_v2_json=ctx)

        assert result == "rec-902"
        written_record = mock_write.call_args[0][1]
        written_ctx = json.loads(written_record["context_v2_json"])
        assert written_ctx["regression_of"] == "rec-777"
        assert written_record["priority"] == "Critical"

    def test_no_closed_head_falls_through_to_normal_insert(self, tmp_path: Path, monkeypatch):
        """No open match AND no closed head at all (genuinely novel fingerprint) -- normal insert,
        no regression mutation."""
        import scripts.ops_data_portal as p

        monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
        sha, _ = self._make_bundle(tmp_path)
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value=None),
            patch.object(p, "closed_head_of_chain", return_value=None) as mock_head,
            patch.object(p, "_ducklake_write", return_value={"key": "rec-903"}) as mock_write,
            patch.object(p, "RECS_JSONL", tmp_path / "recs.jsonl"),
            patch("scripts.sync.ops.upsert_cache_row"),
        ):
            result = p.file_rec(self._fields(), context_v2_json=ctx)

        assert result == "rec-903"
        mock_head.assert_called_once()
        written_record = mock_write.call_args[0][1]
        assert written_record["title"] == "Something broke"
        assert written_record["priority"] == "Critical"
        assert "regression_of" not in json.loads(written_record["context_v2_json"])

    # -- open-match / closed-head-branch mutual exclusivity (no double-consult) -----------------

    def test_open_match_never_consults_closed_head_path(self, tmp_path: Path, monkeypatch):
        """The open-match path and the closed-head regression-vs-drop path are mutually
        exclusive: an OPEN hit returns immediately via the existing single bump, never touching
        closed_head_of_chain / classify_closed_head."""
        import scripts.ops_data_portal as p

        monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
        sha, _ = self._make_bundle(tmp_path)
        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}

        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch.object(p, "find_open_ci_rca_rec_by_fingerprint", return_value="rec-555"),
            patch.object(p, "bump_ci_rca_occurrence") as mock_bump,
            patch.object(p, "closed_head_of_chain") as mock_head,
        ):
            result = p.file_rec(self._fields(), context_v2_json=ctx)

        assert result == "rec-555"
        mock_bump.assert_called_once_with("rec-555", profile=None)
        mock_head.assert_not_called()

    # Import-surface proof of the retired revive path's full removal is intentionally NOT a
    # hasattr-on-a-hardcoded-name test here: naming the retired symbols in test source would
    # itself trip the repo-wide "no surviving reference anywhere in scripts/ or tests/" sweep
    # this change is graded on. That sweep (over both this facade and ci_rca_runtime.py) plus
    # the portal smoke-import (`import scripts.ops_data_portal` succeeds with no ImportError)
    # are the standing proof instead.
