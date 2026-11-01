"""Unit tests for scripts.convergence_health.code_drift's DuckLake-side alarm (rec-2709 Wave 6
package-mirror, further concern-split by PLAN-convergence-health-prod-drift-red), plus the
shared-behaviour classes that belong to neither the ducklake nor the prod half exclusively
(TestUnknownActionFallsThroughToSkipped, TestAcceptanceLint).

Free of live AWS/git dependencies: the S3 client, git runner, and portal caller are injected.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

# boto3 is imported at MODULE scope even though the tests reference it only via
# patch("boto3.Session") strings. This makes the file's heavy-dep requirement visible to the
# fast tier's cheap `--collect-only` pass so pr-validate defers it PROACTIVELY to the full
# post-merge tier, instead of catching it REACTIVELY -- which re-runs the entire changed-test set
# a second time after a runtime ModuleNotFoundError and roughly doubles the pytest cost. boto3 is
# deliberately excluded from requirements-fast.txt; the full tier runs this file. See
# scripts/checks/_scaffolding.py::partition_changed_tests_by_collectability.
import boto3  # noqa: F401

from scripts.build_lambda_config import (
    _DUCKLAKE_CATALOG_DR_FUNCTION,
    _DUCKLAKE_MAINTENANCE_FUNCTION,
    _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,
    _DUCKLAKE_READER_FUNCTION,
    _DUCKLAKE_TELEMETRY_READER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION,
    _DUCKLAKE_WRITER_FUNCTION,
)
from scripts.convergence_health import (
    detect_ducklake_code_drift,
    detect_prod_code_drift,
)

from .conftest import GitRunnerStub, _FakeDeployRecordsS3, _RecordingS3

_ALL_DUCKLAKE_FUNCTIONS = {
    _DUCKLAKE_WRITER_FUNCTION,
    _DUCKLAKE_READER_FUNCTION,
    _DUCKLAKE_MAINTENANCE_FUNCTION,
    _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,
    _DUCKLAKE_CATALOG_DR_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION,
    _DUCKLAKE_TELEMETRY_READER_FUNCTION,
}


class TestFindOpenDucklakeDriftRec:
    """rec-3291 / rec-3563: find_open_ducklake_drift_rec no longer exists (migrated onto
    scripts.rec_episode.run_episode's source-scoped find_rec). Re-pointed at
    detect_ducklake_code_drift's real dedup behaviour."""

    def test_matches_the_open_rec_for_this_source(self) -> None:
        acts: list[str] = []
        existing = {"id": "rec-101", "source": "ducklake_code_drift", "status": "open"}
        stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_OLD": "SHA_OLD"})
        result = detect_ducklake_code_drift(
            git_runner=stub,
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=lambda a, f: acts.append(a),
            open_recs=[existing],
        )
        assert result == {"action": "update", "rec_id": "rec-101"}

    def test_scoped_read_never_matches_a_different_source(self) -> None:
        other_source_row = {"id": "rec-100", "source": "tf_convergence_stale", "status": "open"}
        result = detect_ducklake_code_drift(
            git_runner=lambda argv: "SHA_OLD",
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=lambda a, f: "rec-x",
            open_recs=[other_source_row],
        )
        assert result == {"action": "none", "rec_id": None}


class TestDetectDucklakeCodeDrift:
    def _acts_caller(self, acts: list[str]):
        def _caller(action: str, fields: dict[str, Any]) -> Any:
            acts.append(action)
            return "rec-DRYRUN"

        return _caller

    def test_fresh_all_records_match_no_file(self) -> None:
        """Every deploy record's recorded sha reaches the same commit as HEAD's latest -- a
        single constant-answer git_runner correctly simulates this (every lookup, including the
        shallow-history guard, returns the same non-"true" value)."""
        acts: list[str] = []
        result = detect_ducklake_code_drift(
            git_runner=lambda argv: "SHA_OLD",
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=self._acts_caller(acts),
            open_recs=[],
        )
        assert result == {"action": "none", "rec_id": None}
        assert acts == []

    def test_stale_all_records_mismatch_files_exactly_one(self) -> None:
        acts: list[str] = []
        stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_OLD": "SHA_OLD"})
        result = detect_ducklake_code_drift(
            git_runner=stub,
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=self._acts_caller(acts),
            open_recs=[],
        )
        assert result["action"] == "file"
        assert acts.count("file") == 1

    def test_one_function_stale_still_files_exactly_one(self) -> None:
        """Only the writer is behind main -- ANY stale function triggers ONE rec, not per-function."""
        acts: list[str] = []
        stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_NEW": "SHA_NEW", "SHA_OLD": "SHA_OLD"})
        s3 = _FakeDeployRecordsS3(
            default_sha="SHA_NEW",
            sha_by_function={_DUCKLAKE_WRITER_FUNCTION: "SHA_OLD"},
        )
        result = detect_ducklake_code_drift(
            git_runner=stub,
            s3_client=s3,
            portal_caller=self._acts_caller(acts),
            open_recs=[],
        )
        assert result["action"] == "file"
        assert acts == ["file"]

    def test_missing_record_counts_as_stale(self) -> None:
        """A function with NO deploy record at all (never governed-deployed) is stale, not fresh."""
        acts: list[str] = []
        result = detect_ducklake_code_drift(
            git_runner=lambda argv: "SHA_NEW",
            s3_client=_FakeDeployRecordsS3(default_sha=None),  # every get_object raises NoSuchKey
            portal_caller=self._acts_caller(acts),
            open_recs=[],
        )
        assert result["action"] == "file"

    def test_dedup_second_stale_tick_updates_not_files(self) -> None:
        acts: list[str] = []
        existing = {"id": "rec-321", "source": "ducklake_code_drift", "status": "open"}
        stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_OLD": "SHA_OLD"})
        result = detect_ducklake_code_drift(
            git_runner=stub,
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=self._acts_caller(acts),
            open_recs=[existing],
        )
        assert result == {"action": "update", "rec_id": "rec-321"}
        assert acts == ["update"]

    def test_fresh_with_open_rec_closes(self) -> None:
        acts: list[str] = []
        existing = {"id": "rec-654", "source": "ducklake_code_drift", "status": "open"}
        result = detect_ducklake_code_drift(
            git_runner=lambda argv: "SHA_OLD",
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=self._acts_caller(acts),
            open_recs=[existing],
        )
        assert result == {"action": "close", "rec_id": "rec-654"}
        assert acts == ["close"]

    def test_reads_all_ducklake_functions(self) -> None:
        seen_functions: set[str] = set()
        detect_ducklake_code_drift(
            git_runner=lambda argv: "SHA_OLD",
            s3_client=_RecordingS3(seen_functions),
            portal_caller=lambda a, f: "rec-x",
            open_recs=[],
        )
        assert seen_functions == _ALL_DUCKLAKE_FUNCTIONS

    def test_telemetry_writer_source_directory_is_watched(self) -> None:
        from scripts.convergence_health.code_drift import DUCKLAKE_SOURCE_PATHSPECS

        assert "src/lambdas/ducklake_telemetry_writer" in DUCKLAKE_SOURCE_PATHSPECS

    def test_telemetry_reader_source_directory_is_watched(self) -> None:
        from scripts.convergence_health.code_drift import DUCKLAKE_SOURCE_PATHSPECS

        assert "src/lambdas/ducklake_telemetry_reader" in DUCKLAKE_SOURCE_PATHSPECS

    def test_git_runner_receives_ducklake_source_pathspecs(self) -> None:
        stub = GitRunnerStub(head_latest="SHA_OLD", reachable={"SHA_OLD": "SHA_OLD"})
        detect_ducklake_code_drift(
            git_runner=stub,
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=lambda a, f: "rec-x",
            open_recs=[],
        )
        assert ["git", "rev-parse", "--is-shallow-repository"] in stub.calls
        head_calls = [c for c in stub.calls if c[:4] == ["git", "log", "-1", "--format=%H"] and c[4] == "--"]
        assert len(head_calls) == 1
        argv = head_calls[0]
        assert "src/common/ducklake_*.py" in argv
        assert "config/lambda/ducklake" in argv

    def test_rec_fields_shape_on_file(self) -> None:
        captured: dict[str, Any] = {}

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            if action == "file":
                captured.update(fields)
            return "rec-999"

        stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_OLD": "SHA_OLD"})
        detect_ducklake_code_drift(
            git_runner=stub,
            s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
            portal_caller=_caller,
            open_recs=[],
        )
        assert captured["source"] == "ducklake_code_drift"
        assert captured["priority"] == "High"
        assert captured["status"] == "open"
        assert _DUCKLAKE_WRITER_FUNCTION in captured["context"]

    def test_open_recs_none_fetches_via_scoped_current_state_read(self) -> None:
        reader = MagicMock()
        reader.current_state.return_value = []
        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader) as mk:
            result = detect_ducklake_code_drift(
                git_runner=lambda argv: "SHA_OLD",
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
                portal_caller=lambda a, f: "rec-x",
            )
        mk.assert_called_once_with(profile=None)
        reader.current_state.assert_called_once_with("ops_recommendations", row_filter="source = 'ducklake_code_drift'")
        assert result == {"action": "none", "rec_id": None}

    def test_s3_client_none_creates_boto3_session_client(self) -> None:
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = _FakeDeployRecordsS3(default_sha="SHA_OLD")
            result = detect_ducklake_code_drift(
                git_runner=lambda argv: "SHA_OLD",
                portal_caller=lambda a, f: "rec-x",
                open_recs=[],
                profile="agent_platform",
            )
        mock_session.assert_called_once_with(profile_name="agent_platform")
        assert result == {"action": "none", "rec_id": None}

    def test_no_portal_caller_uses_real_file_rec(self) -> None:
        with patch("scripts.ops_data_portal.file_rec", return_value="rec-live") as fr:
            stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_OLD": "SHA_OLD"})
            result = detect_ducklake_code_drift(
                git_runner=stub,
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
                open_recs=[],
            )
        fr.assert_called_once()
        assert result == {"action": "file", "rec_id": "rec-live"}

    def test_no_portal_caller_uses_real_update_rec_for_update(self) -> None:
        # Stale record + already-open rec -> the update action's real (portal_caller=None)
        # update_rec branch. Mirrors escalate()'s test_escalate_update_uses_real_portal_when_no_caller.
        existing = {"id": "rec-210", "source": "ducklake_code_drift", "status": "open"}
        with patch("scripts.ops_data_portal.update_rec") as ur:
            stub = GitRunnerStub(head_latest="SHA_NEW", reachable={"SHA_OLD": "SHA_OLD"})
            result = detect_ducklake_code_drift(
                git_runner=stub,
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
                open_recs=[existing],
            )
        ur.assert_called_once()
        assert result == {"action": "update", "rec_id": "rec-210"}

    def test_no_portal_caller_uses_real_update_rec_for_close(self) -> None:
        existing = {"id": "rec-200", "source": "ducklake_code_drift", "status": "open"}
        with patch("scripts.ops_data_portal.update_rec") as ur:
            result = detect_ducklake_code_drift(
                git_runner=lambda argv: "SHA_OLD",
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
                open_recs=[existing],
            )
        ur.assert_called_once()
        assert result == {"action": "close", "rec_id": "rec-200"}

    def test_default_git_runner_invokes_subprocess(self) -> None:
        completed = MagicMock(returncode=0, stdout="SHA_FROM_SUBPROCESS\n")
        with patch("scripts.convergence_health.code_drift.subprocess.run", return_value=completed) as run:
            result = detect_ducklake_code_drift(
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_FROM_SUBPROCESS"),
                portal_caller=lambda a, f: "rec-x",
                open_recs=[],
            )
        assert run.called
        assert run.call_count >= 2  # shallow-history guard + HEAD latest lookup, at minimum
        assert result == {"action": "none", "rec_id": None}


