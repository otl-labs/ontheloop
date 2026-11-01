"""rec-4598: --close and --auto report only the effects they perform (no SESSION_LOG write, no intent check)."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.session_postflight_module import postflight as _postflight


def _help_text(capsys: pytest.CaptureFixture) -> str:
    with patch.object(sys, "argv", ["postflight", "--help"]), pytest.raises(SystemExit):
        _postflight.main()
    return capsys.readouterr().out


def test_close_output_claims_no_session_log_write(capsys: pytest.CaptureFixture) -> None:
    diff_result = MagicMock(returncode=0, stdout="1 file changed", stderr="")
    sanity_result = MagicMock(returncode=0, stdout=json.dumps({"status": "PASS"}), stderr="")
    with (
        patch("scripts.postflight._common.find_plan_file", return_value=None),
        patch("scripts.postflight._common._run", side_effect=[diff_result, sanity_result]),
    ):
        _postflight.run_close()
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"sanity_status", "details"}
    assert set(data["details"]) == {"diff_summary", "plan_file"}
    doc = _postflight.run_close.__doc__ or ""
    assert "SESSION_LOG" not in doc and "ntent" not in doc
    help_text = _help_text(capsys)
    assert "--close" in help_text
    assert "SESSION_LOG" not in help_text and "ntent verification" not in help_text
    assert "SESSION_LOG" not in (_postflight.__doc__ or "")


def test_auto_results_claim_no_session_log_write(capsys: pytest.CaptureFixture) -> None:
    def fake_close() -> int:
        print(json.dumps({"sanity_status": "FAIL", "details": {}}))
        return 0

    with patch.object(_postflight, "run_close", side_effect=fake_close):
        _postflight.run_auto("msg")
    out = capsys.readouterr().out
    assert "session_log_entry" not in out and "intent_achieved" not in out
    assert "sanity_failed" in out
    assert "SESSION_LOG" not in (_postflight.run_auto.__doc__ or "")
