"""Unit tests for scripts.convergence_health.sensor_liveness (audit finding LSA-02).

Every external dependency is injected (gh_caller, git_runner, open_recs, resolved_recs,
portal_caller, now), so this module never touches the network, AWS, or a real git checkout."""

from __future__ import annotations

import fnmatch
import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from unittest.mock import patch

import pytest

from scripts.convergence_health import sensor_liveness as sl

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
LOOP = "loop.yml"
CRON = "0 6 * * 1"  # weekly -> threshold 1209600s (14d), a clean power-of-two base for bucket math
THRESHOLD = sl.staleness_threshold_seconds(CRON)
PEERS = {LOOP: [CRON]}
_DEAD = object()


def _run(run_id: int, created_at: datetime) -> dict[str, Any]:
    return {"id": run_id, "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _gh_caller(
    runs: Optional[dict[str, Any]] = None,
    states: Optional[dict[str, str]] = None,
    states_dead: bool = False,
    success_runs: Optional[dict[str, Any]] = None,  # unconfigured (None) mirrors `runs`
) -> Callable[[str], Any]:
    def caller(url: str) -> Any:
        if url.endswith("/workflows?per_page=100"):
            return (
                None
                if states_dead
                else {"workflows": [{"path": f".github/workflows/{n}", "state": s} for n, s in (states or {}).items()]}
            )
        match = re.search(r"/workflows/([^/]+)/runs", url)
        if match:
            loop = match.group(1)
            pool = success_runs if (success_runs is not None and "status=success" in url) else (runs or {})
            if loop not in pool:
                return {"workflow_runs": []}
            value = pool[loop]
            if value is _DEAD:
                return None
            return {"workflow_runs": [value] if value else []}
        return None

    return caller


def _git_runner(sha: str = "cafef00d", ts: int = 0, shallow: bool = False) -> Callable[[list[str]], str]:
    def runner(cmd: list[str]) -> str:
        if cmd[:2] == ["git", "rev-parse"]:
            return "true" if shallow else "false"
        if cmd[:2] == ["git", "log"]:
            return f"{sha} {ts}\n"
        return ""

    return runner


