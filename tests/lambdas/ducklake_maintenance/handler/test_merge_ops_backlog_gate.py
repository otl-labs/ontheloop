"""merge_ops_gated dispatch through the maintenance handler (PLAN-telemetry-read-timeouts): a skipped or unreadable
tick opens no catalog connection and emits the backlog signal; a triggered tick runs the unchanged merge_ops pass."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from src.common import ducklake_maintenance_scope as scope_mod
from src.common.ducklake_scd2_schema import load_field_semantics
from src.lambdas.ducklake_maintenance import backlog_gate
from tests.fixtures.ducklake_maintenance_handler import _FULL_DSN

pytestmark = pytest.mark.unit

META = "ducklake_telemetry"
EVENT = {
    "action": "merge_ops_gated",
    "data_path": "s3://b/ducklake-telemetry/",
    "meta_schema": META,
    "backlog_gate": {"min_commits": 60, "max_unmerged_minutes": 30},
}
LAST_PASS = datetime(2026, 11, 17, 17, 0, tzinfo=UTC)
CLEAN = {"failures": [], "refused": [], "per_table": [], "skipped": [], "deferred": []}


@pytest.fixture(autouse=True)
def _reset_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backlog_gate, "_LAST_TRIGGER", None)


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float, Any]]:
    seen: list[tuple[str, float, Any]] = []
    monkeypatch.setattr(h.rt, "emit_metric", lambda name, value, **kw: seen.append((name, value, kw.get("dimensions"))))
    return seen


def _decision(merge: bool, reason: str, commits: int | None) -> backlog_gate.GateDecision:
    return backlog_gate.GateDecision(merge, reason, commits, None, LAST_PASS if commits is not None else None)


def _patch_gate(monkeypatch: pytest.MonkeyPatch, decision: backlog_gate.GateDecision) -> MagicMock:
    opener = MagicMock(side_effect=AssertionError("a skipped tick must not open a catalog connection"))
    monkeypatch.setattr(backlog_gate, "decide", lambda *a, **k: decision)
    monkeypatch.setattr(backlog_gate.boto3, "client", lambda _name: MagicMock())
    monkeypatch.setattr(h.rt, "open_connection", opener)
    monkeypatch.setattr(h.rt, "fetch_dsn", opener)
    return opener


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def test_skip_opens_no_connection(monkeypatch: pytest.MonkeyPatch, emitted: list) -> None:
    opener = _patch_gate(monkeypatch, _decision(False, "below_threshold", 12))
    response = h.handler(EVENT)
    assert response["statusCode"] == 200
    body = _body(response)
    assert (body["skipped"], body["reason"], body["commits"]) == (True, "below_threshold", 12)
    assert body["last_pass"] == LAST_PASS.isoformat()
    opener.assert_not_called()
    assert emitted == [("MergeOpsBacklogCommits", 12.0, {"MetaSchema": META})]


def test_unreadable_skips_and_emits(monkeypatch: pytest.MonkeyPatch, emitted: list) -> None:
    opener = _patch_gate(monkeypatch, _decision(False, "gate_unreadable", None))
    body = _body(h.handler(EVENT))
    assert (body["skipped"], body["reason"], body["commits"]) == (True, "gate_unreadable", None)
    opener.assert_not_called()
    assert emitted == [("MergeOpsGateUnreadable", 1.0, {"MetaSchema": META})]


def _run_pass(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], MagicMock]:
    semantics = load_field_semantics()
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(n,) for n in sorted(scope_mod.build_registry(semantics))]
    opener = MagicMock(return_value=con)
    monkeypatch.setattr(backlog_gate, "decide", lambda *a, **k: _decision(True, "backlog", 75))
    monkeypatch.setattr(backlog_gate.boto3, "client", lambda _name: MagicMock())
    monkeypatch.setattr(h.rt, "fetch_dsn", lambda: _FULL_DSN)
    monkeypatch.setattr(h.rt, "open_connection", opener)
    monkeypatch.setattr(h.rt, "load_field_semantics", lambda: semantics)
    monkeypatch.setattr(h.maint, "_count_files", lambda *a, **k: 3)
    monkeypatch.setattr(h.maint, "merge_adjacent_files", lambda *a, **k: None)
    monkeypatch.setattr(h.current_compaction, "compact_current_tables", lambda *a, **k: {**CLEAN, "meta_schema": META})
    return _body(h.handler(EVENT)), opener


def test_merge_runs_the_unchanged_pass(monkeypatch: pytest.MonkeyPatch, emitted: list) -> None:
    body, opener = _run_pass(monkeypatch)
    assert body.get("ok") is True and body["action"] == "merge_ops", body
    assert opener.call_args.kwargs["meta_schema"] == META
    assert opener.call_args.kwargs["data_path"] == EVENT["data_path"]
    names = [name for name, _value, _dims in emitted]
    assert names[:2] == ["MergeOpsBacklogCommits", "MergeOpsGateTriggered"]
    assert "MergeOpsTablesCount" in names and names.index("MergeOpsGateTriggered") < names.index("MergeOpsTablesCount")
    assert backlog_gate._LAST_TRIGGER is not None


def test_files_before_total_is_emitted_for_telemetry(monkeypatch: pytest.MonkeyPatch, emitted: list) -> None:
    _run_pass(monkeypatch)
    files_before = [(value, dims) for name, value, dims in emitted if name == "MergeOpsFilesBeforeTotal"]
    assert files_before and files_before[0][1] == {"MetaSchema": META}
