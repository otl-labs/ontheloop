"""The four stored-row silent-failure checks (PLAN-telemetry-verification-monitor, VP step 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.telemetry_verify import silent_checks as sc

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _row(kind: str, n: int, **kw: Any) -> dict[str, Any]:
    stamp = T0 + timedelta(seconds=n)
    base = {
        "producer": "claude_code",
        "parser_version": 4,
        "session_id": "s1",
        "event_kind": kind,
        "event_id": f"e{n}-{kind}-{kw.get('agent_run_id', '')}",
        "event_timestamp": stamp,
        "created_timestamp": stamp,
        "session_started_at": T0,
        "parent_session_id": None,
        "source_ordinal": n,
    }
    return {**base, **kw}


def _call(n: int) -> dict[str, Any]:
    return _row("emit", n, observation_type="model_call", observation_id=f"o{n}", source_ordinal=n)


def test_each_check_fires_on_a_minimal_planted_row() -> None:
    sessions = [
        _row("open", 1, harness_session_key=None, checkout_sha=None),
        _row("open", 2, session_id="s2", harness_session_key="k", checkout_sha="abc"),
        _row("resume", 30, harness_session_key=None, checkout_sha=None),
    ]
    agents = [
        _row("open", 3, agent_run_id="a", trigger="subagent_background"),
        _row("close", 4, agent_run_id="a", trigger="subagent_background", error="notice_status:absent"),
        _row("open", 5, agent_run_id="b", trigger="subagent_background"),
        _row("close", 6, agent_run_id="b", error="notice_status:killed"),
        _row("open", 7, agent_run_id="c", trigger="subagent_background"),
        _row("close", 8, agent_run_id="c", error="notice_status:weird"),
        _row("open", 9, agent_run_id="d", trigger="subagent_sync"),
        _row("close", 10, agent_run_id="d", error="notice_status:weird"),
    ]
    counts = sc.check_tree({"telemetry_sessions": sessions, "telemetry_observations": [_call(10)], "telemetry_agents": agents})
    assert counts["harness_key_missing"] == (1, 2)
    assert counts["checkout_sha_missing"] == (2, 3)
    assert counts["background_notice_unknown"] == (2, 3)
    assert counts["resume_without_prior_close"] == (1, 1)


def test_each_check_reports_untested_without_v4_rows() -> None:
    old = [_row("open", 1, parser_version=3, harness_session_key=None, checkout_sha=None)]
    counts = sc.check_trees([{"telemetry_sessions": old}, {}])
    assert counts == dict.fromkeys(sc.CHECKS, (0, 0))
    assert sc.untested(counts) == list(sc.CHECKS)
    assert sc.untested({"harness_key_missing": (0, 5)}) == []


def test_a_closed_or_empty_segment_does_not_fire_the_resume_check() -> None:
    closed = [_row("open", 1), _row("close", 20), _row("resume", 30)]
    empty = [_row("open", 1), _row("resume", 30)]
    child = [
        _row("open", 1, session_id="c", parent_session_id="s1"),
        _row("resume", 30, session_id="c", parent_session_id="s1"),
    ]
    assert sc.check_tree({"telemetry_sessions": closed, "telemetry_observations": [_call(10)]})[
        "resume_without_prior_close"
    ] == (0, 1)
    assert sc.check_tree({"telemetry_sessions": empty, "telemetry_observations": []})["resume_without_prior_close"] == (0, 1)
    assert sc.check_tree({"telemetry_sessions": child, "telemetry_observations": [_call(10)]})[
        "resume_without_prior_close"
    ] == (0, 0)
    middle = [_row("open", 1), _row("resume", 30), _row("resume", 60)]
    assert sc.check_tree({"telemetry_sessions": middle, "telemetry_observations": [_call(40)]})[
        "resume_without_prior_close"
    ] == (1, 2)


def test_checkout_sha_missing_is_reported_never_filed() -> None:
    assert "checkout_sha_missing" in sc.REPORT_ONLY_CHECKS
    assert "checkout_sha_missing" not in sc.ALARM_CHECKS
    assert set(sc.CHECKS) == set(sc.ALARM_CHECKS) | set(sc.REPORT_ONLY_CHECKS)


def test_a_retired_generation_never_double_counts() -> None:
    sessions = [
        _row("open", 1, parser_version=3, harness_session_key=None),
        _row("open", 2, harness_session_key="k", event_id="newer"),
    ]
    assert sc.check_tree({"telemetry_sessions": sessions})["harness_key_missing"] == (0, 1)
