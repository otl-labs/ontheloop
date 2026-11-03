"""Tests for scripts/ops_portal/ci_rca_runtime.py's propose_or_close_rec (T3.8 / CD.36
close_proposed lifecycle), the bundle-absent fail-loud c12(ii) gate, S3-object-existence
verification (c5 / INTENT Section 4 check 7), and the warn-mode would-reject stamp (c3
enabler, T1.13 Section 7.2 gauge).

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).

_CI_RCA_FIELDS and _VALID_CONTEXT_V2 are duplicated verbatim from the monolith (also used by
test_ci_rca_schema.py / test_ci_rca_dispute.py / test_ci_rca_runtime.py) rather than hoisted to
tests/fixtures/ -- see test_ci_rca_schema.py's header note.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ops_portal import ci_rca_schema as _ci_rca_schema_mod  # noqa: E402
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402
from tests.fixtures.ops_rec_rows import reader_rec_row  # noqa: E402

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


class TestProposeOrCloseRec:
    """Tests for propose_or_close_rec (T3.8 / CD.36 close_proposed lifecycle support)."""

    def test_deterministic_satisfied_auto_closes(self, monkeypatch) -> None:
        """Deterministic satisfied => update_rec called with status=closed and proof in resolution."""
        import scripts.ops_data_portal as p

        calls: list[dict] = []
        monkeypatch.setattr(
            p, "update_rec", lambda rec_id, updates, profile=None: calls.append({"rec_id": rec_id, "updates": updates})
        )

        result = p.propose_or_close_rec("rec-001", "satisfied", "acceptance probe passed: echo ok", deterministic=True)
        assert result is None
        assert len(calls) == 1
        assert calls[0]["rec_id"] == "rec-001"
        assert calls[0]["updates"]["status"] == "closed"
        assert "acceptance probe passed" in calls[0]["updates"]["resolution"]

    def test_semantic_satisfied_yields_proposal_not_auto_close(self, monkeypatch) -> None:
        """Semantic satisfied => proposal string returned, update_rec NOT called."""
        import scripts.ops_data_portal as p

        calls: list[dict] = []
        monkeypatch.setattr(p, "update_rec", lambda rec_id, updates, profile=None: calls.append(rec_id))

        result = p.propose_or_close_rec(
            "rec-001", "satisfied", "semantic: file foo.py modified in abc12345", deterministic=False
        )
        assert result is not None
        assert "rec-001" in result
        assert "--status closed" in result
        assert len(calls) == 0

    def test_superseded_yields_proposal(self, monkeypatch) -> None:
        """Semantic superseded => proposal string, update_rec NOT called."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        result = p.propose_or_close_rec("rec-002", "superseded", "semantic: closed sibling rec-001 title similarity >= 0.5")
        assert result is not None
        assert "rec-002" in result
        assert "superseded" in result

    def test_duplicate_yields_proposal(self, monkeypatch) -> None:
        """Duplicate verdict => proposal string."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        result = p.propose_or_close_rec("rec-003", "duplicate", "semantic: open duplicate rec-999 title similarity >= 0.7")
        assert result is not None
        assert "duplicate" in result

    def test_duplicate_proposal_names_closure_artifact_flag(self, monkeypatch) -> None:
        """Decision 186 duplicate-ruling fallout: the printed command names --closure-artifact,
        so an operator handed a duplicate verdict on an escape-classified rec is told how to
        satisfy the gate rather than the command silently reading as a bug."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        result = p.propose_or_close_rec("rec-003", "duplicate", "semantic: open duplicate rec-999 title similarity >= 0.7")
        assert result is not None
        assert "--closure-artifact" in result

    def test_contradicted_yields_proposal(self, monkeypatch) -> None:
        """Contradicted verdict => proposal string."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        result = p.propose_or_close_rec("rec-004", "contradicted", "Decision 5 is superseded")
        assert result is not None
        assert "contradicted" in result

    def test_stale_target_yields_proposal(self, monkeypatch) -> None:
        """Stale target verdict => proposal string (not auto-close)."""
        import scripts.ops_data_portal as p

        calls: list[dict] = []
        monkeypatch.setattr(p, "update_rec", lambda rec_id, updates, profile=None: calls.append(rec_id))
        result = p.propose_or_close_rec("rec-005", "stale_target", "target file absent: scripts/old.py")
        assert result is not None
        assert "stale_target" in result
        assert len(calls) == 0

    def test_blocked_by_decision_yields_proposal(self, monkeypatch) -> None:
        """Blocked verdict => proposal string."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        result = p.propose_or_close_rec("rec-006", "blocked_by_decision", "Decision 7 is pending")
        assert result is not None
        assert "blocked_by_decision" in result

    def test_relevant_returns_none(self, monkeypatch) -> None:
        """Relevant verdict => no action (None returned)."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        assert p.propose_or_close_rec("rec-007", "relevant", "no resolution signals detected") is None

    def test_unknown_returns_none(self, monkeypatch) -> None:
        """Unknown verdict => no action (None returned)."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        assert p.propose_or_close_rec("rec-008", "unknown", "no signals available") is None

    def test_evidence_with_quotes_escaped_in_proposal(self, monkeypatch) -> None:
        """Evidence string with quotes is shell-safe in the proposal command."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(p, "update_rec", lambda *a, **k: None)
        result = p.propose_or_close_rec("rec-009", "superseded", 'evidence with "quotes" inside')
        assert result is not None
        assert '\\"quotes\\"' in result

    def test_deterministic_close_on_escape_rec_raises(self) -> None:
        """The fourth path: the deterministic-satisfied auto-close (which calls update_rec
        directly) RAISES ClosureArtifactRequired on an escape-classified rec rather than closing
        it silently, and performs no write."""
        import scripts.ops_data_portal as p

        existing = reader_rec_row(id="rec-010", context_v2_json=json.dumps({"escape_class": "no-edge"}))
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        ):
            with pytest.raises(p.ClosureArtifactRequired):
                p.propose_or_close_rec("rec-010", "satisfied", "acceptance probe passed: echo ok", deterministic=True)
        mock_write.assert_not_called()


class TestBundleAbsentFailLoud:
    """c12(ii): _run_ci_rca_cross_check bundle-absent fail-loud (ops_data_portal.py)."""

    def _ctx_v2(self, **overrides):
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": "test gap explanation with enough chars for the field",
        }
        ctx = {
            "schema_version": 2,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, "c " * 25 + "systemic scripts/validate.py:1"],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }
        ctx.update(overrides)
        return ctx

    def test_strict_rejects_bundle_absent_without_undetermined(self, tmp_path) -> None:
        """No evidence_bundle_ref and rca_confidence != undetermined -> strict rejects."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()  # no evidence_bundle_ref, no rca_confidence
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="CI_RCA_BUNDLE_ABSENT"):
                p._run_ci_rca_cross_check(ctx)

    def test_strict_accepts_bundle_absent_with_undetermined(self, tmp_path, caplog) -> None:
        """No evidence_bundle_ref but rca_confidence=undetermined -> strict accepts (human-route)."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["rca_confidence"] = "undetermined"
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            p._run_ci_rca_cross_check(ctx)  # must not raise
        assert any("routed to mandatory human review" in r.message for r in caplog.records)

    def test_warn_accepts_bundle_absent_and_logs_gauge(self, caplog) -> None:
        """Warn mode (default) accepts a bundle-absent rec but logs the CI_RCA_BUNDLE_ABSENT gauge."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        with caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"):
            p._run_ci_rca_cross_check(ctx)  # must not raise
        assert any("CI_RCA_BUNDLE_ABSENT" in r.message for r in caplog.records)


