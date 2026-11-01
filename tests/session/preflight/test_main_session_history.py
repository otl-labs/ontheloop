"""main()-level wiring of session_history into the preflight report (T3.20 c6)."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.fixtures.session_preflight_module import preflight as _preflight

_OK = {
    "status": "ok",
    "window_days": 2,
    "recent_sessions": ["2026-10-28T09:00Z state=closed outcome=success duration=60s last=2026-10-28T09:01Z session=abc12345"],
    "last_session": "2026-10-28T09:00Z state=closed outcome=success duration=60s last=2026-10-28T09:01Z session=abc12345",
}


def _run(tmp_path: Path, *, creds: str, history: object | None = None, warm_sync: object | None = None) -> dict:
    report_path = tmp_path / ".preflight-report.json"
    with ExitStack() as stack:
        enter = stack.enter_context
        enter(patch("scripts.preflight.env_git.check_venv", return_value=True))
        enter(patch("scripts.preflight.env_git.get_git_status", return_value=("claude/test", False, [])))
        enter(patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False))
        enter(patch("scripts.preflight.aws_infra.check_credentials", return_value=creds))
        enter(patch("scripts.preflight.aws_infra._handle_credentials_startup", return_value=creds))
        enter(patch("scripts.preflight.aws_infra._prime_reader_url"))
        if history is not None:
            enter(patch("scripts.preflight.session_history.read_session_history", **history))
        if warm_sync is not None:
            enter(patch("scripts.sync.ops.warm_sync", side_effect=warm_sync))
        enter(patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None))
        enter(patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None))
        enter(patch("session_preflight.platform_roadmap.compute_state_dict", return_value={}))
        enter(patch("session_preflight.PREFLIGHT_REPORT", report_path))
        enter(patch("builtins.print"))
        _preflight.main()
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_unavailable_history_writes_null_never_empty(tmp_path: Path) -> None:
    unavailable = {"status": "unavailable", "window_days": 2, "recent_sessions": None, "last_session": None, "reason": "x"}
    report = _run(tmp_path, creds="ok", history={"return_value": unavailable})
    assert report["last_session"] is None
    assert report["context"]["recent_sessions"] is None
    assert report["session_history"] == {"status": "unavailable", "window_days": 2, "reason": "x"}


def test_ok_history_fills_both_fields(tmp_path: Path) -> None:
    report = _run(tmp_path, creds="ok", history={"return_value": _OK})
    assert report["last_session"] == _OK["last_session"]
    assert report["context"]["recent_sessions"] == _OK["recent_sessions"]
    assert report["session_history"] == {"status": "ok", "window_days": 2}


def test_creds_degraded_makes_no_reader_call(tmp_path: Path) -> None:
    reader = MagicMock()
    with patch("scripts.preflight._common._make_event_reader", return_value=reader):
        report = _run(tmp_path, creds="unavailable")
    reader.named.assert_not_called()
    assert report["last_session"] is None
    assert report["context"]["recent_sessions"] is None
    assert report["session_history"]["status"] == "unavailable"
    assert report["session_history"]["reason"] == "credentials unavailable"


def test_read_starts_before_warm_sync(tmp_path: Path) -> None:
    order: list[str] = []
    started = MagicMock()

    def fake_start(*a: object, **k: object) -> MagicMock:
        order.append("history_started")
        started.result.return_value = _OK
        return started

    def fake_warm() -> dict:
        order.append("warm_sync")
        return {"pulled": {}, "rows": {}, "reader_ok": {}}

    with patch("scripts.preflight.session_history.start_session_history", side_effect=fake_start):
        _run(tmp_path, creds="ok", warm_sync=fake_warm)
    assert order[:2] == ["history_started", "warm_sync"]
    started.result.assert_called_once()
