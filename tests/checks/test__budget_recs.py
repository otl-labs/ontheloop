"""Tests for scripts/checks/_budget_recs.py -- budget-breach/bypass rec filing (Decision 128).

TestBudgetBreachRecFiling and TestBudgetRecFilingCiGuard are relocated VERBATIM (method bodies
and names unchanged) from tests/validate/test_budget_rec_filing.py, which is deleted as part of
this move -- _file_budget_breach_rec / _file_budget_bypass_rec now live in
scripts/checks/_budget_recs.py, not scripts/checks/_scaffolding.py (Decision 128
decompose-don't-raise). TestFindOpenBudgetBreachRec and TestBudgetBreachRecDedupe are new
(VTS-20, audit validate-test-suite-4df4d48).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.checks._budget_recs import (
    _file_budget_breach_rec,
    _file_budget_bypass_rec,
    _find_open_budget_breach_rec,
    build_budget_record,
)


def _budget_record(**overrides: object) -> dict:
    """build_budget_record with its invariant keyword boilerplate defaulted."""
    kwargs: dict = {"outcome": "breach", "elapsed_s": 400.0, "limit_s": 300.0, "dominant_phase": None, "diff_manifest": []}
    kwargs.update(overrides)
    return build_budget_record(**kwargs)


class TestBudgetBreachRecFiling:
    """Tests for _file_budget_breach_rec and _file_budget_bypass_rec helpers.

    These exercise the LOCAL (non-CI) path -- CI-guard behaviour is covered separately by
    TestBudgetRecFilingCiGuard below. Every test here runs with CI unset regardless of the
    ambient environment (this file itself runs under CI="true" in the pr-validate/main-validate
    CI jobs), so the local-path assertions stay deterministic.
    """

    @pytest.fixture(autouse=True)
    def _no_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)

    def test_breach_rec_calls_file_rec_with_budget_breach_source(self) -> None:
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], None)

        mock_portal.file_rec.assert_called_once()
        fields = mock_portal.file_rec.call_args[0][0]
        assert fields["source"] == "budget_breach"

    def test_breach_rec_context_contains_elapsed_and_manifest(self) -> None:
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py", "tests/test_validate.py"], None)

        fields = mock_portal.file_rec.call_args[0][0]
        assert "scripts/validate.py" in fields["context"]
        assert "6.7 min" in fields["context"] or "6." in fields["context"]

    def test_breach_portal_exception_is_suppressed(self) -> None:
        mock_portal = MagicMock()
        mock_portal.file_rec.side_effect = RuntimeError("DynamoDB unreachable")
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            # Must not raise
            _file_budget_breach_rec(400.0, [], None)

    def test_bypass_rec_calls_file_rec_with_budget_bypass_source(self) -> None:
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue")

        mock_portal.file_rec.assert_called_once()
        fields = mock_portal.file_rec.call_args[0][0]
        assert fields["source"] == "budget_bypass"
        assert "disk issue" in fields["context"]

    def test_bypass_rec_reason_null_when_omitted(self) -> None:
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, [], None)

        fields = mock_portal.file_rec.call_args[0][0]
        assert "none provided" in fields["context"].lower()

    def test_bypass_rec_context_contains_dominant_phase(self) -> None:
        """I4: bypass recs must record the dominant phase like breach recs do (rec corpus gap --
        all 33 pre-fix budget_bypass recs omitted it)."""
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue", "pytest_diff")

        fields = mock_portal.file_rec.call_args[0][0]
        assert "pytest_diff" in fields["context"]

    def test_bypass_rec_dominant_phase_unknown_when_omitted(self) -> None:
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, [], None)

        fields = mock_portal.file_rec.call_args[0][0]
        assert "dominant phase: unknown" in fields["context"].lower()

    def test_bypass_portal_exception_is_suppressed(self) -> None:
        mock_portal = MagicMock()
        mock_portal.file_rec.side_effect = RuntimeError("DynamoDB unreachable")
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            # Must not raise
            _file_budget_bypass_rec(60.0, [], None)

    def test_breach_priority_is_accepted_value(self) -> None:
        """_file_budget_breach_rec must pass a title-case priority (rec-2156)."""
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], None)

        fields = mock_portal.file_rec.call_args[0][0]
        assert fields["priority"] in {"Critical", "High", "Medium", "Low"}

    def test_bypass_priority_is_accepted_value(self) -> None:
        """_file_budget_bypass_rec must pass a title-case priority (rec-2156)."""
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue")

        fields = mock_portal.file_rec.call_args[0][0]
        assert fields["priority"] in {"Critical", "High", "Medium", "Low"}

    def test_breach_priority_survives_real_accepted_values_validator(self) -> None:
        """Anti-vacuous: the priority _file_budget_breach_rec passes must survive the REAL
        row-rule gate, not just a hardcoded set in this test."""
        from scripts.ops_data_portal import _derive_computed_fields, gate_write  # noqa: PLC0415

        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], None)

        fields = dict(mock_portal.file_rec.call_args[0][0])
        _derive_computed_fields(fields)
        gate_write("ops_recommendations", fields, insert=True)  # must not raise

    def test_bypass_priority_survives_real_accepted_values_validator(self) -> None:
        """Anti-vacuous: the priority _file_budget_bypass_rec passes must survive the REAL
        row-rule gate, not just a hardcoded set in this test."""
        from scripts.ops_data_portal import _derive_computed_fields, gate_write  # noqa: PLC0415

        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue")

        fields = dict(mock_portal.file_rec.call_args[0][0])
        _derive_computed_fields(fields)
        gate_write("ops_recommendations", fields, insert=True)  # must not raise


class TestBudgetRecFilingCiGuard:
    """CI-guard on the budget rec-filing helpers (Decision 84 I-4 / ULID anomaly root cause).

    The pr-validate CI job configures no AWS credentials, so a real portal file_rec() write there
    raises a swallowed ModuleNotFoundError from ducklake_runtime's mint_write_identity. With
    CI=="true" neither helper may even attempt the portal import -- it must print a loud
    diagnostic instead (never a silent skip, never a buffered outbox entry).
    """

    @pytest.fixture(autouse=True)
    def _no_step_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Under real GitHub Actions GITHUB_STEP_SUMMARY points at the live job summary file.
        Unset it so these tests never append to it; the mirror-write has its own test below,
        which sets the variable to a tmp_path file explicitly."""
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def test_breach_rec_skips_file_rec_under_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        mock_portal = MagicMock()

        with (
            patch("scripts.checks._common.run") as mock_run,
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")

        mock_portal.file_rec.assert_not_called()
        mock_run.assert_not_called()

    def test_breach_rec_prints_diagnostic_under_ci(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("CI", "true")

        _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")

        captured = capsys.readouterr()
        assert "pytest_diff" in captured.err
        assert "400.0" not in captured.err  # sanity: elapsed is rendered as minutes, not raw seconds
        assert "6.7" in captured.err or "6." in captured.err

    def test_breach_rec_mirrors_diagnostic_to_github_step_summary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CI-native diagnosability (no portal, no outbox): the same loud message is APPENDED to
        the job's step summary, so a CI reader sees the breach without digging through stderr."""
        monkeypatch.setenv("CI", "true")
        summary = tmp_path / "step_summary.md"
        summary.write_text("## Earlier step\n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")

        written = summary.read_text(encoding="utf-8")
        assert written.startswith("## Earlier step\n"), "the summary must be appended to, never truncated"
        assert "## Fast-tier budget breach" in written
        assert "pytest_diff" in written
        assert "scripts/validate.py" in written
        assert "Rec NOT filed (CI)." in written

    def test_breach_rec_writes_no_summary_when_the_variable_is_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """With GITHUB_STEP_SUMMARY unset the stderr print is the only diagnostic; nothing is
        written anywhere on disk."""
        monkeypatch.setenv("CI", "true")
        before = sorted(p.name for p in tmp_path.iterdir())

        _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")

        assert sorted(p.name for p in tmp_path.iterdir()) == before
        assert "pytest_diff" in capsys.readouterr().err

    def test_breach_rec_calls_file_rec_when_ci_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")

        mock_portal.file_rec.assert_called_once()

    def test_bypass_rec_skips_file_rec_under_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        mock_portal = MagicMock()

        with (
            patch("scripts.checks._common.run") as mock_run,
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue")

        mock_portal.file_rec.assert_not_called()
        mock_run.assert_not_called()

    def test_bypass_rec_prints_diagnostic_under_ci(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("CI", "true")

        _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue")

        captured = capsys.readouterr()
        assert "disk issue" in captured.err

    def test_bypass_rec_prints_dominant_phase_under_ci(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("CI", "true")

        _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue", "pytest_diff")

        captured = capsys.readouterr()
        assert "pytest_diff" in captured.err

    def test_bypass_rec_calls_file_rec_when_ci_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        mock_portal = MagicMock()
        git_result = MagicMock(returncode=0, stdout="agent/test\n")

        with (
            patch("scripts.checks._common.run", return_value=git_result),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
        ):
            _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue")

        mock_portal.file_rec.assert_called_once()

    def test_bypass_rec_mirrors_diagnostic_to_github_step_summary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """G2 parity: the bypass CI branch mirrors to the step summary exactly as the breach branch
        does. It was stderr-only -- an asymmetry that is a live drift risk however unreachable."""
        monkeypatch.setenv("CI", "true")
        summary = tmp_path / "step_summary.md"
        summary.write_text("## Earlier step\n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        _file_budget_bypass_rec(60.0, ["scripts/validate.py"], "disk issue", "pytest_diff")

        written = summary.read_text(encoding="utf-8")
        assert written.startswith("## Earlier step\n"), "the summary must be appended to, never truncated"
        assert "## Fast-tier budget bypass" in written
        assert "pytest_diff" in written
        assert "disk issue" in written


class TestBuildBudgetRecord:
    """build_budget_record: the pure, I/O-free budget record scripts/validate.py attaches to the
    selection manifest, so the artifact ci.yml already uploads carries a machine-readable outcome.
    Identity is GITHUB_* env vars ONLY -- a git subprocess would break the CI path's
    mock_run.assert_not_called() contract above."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("CI", "GITHUB_HEAD_REF", "GITHUB_RUN_ID", "GITHUB_REPOSITORY"):
            monkeypatch.delenv(name, raising=False)

    def test_identity_reads_github_env_and_never_shells_out_to_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("GITHUB_HEAD_REF", "claude/some-branch")
        monkeypatch.setenv("GITHUB_RUN_ID", "12345")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

        with patch("scripts.checks._common.run") as mock_run:
            record = _budget_record(dominant_phase="pytest_diff", diff_manifest=["scripts/validate.py"])

        mock_run.assert_not_called()
        assert record["branch"] == "claude/some-branch"
        assert record["run_id"] == "12345"
        assert record["repository"] == "owner/repo"
        assert record["ci"] is True
        assert record["dominant_phase"] == "pytest_diff"

    def test_absent_env_degrades_to_unknown_without_raising(self) -> None:
        record = _budget_record(outcome="within_budget", elapsed_s=12.0)
        assert record["branch"] == record["run_id"] == record["repository"] == "unknown"
        assert record["ci"] is False
        assert record["dominant_phase"] is None
        assert record["limit_s"] == 300.0

    def test_diff_manifest_truncated_at_twenty_with_the_full_count_kept(self) -> None:
        paths = [f"src/mod_{i:02d}.py" for i in range(25)]
        record = _budget_record(diff_manifest=paths)
        assert record["diff_file_count"] == 25
        assert record["diff_manifest"] == paths[:20]

    def test_phase_times_keep_the_ten_slowest(self) -> None:
        record = _budget_record(phase_times={f"phase_{i:02d}": float(i) for i in range(12)})
        assert len(record["phase_times"]) == 10
        assert "phase_11" in record["phase_times"]
        assert "phase_00" not in record["phase_times"]

    def test_rec_filing_fields_reflect_outcome_and_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        local_breach = _budget_record()
        assert (local_breach["rec_filed"], local_breach["rec_skipped_reason"]) == (True, None)
        for outcome in ("within_budget", "forced_waived", "forced_ceiling_breach"):
            record = _budget_record(outcome=outcome)
            assert (record["rec_filed"], record["rec_skipped_reason"]) == (False, "no_rec_for_outcome")
        monkeypatch.setenv("CI", "true")
        ci_bypass = _budget_record(outcome="bypass")
        assert (ci_bypass["rec_filed"], ci_bypass["rec_skipped_reason"]) == (False, "ci_no_portal_access")


_LIVE_TITLE = "Fast-tier budget breach (8.0 min) on agent/foo"
_LIVE_CONTEXT = "Fast-tier budget breach: 8.0 min elapsed (limit 5 min). Branch: agent/foo. Dominant phase: lint. "


def _live_row(**overrides: object) -> dict:
    """An open budget_breach rec exactly as scripts.rec_episode.find_rec's scoped read returns it
    (rec-3291 / rec-3563): current_state("ops_recommendations", row_filter="source = "
    "'budget_breach'") is an unnarrowed structural read, so a live row always carries `status` and
    `source` -- unlike the retired `open_recs` named verb, which projected neither. An override
    adds or replaces a key."""
    row: dict = {
        "id": "rec-1",
        "title": _LIVE_TITLE,
        "context": _LIVE_CONTEXT,
        "created_timestamp": "2026-08-26T10:00:00+00:00",
        "automatable": False,
        "status": "open",
        "source": "budget_breach",
    }
    row.update(overrides)
    return row


class TestIsOpenBudgetBreachRow:
    """_is_open_budget_breach_row is a straight status/source check now that a live row (via
    scripts.rec_episode.find_rec's scoped read) always carries both (rec-3291 / rec-3563)."""

    def test_open_budget_breach_row_is_true(self) -> None:
        from scripts.checks._budget_recs import _is_open_budget_breach_row

        assert _is_open_budget_breach_row(_live_row()) is True

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"status": "closed"}, id="closed"),
            pytest.param({"source": "budget_bypass"}, id="foreign-source"),
        ],
    )
    def test_non_matching_row_is_false(self, overrides: dict) -> None:
        from scripts.checks._budget_recs import _is_open_budget_breach_row

        assert _is_open_budget_breach_row(_live_row(**overrides)) is False


class TestFindOpenBudgetBreachRec:
    """Pure matching-logic tests for _find_open_budget_breach_rec (VTS-20 / rec-3291 / rec-3563).

    Rows are LIVE-shaped by default (full projection, via scripts.rec_episode.find_rec's rows=
    test-injection seam). _find_open_budget_breach_rec used to require `source == "budget_breach"`
    and `status == "open"` against a bulk `open_recs` fetch that projected neither column, so it
    returned None for every real open rec -- the local path re-filed instead of updating."""

    def test_matches_a_live_row(self) -> None:
        row = _live_row()
        assert _find_open_budget_breach_rec([row], "agent/foo", "lint") is row

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"context": "Branch: agent/bar. Dominant phase: lint. "}, id="other-branch"),
            pytest.param({"context": "Branch: agent/foo. Dominant phase: pytest_diff. "}, id="other-phase"),
            pytest.param({"context": None}, id="no-context"),
            pytest.param({"status": "closed"}, id="explicit-closed"),
            pytest.param({"status": "in_progress"}, id="explicit-in-progress"),
            pytest.param({"source": "budget_bypass"}, id="explicit-foreign-source"),
        ],
    )
    def test_rejects_a_row_that_is_not_this_episodes_open_breach_rec(self, overrides: dict) -> None:
        assert _find_open_budget_breach_rec([_live_row(**overrides)], "agent/foo", "lint") is None

    def test_empty_rows_returns_none(self) -> None:
        assert _find_open_budget_breach_rec([], "agent/foo", "lint") is None

    def test_raises_when_a_row_is_missing_status_or_source(self) -> None:
        """A row a scoped read hands back without `status`/`source` must raise, never be silently
        treated as no-match (the exact defect this migration removes) -- scripts.rec_episode.
        find_recs's runtime assertion, exercised through this matcher."""
        narrowed_row = {"id": "rec-1", "context": _LIVE_CONTEXT}
        with pytest.raises(RuntimeError, match="missing filtered key"):
            _find_open_budget_breach_rec([narrowed_row], "agent/foo", "lint")


