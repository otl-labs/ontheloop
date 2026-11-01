"""Rules HG-1..HG-3: the pure twin key and lateness verdicts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.turn_capture.hook_groups import is_late_session_start, late_resume_start, twin_key

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _att(**over) -> dict:
    att = {"toolUseID": "t1", "hookEvent": "SessionStart", "hookName": "SessionStart:startup", "command": "c", "durationMs": 5}
    att.update(over)
    return att


def test_twin_key_needs_the_full_key() -> None:
    base = twin_key(_att())
    assert base == twin_key(_att(stdout="x", heldContent=True))
    for field, other in (("hookEvent", "Stop"), ("hookName", "SessionStart:resume"), ("command", "d"), ("durationMs", 6)):
        assert twin_key(_att(**{field: other})) != base, field
    assert twin_key(_att(toolUseID="t2")) != base


def test_twin_key_is_none_without_a_tool_use_id() -> None:
    assert twin_key(_att(toolUseID=None)) is None
    assert twin_key(_att(toolUseID=7)) is None
    assert twin_key({}) is None


def test_late_startup_is_any_own_prompt_before_it() -> None:
    assert is_late_session_start("SessionStart:startup", True) and is_late_session_start("SessionStart:clear", True)
    assert not is_late_session_start("SessionStart:startup", False)
    assert not is_late_session_start("SessionStart:resume", True) and not is_late_session_start("PreToolUse:Bash", True)


def test_late_resume_compares_hook_start_with_the_latest_reply() -> None:
    reply = T0 + timedelta(seconds=10)
    assert late_resume_start(T0 + timedelta(seconds=12), 5000, reply)
    assert not late_resume_start(T0 + timedelta(seconds=12), 1000, reply)
    assert not late_resume_start(T0 + timedelta(seconds=12), 5000, None)
    assert not late_resume_start(None, 5000, reply)


def test_late_resume_uses_ts_alone_without_duration() -> None:
    reply = T0 + timedelta(seconds=10)
    for duration in (None, "5000", 5.0, True, -5000, 10**20):
        assert late_resume_start(T0 + timedelta(seconds=9), duration, reply) is True
        assert late_resume_start(T0 + timedelta(seconds=11), duration, reply) is False


def test_held_first_record_is_judged_by_the_latest_reply() -> None:
    reply = T0 + timedelta(seconds=10)
    release = T0 + timedelta(seconds=10, milliseconds=500)
    assert late_resume_start(release, 2000, reply) and not late_resume_start(release, 100, reply)
