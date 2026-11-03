"""Every ref and external_ref grammar builder (the pinned row-role vocabulary); no other module composes a ref."""

from __future__ import annotations

ROLE_SESSION = ("open", "resume", "compact", "close")
ROLE_OBSERVATION = ("turn_open", "turn_close", "tool_call_open", "tool_call_close", "process_event", "gate_event")
ROLE_AGENT = ("agent_open", "agent_close")
ROLE_TRANSCRIPT = ("full",)


def root_session_ref(session_id: str) -> str:
    return session_id


def subagent_session_ref(session_id: str, agent_id: str) -> str:
    return f"{session_id}:{agent_id}"


def turn_entity_ref(session_ref: str, prompt_id: str) -> str:
    return f"{session_ref}/{prompt_id}"


def role_ref(source_record_id: str, block_index: int, role: str) -> str:
    return f"{source_record_id}#{block_index}/{role}"


def transcript_ref(source_record_id: str, block_index: int) -> str:
    return f"{source_record_id}#{block_index}"


def full_output_ref(source_record_id: str, block_index: int) -> str:
    return role_ref(source_record_id, block_index, "full")


def model_call_ref(message_id: str) -> str:
    return f"{message_id}/model_call"


def session_marker_ref(session_ref: str, role: str = "open") -> str:
    return f"{session_ref}#0/{role}"


def gate_event_ref(source_record_id: str, block_index: int, n: int) -> str:
    return role_ref(source_record_id, block_index, f"gate_event.{n}")