class TestEvidenceS3Existence:
    """c5 / INTENT Section 4 check 7: S3-object-existence verification (ops_data_portal.py)."""

    _S3_URI = "s3://agent-platform-data-lake/ci-rca-evidence/" + "a" * 64 + ".json"

    def _ctx_v2(self, **overrides):
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": "test gap explanation with enough chars for the field",
        }
        ctx = {
            "schema_version": 2,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, "c " * 25 + "systemic scripts/validate.py:1"],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }
        ctx.update(overrides)
        return ctx

    def test_object_present_accepts(self, tmp_path) -> None:
        """upload_status=ok and head_object succeeds -> accepted, S3 verified."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": "a" * 64, "s3_uri": self._S3_URI, "upload_status": "ok"}
        mock_client = MagicMock()
        mock_client.head_object.return_value = {}
        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            p._run_ci_rca_cross_check(ctx, s3_client_factory=lambda: mock_client)  # must not raise
        mock_client.head_object.assert_called_once()

    def test_object_missing_strict_rejects(self, tmp_path) -> None:
        """upload_status=ok but head_object 404s -> strict mode rejects."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": "a" * 64, "s3_uri": self._S3_URI, "upload_status": "ok"}
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception("404 NoSuchKey")
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
        ):
            with pytest.raises(ValueError, match="CI_RCA_EVIDENCE_S3_MISSING"):
                p._run_ci_rca_cross_check(ctx, s3_client_factory=lambda: mock_client)

    def test_object_missing_warn_warns(self, tmp_path, caplog) -> None:
        """upload_status=ok but head_object 404s -> warn mode accepts + logs."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": "a" * 64, "s3_uri": self._S3_URI, "upload_status": "ok"}
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception("404 NoSuchKey")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            p._run_ci_rca_cross_check(ctx, s3_client_factory=lambda: mock_client)  # must not raise
        assert any("CI_RCA_EVIDENCE_S3_MISSING" in r.message for r in caplog.records)

    def test_upload_failed_takes_degraded_path_no_s3_call(self, tmp_path, caplog) -> None:
        """upload_status=upload_failed -> degraded accept, no head_object call made."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": "a" * 64, "s3_uri": self._S3_URI, "upload_status": "upload_failed"}
        mock_client = MagicMock()
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            p._run_ci_rca_cross_check(ctx, s3_client_factory=lambda: mock_client)  # must not raise
        mock_client.head_object.assert_not_called()
        assert any("CI_RCA_EVIDENCE_S3_DEGRADED" in r.message for r in caplog.records)

    def test_s3_permission_error_fails_open(self, tmp_path, caplog) -> None:
        """A non-404 S3 client error (e.g. AccessDenied) fails open with a warning, even in strict mode."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {"sha256": "a" * 64, "s3_uri": self._S3_URI, "upload_status": "ok"}
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception("AccessDenied: permission denied")
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with (
            patch.object(_ci_rca_schema_mod, "ROOT", tmp_path),
            patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            p._run_ci_rca_cross_check(ctx, s3_client_factory=lambda: mock_client)  # must not raise
        assert any("CI_RCA_EVIDENCE_S3_FAIL_OPEN" in r.message for r in caplog.records)


class TestWarnModeRejectMarker:
    """c3 enabler: warn-mode would-reject stamp on source=ci_rca recs (T1.13 Section 7.2 gauge)."""

    def _ctx_v2(self, **overrides):
        dg = {
            "earliest_viable_gate": "pre",
            "actual_gate_that_caught_it": "CI",
            "gap_explanation": "test gap explanation with enough chars for the field",
        }
        ctx = {
            "schema_version": 2,
            "proximate_cause": "x" * 100,
            "why_chain": ["a " * 25, "b " * 25, "c " * 25 + "systemic scripts/validate.py:1"],
            "detection_gap": dg,
            "recurrence_class": "novel",
            "corrective_action": "x" * 100,
            "preventive_action": "x" * 100,
        }
        ctx.update(overrides)
        return ctx

    def test_schema_deficiency_warn_stamps_marker(self, tmp_path: Path) -> None:
        """Warn mode accepts a schema-deficient write and stamps warn_mode_reject.reasons."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        deficient_ctx = {
            **_VALID_CONTEXT_V2,
            "why_chain": ["short", "also short", "still short"],
            "rca_confidence": "undetermined",  # isolate the schema-deficiency stamp from bundle-absent
        }
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9101"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            rec_id = p.file_rec(dict(_CI_RCA_FIELDS), context_v2_json=deficient_ctx)
        assert rec_id == "rec-9101"
        entry = mock_write.call_args.args[1]
        stored = json.loads(entry["context_v2_json"])
        # CIRCA-04: per-rule stamping -- a too-short why_chain entry stamps the specific
        # schema_why_chain_too_short tag, not the bare "schema_deficiency" bucket.
        assert stored["warn_mode_reject"]["reasons"] == ["schema_why_chain_too_short"]
        assert stored["warn_mode_reject"]["mode_at_write"] == "warn"

    def test_schema_deficiency_strict_raises_no_rec(self, tmp_path: Path) -> None:
        """Strict mode raises on a schema-deficient write -- no rec is created, no marker exists."""
        import scripts.ops_data_portal as p

        deficient_ctx = {**_VALID_CONTEXT_V2, "why_chain": ["short", "also short", "still short"]}
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="CI_RCA_STRICT_MODE=strict"):
                p.file_rec(dict(_CI_RCA_FIELDS), context_v2_json=deficient_ctx)
        assert "warn_mode_reject" not in deficient_ctx

    def test_bundle_absent_warn_stamps_marker(self) -> None:
        """Warn mode accepts a bundle-absent write and stamps warn_mode_reject.reasons."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()  # no evidence_bundle_ref, no rca_confidence
        p._run_ci_rca_cross_check(ctx)  # must not raise
        assert ctx["warn_mode_reject"]["reasons"] == ["bundle_absent"]
        assert ctx["warn_mode_reject"]["mode_at_write"] == "warn"

    def test_bundle_absent_strict_raises_no_marker(self, tmp_path: Path) -> None:
        """Strict mode raises on a bundle-absent write -- no marker is stamped."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="CI_RCA_STRICT_MODE=strict"):
                p._run_ci_rca_cross_check(ctx)
        assert "warn_mode_reject" not in ctx

    def test_s3_missing_warn_stamps_marker(self, tmp_path: Path) -> None:
        """Warn mode accepts an S3-missing write and stamps warn_mode_reject.reasons."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()
        ctx["evidence_bundle_ref"] = {
            "sha256": "a" * 64,
            "s3_uri": "s3://agent-platform-data-lake/ci-rca-evidence/" + "a" * 64 + ".json",
            "upload_status": "ok",
        }
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception("404 NoSuchKey")
        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            p._run_ci_rca_cross_check(ctx, s3_client_factory=lambda: mock_client)  # must not raise
        assert ctx["warn_mode_reject"]["reasons"] == ["s3_missing"]

    def test_cross_check_disagreement_warn_stamps_marker(self, tmp_path: Path) -> None:
        """Warn mode accepts a check-2 cross-check disagreement and stamps warn_mode_reject.reasons."""
        import hashlib as _hashlib
        import json as _json

        import scripts.ops_data_portal as p

        bundle_data = {"earliest_viable_gate": "CI", "escape_mode": "tier_misplaced", "vacuous_pass": False}
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
        ctx = self._ctx_v2(earliest_viable_gate="pre")
        ctx["evidence_bundle_ref"] = {"sha256": sha, "s3_uri": "", "upload_status": "ok"}
        with patch.object(_ci_rca_schema_mod, "ROOT", tmp_path):
            p._run_ci_rca_cross_check(ctx)  # must not raise
        assert ctx["warn_mode_reject"]["reasons"] == ["cross_check_check_2"]

    def test_conformant_warn_write_carries_no_marker(self) -> None:
        """A fully-conformant warn-mode write carries NO warn_mode_reject marker (absent, not empty)."""
        import scripts.ops_data_portal as p

        ctx = self._ctx_v2()  # no evidence_bundle_ref, rca_confidence=undetermined routes cleanly
        ctx["rca_confidence"] = "undetermined"
        p._run_ci_rca_cross_check(ctx)  # must not raise
        assert "warn_mode_reject" not in ctx

    def test_marker_survives_ci_rca_context_round_trip(self) -> None:
        """A stamped warn_mode_reject marker survives a CiRcaContext model parse round-trip."""
        import scripts.ops_data_portal as p

        ctx = dict(_VALID_CONTEXT_V2)
        p._stamp_warn_mode_reject(ctx, ["schema_deficiency", "bundle_absent"])
        parsed = p.CiRcaContext.model_validate(ctx)
        assert parsed.warn_mode_reject == {
            "reasons": ["schema_deficiency", "bundle_absent"],
            "mode_at_write": "warn",
        }
