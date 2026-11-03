"""The capture hook registrations in .claude/settings.json (rule HK-1) and the SessionStart pin parity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.turn_capture.observations import hook_name
from src.turn_capture.transcript import MemTree, project_ref_pin

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
PIN_CMD = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=ontheloop'
STOP_CMD = '"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/stop_log_turn.py"'
END_CMD = '"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/session_end_log_turn.py"'
CAPTURE = ("session_start_log_turn", "stop_log_turn", "session_end_log_turn")
SID = "aaaaaaaa-0000-4000-8000-000000000001"


def _hooks() -> dict[str, list[dict[str, Any]]]:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]


def _entries(event: str) -> list[dict[str, Any]]:
    return [h for group in _hooks().get(event, []) for h in group["hooks"]]


def _capture_entries() -> list[tuple[str, dict[str, Any]]]:
    return [(e, h) for e in _hooks() for h in _entries(e) if any(stem in h["command"] for stem in CAPTURE)]


def test_the_four_capture_hooks_are_registered() -> None:
    capture = [(event, h["command"]) for event, h in _capture_entries()]
    assert sorted(capture) == sorted(
        [
            ("SessionStart", PIN_CMD),
            ("Stop", STOP_CMD),
            ("StopFailure", STOP_CMD),
            ("UserPromptSubmit", STOP_CMD),
            ("SessionEnd", END_CMD),
        ]
    )
    for event in ("Stop", "StopFailure", "UserPromptSubmit", "SessionEnd"):
        assert [h["command"] for h in _entries(event)] == [END_CMD if event == "SessionEnd" else STOP_CMD]
    for event, _ in _capture_entries():
        assert all("matcher" not in group for group in _hooks()[event])


def test_only_session_end_carries_a_timeout_and_nothing_is_async() -> None:
    for event, entry in _capture_entries():
        assert entry["type"] == "command" and "async" not in entry
        assert entry.get("timeout") == (60 if event == "SessionEnd" else None)
    assert all("async" not in h for event in _hooks() for h in _entries(event))


def test_existing_hooks_keep_their_order_and_the_pin_group_is_appended() -> None:
    groups = _hooks()["SessionStart"]
    assert [h["command"] for h in groups[-1]["hooks"]] == [PIN_CMD]
    earlier = [h["command"] for g in groups[:-1] for h in g["hooks"]]
    assert earlier and earlier[0] == "bash .claude/hooks/session_start_aws.sh"
    assert not any(stem in c for c in earlier for stem in CAPTURE)
    assert list(_hooks())[:2] == ["SessionStart", "PreToolUse"]


def test_capture_commands_resolve_every_path_from_the_project_dir() -> None:
    for _, entry in _capture_entries():
        paths = [t.strip('"') for t in entry["command"].split() if "/" in t and not t.startswith("--")]
        assert paths and all(p.startswith("$CLAUDE_PROJECT_DIR/") for p in paths)


def test_no_literal_clone_path_in_the_capture_runtime() -> None:
    files = [ROOT / ".claude" / "hooks" / name for name in ("session_start_log_turn.sh", "stop_log_turn.py")]
    files += [ROOT / ".claude" / "hooks" / "session_end_log_turn.py", ROOT / "config" / "telemetry" / "capture.yaml"]
    files += sorted((ROOT / "scripts" / "telemetry_capture").glob("*.py"))
    for path in files:
        assert "/home/user/" not in path.read_text(encoding="utf-8"), path


def test_pin_parity_with_the_registered_session_start_command() -> None:
    attachment = {"type": "hook_success", "hookName": "SessionStart:startup", "hookEvent": "SessionStart", "command": PIN_CMD}
    record = {"type": "attachment", "uuid": "h1", "timestamp": "2026-01-01T00:00:00.000Z", "attachment": attachment}
    prompt = {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:01.000Z", "message": {"content": "hi"}}
    body = "".join(json.dumps(r) + "\n" for r in (record, prompt))
    assert project_ref_pin(MemTree({f"{SID}.jsonl": body})) == "ontheloop"


def test_capture_commands_name_their_scripts() -> None:
    names = {event: hook_name({"command": entry["command"]}) for event, entry in _capture_entries()}
    assert names == {
        "SessionStart": "hook:session_start_log_turn",
        "Stop": "hook:stop_log_turn",
        "StopFailure": "hook:stop_log_turn",
        "UserPromptSubmit": "hook:stop_log_turn",
        "SessionEnd": "hook:session_end_log_turn",
    }
