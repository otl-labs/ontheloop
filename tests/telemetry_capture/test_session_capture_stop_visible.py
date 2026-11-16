"""rec-5204: a persistent capture stop (for example a proxy 502) is visible in the session, as a fixed-token warning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.telemetry_capture import capture_warning as cw
from scripts.telemetry_capture import runner
from scripts.telemetry_capture.state import CaptureState, state_dir
from tests.fixtures.telemetry_capture_writer import Writer, records, run, state, write
from tests.fixtures.turn_capture_corpus import SID, make_files


def _root(tmp_path: Path) -> Path:
    root = tmp_path / f"{SID}.jsonl"
    root.write_text("{}\n", encoding="utf-8")
    return root


def _pass(root: Path, outcome: str = "stopped", token: str | None = "status_502", event: str = "Stop") -> None:
    record: dict[str, Any] = {"event": event, "final": False, "at": 1, "outcome": outcome, "diag": {}}
    if token is not None:
        record["stop_token"] = token
    CaptureState(state_dir(root, root.stem)).append_diagnostic(record)


def _warn(root: Path) -> str | None:
    return cw.prompt_warning(root, root.stem)


def test_three_stopped_passes_warn_once_with_the_stop_token(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for _ in range(3):
        _pass(root)
    out = _warn(root)
    assert out is not None
    text = json.loads(out)["systemMessage"]
    assert "status_502" in text and "3 Stop passes" in text
    assert _warn(root) is None


def test_the_stopped_warning_rearms_after_rearm_every_more_passes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for _ in range(3):
        _pass(root)
    assert _warn(root) is not None
    for _ in range(cw.REARM_EVERY):
        _pass(root)
    assert _warn(root) is not None


def test_two_stopped_passes_or_a_later_sent_pass_stay_silent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for _ in range(2):
        _pass(root)
    assert _warn(root) is None
    _pass(root)
    assert _warn(root) is not None
    _pass(root, "sent", None)
    assert _warn(root) is None
    assert not (state_dir(root, root.stem) / cw.WARNING_MARKER).exists()


def test_a_stopped_pass_records_its_stop_token(tmp_path: Path) -> None:
    write(tmp_path, make_files(records(1)))
    writer = Writer()
    writer.answers = [(502, {"ok": False})]
    result = run(tmp_path, writer)
    assert result.outcome == "stopped" and result.diag["stop_token"] == "status_502"

    def unavailable(env, st, budget, **kw):
        raise runner.CaptureUnavailable("credential_transport")

    assert run(tmp_path, writer, client_factory=unavailable).diag["stop_token"] == "credential_transport"
    state(tmp_path).write_halt({"reason": "x"})
    assert "stop_token" not in run(tmp_path, writer).diag


def test_an_absent_or_malformed_token_warns_as_unknown(tmp_path: Path) -> None:
    for token in (None, "Bad Token http://x.example/123456789012"):
        root = tmp_path / str(token is None) / f"{SID}.jsonl"
        root.parent.mkdir(parents=True)
        root.write_text("{}\n", encoding="utf-8")
        for _ in range(3):
            _pass(root, token=token)
        text = json.loads(_warn(root) or "{}")["systemMessage"]
        assert "(unknown)" in text and "example" not in text and "123456789012" not in text
