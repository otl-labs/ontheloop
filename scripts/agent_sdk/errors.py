"""Typed exceptions for the ducklake_reader boundary's error responses (Decision 55 fail-loud).

The reader Lambda (src/lambdas/ducklake_reader/handler.py) returns these failure shapes:
  HTTP 400 {"ok": false, "error": ..., "actions": [...]}        -- the action name itself is unknown.
  HTTP 400 {"ok": false, "error_type": "named_read_input", ...} -- a named verb refused caller input
                                                                    (a bad bind or an error() guard).
  HTTP 400 {"ok": false, "error_type": "bad_request", ...}      -- the body is not a JSON object.
  HTTP 413 {"ok": false, "error_type": "response_too_large"...} -- the response exceeded the size cap.
  HTTP 500 {"ok": false, "error_type": "version_mismatch", ...} -- rt.VersionMismatchError.
  HTTP 500 {"ok": false, "error_type": "runtime", ...}          -- rt.DuckLakeRuntimeError (unknown
                                                                    verb, bad params, unknown table).
  HTTP 500 {"ok": false, "error_type": "unmapped", ...}         -- an exception no row maps (class + request id).
  HTTP 503 {"ok": false, "error_type": "catalog_unavailable"...} -- a dead catalog connection (retryable).
  HTTP 504 {"ok": false, "error_type": "read_deadline_exceeded"...} -- an event-journal read hit the reader's deadline.
src.common.ducklake_reader_client.DuckLakeReader raises ReaderInvokeError (carrying the HTTP status and the
parsed JSON body) for any of these; map_reader_error() below maps that onto one of the types here,
derived from what the handler actually returns -- not the writer's envelope shape. A shape this
mapping does not recognise maps to ReaderBoundaryError, a loud generic, rather than being swallowed.
"""

from __future__ import annotations

from src.common.ducklake_reader_client import ReaderInvokeError


class AgentToolError(RuntimeError):
    """Base class for every typed error this module raises."""


class UnknownActionError(AgentToolError):
    """The reader rejected the action name itself (HTTP 400, no `error_type` field)."""

    def __init__(self, message: str, actions: list[str]) -> None:
        self.actions = actions
        super().__init__(message)


class VersionMismatchError(AgentToolError):
    """The reader's `error_type` was `"version_mismatch"` (rt.VersionMismatchError server-side)."""


class ReaderRuntimeError(AgentToolError):
    """The reader's `error_type` was `"runtime"` (rt.DuckLakeRuntimeError server-side --

    unknown verb, a param mismatch, or an unknown ops table).
    """


class ReaderInputError(AgentToolError):
    """The reader's `error_type` was `"named_read_input"`: the caller can fix its input (a bad bind or an error() guard)."""


class ReaderUnavailableError(AgentToolError):
    """The reader's `error_type` was `"catalog_unavailable"`: a dead catalog connection, transient, retry later."""


class ReaderDeadlineError(AgentToolError):
    """The reader's `error_type` was `"read_deadline_exceeded"`: the read was interrupted at its deadline, never retried."""


class ReaderBoundaryError(AgentToolError):
    """An unrecognised reader failure shape. Never silently swallowed (Decision 55)."""


def map_reader_error(exc: ReaderInvokeError) -> AgentToolError:
    """Map *exc* onto one of this module's typed exceptions.

    Falls through to ReaderBoundaryError for any shape this mapping does not recognise (a missing
    or non-JSON body, an unexpected error_type, or a 400 without an `actions` list) -- an
    unrecognised error must surface loudly, never as a silent default or a swallowed exception.
    """
    body = exc.body
    if not isinstance(body, dict):
        return ReaderBoundaryError(str(exc))

    error_type = body.get("error_type")
    if error_type == "version_mismatch":
        return VersionMismatchError(str(body.get("error") or exc))
    if error_type == "runtime":
        return ReaderRuntimeError(str(body.get("error") or exc))
    if error_type == "named_read_input":
        return ReaderInputError(str(body.get("error") or exc))
    if error_type == "catalog_unavailable":
        return ReaderUnavailableError(str(exc))
    if error_type == "read_deadline_exceeded":
        return ReaderDeadlineError("the read hit the reader's deadline before finishing: narrow the window and retry")
    if error_type == "unmapped":
        return ReaderBoundaryError(
            f"reader unmapped error: error_class={body.get('error_class')} request_id={body.get('request_id')}"
        )
    if exc.status == 400 and isinstance(body.get("actions"), list):
        return UnknownActionError(str(body.get("error") or exc), list(body["actions"]))

    return ReaderBoundaryError(str(exc))