def _portal_spy() -> tuple[list[tuple[str, dict[str, Any]]], Callable[[str, dict[str, Any]], Any]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def caller(action: str, fields: dict[str, Any]) -> Any:
        calls.append((action, dict(fields)))
        return f"rec-new-{len(calls)}" if action == "file" else None

    return calls, caller


def _episode_title(loop: str, anchor: str, bucket: int) -> str:
    return f"Loop: {loop}. Episode: {anchor}. Bucket: {bucket}. Scheduled loop has not run within its derived threshold"


def _like_matches(pattern: str, candidate: str) -> bool:
    return fnmatch.fnmatchcase(candidate, pattern.replace("_", "?").replace("%", "*"))  # LIKE -> fnmatch glob


class TestPeerDerivation:
    # Graduated whole-class as sensor-liveness-peer-derivation-on-key (VP step 1): the load-bearing
    # false-negative guard -- signature default, both `on:` key spellings, the opt-out, and the raise.
    def test_on_key_true_normalisation(self) -> None:
        doc = {"name": "x", True: {"schedule": [{"cron": "0 6 * * 1"}], "workflow_dispatch": None}}
        assert sl.peers_from_workflow_docs({"w.yml": doc}) == {"w.yml": ["0 6 * * 1"]}

    def test_plain_on_key(self) -> None:
        doc = {"name": "x", "on": {"schedule": [{"cron": "0 6 * * 1"}]}}
        assert sl.peers_from_workflow_docs({"w.yml": doc}) == {"w.yml": ["0 6 * * 1"]}

    def test_non_scheduled_workflow_excluded_with_opt_out(self) -> None:
        doc = {"name": "x", "on": {"push": None}}
        assert sl.peers_from_workflow_docs({"w.yml": doc}, require_non_empty=False) == {}

    def test_empty_result_raises_by_default(self) -> None:
        with pytest.raises(RuntimeError):
            sl.peers_from_workflow_docs({})

    def test_require_non_empty_defaults_true(self) -> None:
        default = inspect.signature(sl.peers_from_workflow_docs).parameters["require_non_empty"].default
        assert default is True

    def test_derive_scheduled_peers_reads_the_live_tree(self) -> None:
        # Membership floor, not an exact count (tests/CLAUDE.md test-count-coupling rule): the set
        # legitimately grows; VP step 2 owns the deliberately un-graduated exhaustive live count.
        peers = sl.derive_scheduled_peers()
        known = {
            "ci-rca-inactivity-sweep.yml",
            "codeql.yml",
            "convergence-health.yml",
            "cost-reconciliation.yml",
            "dedup-probe.yml",
            "dependabot-stranded.yml",
            "ghas-probe.yml",
            "main-canary.yml",
            "terraform-drift.yml",
        }
        assert known <= set(peers), sorted(known - set(peers))
        assert peers["terraform-drift.yml"] == ["17 * * * *"]

    def test_non_dict_workflow_document_is_skipped(self) -> None:
        assert sl.peers_from_workflow_docs({"broken.yml": "not a mapping"}, require_non_empty=False) == {}


class TestThresholdOracle:
    # Graduated whole-class as sensor-liveness-threshold-oracle (VP step 3): the four live cron
    # shapes and their clamp behaviour, exactly as VP step 3 asserts them.
    def test_hourly_shape(self) -> None:
        assert sl.cron_period_seconds("37 * * * *") == 3600

    def test_every_n_hours_shape(self) -> None:
        assert sl.cron_period_seconds("0 */3 * * *") == 10800

    def test_weekly_shape(self) -> None:
        assert sl.cron_period_seconds("0 6 * * 1") == 604800

    def test_monthly_shape(self) -> None:
        assert sl.cron_period_seconds("0 6 4 * *") == 2678400

    def test_unclassifiable_shape_raises(self) -> None:
        with pytest.raises(ValueError):
            sl.cron_period_seconds("0 6 4 * 1")

    def test_hourly_clamps_up_to_floor(self) -> None:
        assert sl.staleness_threshold_seconds("37 * * * *") == 86400

    def test_three_hour_period_clamps_up_to_floor(self) -> None:
        assert sl.staleness_threshold_seconds("0 */3 * * *") == 86400

    def test_weekly_unclamped(self) -> None:
        assert sl.staleness_threshold_seconds("0 6 * * 1") == 1209600

    def test_monthly_clamps_down_to_ceiling(self) -> None:
        assert sl.staleness_threshold_seconds("0 6 4 * *") == 3888000

    def test_fleet_threshold_is_min_over_derived_peers(self) -> None:
        assert sl.fleet_threshold_seconds({"a.yml": ["0 * * * *"], "b.yml": ["0 6 * * 1"]}) == 86400

    def test_wrong_field_count_raises(self) -> None:
        with pytest.raises(ValueError, match="unclassifiable"):
            sl.cron_period_seconds("0 6 * *")

    def test_step_minute_shape_is_accepted(self) -> None:
        assert sl.cron_period_seconds("*/15 * * * *") == 900  # not load-bearing; no live peer uses it


class TestEpisodeGrain:
    def test_fresh_stale_loop_with_no_history_files(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=portal
        )
        assert result["filed"] and result["filed"][0]["loop"] == LOOP
        assert calls[0][0] == "file"
        assert calls[0][1]["source"] == "loop_liveness_stale"
        assert calls[0][1]["acceptance"] == f"bin/venv-python -m scripts.convergence_health --liveness-probe {LOOP}"

    def test_redeath_updates_the_open_rec(self) -> None:
        open_recs = [{"id": "rec-open", "title": _episode_title(LOOP, "run:1", 1)}]
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(999, created_at)}, success_runs={LOOP: _run(0, NOW)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=open_recs, resolved_recs=[], portal_caller=portal
        )
        assert result["updated"] == [{"loop": LOOP, "rec_id": "rec-open", "action": "update"}]
        assert result["filed"] == []
        assert calls[0][0] == "update" and calls[0][1]["id"] == "rec-open"
        assert "run:999" in calls[0][1]["title"]

    def test_unchanged_episode_skips_the_portal(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)}, success_runs={LOOP: _run(0, NOW)})
        calls1, portal1 = _portal_spy()
        sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=portal1)
        open_recs = [{"id": "rec-1", "title": calls1[0][1]["title"], "context": calls1[0][1]["context"]}]
        calls2, portal2 = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=open_recs, resolved_recs=[], portal_caller=portal2
        )
        assert result["unchanged"] == [{"loop": LOOP, "rec_id": "rec-1", "action": "unchanged"}]
        assert calls2 == []

    def test_one_bucket_back_drops(self) -> None:
        anchor = "run:5"
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)  # bucket 2
        resolved = [
            {"id": "rec-r1", "title": _episode_title(LOOP, anchor, 1), "status": "closed", "source": "loop_liveness_stale"}
        ]
        gh = _gh_caller(runs={LOOP: _run(5, created_at)}, success_runs={LOOP: _run(0, NOW)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=resolved, portal_caller=portal
        )
        assert result["dropped"] == [{"loop": LOOP, "rec_id": "rec-r1", "action": "drop"}]
        assert calls == []

    def test_two_buckets_later_files_a_new_rec(self) -> None:
        anchor = "run:5"
        created_at = NOW - timedelta(seconds=THRESHOLD * 4)  # bucket 3 -- resolved max bucket + 2
        resolved = [
            {"id": "rec-r1", "title": _episode_title(LOOP, anchor, 1), "status": "closed", "source": "loop_liveness_stale"}
        ]
        gh = _gh_caller(runs={LOOP: _run(5, created_at)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=resolved, portal_caller=portal
        )
        assert result["filed"] and result["dropped"] == []
        assert calls[0][0] == "file"

    def test_max_resolved_bucket_wins_over_first_hit(self) -> None:
        anchor = "run:7"
        rows = [
            {"id": "rec-low", "title": _episode_title(LOOP, anchor, 1), "status": "closed", "source": "loop_liveness_stale"},
            {"id": "rec-high", "title": _episode_title(LOOP, anchor, 3), "status": "closed", "source": "loop_liveness_stale"},
        ]
        best = sl._same_anchor_best_row(rows, anchor)
        assert best is not None and best["id"] == "rec-high"

    def test_new_anchor_within_a_threshold_of_closure_drops(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        closed_at = NOW - timedelta(seconds=THRESHOLD / 2)
        resolved = [
            {
                "id": "rec-old",
                "title": _episode_title(LOOP, "run:1", 1),
                "status": "closed",
                "source": "loop_liveness_stale",
                "last_updated_timestamp": closed_at,
            }
        ]
        gh = _gh_caller(runs={LOOP: _run(99, created_at)}, success_runs={LOOP: _run(0, NOW)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=resolved, portal_caller=portal
        )
        assert result["dropped"] and result["dropped"][0]["rec_id"] == "rec-old"
        assert calls == []

    def test_new_anchor_after_the_dwell_files(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        closed_at = NOW - timedelta(seconds=THRESHOLD * 2)
        resolved = [
            {
                "id": "rec-old",
                "title": _episode_title(LOOP, "run:1", 1),
                "status": "closed",
                "source": "loop_liveness_stale",
                "last_updated_timestamp": closed_at,
            }
        ]
        gh = _gh_caller(runs={LOOP: _run(99, created_at)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=resolved, portal_caller=portal
        )
        assert result["filed"]
        assert calls and calls[0][0] == "file"

    def test_title_prefix_composition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _FakeReader:
            def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
                captured["verb"] = verb
                captured["params"] = params
                return []

        monkeypatch.setattr("src.common.ducklake_reader_client.make_reader", lambda **kw: _FakeReader())
        sl._fetch_resolved_loop_recs("convergence-health.yml")
        assert captured["verb"] == "recs_by_title_prefix"
        pattern = captured["params"]["title_prefix"]
        assert pattern == "Loop: convergence-health.yml.%"
        golden_title = _episode_title("convergence-health.yml", "run:123", 2)
        assert golden_title.startswith("Loop: ")
        assert _like_matches(pattern, golden_title)
        bad_title = "Scheduled loop stale -- Loop: convergence-health.yml. Episode: run:123. Bucket: 2."
        assert not _like_matches(pattern, bad_title)

    def test_underscore_wildcard_row_is_refiltered_out(self) -> None:
        pattern = "Loop: __fleet__.%"
        sneaky_title = _episode_title("XXfleetYY", "run:1", 1)
        assert _like_matches(pattern, sneaky_title)  # LIKE's `_` wildcard would match this
        rows = [{"id": "rec-x", "title": sneaky_title, "status": "closed", "source": "loop_liveness_stale"}]
        assert sl._filter_resolved_rows(rows, "__fleet__") == []

    def test_dry_run_prints_would_file_and_never_calls_the_portal(self, capsys: pytest.CaptureFixture[str]) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)}, success_runs={LOOP: _run(0, NOW)})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=portal, dry_run=True
        )
        assert calls == [], "dry_run must never call the portal caller"
        assert result["filed"] == [{"loop": LOOP, "rec_id": None, "action": "would_file"}]
        assert "DRY-RUN would_file" in capsys.readouterr().out

    def test_cross_anchor_live_fetch_charges_the_id_cap_and_calls_rec_by_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A resolved row shaped like the REAL sweep projection (no last_updated_timestamp) forces
        # the one-rec_by_id-per-episode live path even though resolved_recs itself is injected.
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        closed_at = NOW - timedelta(seconds=THRESHOLD * 2)
        resolved = [
            {"id": "rec-old", "title": _episode_title(LOOP, "run:1", 1), "status": "closed", "source": "loop_liveness_stale"}
        ]
        fetched = {"last_updated_timestamp": closed_at}
        calls: list[Any] = []

        def fake_fetch_rec_by_id(rec_id: Any, profile: Optional[str] = None) -> dict[str, Any]:
            calls.append(rec_id)
            return fetched

        monkeypatch.setattr(sl, "_fetch_rec_by_id", fake_fetch_rec_by_id)
        gh = _gh_caller(runs={LOOP: _run(99, created_at)})
        _, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=resolved, portal_caller=portal
        )
        assert calls == ["rec-old"]
        assert result["filed"]

    def test_resolved_recs_none_sweeps_live_per_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake_sweep(loop: str, profile: Optional[str] = None) -> list[dict[str, Any]]:
            calls.append(loop)
            return []

        monkeypatch.setattr(sl, "_fetch_resolved_loop_recs", fake_sweep)
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)})
        _, portal = _portal_spy()
        sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], portal_caller=portal)
        assert calls == [LOOP]

    def test_live_portal_and_reader_paths_are_reachable_with_no_injected_caller(self) -> None:
        # _portal_file/_portal_update/_fetch_rec_by_id fall through to the real
        # scripts.ops_data_portal / DuckLake reader modules when no caller is injected.
        with patch("scripts.ops_data_portal.file_rec", return_value="rec-live") as file_rec:
            assert sl._portal_file({"title": "x"}, None, "agent_platform") == "rec-live"
            file_rec.assert_called_once_with({"title": "x"}, profile="agent_platform")
        with patch("scripts.ops_data_portal.update_rec") as update_rec:
            sl._portal_update("rec-1", {"title": "y"}, None, "agent_platform")
            update_rec.assert_called_once_with("rec-1", {"title": "y"}, profile="agent_platform")

        named = lambda self, v, **p: [{"id": p.get("id"), "last_updated_timestamp": NOW}]  # noqa: E731
        with patch("src.common.ducklake_reader_client.make_reader", lambda **kw: type("R", (), {"named": named})()):
            assert sl._fetch_rec_by_id("rec-9")["id"] == "rec-9"