class TestUnknownActionFallsThroughToSkipped:
    """decide()'s truth table has exactly four outcomes (file/update/close/none); this exercises
    run_episode's (and so each detector's) defensive fallback for anything else it might ever
    return."""

    def test_ducklake_drift_unknown_action_falls_through_to_skipped(self) -> None:
        with patch("scripts.rec_episode.decide", return_value="bogus"):
            result = detect_ducklake_code_drift(
                git_runner=lambda argv: "SHA_NEW",
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
                portal_caller=lambda a, f: "rec-x",
                open_recs=[],
            )
        assert result == {"action": "skipped", "rec_id": None}

    def test_prod_drift_unknown_action_falls_through_to_skipped(self) -> None:
        with patch("scripts.rec_episode.decide", return_value="bogus"):
            result = detect_prod_code_drift(
                git_runner=lambda argv: "SHA_NEW",
                s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
                portal_caller=lambda a, f: "rec-x",
                open_recs=[],
            )
        assert result == {"action": "skipped", "rec_id": None}


class TestAcceptanceLint:
    """VP step 2: both drift builders emit a lint-valid acceptance, no mocking of the validator,
    AND (Decision 103) the acceptance string encodes the reachability oracle -- a bare equality
    grep against latest_sha would still pass lint_acceptance_command unchanged, so that alone
    cannot prove the acceptance is semantically correct under the new oracle."""

    def test_ducklake_drift_acceptance_lint_valid(self) -> None:
        from scripts.convergence_health.code_drift import _build_ducklake_drift_rec_fields
        from scripts.executor.acceptance_lint import lint_acceptance_command

        fields = _build_ducklake_drift_rec_fields([_DUCKLAKE_WRITER_FUNCTION], "abc123def456")
        assert lint_acceptance_command(fields["acceptance"]) == (True, None)

    def test_prod_drift_acceptance_lint_valid(self) -> None:
        from scripts.convergence_health.code_drift import _build_prod_drift_rec_fields
        from scripts.executor.acceptance_lint import lint_acceptance_command

        fields = _build_prod_drift_rec_fields(["agent-platform-scheduled-agent-dispatcher"], "abc123def456")
        assert lint_acceptance_command(fields["acceptance"]) == (True, None)

    def test_ducklake_drift_acceptance_encodes_reachability_not_bare_grep(self) -> None:
        from scripts.convergence_health.code_drift import _build_ducklake_drift_rec_fields

        latest_sha = "d024e63fd024e63fd024e63fd024e63fd024e63f"
        fields = _build_ducklake_drift_rec_fields([_DUCKLAKE_WRITER_FUNCTION], latest_sha)
        acceptance = fields["acceptance"]
        # The pre-fix shape this must NOT be: a bare grep for latest_sha inside the raw record.
        bare_grep = (
            f"aws s3 cp s3://agent-platform-data-lake/deploy-records/ducklake/{_DUCKLAKE_WRITER_FUNCTION}.json - "
            f'--profile agent_platform | grep -q "{latest_sha}"'
        )
        assert acceptance != bare_grep
        assert "merge-base" in acceptance
        assert "--is-ancestor" in acceptance
        assert latest_sha in acceptance

    def test_prod_drift_acceptance_encodes_reachability_not_bare_grep(self) -> None:
        from scripts.convergence_health.code_drift import _build_prod_drift_rec_fields

        latest_sha = "d024e63fd024e63fd024e63fd024e63fd024e63f"
        fn = "agent-platform-scheduled-agent-dispatcher"
        fields = _build_prod_drift_rec_fields([fn], latest_sha)
        acceptance = fields["acceptance"]
        bare_grep = (
            f"aws s3 cp s3://agent-platform-data-lake/deploy-records/prod/{fn}.json - "
            f'--profile agent_platform | grep -q "{latest_sha}"'
        )
        assert acceptance != bare_grep
        assert "merge-base" in acceptance
        assert "--is-ancestor" in acceptance
        assert latest_sha in acceptance

    def test_acceptance_joins_multiple_stale_functions_with_and(self) -> None:
        from scripts.convergence_health.code_drift import _build_prod_drift_rec_fields

        fields = _build_prod_drift_rec_fields(
            ["agent-platform-findings-processor", "agent-platform-scheduled-agent-dispatcher"],
            "abc123def456",  # pragma: allowlist secret -- fake sha fixture, not a real credential
        )
        assert fields["acceptance"].count(" && git merge-base --is-ancestor ") == 2


def test_pathspecs_include_row_rules() -> None:
    stub = GitRunnerStub(head_latest="SHA_OLD", reachable={"SHA_OLD": "SHA_OLD"})
    detect_ducklake_code_drift(
        git_runner=stub,
        s3_client=_FakeDeployRecordsS3(default_sha="SHA_OLD"),
        portal_caller=lambda a, f: "rec-x",
        open_recs=[],
    )
    argv = next(c for c in stub.calls if c[:4] == ["git", "log", "-1", "--format=%H"] and c[4] == "--")
    assert "src/row_rules" in argv


def test_ducklake_pathspecs_include_src_telemetry() -> None:
    """The telemetry kernel is bundled into a DuckLake function, so a kernel change advances the drift reference."""
    from scripts.convergence_health.code_drift import DUCKLAKE_SOURCE_PATHSPECS

    assert "src/telemetry" in DUCKLAKE_SOURCE_PATHSPECS
