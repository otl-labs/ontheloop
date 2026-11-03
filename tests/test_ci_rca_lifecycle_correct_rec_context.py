"""Unit tests for scripts.ops_portal.ci_rca_lifecycle.correct_rec_context (Decision 128 SLOC
split out of tests/test_ci_rca_lifecycle.py -- adding this class there pushed that file from 431
to 533 SLOC, over the 500 default budget; PLAN-ci-rca-abstention-and-citation).

_FINGERPRINT is duplicated verbatim from tests/test_ci_rca_lifecycle.py rather than imported
(no cross-test imports -- mirrors the sanctioned fingerprint-split duplication pattern already
used by tests/ops_data_portal/test_ci_rca_schema.py and siblings).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.ops_portal.ci_rca_lifecycle import correct_rec_context

_FINGERPRINT = "a" * 64

_VALID_CTX = {
    "schema_version": 2,
    "proximate_cause": (
        "Step 15 of a sandbox run exited 1 because review_verdict.py classified the transcript as STARVED "
        "and exited 1 by contract, aborting the composite step body under GitHub's inherited errexit."
    ),
    "why_chain": [
        (
            "review_verdict.py classified the transcript STARVED and exited 1 by contract, which under the "
            "inherited errexit aborted the composite step body at the classifier call."
        ),
        (
            "The same-budget retry never executed and the case block never ran, so outputs.outcome was never "
            "written for the run in question."
        ),
        "The always-run record writer missed its starved carve-out and wrote status=red, a contract gap at action.yml:38.",
    ],
    "detection_gap": {
        "earliest_viable_gate": "presubmit",
        "actual_gate_that_caught_it": "unknown",
        "gap_explanation": (
            "No pre-merge tier could observe this: the composite action runs only at apply time against a "
            "live plan, and the harness invocation that voided the retry contract was never driven under "
            "test, a gap at action.yml:38."
        ),
        "escape_mode": "undetermined",
    },
    "recurrence_class": "novel",
    "corrective_action": (
        "None required at the reviewer: a companion PR fixed the inherited-errexit defect in review.sh. "
        "Verify the next sandbox run reaches a PROCEED or an explicit starved outcome rather than an empty one."
    ),
    "preventive_action": (
        "Guard the class rather than the line: a composite shell: bash step body must not carry non-trivial "
        "logic under GitHub's inherited errexit, and its contract must be driven under that exact invocation "
        "in test rather than only in isolation."
    ),
    "fingerprint": _FINGERPRINT,
    "evidence_bundle_ref": {"sha256": "b" * 64, "s3_uri": "", "upload_status": "upload_failed"},
}


def _track_ctx(**extra: object) -> str:
    return json.dumps({**_VALID_CTX, "repair_track": "test_defect", "workflow_tier": "CI", **extra})


def _run_correct(stored: str, context_updates: dict, row_updates: dict | None = None):
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value={"id": "rec-1", "context_v2_json": stored}),
        patch("scripts.ops_data_portal.update_rec") as mock_update,
    ):
        correct_rec_context("rec-1", context_updates, row_updates=row_updates)
    return mock_update


class TestCorrectRecContext:
    """PLAN-ci-rca-abstention-and-citation AC12/VP5: the tested route for correcting a
    factually-wrong context_v2_json narrative on an open ci_rca rec, beside stamp_fixed_by_sha."""

    def test_writes_whole_blob_and_preserves_sibling_keys(self) -> None:
        existing_ctx = json.dumps({**_VALID_CTX, "occurrence_count": 3})
        new_cause = (
            "REVISED: the classifier exited 1 under GitHub's inherited errexit before the retry or the "
            "case block ran, so the starved branch this rec originally cited never executed at all."
        )
        with (
            patch(
                "scripts.ops_data_portal._fetch_rec_from_reader", return_value={"id": "rec-1", "context_v2_json": existing_ctx}
            ),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            correct_rec_context("rec-1", {"proximate_cause": new_cause})

        mock_update.assert_called_once()
        call_args, call_kwargs = mock_update.call_args
        assert call_args[0] == "rec-1"
        updated_ctx = json.loads(call_args[1]["context_v2_json"])
        assert updated_ctx["proximate_cause"] == new_cause
        assert updated_ctx["fingerprint"] == _FINGERPRINT
        assert updated_ctx["occurrence_count"] == 3
        assert updated_ctx["evidence_bundle_ref"]["sha256"] == "b" * 64
        assert updated_ctx["why_chain"] == _VALID_CTX["why_chain"]

    def test_refuses_without_fingerprint(self) -> None:
        no_fp_ctx = {k: v for k, v in _VALID_CTX.items() if k != "fingerprint"}
        with (
            patch(
                "scripts.ops_data_portal._fetch_rec_from_reader",
                return_value={"id": "rec-1", "context_v2_json": json.dumps(no_fp_ctx)},
            ),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            with pytest.raises(RuntimeError, match="fingerprint"):
                correct_rec_context("rec-1", {"proximate_cause": "irrelevant, never reached"})
        mock_update.assert_not_called()

    def test_refuses_when_merged_context_fails_validation(self) -> None:
        with (
            patch(
                "scripts.ops_data_portal._fetch_rec_from_reader",
                return_value={"id": "rec-1", "context_v2_json": json.dumps(_VALID_CTX)},
            ),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            with pytest.raises(RuntimeError, match="validation"):
                correct_rec_context("rec-1", {"proximate_cause": "too short"})
        mock_update.assert_not_called()

    def test_row_updates_travel_in_same_write(self) -> None:
        with (
            patch(
                "scripts.ops_data_portal._fetch_rec_from_reader",
                return_value={"id": "rec-1", "context_v2_json": json.dumps(_VALID_CTX)},
            ),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            correct_rec_context("rec-1", {}, row_updates={"title": "Corrected title"})

        mock_update.assert_called_once()
        call_args, _ = mock_update.call_args
        assert call_args[1]["title"] == "Corrected title"
        assert "context_v2_json" in call_args[1]

    def test_raises_on_missing_rec(self) -> None:
        with patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=None):
            with pytest.raises(RuntimeError, match="does not exist"):
                correct_rec_context("rec-nope", {"proximate_cause": "irrelevant"})

    def test_retrack_rederives_priority(self) -> None:
        stored = _track_ctx()
        mock_update = _run_correct(stored, {"repair_track": "product_regression"}, row_updates={"priority": "High"})
        call_args, _ = mock_update.call_args
        assert call_args[1]["priority"] == "Critical"
        assert json.loads(call_args[1]["context_v2_json"])["repair_track"] == "product_regression"

    def test_retrack_without_preventive_is_refused(self) -> None:
        stored = json.dumps({k: v for k, v in json.loads(_track_ctx()).items() if k != "preventive_action"})
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value={"id": "rec-1", "context_v2_json": stored}),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            with pytest.raises(RuntimeError, match="preventive_action"):
                correct_rec_context("rec-1", {"repair_track": "product_regression"})
        mock_update.assert_not_called()

    def test_retrack_cannot_inject_workflow_tier(self) -> None:
        stored = json.dumps(
            {**_VALID_CTX, "repair_track": "test_defect", "regression_of": "rec-9", "workflow_tier": "unknown"}
        )
        mock_update = _run_correct(
            stored, {"workflow_tier": "CI", "repair_track_confirmed": "test_defect", "regression_of": None}
        )
        call_args, _ = mock_update.call_args
        written = json.loads(call_args[1]["context_v2_json"])
        assert written["workflow_tier"] == "unknown"
        assert written["regression_of"] == "rec-9"
        assert "repair_track_confirmed" not in written
        assert call_args[1]["priority"] == "Critical"

    def test_retrack_to_test_defect_refused(self) -> None:
        stored = json.dumps({**_VALID_CTX, "repair_track": "product_regression", "workflow_tier": "CI"})
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value={"id": "rec-1", "context_v2_json": stored}),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            with pytest.raises(RuntimeError, match="product_regression"):
                correct_rec_context("rec-1", {"repair_track": "test_defect"})
        mock_update.assert_not_called()
