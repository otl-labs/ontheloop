"""Telemetry-store gauge (T2.36 c3, Decision 215 cl.2): tri-state, one reader call, named catalog, written to the report."""

from __future__ import annotations

import contextlib
import json
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight import telemetry_health as th
from tests.fixtures.platform_roadmap_state import live_state_dict
from tests.fixtures.session_preflight_module import MODULE_PATH
from tests.fixtures.session_preflight_module import preflight as _preflight

_CATALOG = "ducklake_smoke"
_CONTEXT = {
    "roadmap_phase": "unknown",
    "open_decisions_count": 0,
    "strategic_review_due": False,
    "recommendations_count": 0,
}


def _reader(counts: dict[str, int], *, catalog: str = _CATALOG) -> MagicMock:
    reader = MagicMock()
    reader.named.return_value = [
        {"table_name": name, "record_count": count, "meta_schema": catalog} for name, count in sorted(counts.items())
    ]
    return reader


def _all(**overrides: int) -> dict[str, int]:
    return {**dict.fromkeys(th.TABLES, 0), **overrides}


def test_pass_when_sessions_hold_rows() -> None:
    gauge = th.check_telemetry_health(_reader(_all(telemetry_sessions=4, telemetry_observations=9)))
    assert gauge["status"] == "pass"
    assert gauge["tables"] == {
        "telemetry_sessions": 4,
        "telemetry_observations": 9,
        "telemetry_transcripts": 0,
        "telemetry_agents": 0,
    }


def test_pass_when_only_a_non_sessions_table_holds_rows() -> None:
    """A non-replayable producer writes no sessions row (envelope R8), so any populated journal passes."""
    assert th.check_telemetry_health(_reader(_all(telemetry_agents=1)))["status"] == "pass"


def test_unpopulated_when_all_tables_are_empty() -> None:
    gauge = th.check_telemetry_health(_reader(_all()))
    assert gauge["status"] == "unpopulated" and set(gauge["tables"].values()) == {0}


def test_unavailable_carries_null_counts_never_zero() -> None:
    reader = MagicMock()
    reader.named.side_effect = RuntimeError("reader down")
    gauge = th.check_telemetry_health(reader)
    assert gauge["status"] == "unavailable" and gauge["catalog"] is None
    assert gauge["tables"] == dict.fromkeys(th.TABLES) and "RuntimeError" in gauge["reason"]
    missing = th.check_telemetry_health(_reader({"telemetry_sessions": 0}))
    assert missing["status"] == "unavailable" and missing["tables"]["telemetry_agents"] is None
    assert missing["tables"]["telemetry_sessions"] == 0
    assert th.check_telemetry_health(MagicMock(named=MagicMock(return_value=[])))["status"] == "unavailable"
    populated_but_partial = th.check_telemetry_health(_reader({"telemetry_sessions": 3}))
    assert populated_but_partial["status"] == "pass"


def test_gauge_names_the_catalog() -> None:
    gauge = th.check_telemetry_health(_reader(_all(telemetry_sessions=1), catalog="ducklake_smoke"))
    assert gauge["catalog"] == "ducklake_smoke"
    assert "pass on ducklake_smoke" in th.format_gauge(gauge)
    assert "unknown catalog" in th.format_gauge(th.unavailable_gauge("x"))
    assert th.format_gauge(gauge).startswith("Telemetry store: pass")


def test_exactly_one_reader_call() -> None:
    reader = _reader(_all(telemetry_sessions=1))
    th.check_telemetry_health(reader)
    assert reader.named.call_args_list == [(("telemetry_health_count",), {})]
    with patch.object(th._common, "_make_event_reader", return_value=reader) as make:
        th.check_telemetry_health()
    make.assert_called_once()


def test_gauge_reads_through_the_common_event_reader_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.telemetry_capture import read_route

    reader = _reader(_all(telemetry_sessions=2))
    built: list[str | None] = []
    with monkeypatch.context() as routed:
        routed.setattr(read_route, "make_routed_reader", lambda profile=None: built.append(profile) or reader)
        assert th.check_telemetry_health()["status"] == "pass" and built == [None]
    assert reader.named.call_args_list == [(("telemetry_health_count",), {})]

    ops = MagicMock()
    monkeypatch.setattr(read_route, "make_reader", lambda profile=None: ops)
    monkeypatch.setattr(read_route, "_parameter", lambda *a, **k: None)
    monkeypatch.delenv(read_route.CATALOG_ENV, raising=False)
    gauge = th.check_telemetry_health()
    assert gauge["status"] == "unavailable" and gauge["reason"].startswith("TelemetryCatalogUnresolved: ")
    assert gauge["tables"] == dict.fromkeys(th.TABLES) and ops.named.call_count == 0