class TestFleetCollapse:
    def test_fleet_bucket_rises_when_a_new_peer_joins_after_closure(self) -> None:
        t0 = NOW - timedelta(seconds=1209600)
        peers = {"a.yml": ["37 * * * *"], "b.yml": ["17 * * * *"], "c.yml": ["0 */3 * * *"], "d.yml": ["0 6 * * 1"]}
        runs = {name: _run(i, t0) for i, name in enumerate(peers, start=1)}
        gh = _gh_caller(runs=runs)
        since_anchor = f"since:{t0.isoformat().replace('+00:00', 'Z')}"
        resolved = [
            {
                "id": "rec-fleet-closed",
                "title": _episode_title("__fleet__", since_anchor, 2),
                "status": "closed",
                "source": "loop_liveness_stale",
                "last_updated_timestamp": NOW - timedelta(days=1),
            }
        ]
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=peers, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=resolved, portal_caller=portal
        )
        assert result["fleet_collapse"] is True
        # under the rejected min(bucket over stale peers) formula this would DROP (min bucket = 1 <= 2+1);
        # the own-anchor derivation (bucket 4 at a 24h fleet threshold) instead rises past the dwell and files.
        assert result["filed"], "fleet bucket must rise (own-anchor age), never fall, as a new peer joins"
        assert result["dropped"] == []

    def test_open_per_loop_recs_are_suppressed_not_closed(self) -> None:
        peers = {"a.yml": ["37 * * * *"], "b.yml": ["17 * * * *"], "c.yml": ["0 */3 * * *"]}
        t0 = NOW - timedelta(seconds=sl.fleet_threshold_seconds(peers))
        runs = {name: _run(i, t0) for i, name in enumerate(peers, start=1)}
        gh = _gh_caller(runs=runs, success_runs={name: _run(0, NOW) for name in peers})
        open_per_loop = {"id": "rec-a-open", "title": _episode_title("a.yml", "run:1", 1)}
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=peers, now=NOW, gh_caller=gh, open_recs=[open_per_loop], resolved_recs=[], portal_caller=portal
        )
        assert result["fleet_collapse"] is True
        assert result["updated"] == []
        assert result["dropped"] == []
        assert len(result["filed"]) == 1 and result["filed"][0]["loop"] == "__fleet__"


