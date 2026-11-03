"""Detector class rows (stdlib re only). Each replaced part refuses to start at a marker, so a second pass counts nothing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

_KEY_PREFIXES = "AKIA|ASIA|AIDA|AROA|AGPA|ANPA|ANVA|ASCA|APKA"
_UNIQUE_PREFIXES = "AIDA|AROA|AGPA|ANPA|ANVA|ASCA|APKA"
_CONTEXT_TAIL = r"""\\*"?[ \t]*[:=][ \t]*\\*"?)([A-Za-z0-9/+=_\-]{8,})"""
_NOT_MARKER = r"(?!\[REDACTED:)"
_VALUE = r"(?:'[^']*'|\"[^\"]*\"|[^\s&;,]+)"


class AcceptSpec(NamedTuple):
    name: str
    params: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class DetectorClass:
    id: str
    family: str
    pattern: str
    kind: str
    accept: AcceptSpec | None = None
    flags: int = 0
    regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "regex", re.compile(self.pattern, self.flags))


_ROWS = (
    # scrub.py _AWS_ACCESS
    DetectorClass("AWS_ACCESS_KEY_ID", "aws", rf"(?<![A-Za-z0-9])(?:{_KEY_PREFIXES})[A-Z0-9]{{16}}(?![A-Za-z0-9])", "whole"),
    # scrub.py _AWS_SECRET
    DetectorClass(
        "AWS_SECRET_ACCESS_KEY",
        "aws",
        r"((?:aws_secret_access_key|secretaccesskey)" + _CONTEXT_TAIL,
        "keep_prefix",
        flags=re.IGNORECASE,
    ),
    # scrub.py _AWS_SESSION
    DetectorClass(
        "AWS_SESSION_TOKEN",
        "aws",
        r"((?:aws_session_token|sessiontoken)" + _CONTEXT_TAIL,
        "keep_prefix",
        flags=re.IGNORECASE,
    ),
    # scrub.py _ANTHROPIC
    DetectorClass("ANTHROPIC_API_KEY", "token", r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}", "whole"),
    # scrub.py _GITHUB
    DetectorClass(
        "GITHUB_TOKEN",
        "token",
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{80,})(?![A-Za-z0-9_])",
        "whole",
    ),
    # scrub.py _BEARER, with its long-or-digit heuristic as a declared accept spec
    DetectorClass(
        "BEARER_TOKEN",
        "token",
        r"(\bBearer[ \t]+)([A-Za-z0-9._~+/=\-]{20,})",
        "keep_prefix",
        AcceptSpec("long_or_digit", (("min_len", 32),)),
    ),
    # new: seeded from validate_actions_evidence._SECRET_SHAPES' URL-credential branch, widened to any scheme
    DetectorClass(
        "URL_CREDENTIAL",
        "network",
        rf"""((?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://){_NOT_MARKER}[^/\s@"'<>]+@[^/\s"'<>),;]*""",
        "keep_prefix",
    ),
    # ducklake_handler_errors.py first pattern, keep_prefix so the key stays readable
    DetectorClass(
        "PASSWORD_ASSIGNMENT", "network", rf"(password\s*=\s*){_NOT_MARKER}{_VALUE}", "keep_prefix", flags=re.IGNORECASE
    ),
    # ducklake_handler_errors.py second pattern, keep_prefix
    DetectorClass(
        "X_AMZ_PARAM", "network", rf"(X-Amz-[A-Za-z0-9-]+\s*=\s*){_NOT_MARKER}{_VALUE}", "keep_prefix", flags=re.IGNORECASE
    ),
    # redaction_check.UNIQUE_ID_SHAPE widened to every unique-id prefix in scrub.py's _KEY_PREFIXES except AKIA/ASIA
    DetectorClass(
        "UNIQUE_ID", "identity", rf"(?<![0-9A-Za-z])(?:{_UNIQUE_PREFIXES})[A-Z0-9]{{16,17}}(?![0-9A-Za-z])", "whole"
    ),
    # redaction_check.ACCOUNT_ID_SHAPE verbatim
    DetectorClass("ACCOUNT_ID", "identity", r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])", "whole"),
    # new: no copy carries it; an SSO session name is the person's sign-in name
    DetectorClass(
        "ASSUMED_ROLE_SESSION",
        "identity",
        r"(assumed-role/[A-Za-z0-9+=,.@_\-]+/)[A-Za-z0-9+=,.@_\-]{2,64}",
        "keep_prefix",
    ),
    # .pre-commit-config.yaml block-sensitive-identifiers, third branch verbatim
    DetectorClass("SSO_HUB_PROFILE", "identity", r"\b[a-z]+_information_hub\b", "whole"),
)

CLASSES: dict[str, DetectorClass] = {row.id: row for row in _ROWS}