def _run_main(tmp_path: Path, *, creds: str, reader: MagicMock) -> tuple[dict, MagicMock]:
    freshness = {
        "status": "ok",
        "fetched_at": "2026-06-15T00:00:00+00:00",
        "commits_behind": 0,
        "commits_ahead": 0,
        "main_files_changed_since_branch": [],
    }
    report_path = tmp_path / ".preflight-report.json"
    patches = [
        patch("scripts.preflight._common._make_reader", return_value=reader),
        patch("scripts.preflight._common._make_event_reader", return_value=reader),
        patch("scripts.preflight.env_git.check_venv", return_value=True),
        patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
        patch("scripts.preflight.env_git.check_main_freshness", return_value=freshness),
        patch("scripts.preflight.env_git.run_log_sync", return_value={"status": "skipped", "files": []}),
        patch("scripts.preflight.env_git._get_recent_main_commits", return_value=[]),
        patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
        patch("scripts.preflight.aws_infra.check_credentials", return_value=creds),
        patch("scripts.preflight.aws_infra._handle_credentials_startup", return_value=creds),
        patch(
            "scripts.preflight.session_history.read_session_history",
            return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
        ),
        patch("scripts.preflight.context_docs.print_data_quality_health"),
        patch("scripts.preflight.context_docs.read_context_files", return_value=_CONTEXT),
        patch("scripts.preflight.context_docs.check_data_quality_coverage", return_value={}),
        patch("scripts.preflight.context_docs._check_endstate_drift", return_value={}),
        patch("scripts.preflight.context_docs._scan_provisional_contracts", return_value=[]),
        patch("scripts.preflight.decision_conditions.preflight_bucket", return_value={}),
        patch("scripts.preflight.decision_conditions.print_decision_conditions"),
        patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
        patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None),
        patch("session_preflight.platform_roadmap.compute_state_dict", new=live_state_dict),
        patch("session_preflight.PREFLIGHT_REPORT", report_path),
        patch("builtins.print"),
    ]
    with contextlib.ExitStack() as stack:
        for entry in patches:
            stack.enter_context(entry)
        _preflight.main()
    return json.loads(report_path.read_text(encoding="utf-8")), reader


def test_preflight_main_writes_telemetry_health_once(tmp_path: Path) -> None:
    reader = _reader(_all(telemetry_sessions=2))
    report, reader = _run_main(tmp_path, creds="ok", reader=reader)
    health_calls = [c for c in reader.named.call_args_list if c.args and c.args[0] == "telemetry_health_count"]
    assert len(health_calls) == 1
    assert report["telemetry_health"]["status"] == "pass" and report["telemetry_health"]["catalog"] == _CATALOG


def test_no_reader_call_without_credentials(tmp_path: Path) -> None:
    report, reader = _run_main(tmp_path, creds="unavailable", reader=_reader(_all(telemetry_sessions=2)))
    assert not [c for c in reader.named.call_args_list if c.args and c.args[0] == "telemetry_health_count"]
    assert report["telemetry_health"]["status"] == "unavailable"
    assert report["telemetry_health"]["tables"] == dict.fromkeys(th.TABLES)


def test_health_flag_prints_the_telemetry_gauge(capsys: pytest.CaptureFixture) -> None:
    gauge = {"status": "pass", "catalog": _CATALOG, "tables": dict.fromkeys(th.TABLES, 1)}
    with (
        patch.object(th, "check_telemetry_health", return_value=gauge),
        patch("scripts.preflight.context_docs.print_data_quality_health"),
        patch.object(sys, "argv", ["preflight", "--health"]),
        pytest.raises(SystemExit) as exit_info,
    ):
        runpy.run_path(str(MODULE_PATH), run_name="__main__")
    assert exit_info.value.code == 0
    assert "Telemetry store: pass on ducklake_smoke" in capsys.readouterr().out
