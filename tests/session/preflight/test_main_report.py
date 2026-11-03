"""main()-integration and report-shape tests: preflight-summary formatting, stdout must-not-dump-
full-json, JSON output schema, main() priority-queue integration, CI-RCA hard-block banner,
preflight-report dispute-key presence (rec-2709 Wave 4).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

boto3 = pytest.importorskip("boto3")

from tests.fixtures.platform_roadmap_state import live_state_dict  # noqa: E402
from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402


class TestFormatPreflightSummary:
    def test_summary_is_a_single_block_referencing_the_report_path(self) -> None:
        report = {
            "venv_ok": True,
            "creds_status": "ok",
            "branch": "agent/foo",
            "main_freshness": {"commits_behind": 3, "commits_ahead": 1},
            "open_recommendations": 12,
            "non_automatable_recommendations": 4,
            "ci_rca_recs": [{"id": "rec-1"}, {"id": "rec-2"}],
            "ci_rca_unresolved_recs": [{"id": "rec-1"}],
            "ci_rca_likely_resolved_recs": [{"id": "rec-2"}],
        }
        summary = _preflight._format_preflight_summary(report, Path("/tmp/foo.json"))
        assert "/tmp/foo.json" in summary
        assert "agent/foo" in summary
        assert "3 behind" in summary
        assert "1 ahead" in summary
        assert "open_recs=12" in summary
        assert "ci_rca_unresolved=1" in summary
        assert "ci_rca_likely_resolved=1" in summary
        assert "Read the report file" in summary

    def test_summary_handles_missing_main_freshness(self) -> None:
        report = {
            "venv_ok": False,
            "creds_status": "unavailable",
            "branch": "main",
            "open_recommendations": 0,
            "non_automatable_recommendations": 0,
            "ci_rca_recs": [],
        }
        summary = _preflight._format_preflight_summary(report, Path("/tmp/foo.json"))
        assert "? behind" in summary
        assert "? ahead" in summary


class TestStdoutDoesNotDumpFullJson:
    def test_main_does_not_print_full_json_to_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        preflight_report = tmp_path / ".preflight-report.json"
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 1.5",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
        ):
            _preflight.main()
        captured = capsys.readouterr()
        assert "Preflight OK ->" in captured.out
        assert "Read the report file" in captured.out
        assert '"venv_ok": true' not in captured.out, (
            "Stdout should NOT contain the full report JSON -- that duplicates "
            "the file write and costs every consuming agent ~12-15k tokens."
        )


class TestJsonOutputSchema:
    def test_all_required_keys_present(self, tmp_path: Path) -> None:
        preflight_report = tmp_path / ".preflight-report.json"

        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
            patch(
                "scripts.preflight.env_git.check_main_freshness",
                return_value={
                    "status": "ok",
                    "fetched_at": "2026-05-24T00:00:00+00:00",
                    "commits_behind": 0,
                    "commits_ahead": 0,
                    "main_files_changed_since_branch": [],
                },
            ),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={
                    "status": "ok",
                    "window_days": 2,
                    "recent_sessions": ["2026-10-28T09:00Z"],
                    "last_session": "2026-10-28T09:00Z",
                },
            ),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 1.5",
                    "open_decisions_count": 2,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        assert preflight_report.exists()
        data = json.loads(preflight_report.read_text(encoding="utf-8"))

        required_keys = [
            "venv_ok",
            "branch",
            "uncommitted_changes",
            "stash_entries",
            "main_freshness",
            "creds_status",
            "terraform_pending",
            "last_session",
            "session_history",
            "open_recommendations",
            "aging_recommendations",
            "non_automatable_recommendations",
            "priority_queue",
            "priority_queue_source",
            "friction_patterns",
            "context",
            "session_start",
            "recommendation_sync",
        ]
        assert "non_automatable_details" not in data, (
            "non_automatable_details was dropped from the slim report -- "
            "Decision 73 suspends individual rec review, so the detail list is dead weight"
        )
        for key in required_keys:
            assert key in data, f"Missing key: {key}"

    def test_recommendation_sync_field_in_output(self, tmp_path: Path) -> None:
        """recommendation_sync field appears in output and is derived from sync_ops.warm_sync()['pulled']."""
        preflight_report = tmp_path / ".preflight-report.json"

        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch(
                "scripts.preflight.env_git.check_main_freshness",
                return_value={
                    "status": "ok",
                    "fetched_at": "2026-05-24T00:00:00+00:00",
                    "commits_behind": 0,
                    "commits_ahead": 0,
                    "main_files_changed_since_branch": [],
                },
            ),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch(
                "scripts.sync.ops.warm_sync",
                return_value={
                    "drained": {},
                    "pulled": {"ops_recommendations": 5},
                    "rows": {"ops_recommendations": [], "ops_decisions": [], "ops_priority_queue": []},
                    "reader_ok": {"ops_recommendations": True, "ops_decisions": True, "ops_priority_queue": True},
                },
            ) as mock_warm_sync,
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 1.5",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        mock_warm_sync.assert_called_once()
        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert data["recommendation_sync"] == {"ops_recommendations": 5}


class TestMainIncludesPriorityQueue:
    """Verify main() integrates priority_queue into report and output."""

    def test_report_contains_priority_queue_key(
        self,
        tmp_path: Path,
    ) -> None:
        """priority_queue key appears in the preflight JSON report."""
        preflight_report = tmp_path / ".preflight-report.json"
        mock_queue = [
            {
                "rank": 1,
                "rec_id": "rec-500",
                "rationale": "Test",
                "north_star_impact": "high",
            },
        ]
        with (
            patch(
                "scripts.preflight.env_git.check_venv",
                return_value=True,
            ),
            patch(
                "scripts.preflight.env_git.get_git_status",
                return_value=("main", False, []),
            ),
            patch(
                "scripts.preflight.aws_infra.check_terraform_pending",
                return_value=False,
            ),
            patch(
                "scripts.preflight.aws_infra.check_credentials",
                return_value="ok",
            ),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch(
                "scripts.preflight.priority_queue.read_priority_queue",
                return_value=mock_queue,
            ),
            patch(
                "session_preflight._sync_ops_pull",
                return_value={},
            ),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch(
                "session_preflight.PREFLIGHT_REPORT",
                preflight_report,
            ),
            patch("builtins.print"),
        ):
            _preflight.main()

        data = json.loads(
            preflight_report.read_text(encoding="utf-8"),
        )
        assert "priority_queue" in data
        assert len(data["priority_queue"]) == 1
        assert data["priority_queue"][0]["rec_id"] == "rec-500"
        assert data.get("priority_queue_source") == "ducklake_reader"

    def test_terminal_output_includes_queue_section(
        self,
        tmp_path: Path,
    ) -> None:
        """Terminal output includes the priority queue section header."""
        preflight_report = tmp_path / ".preflight-report.json"
        printed: list[str] = []

        def capture_print(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        with (
            patch(
                "scripts.preflight.env_git.check_venv",
                return_value=True,
            ),
            patch(
                "scripts.preflight.env_git.get_git_status",
                return_value=("main", False, []),
            ),
            patch(
                "scripts.preflight.aws_infra.check_terraform_pending",
                return_value=False,
            ),
            patch(
                "scripts.preflight.aws_infra.check_credentials",
                return_value="ok",
            ),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch(
                "scripts.preflight.priority_queue.read_priority_queue",
                return_value=[],
            ),
            patch(
                "session_preflight._sync_ops_pull",
                return_value={},
            ),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch(
                "session_preflight.PREFLIGHT_REPORT",
                preflight_report,
            ),
            patch("builtins.print", side_effect=capture_print),
        ):
            _preflight.main()

        output = "\n".join(printed)
        assert "--- Priority Queue (top 5) ---" in output


class TestCiRcaHardBlock:
    """Tests for the HARD BLOCK banner in print_ci_rca_recs()."""

    def test_hard_block_present_when_recs_non_empty(self) -> None:
        printed: list[str] = []

        def capture(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=capture):
            _preflight.print_ci_rca_recs(
                [{"id": "rec-999", "title": "CI broken", "priority": "critical", "created_timestamp": "2026-05-13"}]
            )
        output = "\n".join(printed)
        assert "HARD BLOCK" in output

    def test_hard_block_absent_when_recs_empty(self) -> None:
        printed: list[str] = []

        def capture(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=capture):
            _preflight.print_ci_rca_recs([])
        output = "\n".join(printed)
        assert "HARD BLOCK" not in output


class TestPreflightReportDisputeKey:
    """The preflight report JSON must include ci_rca_dispute_recs."""

    def test_report_json_contains_ci_rca_dispute_recs(self, tmp_path: Path) -> None:
        """session_preflight.py writes ci_rca_dispute_recs to the report JSON.

        The autouse fixture stubs _make_reader, warm_sync, check_main_freshness, and
        _sync_ops_pull; we only need to patch the infrastructure that main() calls directly.
        """
        preflight_report = tmp_path / ".preflight-report.json"
        dispute_recs = [
            {"id": "rec-901", "title": "Dispute rec", "priority": "low", "created_timestamp": "2026-06-29T10:00:00Z"}
        ]

        with (
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("scripts.preflight.ci_rca_signals._fetch_ci_rca_dispute_recs", return_value=dispute_recs),
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("claude/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.context_docs.read_context_files", return_value={}),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("builtins.print"),
        ):
            _preflight.main()

        assert preflight_report.exists()
        report = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert "ci_rca_dispute_recs" in report, f"ci_rca_dispute_recs missing from report keys: {list(report)[:30]}"
        assert report["ci_rca_dispute_recs"] == dispute_recs


class TestDecisionConditionsGlue:
    """Net-new glue lines wiring scripts.preflight.decision_conditions into main() (SEQ-02 /
    Decision 133 follow-on). This mirror-package file is the 100%-coverage home for these lines
    per the Decision-131 mirror rule -- see PLAN-reversal-condition-monitor.yaml.
    """

    def test_report_contains_decision_conditions_bucket(self, tmp_path: Path) -> None:
        """report["decision_conditions"] is present and carries the preflight_bucket() shape."""
        preflight_report = tmp_path / ".preflight-report.json"
        canned_bucket = {
            "monitored": [133],
            "surfaced": [
                {"decision": 901, "state": "manual-review-due", "review_by": "2020-01-01", "fired_condition_ids": []}
            ],
            "malformed": [],
        }
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("scripts.preflight.decision_conditions.preflight_bucket", return_value=canned_bucket),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        assert preflight_report.exists()
        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert "decision_conditions" in data, f"decision_conditions missing from report keys: {list(data)[:30]}"
        assert data["decision_conditions"] == canned_bucket
        assert set(data["decision_conditions"]) == {"monitored", "surfaced", "malformed"}

    def test_stdout_renders_decision_conditions_section(self) -> None:
        """The stdout section (mirroring '--- Provisional contracts due ---') renders during
        main(), naming the surfaced decision."""
        preflight_report = Path("/tmp/_test_decision_conditions_stdout_report.json")
        canned_bucket = {
            "monitored": [901],
            "surfaced": [
                {"decision": 901, "state": "manual-review-due", "review_by": "2020-01-01", "fired_condition_ids": []}
            ],
            "malformed": [],
        }
        printed: list[str] = []

        def capture_print(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        try:
            with (
                patch("scripts.preflight.env_git.check_venv", return_value=True),
                patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
                patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
                patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
                patch(
                    "scripts.preflight.session_history.read_session_history",
                    return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
                ),
                patch("scripts.preflight.priority_queue.read_priority_queue", return_value=[]),
                patch("session_preflight._sync_ops_pull", return_value={}),
                patch(
                    "scripts.preflight.context_docs.read_context_files",
                    return_value={
                        "roadmap_phase": "Phase 2",
                        "open_decisions_count": 0,
                        "strategic_review_due": False,
                        "recommendations_count": 0,
                    },
                ),
                patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
                patch("scripts.preflight.decision_conditions.preflight_bucket", return_value=canned_bucket),
                patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
                patch("builtins.print", side_effect=capture_print),
            ):
                _preflight.main()
        finally:
            preflight_report.unlink(missing_ok=True)

        output = "\n".join(printed)
        assert "--- Decisions past review date / reversal conditions fired ---" in output
        assert "Decision 901: REVIEW DUE" in output


class TestLiveRoadmapStateWiring:
    """rec-4006 seam B: the package autouse fixture installs live_state_dict on the facade
    attribute main() reads (scripts.roadmap.platform_roadmap.compute_state_dict via
    session_preflight.platform_roadmap)."""

    def test_facade_attribute_is_live_state_dict(self) -> None:
        assert _preflight.platform_roadmap.compute_state_dict is live_state_dict
