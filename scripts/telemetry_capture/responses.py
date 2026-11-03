"""RN-7's writer-response table as data, and the value-free halt fields.

classify(status, body) returns the runner's next action: ok, retry, rechunk, register_tenant, redeliver, halt or stop.
The first row whose status matches and whose error_type is None or equals the body's error_type wins; an unlisted status
stops the pass (never halts). retry rows are TR-2's statuses: the transport already retried them within the deadline, so
the runner treats a retry that reaches it as a stop.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

OK, RETRY, RECHUNK, REGISTER_TENANT, REDELIVER, HALT, STOP = (
    "ok",
    "retry",
    "rechunk",
    "register_tenant",
    "redeliver",
    "halt",
    "stop",
)

RESPONSES: tuple[tuple[int, str | None, str], ...] = (
    (200, None, OK),
    (403, "tenant_unresolved", REGISTER_TENANT),
    (403, "project_unregistered", HALT),
    (422, "blob_not_verified", REDELIVER),
    (422, None, HALT),
    (409, None, HALT),
    (400, None, HALT),
    (413, None, RECHUNK),
    (429, None, RETRY),
    (503, None, RETRY),
    (500, None, STOP),
)
DEFAULT = STOP
_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


def error_type(body: Any) -> str | None:
    value = body.get("error_type") if isinstance(body, Mapping) else None
    return value if isinstance(value, str) else None


def classify(status: int, body: Any) -> str:
    kind = error_type(body)
    for row_status, row_error, action in RESPONSES:
        if row_status == status and row_error in (None, kind):
            if action == OK and not (isinstance(body, Mapping) and body.get("ok") is True):
                return STOP
            return action
    return DEFAULT


def _name(value: Any) -> str | None:
    return value if isinstance(value, str) and _NAME.match(value) else None


def halt_fields(status: int, body: Any) -> dict[str, Any]:
    """status, error_type, rule, column and the 0-based row index; declared names only, never a value."""
    data = body if isinstance(body, Mapping) else {}
    row = data.get("row")
    fields = {
        "status": status,
        "error_type": _name(data.get("error_type")),
        "rule": _name(data.get("rule")),
        "column": _name(data.get("column")),
        "row": row if type(row) is int else None,
    }
    return {k: v for k, v in fields.items() if v is not None}
