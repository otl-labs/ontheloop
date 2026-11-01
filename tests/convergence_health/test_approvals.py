"""Unit tests for scripts.convergence_health.approvals (rec-2709 Wave 6 package-mirror).

GitHub-Actions stuck-approval / Reconcile-episode signal helpers. Free of live network
dependencies: the GitHub API caller is injected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from scripts.convergence_health import (
    _make_github_caller,
    diagnose_stuck_approvals,
    filter_stuck_runs,
    find_reconcile_runs_since,
    find_stuck_gated_approvals,
    has_in_flight_reconcile_for_episode,
)


class TestFindReconcileRunsSince:
    def test_returns_workflow_runs_list(self) -> None:
        mock_data = {
            "workflow_runs": [
                {"id": 1, "created_at": "2026-06-27T06:00:00Z"},
                {"id": 2, "created_at": "2026-06-27T07:00:00Z"},
            ]
        }
        result = find_reconcile_runs_since(gh_caller=lambda url: mock_data)
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_url_targets_reconcile_workflow(self) -> None:
        captured: list[str] = []

        def _capture(url: str) -> Any:
            captured.append(url)
            return {"workflow_runs": []}

        find_reconcile_runs_since(gh_caller=_capture)
        assert captured
        assert "reconcile.yml/runs" in captured[0]
        # Deliberately no status= filter -- any status counts as "in-flight/recent" (T2.37 c4).
        assert "status=" not in captured[0]

    def test_returns_empty_when_caller_returns_none(self) -> None:
        assert find_reconcile_runs_since(gh_caller=lambda url: None) == []

    def test_swallows_caller_exception(self) -> None:
        def _boom(url: str) -> Any:
            raise RuntimeError("api down")

        assert find_reconcile_runs_since(gh_caller=_boom) == []

    def test_no_token_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert find_reconcile_runs_since() == []

    def test_missing_workflow_runs_key_returns_empty(self) -> None:
        assert find_reconcile_runs_since(gh_caller=lambda url: {}) == []


class TestHasInFlightReconcileForEpisode:
    def test_run_created_after_red_since_is_in_flight(self) -> None:
        red_since = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
        runs = [{"id": 1, "created_at": "2026-06-27T07:00:00Z"}]
        assert has_in_flight_reconcile_for_episode(runs, red_since=red_since, now=now) is True

    def test_run_created_before_red_since_is_not_in_flight(self) -> None:
        # A reconcile run from a PRIOR (already-resolved) episode must not suppress escalation
        # for a NEW red episode -- not matched by head_sha, matched purely by the time window.
        red_since = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
        runs = [{"id": 1, "created_at": "2026-06-26T23:00:00Z"}]
        assert has_in_flight_reconcile_for_episode(runs, red_since=red_since, now=now) is False

    def test_no_runs_is_not_in_flight(self) -> None:
        red_since = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
        assert has_in_flight_reconcile_for_episode([], red_since=red_since) is False

    def test_run_missing_created_at_is_skipped(self) -> None:
        red_since = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
        runs = [{"id": 1}]
        assert has_in_flight_reconcile_for_episode(runs, red_since=red_since, now=now) is False

    def test_run_exactly_at_red_since_counts_as_in_flight(self) -> None:
        red_since = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
        runs = [{"id": 1, "created_at": "2026-06-27T06:00:00Z"}]
        assert has_in_flight_reconcile_for_episode(runs, red_since=red_since, now=now) is True

    def test_defaults_now_to_current_time(self) -> None:
        # now=None path -- exercised without mocking datetime.now; just assert it doesn't raise
        # and a run from far in the past (well before "red_since") is correctly excluded.
        red_since = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
        runs = [{"id": 1, "created_at": "2020-01-01T00:00:00Z"}]
        assert has_in_flight_reconcile_for_episode(runs, red_since=red_since) is False


class TestFindStuckGatedApprovals:
    def test_returns_runs_over_threshold(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        mock_data = {
            "workflow_runs": [
                {
                    "id": 12345,
                    "created_at": "2026-06-27T06:00:00Z",
                    "html_url": "https://github.com/example/runs/12345",
                },
            ]
        }

        def _caller(url: str) -> Any:
            return mock_data

        result = find_stuck_gated_approvals(gh_caller=_caller, threshold_hours=6.0, now=now)
        assert len(result) == 1
        assert result[0]["run_id"] == 12345
        assert result[0]["age_hours"] >= 6.0

    def test_excludes_runs_under_threshold(self) -> None:
        now = datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)
        mock_data = {
            "workflow_runs": [
                {
                    "id": 99999,
                    "created_at": "2026-06-27T06:00:00Z",
                    "html_url": "https://github.com/example/runs/99999",
                },
            ]
        }

        def _caller(url: str) -> Any:
            return mock_data

        result = find_stuck_gated_approvals(gh_caller=_caller, threshold_hours=6.0, now=now)
        assert result == []

    def test_returns_empty_when_caller_returns_none(self) -> None:
        result = find_stuck_gated_approvals(gh_caller=lambda url: None)
        assert result == []


class TestFindStuckDefaultCaller:
    def test_no_token_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert find_stuck_gated_approvals() == []

    def test_skips_run_with_blank_created_at(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        data = {"workflow_runs": [{"id": 1, "created_at": "", "html_url": "u"}]}
        assert find_stuck_gated_approvals(gh_caller=lambda url: data, now=now) == []

    def test_swallows_caller_exception(self) -> None:
        def _boom(url: str) -> Any:
            raise RuntimeError("api down")

        assert find_stuck_gated_approvals(gh_caller=_boom) == []

    def test_default_caller_makes_authenticated_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GH_TOKEN", "tok-abc")
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        payload = json.dumps({"workflow_runs": [{"id": 7, "created_at": "2026-06-27T06:00:00Z", "html_url": "u"}]}).encode()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        with patch("urllib.request.urlopen", return_value=_Resp()) as uo:
            result = find_stuck_gated_approvals(now=now)
        uo.assert_called_once()
        assert result[0]["run_id"] == 7


class TestMakeGithubCaller:
    def test_empty_token_caller_returns_none(self) -> None:
        caller = _make_github_caller("")
        assert caller("https://api.github.com/test") is None

    def test_caller_with_token_makes_authenticated_request(self) -> None:
        payload = json.dumps({"workflow_runs": []}).encode()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        with patch("urllib.request.urlopen", return_value=_Resp()) as uo:
            caller = _make_github_caller("tok-xyz")
            result = caller("https://api.github.com/repos/o/r/actions/workflows/w/runs")
        uo.assert_called_once()
        req = uo.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer tok-xyz"
        assert result == {"workflow_runs": []}


class TestFilterStuckRuns:
    def test_partitions_mixed_age_runs_by_threshold(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        runs = [
            {"id": 1, "created_at": "2026-06-27T06:00:00Z", "html_url": "u1"},  # age=14h
            {"id": 2, "created_at": "2026-06-27T19:00:00Z", "html_url": "u2"},  # age=1h
        ]
        result = filter_stuck_runs(runs, threshold_hours=6.0, now=now)
        assert [r["run_id"] for r in result] == [1]
        assert result[0]["age_hours"] >= 6.0
        assert result[0]["url"] == "u1"
        assert result[0]["created_at"] == "2026-06-27T06:00:00Z"

    def test_boundary_exactly_at_threshold_is_included(self) -> None:
        now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
        runs = [{"id": 5, "created_at": "2026-06-27T06:00:00Z", "html_url": "u5"}]  # age=6.0h
        result = filter_stuck_runs(runs, threshold_hours=6.0, now=now)
        assert len(result) == 1
        assert result[0]["run_id"] == 5

    def test_returns_empty_list_when_no_runs_meet_threshold(self) -> None:
        now = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
        runs = [{"id": 3, "created_at": "2026-06-27T09:00:00Z", "html_url": "u3"}]  # age=1h
        result = filter_stuck_runs(runs, threshold_hours=6.0, now=now)
        assert result == []

    def test_returns_empty_list_on_empty_input(self) -> None:
        assert filter_stuck_runs([], threshold_hours=6.0) == []

    def test_skips_runs_with_blank_created_at(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        runs = [{"id": 4, "created_at": "", "html_url": "u4"}]
        assert filter_stuck_runs(runs, threshold_hours=0.0, now=now) == []

    def test_threshold_zero_returns_all_runs(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        runs = [
            {"id": 1, "created_at": "2026-06-27T06:00:00Z", "html_url": "u1"},
            {"id": 2, "created_at": "2026-06-27T19:30:00Z", "html_url": "u2"},
        ]
        result = filter_stuck_runs(runs, threshold_hours=0.0, now=now)
        assert len(result) == 2


class TestDiagnoseStuckApprovals:
    def test_returns_parsed_runs_at_threshold_zero(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        runs = {"workflow_runs": [{"id": 9, "created_at": "2026-06-27T18:00:00Z", "html_url": "u9"}]}
        out = diagnose_stuck_approvals(gh_caller=lambda url: runs, threshold_hours=0.0, now=now)
        assert len(out) == 1
        assert out[0]["run_id"] == 9
        assert out[0]["url"] == "u9"

    def test_returns_empty_when_caller_yields_none(self) -> None:
        assert diagnose_stuck_approvals(gh_caller=lambda url: None) == []

    def test_swallows_caller_exception(self) -> None:
        def _boom(url: str) -> Any:
            raise RuntimeError("api down")

        assert diagnose_stuck_approvals(gh_caller=_boom) == []

    def test_does_not_filter_by_status_waiting(self) -> None:
        now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)
        captured: list[str] = []

        def _capture(url: str) -> Any:
            captured.append(url)
            return {"workflow_runs": []}

        diagnose_stuck_approvals(gh_caller=_capture, now=now)
        assert captured, "caller was never invoked"
        assert "status=waiting" not in captured[0], f"diagnose URL must not filter by status: {captured[0]}"


@pytest.mark.parametrize("query", [find_stuck_gated_approvals, find_reconcile_runs_since, diagnose_stuck_approvals])
def test_every_approvals_query_follows_github_repository(monkeypatch: pytest.MonkeyPatch, query: Any) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/example-repo")
    captured: list[str] = []
    query(gh_caller=lambda url: captured.append(url) or {"workflow_runs": []})
    assert captured
    assert all("repos/example-org/example-repo/" in url for url in captured)
