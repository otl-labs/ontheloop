"""Profile membership and the pinned fingerprints (a class-row or profile change is a reviewed edit here)."""

from __future__ import annotations

from src.sensitive_values import CLASSES, MARKER_FORMAT, PROFILES, fingerprint

PRE_CHANGE_CLASS_ORDER = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "BEARER_TOKEN",
)
ADDED = (
    "URL_CREDENTIAL",
    "PASSWORD_ASSIGNMENT",
    "X_AMZ_PARAM",
    "UNIQUE_ID",
    "ACCOUNT_ID",
    "ASSUMED_ROLE_SESSION",
    "SSO_HUB_PROFILE",
)
PINNED_FINGERPRINTS = {
    "write_time": "write_time@7f9f5851623601e8",
    "read_masked": "read_masked@7877707e166f59ad",
}


def test_write_time_is_the_pre_change_class_order() -> None:
    assert PROFILES["write_time"] == PRE_CHANGE_CLASS_ORDER


def test_read_masked_is_write_time_then_added() -> None:
    assert PROFILES["read_masked"] == PRE_CHANGE_CLASS_ORDER + ADDED
    assert all(class_id in CLASSES for ids in PROFILES.values() for class_id in ids)


def test_marker_format() -> None:
    assert MARKER_FORMAT.format(cls="ACCOUNT_ID") == "[REDACTED:ACCOUNT_ID]"


def test_profile_fingerprints_pinned() -> None:
    assert {name: fingerprint(name) for name in PROFILES} == PINNED_FINGERPRINTS