class TestBudgetBreachRecDedupe:
    """VTS-20 (audit validate-test-suite-4df4d48): a repeated fast-tier budget breach on the
    same (branch, dominant_phase) updates the existing open budget_breach rec instead of filing
    a duplicate. The dedupe lookup reads the DuckLake reader boundary via
    scripts.rec_episode.find_rec's source-scoped structural read (src.common.
    ducklake_reader_client.make_reader), never logs/.recommendations-log.jsonl."""

    @pytest.fixture(autouse=True)
    def _no_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def _breach(self, open_recs: list | Exception, *, branch: str = "agent/test") -> MagicMock:
        """One local breach filing with the reader stubbed to *open_recs* (rows, or an exception
        to raise), returning the mocked scripts.ops_data_portal module."""
        mock_portal = MagicMock()
        stub = {"side_effect": open_recs} if isinstance(open_recs, Exception) else {"return_value": open_recs}
        with (
            patch("scripts.checks._common.run", return_value=MagicMock(returncode=0, stdout=f"{branch}\n")),
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
            patch("scripts.checks._budget_recs.find_recs", **stub),
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")
        return mock_portal

    def test_repeat_breach_same_branch_and_phase_updates_existing_rec(self) -> None:
        existing_rec = _live_row(
            id="rec-9001",
            title="Fast-tier budget breach (8.0 min) on agent/test",
            context=(
                "Fast-tier budget breach: 8.0 min elapsed (limit 5 min). Branch: agent/test. "
                "Dominant phase: pytest_diff. Diff manifest (1 files): scripts/validate.py. "
            ),
        )
        mock_portal = self._breach([existing_rec])
        mock_portal.file_rec.assert_not_called()
        mock_portal.update_rec.assert_called_once()
        call_args = mock_portal.update_rec.call_args[0]
        assert call_args[0] == "rec-9001"
        assert "pytest_diff" in call_args[1]["context"]

    def test_the_row_this_writer_files_is_matched_on_the_next_breach(self) -> None:
        """Anti-drift pin on the LOCAL writer's half of the marker contract: whatever title and
        context _file_budget_breach_rec writes, projected down to a live row's shape, must be
        found by the matcher. The ingester writer's half is pinned in
        tests/convergence_health/budget_ingest/test_ingest.py."""
        filed = self._breach([]).file_rec.call_args[0][0]
        row = _live_row(title=filed["title"], context=filed["context"])
        assert _find_open_budget_breach_rec([row], "agent/test", "pytest_diff") is row

    def test_new_branch_or_phase_files_new_rec(self) -> None:
        mock_portal = self._breach([], branch="agent/other")
        mock_portal.file_rec.assert_called_once()
        mock_portal.update_rec.assert_not_called()

    def test_non_matching_existing_rec_files_new_rec_not_update(self) -> None:
        """A DIFFERENT branch/phase's open budget_breach rec must not be mistaken for a match."""
        other_rec = _live_row(
            id="rec-1234",
            title="Fast-tier budget breach (6.0 min) on some/other-branch",
            context="Branch: some/other-branch. Dominant phase: lint. ",
        )
        mock_portal = self._breach([other_rec])
        mock_portal.file_rec.assert_called_once()
        mock_portal.update_rec.assert_not_called()

    def test_reader_unreachable_degrades_to_file_rec(self) -> None:
        """A reader exception during the dedupe lookup must not crash -- it falls through to
        filing a new rec (the breach is still recorded, Decision 55)."""
        mock_portal = self._breach(RuntimeError("reader unreachable"))
        mock_portal.file_rec.assert_called_once()
        mock_portal.update_rec.assert_not_called()

    def test_ci_guard_touches_neither_reader_nor_portal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CI", "true")
        mock_portal = MagicMock()

        with (
            patch("scripts.checks._common.run") as mock_run,
            patch.dict(sys.modules, {"scripts.ops_data_portal": mock_portal}),
            patch("scripts.checks._budget_recs.find_recs") as mock_fetch,
        ):
            _file_budget_breach_rec(400.0, ["scripts/validate.py"], "pytest_diff")

        mock_portal.file_rec.assert_not_called()
        mock_portal.update_rec.assert_not_called()
        mock_run.assert_not_called()
        mock_fetch.assert_not_called()

    def test_dedupe_never_reads_local_recommendations_cache(self) -> None:
        """The dedupe lookup must read the reader boundary, never
        logs/.recommendations-log.jsonl (Decision 84 warehouse-SoT: a read cache is never a write
        source). Asserts neither of the two established local-cache-read mechanisms
        (RECOMMENDATIONS_FILE / read_jsonl) is imported into this module -- a structural
        dependency check, not a string-absence scan (the module's own docstring legitimately
        documents this invariant by naming the path in prose)."""
        from scripts.checks import _budget_recs

        assert not hasattr(_budget_recs, "RECOMMENDATIONS_FILE")
        assert not hasattr(_budget_recs, "read_jsonl")
