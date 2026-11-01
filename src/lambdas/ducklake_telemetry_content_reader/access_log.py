"""One value-free access-log line per content read (Decision 239 cl.4).

The line carries the assumed-role name (masked), verb, status, session, counts and request id; never an ARN, an account id, a
session name, text or any param beyond a validated session_id.
"""

from __future__ import annotations

import json
from typing import Any

from src.sensitive_values import mask

EVENT = "telemetry_content_read"
_FALLBACK = {"event": EVENT, "role": "unknown", "verb": "unknown", "status": 500, "session_id": None, "rows": 0}


def _role(lambda_event: Any) -> str:
    try:
        arn = str(lambda_event["requestContext"]["authorizer"]["iam"]["userArn"])
        segments = arn.split(":", 5)[-1].split("/")
        name = segments[1] if len(segments) > 1 else ""
    except (KeyError, TypeError):
        return "unknown"
    return mask(name, "read_masked").text if name else "unknown"


def write(
    lambda_event: Any,
    verb: str,
    status: int,
    rows: int,
    masked_bytes: int,
    mask_counts: dict[str, int],
    request_id: str | None,
    session_id: str | None,
) -> None:
    """Print the line; never raises (it runs in the entrypoint's finally and must not replace the typed answer)."""
    try:
        line = {
            "event": EVENT,
            "role": _role(lambda_event),
            "verb": verb,
            "status": status,
            "session_id": session_id,
            "rows": rows,
            "masked_bytes": masked_bytes,
            "mask_counts": mask_counts,
            "request_id": request_id,
        }
        print(json.dumps(line, separators=(",", ":")))
    except Exception:  # noqa: BLE001 -- the fixed fallback names no value
        print(json.dumps(_FALLBACK, separators=(",", ":")))
