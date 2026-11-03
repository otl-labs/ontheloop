"""STATUS lines emitted by the ops portal CLI recording modes (--file-rec, --update-rec): stdout and exit codes unchanged."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.ops_portal.ci_rca_lifecycle import ChainRecord
from src.turn_capture.markers import parse_markers
from tests.fixtures.ops_portal_records import VALID_FIELDS as _VALID_FIELDS

_FINGERPRINT = "b" * 64
_CTX_V2 = {"schema_version": 2, "fingerprint": _FINGERPRINT}


def _argv(**overrides: str) -> list[str]:
    values = {
        "title": _VALID_FIELDS["title"],
        "file": _VALID_FIELDS["file"],
        "context": _VALID_FIELDS["context"],
        "acceptance": _VALID_FIELDS["acceptance"],
        "effort": _VALID_FIELDS["effort"],
        "priority": _VALID_FIELDS["priority"],
        "source": _VALID_FIELDS["source"],
        **overrides,
    }
    argv = ["--file-rec"]
    for flag, value in values.items():
        argv.extend([f"--{flag}", value])
    return argv


def _ci_rca_argv() -> list[str]:
    return _argv(source="ci_rca", **{"context-v2-json": json.dumps(_CTX_V2)})


def _statuses(err: str) -> list[tuple[str, str | None]]:
    return [(m.signature, m.result) for m in parse_markers(err)]


def _main(argv: list[str]) -> int:
    from scripts.ops_data_portal import main

    return main(argv)


def test_insert_emits_done(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-700"}):
        rc = _main(_argv())
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "rec-700\n"
    assert _statuses(captured.err) == [("ops_portal.file_rec.done", "done")], "unexpected status lines"


def test_open_duplicate_emits_skipped(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
    with (
        patch("scripts.ops_data_portal._run_ci_rca_cross_check"),
        patch("scripts.ops_data_portal.get_ci_rca_strict_mode", return_value="warn"),
        patch("scripts.ops_data_portal.find_open_ci_rca_rec_by_fingerprint", return_value="rec-55"),
        patch("scripts.ops_data_portal.bump_ci_rca_occurrence"),
        patch("scripts.ops_data_portal._ducklake_write") as write,
    ):
        rc = _main(_ci_rca_argv())
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "rec-55\n"
    assert _statuses(captured.err) == [("ops_portal.file_rec.skipped", "skipped")], "unexpected status lines"
    assert "reason=ci_rca_open_duplicate" in captured.err
    write.assert_not_called()


def test_stale_rerun_emits_skipped(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
    head = ChainRecord(rec_id="rec-777", status="closed", fixed_by_sha="fixed", last_touched="")
    with (
        patch("scripts.ops_data_portal._run_ci_rca_cross_check"),
        patch("scripts.ops_data_portal.get_ci_rca_strict_mode", return_value="warn"),
        patch("scripts.ops_data_portal.find_open_ci_rca_rec_by_fingerprint", return_value=None),
        patch("scripts.ops_data_portal.closed_head_of_chain", return_value=head),
        patch("scripts.ops_data_portal.current_commit_sha", return_value="failing"),
        patch("scripts.ops_data_portal.classify_closed_head", return_value="drop"),
        patch("scripts.ops_data_portal._ducklake_write") as write,
    ):
        rc = _main(_ci_rca_argv())
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "rec-777\n"
    assert _statuses(captured.err) == [("ops_portal.file_rec.skipped", "skipped")], "unexpected status lines"
    assert "reason=ci_rca_stale_rerun" in captured.err
    write.assert_not_called()


def test_missing_required_emits_failed(capsys: pytest.CaptureFixture[str]) -> None:
    rc = _main(["--file-rec", "--title", "only a title"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _statuses(captured.err) == [("ops_portal.file_rec.failed", "failed")], "unexpected status lines"
    assert "reason=missing_required" in captured.err


def test_invalid_fields_emit_failed(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal._ducklake_write") as write:
        rc = _main(_argv(title="x"))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _statuses(captured.err) == [("ops_portal.file_rec.failed", "failed")], "unexpected status lines"
    assert "reason=invalid_fields" in captured.err
    write.assert_not_called()


def test_invalid_context_v2_json_emits_failed(capsys: pytest.CaptureFixture[str]) -> None:
    rc = _main(_argv(**{"context-v2-json": "{not json"}))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _statuses(captured.err) == [("ops_portal.file_rec.failed", "failed")], "unexpected status lines"
    assert "reason=invalid_context_v2_json" in captured.err


def test_file_rec_writer_error_emits_failed_and_raises(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal._ducklake_write", side_effect=RuntimeError("boom")), pytest.raises(RuntimeError):
        _main(_argv())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _statuses(captured.err) == [("ops_portal.file_rec.failed", "failed")], "unexpected status lines"
    assert "reason=writer_error" in captured.err


def test_update_rec_done(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal.update_rec"):
        rc = _main(["--update-rec", "rec-1", "--status", "closed"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "Updated rec-1: status\n"
    assert _statuses(captured.err) == [("ops_portal.update_rec.done", "done")], "unexpected status lines"


def test_update_rec_no_field_emits_failed(capsys: pytest.CaptureFixture[str]) -> None:
    rc = _main(["--update-rec", "rec-1"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _statuses(captured.err) == [("ops_portal.update_rec.failed", "failed")], "unexpected status lines"
    assert "reason=invalid_update" in captured.err


def test_update_rec_rejected_flag_emits_failed(capsys: pytest.CaptureFixture[str]) -> None:
    rc = _main(["--update-rec", "rec-1", "--effort", "S"])
    captured = capsys.readouterr()
    assert rc == 1
    assert _statuses(captured.err) == [("ops_portal.update_rec.failed", "failed")], "unexpected status lines"


def test_update_rec_validation_error_emits_failed(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal.update_rec", side_effect=ValueError("bad")):
        rc = _main(["--update-rec", "rec-1", "--status", "closed"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "reason=invalid_update" in captured.err


def test_update_rec_writer_error_emits_failed_and_raises(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal.update_rec", side_effect=RuntimeError("boom")), pytest.raises(RuntimeError):
        _main(["--update-rec", "rec-1", "--status", "closed"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _statuses(captured.err) == [("ops_portal.update_rec.failed", "failed")], "unexpected status lines"
    assert "reason=writer_error" in captured.err


def test_emitted_lines_parse_as_status_markers(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-700"}):
        _main(_argv())
    markers = parse_markers(capsys.readouterr().err)
    assert [m.source for m in markers] == ["status"], "the emitted line did not parse as a status marker"


def test_unknown_non_insert_reason_emits_failed(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.ops_portal.outcomes import FileRecOutcome

    with patch("scripts.ops_data_portal.file_rec_with_outcome", return_value=FileRecOutcome("rec-9", False, "new_reason")):
        rc = _main(_argv())
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "rec-9\n"
    assert _statuses(captured.err) == [("ops_portal.file_rec.failed", "failed")], "unexpected status lines"
    assert "reason=unknown_outcome" in captured.err
