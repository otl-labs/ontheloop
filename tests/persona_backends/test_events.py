import hashlib
import json
import os

from src.persona_backends import events as ev
from tests.persona_backends import fakes


def test_header_turns_and_end():
    seen = []
    transport = fakes.ScriptedTransport([fakes.tool({"blob": "notes", "mode": "lines"}), fakes.final()])
    result, events = fakes.drive(transport, on_event=seen.append)
    head, first, second, end = events
    assert result["outcome"] == "success" and seen == events
    assert head["persona"] == "fixture_persona" and isinstance(head["started_at_ms"], int)
    assert head["rendered_prompt_sha256"] == hashlib.sha256(fakes.PROMPT.read_bytes()).hexdigest()
    assert first["kind"] == "tool" and first["tool_calls"] == ["read_input"] and first["attempts"][0]["outcome"] == "ok"
    assert second["kind"] == "terminal_valid" and second["record"]["tokens_input"] == 10
    assert end["outcome"] == "success" and end["turns_used"] == 2


def test_write_atomic_leaves_no_tmp_and_whole_document(tmp_path):
    path = tmp_path / "events.json"
    head = ev.header("c", "p", 1, "h", "i", 1)
    ev.write_atomic(path, head, [], None)
    assert json.loads(path.read_text())["end"] is None
    ev.write_atomic(path, head, [{"turn": 1}], ev.end_record("success", None, 1, 0, 2))
    assert json.loads(path.read_text())["end"]["turns_used"] == 1
    assert os.listdir(tmp_path) == ["events.json"]


def test_end_record_carries_error_only_when_given():
    assert "error" not in ev.end_record("success", None, 1, 0, 2)
    assert ev.end_record("aborted", None, 1, 0, 2, "outage")["error"] == "outage"
