"""The capture hook registrations in .claude/settings.json (rule HK-1) and the SessionStart pin parity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.turn_capture.observations import hook_name
from src.turn_capture.provenance import pin_facts
from src.turn_capture.transcript import PIN_SCRIPT_STEM, MemTree, hook_script, project_ref_pin
from src.turn_capture.witness import is_witness_record

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
PIN_CMD = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=ontheloop'
STOP_CMD = '"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/stop_log_turn.py"'
END_CMD = '"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/session_end_log_turn.py"'
CANARY_CMD = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/hook_canary.sh"'
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
    expected = {
        "Stop": [STOP_CMD],
        "StopFailure": [STOP_CMD],
        "UserPromptSubmit": [STOP_CMD, CANARY_CMD],
        "SessionEnd": [END_CMD],
    }
    for event in expected:
        assert [h["command"] for h in _entries(event)] == expected[event]
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


PLUGIN_HOOKS = ROOT / ".claude" / "hooks" / "hooks.json"
STATUS_MESSAGES = {
    "SessionStart": "session_start_log_turn.sh --project-ref=ontheloop",
    "Stop": "stop_log_turn.py",
    "StopFailure": "stop_log_turn.py",
    "UserPromptSubmit": "stop_log_turn.py",
    "SessionEnd": "session_end_log_turn.py",
}
WITNESS_STDERR = "[stop_log_turn] turn witness\n"
PIN_STDERR = "[session_start_log_turn] capture pin checkout=" + "a" * 40 + " base=" + "b" * 40 + " dirty=false\n"


def _capture_status(hooks: dict[str, list[dict[str, Any]]]) -> list[tuple[str, str | None]]:
    return sorted(
        (event, h.get("statusMessage"))
        for event, groups in hooks.items()
        for group in groups
        for h in group["hooks"]
        if any(stem in h["command"] for stem in CAPTURE)
    )


def _with_status(hooks: dict[str, list[dict[str, Any]]]) -> list[tuple[str, str]]:
    return sorted((e, h["statusMessage"]) for e, g in hooks.items() for grp in g for h in grp["hooks"] if "statusMessage" in h)


def test_capture_hooks_carry_their_status_message() -> None:
    expected = sorted(STATUS_MESSAGES.items())
    plugin = json.loads(PLUGIN_HOOKS.read_text(encoding="utf-8"))["hooks"]
    for hooks in (_hooks(), plugin):
        assert _capture_status(hooks) == expected
        assert _with_status(hooks) == expected
    command_ref = [t for t in PIN_CMD.split() if t.startswith("--project-ref=")]
    assert command_ref and STATUS_MESSAGES["SessionStart"].endswith(command_ref[0])


def _attachment(event: str, command: str, stderr: str) -> dict[str, Any]:
    return {"type": "hook_success", "hookName": f"{event}:startup", "hookEvent": event, "command": command, "stderr": stderr}


def _pin_tree(command: str) -> MemTree:
    records = [
        {
            "type": "attachment",
            "uuid": "h1",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "attachment": _attachment("SessionStart", command, PIN_STDERR),
        },
        {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:01.000Z", "message": {"content": "hi"}},
    ]
    return MemTree({f"{SID}.jsonl": "".join(json.dumps(r) + "\n" for r in records)})


def test_the_recorded_status_message_satisfies_the_parser_rules() -> None:
    names = {event: hook_name({"command": message}) for event, message in STATUS_MESSAGES.items()}
    assert names == {
        "SessionStart": "hook:session_start_log_turn",
        "Stop": "hook:stop_log_turn",
        "StopFailure": "hook:stop_log_turn",
        "UserPromptSubmit": "hook:stop_log_turn",
        "SessionEnd": "hook:session_end_log_turn",
    }
    pin = STATUS_MESSAGES["SessionStart"]
    assert project_ref_pin(_pin_tree(pin)) == "ontheloop"
    assert hook_script(pin)[0] == PIN_SCRIPT_STEM
    assert pin_facts(_attachment("SessionStart", pin, PIN_STDERR)) == ("a" * 40, "b" * 40, False)
    stop = STATUS_MESSAGES["Stop"]
    record = {"type": "attachment", "attachment": _attachment("Stop", stop, WITNESS_STDERR)}
    assert is_witness_record(record)
    label = "command hook 9"
    assert project_ref_pin(_pin_tree(label)) is None
    assert not is_witness_record({"type": "attachment", "attachment": _attachment("Stop", label, WITNESS_STDERR)})
