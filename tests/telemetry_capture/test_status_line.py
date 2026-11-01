from __future__ import annotations

import pytest

from scripts.telemetry_capture.status_line import emit_status
from src.turn_capture.markers import Marker, parse_markers


@pytest.mark.parametrize("result", ["done", "skipped", "failed"])
@pytest.mark.parametrize("reason", [None, "some_reason"])
def test_emits_one_parseable_stderr_line(capsys: pytest.CaptureFixture[str], result: str, reason: str | None) -> None:
    emit_status("demo.mode", result, reason)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert parse_markers(captured.err) == [Marker("status", f"demo.mode.{result}", 0, result)]


@pytest.mark.parametrize(
    ("source", "result", "reason"),
    [
        ("bad source", "done", None),
        ("demo.mode", "weird", None),
        ("demo.mode", "failed", "bad reason"),
        ("x" * 193, "done", None),
    ],
)
def test_bad_argument_writes_diagnostic_without_marker(
    capsys: pytest.CaptureFixture[str], source: str, result: str, reason: str | None
) -> None:
    emit_status(source, result, reason)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()
    assert parse_markers(captured.err) == []


def test_failing_stderr_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def write(self, _text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    monkeypatch.setattr("sys.stderr", _Broken())
    emit_status("demo.mode", "done")
