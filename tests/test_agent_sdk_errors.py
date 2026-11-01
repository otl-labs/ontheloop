"""Tests for scripts/agent_sdk/errors.py -- the typed reader-boundary exception mapping."""

from __future__ import annotations

from scripts.agent_sdk.errors import (
    AgentToolError,
    ReaderBoundaryError,
    ReaderDeadlineError,
    ReaderInputError,
    ReaderRuntimeError,
    ReaderUnavailableError,
    UnknownActionError,
    VersionMismatchError,
    map_reader_error,
)
from src.common.ducklake_reader_client import ReaderInvokeError


def _invoke_error(status: int | None, body: dict | None, action: str = "describe") -> ReaderInvokeError:
    return ReaderInvokeError(action, status, "" if body is None else str(body), body)


def test_version_mismatch_maps_to_version_mismatch_error():
    exc = _invoke_error(500, {"ok": False, "error_type": "version_mismatch", "error": "schema drift"})
    mapped = map_reader_error(exc)
    assert isinstance(mapped, VersionMismatchError)
    assert "schema drift" in str(mapped)


def test_runtime_error_type_maps_to_reader_runtime_error():
    exc = _invoke_error(500, {"ok": False, "error_type": "runtime", "error": "unknown read verb 'bogus'"})
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderRuntimeError)
    assert "unknown read verb" in str(mapped)


def test_unknown_action_maps_to_unknown_action_error():
    exc = _invoke_error(400, {"ok": False, "error": "unknown action 'bogus'", "actions": ["describe", "named_read"]})
    mapped = map_reader_error(exc)
    assert isinstance(mapped, UnknownActionError)
    assert mapped.actions == ["describe", "named_read"]


def test_unrecognised_shape_maps_to_boundary_error_not_swallowed():
    """An error_type this mapping does not recognise must surface loudly, not silently default."""
    exc = _invoke_error(500, {"ok": False, "error_type": "something_new", "error": "boom"})
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderBoundaryError)


def test_non_json_body_maps_to_boundary_error():
    exc = _invoke_error(502, None)
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderBoundaryError)


def test_400_without_actions_list_maps_to_boundary_error():
    """A 400 body missing the `actions` list is an unrecognised shape, not assumed UnknownActionError."""
    exc = _invoke_error(400, {"ok": False, "error": "something else"})
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderBoundaryError)


def test_all_typed_errors_are_agent_tool_errors():
    for cls in (VersionMismatchError, ReaderRuntimeError, UnknownActionError, ReaderBoundaryError):
        assert issubclass(cls, AgentToolError)
    assert issubclass(AgentToolError, RuntimeError)


def test_unknown_action_error_message_uses_reader_error_text():
    exc = _invoke_error(400, {"ok": False, "error": "unknown action 'bogus'", "actions": []})
    mapped = map_reader_error(exc)
    assert "unknown action 'bogus'" in str(mapped)


def test_named_read_input_maps_to_reader_input_error():
    exc = _invoke_error(
        400, {"ok": False, "error_type": "named_read_input", "verb": "recs_related", "error": "narrow the query"}
    )
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderInputError) and isinstance(mapped, AgentToolError)
    assert "narrow the query" in str(mapped)


def test_catalog_unavailable_maps_to_reader_unavailable_error():
    exc = _invoke_error(
        503, {"ok": False, "error_type": "catalog_unavailable", "error_class": "OperationalError", "reopened": True}
    )
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderUnavailableError) and isinstance(mapped, AgentToolError)


def test_unmapped_maps_to_boundary_error_naming_class_and_request_id():
    exc = _invoke_error(500, {"ok": False, "error_type": "unmapped", "error_class": "KeyError", "request_id": "req-7"})
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderBoundaryError)
    assert "KeyError" in str(mapped) and "req-7" in str(mapped)


def test_read_deadline_maps_to_reader_deadline_error():
    exc = _invoke_error(
        504,
        {
            "ok": False,
            "error_type": "read_deadline_exceeded",
            "reason": "reader_deadline",
            "request_id": "r",
            "action": "named_read",
        },
    )
    mapped = map_reader_error(exc)
    assert isinstance(mapped, ReaderDeadlineError)
    assert "narrow the window" in str(mapped)
