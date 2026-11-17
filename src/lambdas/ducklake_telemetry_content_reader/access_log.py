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


def _rest_caller(context: Any) -> dict[str, Any] | None:
    identity, authorizer = context.get("identity"), context.get("authorizer")
    has_iam = isinstance(authorizer, dict) and "iam" in authorizer
    if context.get("apiId") and isinstance(identity, dict) and not has_iam:
        return identity
    return None


def _url_caller(context: Any) -> dict[str, Any] | None:
    authorizer = context.get("authorizer")
    iam = authorizer.get("iam") if isinstance(authorizer, dict) else None
    return iam if isinstance(iam, dict) and not context.get("apiId") else None


def caller_fields(lambda_event: Any) -> tuple[str, str] | None:
    """The delivering path's signed (accountId, userArn), or None when absent, mixed or not a string pair.

    A REST AWS_PROXY event (httpMethod present, requestContext.apiId set) carries them under requestContext.identity;
    a Function URL event under requestContext.authorizer.iam. Both shapes at once, or neither, is no caller
    (Decision 255 cl.2: the caller is read from the path that delivered the request, never from a body field).
    """
    try:
        context = lambda_event["requestContext"]
        if not isinstance(context, dict):
            return None
        rest = _rest_caller(context) if lambda_event.get("httpMethod") else None
        url = _url_caller(context)
        fields = rest if rest is not None else url
        if (rest is None) == (url is None) or fields is None:
            return None
        account, arn = fields.get("accountId"), fields.get("userArn")
    except (KeyError, TypeError, AttributeError):
        return None
    return (account, arn) if isinstance(account, str) and account and isinstance(arn, str) and arn else None


def _role(lambda_event: Any) -> str:
    fields = caller_fields(lambda_event)
    if fields is None:
        return "unknown"
    segments = fields[1].split(":", 5)[-1].split("/")
    name = segments[1] if len(segments) > 1 else ""
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
