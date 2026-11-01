"""STATUS lines from postflight --metrics and --evidence: stdout and exit codes unchanged."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.postflight import _common
from scripts.session import postflight_evidence
from src.turn_capture.markers import parse_markers
from tests.fixtures.session_postflight_module import postflight as _postflight


def _result(code: int) -> MagicMock:
    result = MagicMock()
    result.returncode = code
    result.stdout = "out"
    result.stderr = ""
    return result


def _statuses(err: str) -> list[str]:
    return [m.signature for m in parse_markers(err)]


def _run_metrics(codes: list[int]) -> int:
    with (
        patch.object(_common, "_run", side_effect=[_result(c) for c in codes]),
        patch("scripts.postflight.housekeeping.prune_telemetry_logs", return_value={"pruned": [], "skipped": []}),
    ):
        return _postflight.run_metrics()


@pytest.mark.parametrize(
    ("codes", "expected", "reason"),
    [
        ([0, 0], "postflight.metrics.done", None),
        ([1, 0], "postflight.metrics.failed", "reason=metrics_exit_nonzero"),
        ([0, 1], "postflight.metrics.failed", "reason=plan_audit_exit_nonzero"),
    ],
)
def test_metrics_reports_child_outcome_and_still_returns_zero(
    capsys: pytest.CaptureFixture[str], codes: list[int], expected: str, reason: str | None
) -> None:
    rc = _run_metrics(codes)
    captured = capsys.readouterr()
    assert rc == 0
    assert "metrics" in json.loads(captured.out)
    assert _statuses(captured.err) == [expected], "unexpected status lines"
    assert reason is None or reason in captured.err


def test_metrics_exception_emits_failed_and_raises(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(_common, "_run", side_effect=[_result(0), _result(0)]),
        patch("scripts.postflight.housekeeping.prune_telemetry_logs", side_effect=OSError("disk")),
        pytest.raises(OSError),
    ):
        _postflight.run_metrics()
    captured = capsys.readouterr()
    assert _statuses(captured.err) == ["postflight.metrics.failed"], "unexpected status lines"
    assert "reason=metrics_error" in captured.err


def _valid_evidence() -> dict[str, list[str]]:
    return {category: [] for category in postflight_evidence.CATEGORIES}


def test_evidence_done_on_valid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "evidence.json"
    source.write_text(json.dumps(_valid_evidence()), encoding="utf-8")
    with patch.object(postflight_evidence, "write"):
        rc = _postflight.run_evidence(str(source))
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("## ")
    assert _statuses(captured.err) == ["postflight.evidence.done"], "unexpected status lines"


def test_evidence_failed_on_invalid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "evidence.json"
    source.write_text(json.dumps({"nope": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        _postflight.run_evidence(str(source))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _statuses(captured.err) == ["postflight.evidence.failed"], "unexpected status lines"
    assert "reason=invalid_evidence" in captured.err


def test_evidence_write_error_emits_failed_and_raises(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "evidence.json"
    source.write_text(json.dumps(_valid_evidence()), encoding="utf-8")
    with patch.object(postflight_evidence, "write", side_effect=OSError("disk")), pytest.raises(OSError):
        _postflight.run_evidence(str(source))
    assert "reason=evidence_error" in capsys.readouterr().err


def test_auto_stderr_carries_the_metrics_line(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    close_out = json.dumps({"sanity_status": "PASS", "details": {}})

    def fake_close() -> int:
        print(close_out)
        return 0

    def fake_push() -> int:
        print(json.dumps({"status": "merged", "pr_url": "https://github.com/pr/1"}))
        return 0

    path = tmp_path / "implementation-retrospective.json"
    path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")
    with (
        patch.object(postflight_evidence, "EVIDENCE_PATH", path),
        patch.object(_common, "_run", side_effect=[_result(0), _result(0)]),
        patch("scripts.postflight.housekeeping.prune_telemetry_logs", return_value={"pruned": [], "skipped": []}),
        patch("session_postflight.run_close", side_effect=fake_close),
        patch("session_postflight.run_commit", return_value=0),
        patch("session_postflight.run_rebase", return_value="ok"),
        patch("session_postflight.run_validate", return_value=0),
        patch("session_postflight.run_verify_head", return_value=0),
        patch("scripts.postflight.remote.run_push", side_effect=fake_push),
        patch("scripts.ops_data_portal.sync", return_value={"pulled": {}}),
        patch("scripts.postflight.housekeeping._stage_document_derived_tables"),
    ):
        _postflight.run_auto("feat: test", 0, 0)
    assert "postflight.metrics.done" in _statuses(capsys.readouterr().err)
