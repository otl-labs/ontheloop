"""MIRROR for src/lambdas/ducklake_maintenance/backlog_gate.py (PLAN-telemetry-read-timeouts): the gate decides from
CloudWatch only, merges at the backlog or age bound, skips (and says so) when unreadable, and marks its trigger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.lambdas.ducklake_maintenance import backlog_gate

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 17, 17, 30, 20, tzinfo=UTC)
GATE = {"min_commits": 60, "max_unmerged_minutes": 30}
META = "ducklake_telemetry"
EVENT = {"action": "merge_ops_gated", "data_path": "s3://b/t/", "meta_schema": META, "backlog_gate": GATE}
PASS_KEY = ("DuckLakeMaintenance", "MergeOpsTablesCount")
TRIGGER_KEY = ("DuckLakeMaintenance", "MergeOpsGateTriggered")
COMMIT_KEY = ("DuckLakeTelemetryWriter", "CommitLatencyMs")


class FakeCloudWatch:
    """Datapoints keyed by (namespace, metric); honours StartTime floored to the minute and EndTime, like the service."""

    def __init__(self, points: dict[tuple[str, str], list[tuple[datetime, float]]] | None = None, *, raises: bool = False):
        self.points = points or {}
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("AccessDenied")
        start = kwargs["StartTime"].replace(second=0, microsecond=0)
        rows = self.points.get((kwargs["Namespace"], kwargs["MetricName"]), [])
        return {
            "Datapoints": [{"Timestamp": ts, "SampleCount": count} for ts, count in rows if start <= ts <= kwargs["EndTime"]]
        }


@pytest.fixture(autouse=True)
def _reset_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backlog_gate, "_LAST_TRIGGER", None)


def _minute(minutes_ago: int) -> datetime:
    return (NOW - timedelta(minutes=minutes_ago)).replace(second=0)


def _decide(client: FakeCloudWatch) -> backlog_gate.GateDecision:
    return backlog_gate.decide(GATE, META, NOW, client)


def test_merges_at_the_backlog_threshold() -> None:
    passed = {PASS_KEY: [(_minute(10), 1.0)]}
    at = FakeCloudWatch({**passed, COMMIT_KEY: [(_minute(5), 40.0), (_minute(2), 20.0)]})
    decision = _decide(at)
    assert (decision.merge, decision.reason, decision.commits) == (True, "backlog", 60)
    below = FakeCloudWatch({**passed, COMMIT_KEY: [(_minute(5), 40.0), (_minute(2), 19.0)]})
    decision = _decide(below)
    assert (decision.merge, decision.reason, decision.commits) == (False, "below_threshold", 59)


def test_merges_waiting_commits_after_the_age_bound() -> None:
    passed = {PASS_KEY: [(_minute(45), 1.0)]}
    old = _decide(FakeCloudWatch({**passed, COMMIT_KEY: [(_minute(30), 2.0)]}))
    assert (old.merge, old.reason, old.commits) == (True, "age", 2)
    fresh = _decide(FakeCloudWatch({PASS_KEY: [(_minute(30), 1.0)], COMMIT_KEY: [(_minute(1), 1.0)]}))
    assert (fresh.merge, fresh.reason, fresh.commits) == (False, "below_threshold", 1)
    quiet = _decide(FakeCloudWatch({**passed, COMMIT_KEY: [(_minute(20), 0.0)]}))
    assert (quiet.merge, quiet.reason, quiet.commits, quiet.oldest_commit) == (False, "below_threshold", 0, None)


def test_unreadable_metrics_skip_and_say_so() -> None:
    decision = _decide(FakeCloudWatch(raises=True))
    assert (decision.merge, decision.reason, decision.commits, decision.last_pass) == (False, "gate_unreadable", None, None)


def test_reads_commits_from_the_last_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    pass_at, trigger_at = _minute(50), _minute(20)
    client = FakeCloudWatch({PASS_KEY: [(pass_at, 1.0)], TRIGGER_KEY: [(trigger_at, 1.0)], COMMIT_KEY: [(_minute(20), 7.0)]})
    decision = _decide(client)
    commit_call = client.calls[-1]
    assert (commit_call["Namespace"], commit_call["MetricName"]) == COMMIT_KEY
    assert commit_call["Dimensions"] == [{"Name": "MetaSchema", "Value": META}]
    assert commit_call["StartTime"] == trigger_at and decision.last_pass == trigger_at
    assert decision.commits == 7, "the whole minute bucket holding the last pass is counted (safe side)"
    assert all(call["Dimensions"] == [{"Name": "MetaSchema", "Value": META}] for call in client.calls)
    container_mark = NOW - timedelta(minutes=5)
    monkeypatch.setattr(backlog_gate, "_LAST_TRIGGER", container_mark)
    client = FakeCloudWatch({PASS_KEY: [(pass_at, 1.0)], TRIGGER_KEY: [(trigger_at, 1.0)]})
    assert _decide(client).last_pass == container_mark and client.calls[-1]["StartTime"] == container_mark
    monkeypatch.setattr(backlog_gate, "_LAST_TRIGGER", None)
    none_found = FakeCloudWatch({})
    assert _decide(none_found).last_pass == NOW - backlog_gate.LOOKBACK


def test_rejects_a_malformed_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backlog_gate.boto3, "client", lambda _name: FakeCloudWatch())
    action = backlog_gate.gated(lambda event, con: pytest.fail("the action must not run"))
    for bad in (
        {"min_commits": 60},
        {"min_commits": 0, "max_unmerged_minutes": 30},
        {"min_commits": True, "max_unmerged_minutes": 30},
        None,
    ):
        with pytest.raises(DuckLakeRuntimeError, match="backlog_gate"):
            action({**EVENT, "backlog_gate": bad}, None)


def _gated_run(monkeypatch: pytest.MonkeyPatch, client: FakeCloudWatch, action: Any) -> tuple[dict, list]:
    emitted: list[tuple[str, float, dict | None]] = []
    monkeypatch.setattr(backlog_gate.boto3, "client", lambda _name: client)
    monkeypatch.setattr(backlog_gate, "_now", lambda: NOW)
    monkeypatch.setattr(
        backlog_gate._shared,
        "_emit_maintenance_metric",
        lambda name, value, **kw: emitted.append((name, value, kw.get("dimensions"))),
    )
    return backlog_gate.gated(action)(EVENT, None), emitted


def test_gated_skip_emits_and_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeCloudWatch({PASS_KEY: [(_minute(10), 1.0)], COMMIT_KEY: [(_minute(5), 12.0)]})
    result, emitted = _gated_run(monkeypatch, client, lambda event, con: pytest.fail("the action must not run"))
    assert result == {
        "ok": True,
        "action": "merge_ops_gated",
        "skipped": True,
        "reason": "below_threshold",
        "commits": 12,
        "last_pass": _minute(10).isoformat(),
    }
    assert emitted == [("MergeOpsBacklogCommits", 12.0, {"MetaSchema": META})]


def test_gated_unreadable_emits_and_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    result, emitted = _gated_run(
        monkeypatch, FakeCloudWatch(raises=True), lambda event, con: pytest.fail("the action must not run")
    )
    assert result["skipped"] is True and result["reason"] == "gate_unreadable"
    assert result["commits"] is None and result["last_pass"] is None
    assert emitted == [("MergeOpsGateUnreadable", 1.0, {"MetaSchema": META})]


def test_gated_ops_catalog_emits_undimensioned(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[tuple[str, float, dict | None]] = []
    monkeypatch.setattr(backlog_gate.boto3, "client", lambda _name: FakeCloudWatch())
    monkeypatch.setattr(
        backlog_gate._shared,
        "_emit_maintenance_metric",
        lambda name, value, **kw: emitted.append((name, value, kw.get("dimensions"))),
    )
    backlog_gate.gated(lambda event, con: pytest.fail("the action must not run"))(
        {**EVENT, "meta_schema": "ducklake_ops"}, None
    )
    assert emitted == [("MergeOpsBacklogCommits", 0.0, None)]


def test_trigger_then_skip_in_one_container(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeCloudWatch({COMMIT_KEY: [(_minute(3), 61.0)]})
    calls: list[dict] = []

    def failing_merge(event: dict, con: Any) -> dict:
        assert backlog_gate._LAST_TRIGGER == NOW, "the container mark is set before merge_ops runs"
        assert ("MergeOpsGateTriggered", 1.0, {"MetaSchema": META}) in emitted
        calls.append(event)
        raise DuckLakeRuntimeError("merge pass failed before MergeOpsTablesCount")

    emitted: list[tuple[str, float, dict | None]] = []
    monkeypatch.setattr(backlog_gate.boto3, "client", lambda _name: client)
    monkeypatch.setattr(backlog_gate, "_now", lambda: NOW)
    monkeypatch.setattr(
        backlog_gate._shared,
        "_emit_maintenance_metric",
        lambda name, value, **kw: emitted.append((name, value, kw.get("dimensions"))),
    )
    action = backlog_gate.gated(failing_merge)
    with pytest.raises(DuckLakeRuntimeError):
        action(EVENT, None)
    later = NOW + timedelta(minutes=6)
    monkeypatch.setattr(backlog_gate, "_now", lambda: later)
    second = action(EVENT, None)
    assert (second["skipped"], second["reason"], second["commits"]) == (True, "below_threshold", 0)
    assert second["last_pass"] == NOW.isoformat()
    assert calls == [EVENT]
