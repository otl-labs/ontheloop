"""Tests for the CiRcaEvidenceDispute schema and the Section-4 check-8 evidence-dispute
carve-out in file_rec(), plus CIRCA-02 strict-mode legacy (no context_v2_json) rejection.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3).

_CI_RCA_FIELDS is duplicated verbatim from the monolith (also used by test_ci_rca_schema.py /
test_ci_rca_runtime.py / test_ci_rca_propose_close.py) rather than hoisted to tests/fixtures/ --
see test_ci_rca_schema.py's header note. _DISPUTE_FIELDS / _VALID_DISPUTE_PAYLOAD are
single-consumer (TestCiRcaEvidenceDispute only) so they move here as plain module constants.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb")
from pydantic import ValidationError  # noqa: E402

from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS  # noqa: E402

_CI_RCA_FIELDS = {
    **_VALID_FIELDS,
    "source": "ci_rca",
    "context": ("CI RCA test rec with sufficient length to satisfy the 80-char minimum for the legacy context column field."),
}

_DISPUTE_FIELDS = {
    **_VALID_FIELDS,
    "source": "ci_rca_evidence_dispute",
    "file": "scripts/ops_data_portal.py",
    "context": "",
}

_VALID_DISPUTE_PAYLOAD = {
    "parent_rec_id": "rec-1234",
    "disputed_field": "earliest_viable_gate",
    "agent_value": "pre",
    "bundle_value": "CI",
    "evidence_for_dispute": (
        "scripts/collect_ci_evidence.py:142 shows earliest_viable_gate is derived from validate.py --pre "
        "output; the agent value 'pre' is wrong because the check is guarded by a CI-only env var at "
        "validate.py:87, making 'pre' impossible."
    ),
}


class TestCiRcaEvidenceDispute:
    """Tests for the CiRcaEvidenceDispute schema and the Section-4 check-8 carve-out in file_rec()."""

    def test_valid_payload_passes(self) -> None:
        """CiRcaEvidenceDispute.model_validate accepts a well-formed dispute payload."""
        from scripts.ops_data_portal import CiRcaEvidenceDispute

        result = CiRcaEvidenceDispute.model_validate(_VALID_DISPUTE_PAYLOAD)
        assert result.parent_rec_id == "rec-1234"
        assert result.disputed_field == "earliest_viable_gate"

    def test_bad_parent_rec_id_raises(self) -> None:
        """A parent_rec_id that does not match ^rec-\\d+$ raises ValidationError."""
        from scripts.ops_data_portal import CiRcaEvidenceDispute

        bad = {**_VALID_DISPUTE_PAYLOAD, "parent_rec_id": "REC-1234"}
        with pytest.raises(ValidationError):
            CiRcaEvidenceDispute.model_validate(bad)

    def test_out_of_enum_disputed_field_raises(self) -> None:
        """A disputed_field not in {earliest_viable_gate, actual_gate_that_caught_it} raises ValidationError."""
        from scripts.ops_data_portal import CiRcaEvidenceDispute

        bad = {**_VALID_DISPUTE_PAYLOAD, "disputed_field": "gap_explanation"}
        with pytest.raises(ValidationError):
            CiRcaEvidenceDispute.model_validate(bad)

    def test_too_short_evidence_raises(self) -> None:
        """evidence_for_dispute shorter than 120 chars raises ValidationError."""
        from scripts.ops_data_portal import CiRcaEvidenceDispute

        bad = {**_VALID_DISPUTE_PAYLOAD, "evidence_for_dispute": "Too short."}
        with pytest.raises(ValidationError):
            CiRcaEvidenceDispute.model_validate(bad)

    def test_both_disputed_field_values_accepted(self) -> None:
        """All allowed disputed_field enum values pass schema validation (incl. failure_category)."""
        from scripts.ops_data_portal import CiRcaEvidenceDispute

        for value in ("earliest_viable_gate", "actual_gate_that_caught_it", "failure_category"):
            payload = {**_VALID_DISPUTE_PAYLOAD, "disputed_field": value}
            result = CiRcaEvidenceDispute.model_validate(payload)
            assert result.disputed_field == value

    def test_file_rec_carve_out_bypasses_ci_rca_checks(self, tmp_path: Path) -> None:
        """file_rec(source=ci_rca_evidence_dispute) bypasses ci_rca checks 1-7: no why_chain, no source_file required."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-5001"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            rec_id = p.file_rec(dict(_DISPUTE_FIELDS), context_v2_json=_VALID_DISPUTE_PAYLOAD)
        assert rec_id == "rec-5001"
        entry = mock_write.call_args.args[1]
        assert "context_v2_json" in entry
        stored = json.loads(entry["context_v2_json"])
        assert stored["parent_rec_id"] == "rec-1234"

    def test_file_rec_carve_out_no_ci_rca_context_required(self, tmp_path: Path) -> None:
        """Dispute rec filed without CiRcaContext fields (no why_chain, detection_gap, etc.) does not raise."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-5002"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            rec_id = p.file_rec(dict(_DISPUTE_FIELDS), context_v2_json=_VALID_DISPUTE_PAYLOAD)
        assert rec_id == "rec-5002"

    def test_file_rec_malformed_dispute_hard_raises_in_warn_mode(self, tmp_path: Path) -> None:
        """Malformed dispute payload raises ValueError even when CI_RCA_STRICT_MODE=warn (mode-independent)."""
        import scripts.ops_data_portal as p

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: warn\n", encoding="utf-8")
        bad_payload = {**_VALID_DISPUTE_PAYLOAD, "disputed_field": "invalid_field_name"}
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="ci_rca_evidence_dispute"):
                p.file_rec(dict(_DISPUTE_FIELDS), context_v2_json=bad_payload)

    def test_file_rec_dispute_missing_context_v2_raises(self) -> None:
        """source=ci_rca_evidence_dispute without context_v2_json raises ValueError."""
        import scripts.ops_data_portal as p

        with pytest.raises(ValueError, match="context_v2_json"):
            p.file_rec(dict(_DISPUTE_FIELDS))

    def test_write_guidance_returns_all_five_dispute_fields(self) -> None:
        """get_rec_write_guidance(source='ci_rca_evidence_dispute') returns all five dispute fields as top-level keys."""
        from scripts.executor.rec_write_guidance import get_rec_write_guidance

        guidance = get_rec_write_guidance(source="ci_rca_evidence_dispute")
        for field in ("parent_rec_id", "disputed_field", "agent_value", "bundle_value", "evidence_for_dispute"):
            assert field in guidance, f"guidance missing top-level key {field!r}"
            assert "description" in guidance[field], f"guidance[{field!r}] missing 'description'"
            assert "semantics" in guidance[field], f"guidance[{field!r}] missing 'semantics'"

    def test_dispute_source_registered(self) -> None:
        """validate_source('ci_rca_evidence_dispute') does not raise -- the source is registered."""
        from scripts.executor.rec_write_guidance import validate_source

        validate_source("ci_rca_evidence_dispute")  # should not raise


class TestCiRcaStrictLegacyReject:
    """CIRCA-02: strict mode rejects a source=ci_rca write with context_v2_json=None."""

    def test_strict_mode_rejects_legacy_no_context(self, tmp_path: Path) -> None:
        import scripts.ops_data_portal as p

        flags_file = tmp_path / "feature_flags.yaml"
        flags_file.write_text("CI_RCA_STRICT_MODE: strict\n", encoding="utf-8")
        with patch("scripts.ops_data_portal._FEATURE_FLAGS_YAML", flags_file):
            with pytest.raises(ValueError, match="CI_RCA_STRICT_MODE=strict"):
                p.file_rec(dict(_CI_RCA_FIELDS))

    def test_warn_mode_still_files_legacy_with_deprecation_warning(self, tmp_path: Path, caplog) -> None:
        """Warn mode keeps accepting the legacy no-context_v2_json path (rollout window)."""
        import scripts.ops_data_portal as p

        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9201"}),
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
            caplog.at_level(logging.WARNING, logger="scripts.ops_data_portal"),
        ):
            rec_id = p.file_rec(dict(_CI_RCA_FIELDS))
        assert rec_id == "rec-9201"
        assert any("legacy free-text" in r.message for r in caplog.records)
