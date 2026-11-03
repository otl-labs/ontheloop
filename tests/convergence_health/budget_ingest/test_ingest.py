"""ingest_budget_breaches: the file / update / unchanged action legs, the dry-run report, the
loud-fail posture, and the production defaults (live open-recs fetch + real ops-portal calls).

Split out of the retired single-file test_budget_ingest.py monolith (rec-3288 wave-4 fixups).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from scripts.convergence_health import budget_ingest as bi

from .conftest import MARKERS, _artifact, _budget_block, _caller_for, _fetcher_for, _full_rec, _ingest_one, _rec, _ToyWarehouse


class TestIngestBudgetBreaches:
    def test_files_exactly_one_rec_per_episode(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-4001"

        result = _ingest_one(
            blocks=[_budget_block(), _budget_block(), _budget_block(dominant_phase="mypy")],
            portal_caller=_caller,
        )
        assert result["episodes"] == 3
        assert result["groups"] == 2
        assert [action for action, _ in calls] == ["file", "file"]
        assert [entry["action"] for entry in result["actions"]] == ["file", "file"]

    def test_updates_the_open_rec_matching_branch_and_dominant_phase(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []
        result = _ingest_one(
            open_recs=[_rec()],
            portal_caller=lambda action, fields: calls.append((action, fields)),
        )
        assert result["actions"] == [
            {"action": "update", "rec_id": "rec-3000", "branch": "claude/slow-branch", "dominant_phase": "pytest", "runs": 1}
        ]
        assert calls[0][0] == "update"
        assert set(calls[0][1]) == {"id", "title", "context"}
        assert calls[0][1]["id"] == "rec-3000"

    def test_an_unchanged_episode_skips_the_update_leg_entirely(self) -> None:
        """Correctness 8: an identical title+context has nothing to say. Writing it anyway costs one
        duplicate SCD2 history row per hourly tick -- ~336 per open episode over a 14-day window."""
        calls: list[tuple[str, dict[str, Any]]] = []
        captured: dict[str, Any] = {}
        _ingest_one(portal_caller=lambda action, fields: captured.update(fields))
        stored = _rec(context=captured["context"], title=captured["title"])
        result = _ingest_one(open_recs=[stored], portal_caller=lambda action, fields: calls.append((action, fields)))
        assert result["actions"][0]["action"] == "unchanged"
        assert result["actions"][0]["rec_id"] == "rec-3000"
        assert calls == []

    def test_a_changed_context_still_updates(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []
        stored = _rec(title="Fast-tier budget breach (1.0 min) on claude/slow-branch")
        result = _ingest_one(open_recs=[stored], portal_caller=lambda action, fields: calls.append((action, fields)))
        assert result["actions"][0]["action"] == "update"
        assert [action for action, _ in calls] == ["update"]

    def test_an_open_rec_for_another_phase_does_not_suppress_the_file(self) -> None:
        existing = _rec(context="Branch: claude/slow-branch. Dominant phase: mypy.")
        result = _ingest_one(open_recs=[existing], portal_caller=lambda a, f: "rec-4002")
        assert result["actions"][0]["action"] == "file"

    def test_filed_context_round_trips_through_the_shared_dedupe_matcher(self) -> None:
        """The whole dedupe contract: the title and context this ingester writes must be findable
        by the SAME (branch, dominant_phase) matcher scripts/checks/_budget_recs.py uses locally,
        so a repeat breach updates one rec instead of filing a second. The row fed back in is
        LIVE-shaped (a full-projection row, as scripts.rec_episode.find_recs's scoped read really
        returns it) -- the anti-drift pin on THIS writer's half of the contract."""
        from scripts.checks._budget_recs import _find_open_budget_breach_rec

        captured: dict[str, Any] = {}
        _ingest_one(portal_caller=lambda action, fields: captured.update(fields))
        filed = _rec("rec-4003", context=captured["context"], title=captured["title"])
        assert _find_open_budget_breach_rec([filed], "claude/slow-branch", "pytest") is filed
        assert _find_open_budget_breach_rec([filed], "claude/other", "pytest") is None
        assert _find_open_budget_breach_rec([filed], "claude/slow-branch", "ruff") is None

    def test_no_breach_episodes_never_touches_the_portal_or_the_reader(self) -> None:
        def _explode(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("no episode -- nothing may be filed and no reader call is warranted")

        with patch("scripts.convergence_health.budget_ingest.find_recs", side_effect=_explode):
            result = bi.ingest_budget_breaches(
                gh_caller=_caller_for([_artifact(1)]),
                artifact_fetcher=_fetcher_for({1: {"budget": _budget_block(outcome="within_budget")}}),
                portal_caller=_explode,
            )
        assert result["groups"] == 0
        assert result["actions"] == []

    def test_dry_run_files_nothing_and_reports_what_it_would_do(self, capsys: pytest.CaptureFixture[str]) -> None:
        def _explode(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("--dry-run must never reach the portal")

        result = _ingest_one(
            blocks=[_budget_block(), _budget_block(branch="claude/other")],
            open_recs=[_rec()],
            portal_caller=_explode,
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert sorted(entry["action"] for entry in result["actions"]) == ["would_file", "would_update"]
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "would_update" in out

    def test_portal_failure_propagates_loudly_with_no_outbox(self) -> None:
        """Decision 84 I-4: an unreachable portal fails at the call site. Nothing is buffered."""

        def _caller(_action: str, _fields: dict[str, Any]) -> Any:
            raise RuntimeError("ducklake_writer Function URL unreachable")

        with pytest.raises(RuntimeError, match="ducklake_writer Function URL unreachable"):
            _ingest_one(portal_caller=_caller)

    def test_rec_fields_pass_the_real_acceptance_linter_and_write_time_validators(self) -> None:
        from scripts.executor.acceptance_lint import lint_acceptance_command
        from scripts.ops_portal.risk_scoring import _derive_computed_fields
        from scripts.ops_portal.write_validators import gate_write

        captured: dict[str, Any] = {}
        _ingest_one(portal_caller=lambda action, fields: captured.update(fields))
        assert lint_acceptance_command(captured["acceptance"]) == (True, None)
        _derive_computed_fields(captured)
        gate_write("ops_recommendations", captured, insert=True)

    def test_title_and_context_name_the_outcome_and_the_run(self) -> None:
        captured: dict[str, Any] = {}
        _ingest_one(blocks=[_budget_block(outcome="forced_ceiling_breach")], portal_caller=lambda a, f: captured.update(f))
        assert captured["title"] == "Fast-tier budget forced_ceiling_breach (7.0 min) on claude/slow-branch"
        assert captured["source"] == "budget_breach"
        assert "actions/runs/555" in captured["context"]


class TestIngestLivePaths:
    """Cover the production defaults: the live rec fetches and the real ops-portal calls."""

    def test_fetches_open_recs_and_files_via_the_real_portal_when_not_injected(self) -> None:
        with (
            patch("scripts.convergence_health.budget_ingest.find_recs", return_value=[]) as fetch,
            patch("scripts.convergence_health.budget_ingest._fetch_resolved_budget_recs", return_value=[]) as resolved,
            patch("scripts.ops_data_portal.file_rec", return_value="rec-live") as file_rec,
        ):
            result = bi.ingest_budget_breaches(
                gh_caller=_caller_for([_artifact(1)]),
                artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
                profile="agent_platform",
            )
        fetch.assert_called_once_with("budget_breach", profile="agent_platform")
        resolved.assert_called_once_with("claude/slow-branch", profile="agent_platform")
        file_rec.assert_called_once()
        assert result["actions"][0]["rec_id"] == "rec-live"

    def test_a_live_resolved_match_drops_the_episode_without_touching_the_portal(self) -> None:
        with (
            patch("scripts.convergence_health.budget_ingest.find_recs", return_value=[]),
            patch(
                "scripts.convergence_health.budget_ingest._fetch_resolved_budget_recs",
                return_value=[_full_rec()],
            ),
            patch("scripts.ops_data_portal.file_rec") as file_rec,
            patch("scripts.ops_data_portal.update_rec") as update_rec,
        ):
            result = bi.ingest_budget_breaches(
                gh_caller=_caller_for([_artifact(1)]),
                artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
            )
        file_rec.assert_not_called()
        update_rec.assert_not_called()
        assert result["actions"][0] == {
            "action": "drop",
            "rec_id": "rec-3000",
            "branch": "claude/slow-branch",
            "dominant_phase": "pytest",
            "runs": 1,
        }

    def test_updates_via_the_real_portal_when_not_injected(self) -> None:
        with patch("scripts.ops_data_portal.update_rec") as update_rec:
            result = bi.ingest_budget_breaches(
                gh_caller=_caller_for([_artifact(1)]),
                artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
                open_recs=[_rec(context=MARKERS)],
            )
        update_rec.assert_called_once()
        assert result["actions"][0]["action"] == "update"


class TestHourlyTickIdempotence:
    """The no-op-update guard is only REACHABLE when the open half of the dedupe matches a
    LIVE-shaped row. _ToyWarehouse.open_recs projects to the full-projection columns
    scripts.rec_episode.find_recs's scoped read returns (`status`/`source` included) -- exactly
    what production hands the matcher -- so this is the end-to-end cost check the hourly cron
    actually pays."""

    def test_a_day_of_hourly_ticks_files_once_and_writes_nothing_after(self) -> None:
        warehouse = _ToyWarehouse()
        actions = [
            _ingest_one(
                portal_caller=warehouse.portal,
                open_recs=warehouse.open_recs,
                resolved_recs=warehouse.resolved_recs,
            )["actions"][0]["action"]
            for _ in range(24)
        ]
        assert actions == ["file"] + ["unchanged"] * 23
        assert warehouse.writes == 1
        assert len(warehouse.recs) == 1  # count-coupling-ok: one deliberately-filed toy rec