class TestAntiMasking:
    def test_detect_stale_loops_raises_on_empty_derivation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(require_non_empty: bool = True) -> dict[str, list[str]]:
            raise RuntimeError("derived ZERO schedule-bearing peers")

        monkeypatch.setattr(sl, "derive_scheduled_peers", boom)
        with pytest.raises(RuntimeError, match="ZERO"):
            sl.detect_stale_loops(now=NOW, gh_caller=_gh_caller())

    def test_dead_run_query_raises(self) -> None:
        gh = _gh_caller(runs={LOOP: _DEAD})
        with pytest.raises(RuntimeError, match="no payload"):
            sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[])

    def test_query_is_filtered_to_scheduled_runs(self) -> None:
        captured: list[str] = []

        def gh(url: str) -> Any:
            captured.append(url)
            if url.endswith("/workflows?per_page=100"):
                return {"workflows": []}
            return {"workflow_runs": []}

        git = _git_runner(ts=int((NOW - timedelta(seconds=THRESHOLD * 5)).timestamp()))
        sl.detect_stale_loops(
            peers=PEERS,
            now=NOW,
            gh_caller=gh,
            git_runner=git,
            open_recs=[],
            resolved_recs=[],
            portal_caller=lambda a, f: "rec-x",
        )
        assert any("event=schedule" in u for u in captured if "/runs" in u)

    def test_empty_run_list_falls_back_to_first_commit_age(self) -> None:
        old_ts = int((NOW - timedelta(seconds=THRESHOLD * 3)).timestamp())
        git = _git_runner(sha="cafebabe", ts=old_ts)
        gh = _gh_caller(runs={})
        calls, portal = _portal_spy()
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, git_runner=git, open_recs=[], resolved_recs=[], portal_caller=portal
        )
        assert LOOP in result["stale"]
        assert "intro:cafebabe" in calls[0][1]["title"]

    def test_shallow_clone_raises_before_fallback(self) -> None:
        git = _git_runner(shallow=True)
        gh = _gh_caller(runs={})
        with pytest.raises(RuntimeError, match="shallow"):
            sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh, git_runner=git, open_recs=[], resolved_recs=[])

    def test_disabled_inactivity_state_refines_remediation(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)}, states={LOOP: "disabled_inactivity"})
        calls, portal = _portal_spy()
        sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=portal)
        assert "disabled_inactivity" in calls[0][1]["context"]

    @pytest.mark.parametrize(
        "gh",
        [
            _gh_caller(runs={LOOP: _run(1, NOW - timedelta(seconds=THRESHOLD * 2))}, states={LOOP: "active"}),
            _gh_caller(runs={LOOP: _run(1, NOW - timedelta(seconds=THRESHOLD * 2))}, states_dead=True),
        ],
        ids=["active-state-still-alarms", "dead-states-query-degrades-without-raising"],
    )
    def test_state_never_suppresses_the_age_alarm(self, gh: Callable[[str], Any]) -> None:
        result = sl.detect_stale_loops(
            peers=PEERS, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=lambda a, f: "rec-x"
        )
        assert result["filed"]

    def test_healthy_tick_issues_zero_warehouse_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom_open_recs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("must not fetch open_recs on a healthy tick")

        def boom_resolved(loop: str, profile: Optional[str] = None) -> list[dict[str, Any]]:
            raise AssertionError("must not sweep on a healthy tick")

        monkeypatch.setattr(sl, "find_recs", boom_open_recs)
        monkeypatch.setattr(sl, "_fetch_resolved_loop_recs", boom_resolved)
        gh = _gh_caller(runs={LOOP: _run(1, NOW)})
        result = sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh)
        assert result["stale"] == []

    @pytest.mark.parametrize(
        "key,cap_max,label", [("sweeps", "sweeps_max", "MAX_TITLE_SWEEPS"), ("ids", "ids_max", "MAX_REC_BY_ID")]
    )
    def test_charge_raises_past_the_cap(self, key: str, cap_max: str, label: str) -> None:
        budget = {key: 0, cap_max: 1}
        sl._charge(budget, key, "a.yml", label)
        with pytest.raises(RuntimeError, match=label):
            sl._charge(budget, key, "b.yml", label)

    def test_call_budget_caps_equal_twice_len_peers_plus_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict[str, int]] = []
        original = sl._charge

        def spy(budget: dict[str, int], key: str, loop: str, label: str) -> None:
            captured.append(dict(budget))
            original(budget, key, loop, label)

        monkeypatch.setattr(sl, "_charge", spy)
        peers = {"a.yml": ["37 * * * *"], "b.yml": ["0 6 * * 1"]}
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        runs = {name: _run(i, created_at) for i, name in enumerate(peers, start=1)}
        gh = _gh_caller(runs=runs)
        sl.detect_stale_loops(
            peers=peers, now=NOW, gh_caller=gh, open_recs=[], resolved_recs=[], portal_caller=lambda a, f: "rec-x"
        )
        assert captured and captured[0]["sweeps_max"] == 2 * (len(peers) + 1) == 6 and len(captured) == 4

    def test_first_commit_no_history_raises(self) -> None:
        git = _git_runner()

        def no_history(cmd: list[str]) -> str:
            return "" if cmd[:2] == ["git", "log"] else git(cmd)

        gh = _gh_caller(runs={})
        with pytest.raises(RuntimeError, match="no commit history"):
            sl.detect_stale_loops(peers=PEERS, now=NOW, gh_caller=gh, git_runner=no_history, open_recs=[], resolved_recs=[])

    def test_detect_stale_loops_defaults_now_and_fetches_open_recs_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Covers now=None and open_recs=None together; the run is pinned far in the PAST so it is
        # stale under the real wall clock too, reaching the open_recs is None -> find_recs branch.
        open_recs_calls: list[Any] = []
        monkeypatch.setattr(sl, "find_recs", lambda *a, **k: open_recs_calls.append(1) or [])
        ancient_run = _run(1, datetime(2000, 1, 1, tzinfo=timezone.utc))
        gh = _gh_caller(runs={LOOP: ancient_run})
        result = sl.detect_stale_loops(peers=PEERS, gh_caller=gh, resolved_recs=[], portal_caller=lambda a, f: "rec-x")
        assert result["stale"] == [LOOP]
        assert open_recs_calls == [1]


