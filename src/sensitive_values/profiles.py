"""Named profiles: ordered tuples of class ids, each applied once in order."""

from __future__ import annotations

MARKER_FORMAT = "[REDACTED:{cls}]"

_WRITE_TIME = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "BEARER_TOKEN",
)

PROFILES: dict[str, tuple[str, ...]] = {
    "write_time": _WRITE_TIME,
    "read_masked": _WRITE_TIME
    + (
        "URL_CREDENTIAL",
        "PASSWORD_ASSIGNMENT",
        "X_AMZ_PARAM",
        "UNIQUE_ID",
        "ACCOUNT_ID",
        "ASSUMED_ROLE_SESSION",
        "SSO_HUB_PROFILE",
    ),
}
