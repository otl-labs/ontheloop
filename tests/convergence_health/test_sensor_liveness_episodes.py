"""Unit tests for scripts.convergence_health.sensor_liveness_episodes -- (loop, leg) episode
identity, split out of test_sensor_liveness.py (PLAN-monitor-liveness-sweep)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from scripts.convergence_health import sensor_liveness as sl
from scripts.convergence_health import sensor_liveness_episodes as sle

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
LOOP = "loop.yml"
CRON = "0 6 * * 1"
THRESHOLD = sl.staleness_threshold_seconds(CRON)
PEERS = {LOOP: [CRON]}


def _run(run_id: int, created_at: datetime) -> dict[str, Any]:
    return {"id": run_id, "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _gh_caller(runs: dict[str, Any], success_runs: Optional[dict[str, Any]] = None) -> Callable[[str], Any]:
    """Query-aware over two independently-configurable pools -- mirrors test_sensor_liveness.py's
    stub, kept local so this module stays a self-contained mirror-package test file."""

    def caller(url: str) -> Any:
        pool = success_runs if (success_runs is not None and "status=success" in url) else runs
        match = None
        for loop in pool:
            if f"/workflows/{loop}/runs" in url:
                match = loop
        if match is None:
            return {"workflow_runs": []}
        return {"workflow_runs": [pool[match]]}

    return caller


class TestTwoLegIntegration:
    def test_peer_stale_on_both_legs_files_two_independent_episodes(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)}, success_runs={LOOP: _run(1, created_at)})
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=lambda a, f: "rec-x"
        )
        assert len(result["filed"]) == 2
        assert result["stale"] == [LOOP] and result["stale_success"] == [LOOP]

    def test_legacy_untagged_open_rec_is_not_duplicated_by_the_cadence_leg(self) -> None:
        # rec-3860's live shape, filed before the success leg existed: a marker-less open cadence
        # rec must resolve to the SAME (loop, cadence) episode, not a duplicate file.
        legacy_open = {
            "id": "rec-3860",
            "status": "open",
            "source": "loop_liveness_stale",
            "title": sle._episode_title("main-canary.yml", "run:31336661344", 8),
        }
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        peers = {"main-canary.yml": [CRON]}
        gh = _gh_caller(
            runs={"main-canary.yml": _run(31336661344, created_at)},
            success_runs={"main-canary.yml": _run(1, NOW)},
        )
        result = sl.detect_stale_loops(
            peers=peers, now=NOW, gh_caller=gh, open_recs=[legacy_open], resolved_recs=[], portal_caller=lambda a, f: "x"
        )
        assert result["filed"] == []
        assert result["updated"] and result["updated"][0]["rec_id"] == "rec-3860"

    def test_fleet_collapse_counts_distinct_peers_not_leg_pairs(self) -> None:
        # Two peers stale on BOTH legs is distinct-peer count 2 per leg (below
        # FLEET_COLLAPSE_MIN_STALE=3); the rejected (peer, leg)-PAIR count of 4 would spuriously
        # collapse a correlated-outage-sized event out of two independent peers.
        peers = {"a.yml": ["37 * * * *"], "b.yml": ["17 * * * *"]}
        t0 = NOW - timedelta(seconds=sl.fleet_threshold_seconds(peers))
        runs = {name: _run(i, t0) for i, name in enumerate(peers, start=1)}
        gh = _gh_caller(runs=runs, success_runs=runs)
        result = sl.detect_stale_loops(
            peers=peers, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=lambda a, f: "rec-x"
        )
        assert result["fleet_collapse"] is False
        assert len(result["filed"]) == 4
        assert {c["loop"] for c in result["filed"]} == set(peers)


class TestTitleGrammar:
    def test_cadence_title_carries_no_leg_marker(self) -> None:
        title = sle._episode_title("a.yml", "run:1", 2)
        assert title == "Loop: a.yml. Episode: run:1. Bucket: 2. Scheduled loop has not run within its derived threshold"
        assert "Leg:" not in title

    def test_success_title_carries_an_explicit_marker_after_the_anchored_prefix(self) -> None:
        title = sle._episode_title("a.yml", "run:1", 2, leg="success")
        assert title.startswith("Loop: a.yml. Episode: run:1. Bucket: 2. ")
        assert ". Leg: success." in title

    def test_parse_episode_title_ignores_the_leg_tail(self) -> None:
        cadence = sle._episode_title("a.yml", "run:1", 2)
        success = sle._episode_title("a.yml", "run:1", 2, leg="success")
        assert sle._parse_episode_title(cadence) == ("a.yml", "run:1", 2)
        assert sle._parse_episode_title(success) == ("a.yml", "run:1", 2)

    def test_title_leg_defaults_to_cadence_for_a_legacy_marker_less_title(self) -> None:
        # rec-3860's live shape: filed before the success leg existed.
        legacy = sle._episode_title("main-canary.yml", "run:31336661344", 8)
        assert sle._title_leg(legacy) == "cadence"

    def test_title_leg_detects_the_success_marker(self) -> None:
        success = sle._episode_title("a.yml", "run:1", 1, leg="success")
        assert sle._title_leg(success) == "success"

    def test_parse_episode_title_rejects_malformed_titles(self) -> None:
        assert sle._parse_episode_title("not an episode title") is None
        assert sle._parse_episode_title("Loop: x. Episode: y. Bucket: not-a-number. tail") is None


class TestOpenMatcherLegAware:
    def test_is_open_loop_row_rejects_explicit_non_matches(self) -> None:
        assert sle._is_open_loop_row({"status": "closed", "title": f"Loop: {LOOP}. x"}, LOOP) is False
        assert sle._is_open_loop_row({"source": "budget_breach", "title": f"Loop: {LOOP}. x"}, LOOP) is False

    def test_legacy_marker_less_open_rec_matches_only_the_cadence_leg(self) -> None:
        rec = {
            "id": "rec-3860",
            "status": "open",
            "source": "loop_liveness_stale",
            "title": sle._episode_title("main-canary.yml", "run:31336661344", 8),
        }
        assert sle._is_open_loop_row(rec, "main-canary.yml", leg="cadence") is True
        assert sle._is_open_loop_row(rec, "main-canary.yml", leg="success") is False

    def test_success_marked_open_rec_matches_only_the_success_leg(self) -> None:
        title = sle._episode_title("a.yml", "run:1", 1, leg="success")
        rec = {"id": "rec-1", "status": "open", "source": "loop_liveness_stale", "title": title}
        assert sle._is_open_loop_row(rec, "a.yml", leg="success") is True
        assert sle._is_open_loop_row(rec, "a.yml", leg="cadence") is False

    def test_absent_title_is_treated_as_already_satisfied_regardless_of_leg(self) -> None:
        rec = {"status": "open", "source": "loop_liveness_stale"}
        assert sle._is_open_loop_row(rec, "a.yml", leg="cadence") is True
        assert sle._is_open_loop_row(rec, "a.yml", leg="success") is True


class TestResolvedMatcherLegAware:
    def _row(self, loop: str, anchor: str, bucket: int, leg: str = "cadence", **extra: Any) -> dict[str, Any]:
        return {
            "id": f"rec-{loop}-{anchor}-{bucket}-{leg}",
            "title": sle._episode_title(loop, anchor, bucket, leg=leg),
            "status": "closed",
            "source": "loop_liveness_stale",
            **extra,
        }

    def test_filter_resolved_rows_leg_none_returns_both_legs(self) -> None:
        rows = [self._row("a.yml", "run:1", 1), self._row("a.yml", "run:1", 1, leg="success")]
        assert len(sle._filter_resolved_rows(rows, "a.yml")) == 2

    def test_filter_resolved_rows_discriminates_by_leg(self) -> None:
        rows = [self._row("a.yml", "run:1", 1), self._row("a.yml", "run:1", 1, leg="success")]
        cadence_only = sle._filter_resolved_rows(rows, "a.yml", leg="cadence")
        success_only = sle._filter_resolved_rows(rows, "a.yml", leg="success")
        assert len(cadence_only) == 1 and sle._title_leg(str(cadence_only[0]["title"])) == "cadence"
        assert len(success_only) == 1 and sle._title_leg(str(success_only[0]["title"])) == "success"

    def test_two_legs_stale_on_one_peer_resolve_independently(self) -> None:
        # Two legs closed at different buckets for the SAME peer/anchor must never be confused --
        # each leg's best-row lookup sees only its own rows.
        rows = [
            self._row("a.yml", "run:1", 1, leg="cadence"),
            self._row("a.yml", "run:1", 3, leg="cadence"),
            self._row("a.yml", "run:1", 2, leg="success"),
        ]
        cadence_rows = sle._filter_resolved_rows(rows, "a.yml", leg="cadence")
        success_rows = sle._filter_resolved_rows(rows, "a.yml", leg="success")
        cadence_best = sle._same_anchor_best_row(cadence_rows, "run:1")
        success_best = sle._same_anchor_best_row(success_rows, "run:1")
        assert cadence_best is not None and sle._parse_episode_title(str(cadence_best["title"]))[2] == 3  # type: ignore[index]
        assert success_best is not None and sle._parse_episode_title(str(success_best["title"]))[2] == 2  # type: ignore[index]
        assert cadence_best["id"] != success_best["id"]

    def test_underscore_wildcard_row_is_refiltered_out_regardless_of_leg(self) -> None:
        sneaky = sle._episode_title("XXfleetYY", "run:1", 1)
        rows = [{"id": "rec-x", "title": sneaky, "status": "closed", "source": "loop_liveness_stale"}]
        assert sle._filter_resolved_rows(rows, "__fleet__") == []

    def test_cross_anchor_newest_picks_the_highest_id_at_a_different_anchor(self) -> None:
        rows = [self._row("a.yml", "run:1", 1), self._row("a.yml", "run:2", 1), self._row("a.yml", "run:1", 2)]
        newest = sle._cross_anchor_newest(rows, "run:1")
        assert newest is not None and newest["id"] == self._row("a.yml", "run:2", 1)["id"]

    def test_cross_anchor_newest_returns_none_when_every_row_shares_the_anchor(self) -> None:
        rows = [self._row("a.yml", "run:1", 1), self._row("a.yml", "run:1", 2)]
        assert sle._cross_anchor_newest(rows, "run:1") is None
        assert sle._filter_resolved_rows(rows, "__fleet__", leg="cadence") == []


class TestEpisodeContextLegAware:
    def test_cadence_context_names_the_bare_schedule_query(self) -> None:
        info = {
            "loop": "a.yml",
            "anchor": "run:1",
            "bucket": 1,
            "leg": "cadence",
            "threshold_seconds": 3600,
            "age_seconds": 7200,
            "state": None,
        }
        context = sle._episode_context(info)
        assert "?event=schedule`" in context
        assert "status=success" not in context

    def test_success_context_names_the_success_filtered_query(self) -> None:
        info = {
            "loop": "a.yml",
            "anchor": "run:1",
            "bucket": 1,
            "leg": "success",
            "threshold_seconds": 3600,
            "age_seconds": 7200,
            "state": None,
        }
        context = sle._episode_context(info)
        assert "status=success" in context

    def test_context_defaults_leg_to_cadence_when_absent(self) -> None:
        info = {"loop": "a.yml", "anchor": "run:1", "bucket": 1, "threshold_seconds": 3600, "age_seconds": 1, "state": None}
        assert "?event=schedule`" in sle._episode_context(info)


class TestResolvedRecStatusesPin:
    def test_matches_schema_source_of_truth(self) -> None:
        from src.common.ducklake_scd2_schema import STATUS_TRANSITIONS

        assert sle.RESOLVED_REC_STATUSES == STATUS_TRANSITIONS["ops_recommendations"]["resolved"]