class TestInternals:
    # Direct unit coverage for small private helpers not exercised through the public flows above.

    def test_default_git_runner_shells_out_for_real(self) -> None:
        assert "git version" in sl._default_git_runner(["git", "--version"])

    def test_as_utc_accepts_an_iso_string(self) -> None:
        assert sl._as_utc("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestStalePeersForProbe:
    # --liveness-probe's backing oracle, exercised directly since main_liveness_probe (__main__.py)
    # is only a thin exception-to-exit-code wrapper around it.

    def test_fresh_peer_is_not_reported_stale(self) -> None:
        gh = _gh_caller(runs={LOOP: _run(1, NOW)})
        assert sl.stale_peers_for_probe(peers=PEERS, now=NOW, gh_caller=gh) == []

    def test_stale_peer_is_reported(self) -> None:
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={LOOP: _run(1, created_at)})
        assert sl.stale_peers_for_probe(peers=PEERS, now=NOW, gh_caller=gh) == [LOOP]

    def test_named_workflow_narrows_to_one_peer(self) -> None:
        peers = {"a.yml": [CRON], "b.yml": [CRON]}
        created_at = NOW - timedelta(seconds=THRESHOLD * 2)
        gh = _gh_caller(runs={"a.yml": _run(1, created_at), "b.yml": _run(2, NOW)})
        assert sl.stale_peers_for_probe("a.yml", peers=peers, now=NOW, gh_caller=gh) == ["a.yml"]

    def test_unknown_workflow_raises(self) -> None:
        with pytest.raises(ValueError, match="not a derived scheduled peer"):
            sl.stale_peers_for_probe("nope.yml", peers=PEERS, now=NOW, gh_caller=_gh_caller())

    def test_defaults_peers_and_now_when_omitted(self) -> None:
        # peers=None derives from the live tree; now=None defaults to the real clock -- both
        # exercised together against a live-tree workflow.
        gh = _gh_caller(runs={"convergence-health.yml": _run(1, datetime.now(timezone.utc))})
        assert sl.stale_peers_for_probe("convergence-health.yml", gh_caller=gh) == []

    def test_success_leg_selector_routes_to_the_success_classifier(self) -> None:
        gh = _gh_caller(runs={LOOP: _run(1, NOW)}, success_runs={})
        git = _git_runner(sha="cafebabe", ts=0)
        assert sl.stale_peers_for_probe(LOOP, "success", peers=PEERS, now=NOW, gh_caller=gh, git_runner=git) == [LOOP]


class TestWorkflowWiring:
    def _workflow_text(self) -> str:
        path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "convergence-health.yml"
        return path.read_text(encoding="utf-8")

    def test_step_invokes_sensor_liveness_with_gh_token(self) -> None:
        text = self._workflow_text()
        assert "Assert scheduled loop liveness (LSA-02)" in text
        assert "--sensor-liveness" in text

    def test_header_states_alarm_only_invariants(self) -> None:
        text = self._workflow_text()
        assert "LSA-02" in text
        assert "no schedule edit" in text
        assert "no workflow_dispatch of a stale peer" in text


class TestResolvedRecStatusesPin:
    def test_matches_schema_source_of_truth(self) -> None:
        from src.common.ducklake_scd2_schema import STATUS_TRANSITIONS

        assert sl.RESOLVED_REC_STATUSES == STATUS_TRANSITIONS["ops_recommendations"]["resolved"]
