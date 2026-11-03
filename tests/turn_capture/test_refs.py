"""Ref and external_ref grammar builders."""

from __future__ import annotations

from src.telemetry.identity import canonical_ref
from src.turn_capture import refs


def test_every_ref_passes_canonical_ref() -> None:
    built = [
        refs.root_session_ref("sess"),
        refs.subagent_session_ref("sess", "agent"),
        refs.turn_entity_ref("sess:agent", "prompt"),
        refs.role_ref("uuid-1", 3, "tool_call_open"),
        refs.transcript_ref("uuid-1", 0),
        refs.full_output_ref("uuid-1", 2),
        refs.model_call_ref("msg_01"),
        refs.session_marker_ref("sess"),
        refs.session_marker_ref("sess", "resume"),
    ]
    for ref in built:
        assert canonical_ref(ref) == ref


def test_grammar_strings() -> None:
    assert refs.subagent_session_ref("s", "a") == "s:a"
    assert refs.turn_entity_ref("s", "p") == "s/p"
    assert refs.role_ref("u", 2, "turn_close") == "u#2/turn_close"
    assert refs.transcript_ref("u", 4) == "u#4"
    assert refs.full_output_ref("u", 1) == "u#1/full"
    assert refs.model_call_ref("m") == "m/model_call"
    assert refs.session_marker_ref("s") == "s#0/open"
    assert refs.session_marker_ref("s", "compact") == "s#0/compact"
    assert refs.gate_event_ref("u", 2, 3) == "u#2/gate_event.3"


def test_role_vocabulary() -> None:
    assert refs.ROLE_SESSION == ("open", "resume", "compact", "close")
    assert "process_event" in refs.ROLE_OBSERVATION and "agent_close" in refs.ROLE_AGENT
    assert "gate_event" in refs.ROLE_OBSERVATION
    assert refs.ROLE_TRANSCRIPT == ("full",)
