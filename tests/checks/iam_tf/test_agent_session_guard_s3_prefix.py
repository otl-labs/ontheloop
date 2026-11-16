"""rec-5232: no Deny carries s3:prefix under a positive string operator and no Allow under a negated one.

ValidatePolicy flags S3_PREFIX_IN_NEGATIVE_CONTEXT: a Deny on a positive s3:prefix match only fires for requests that
send a prefix, so a list with no prefix slips through. Text predicate over the session guard and PlatformDev DailyOps.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.checks.iam_tf._ci_platform_role_iam import _jsonencode_statement_blocks
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _strip_comments

_ROOT = Path(__file__).resolve().parents[3]
_FILES = ("agent_session_user.tf", "platform_dev_policies.tf")
_OPERATOR = re.compile(r"^(?:ForAnyValue:|ForAllValues:)?(?P<base>String\w*?)(?P<ifx>IfExists)?$")
_NEGATED = frozenset({"StringNotLike", "StringNotEquals", "StringNotEqualsIgnoreCase"})


def _s3_prefix_operators(block: str) -> list[str]:
    clean = _strip_comments(block)
    masked = _mask(clean)
    cond = re.search(r"\bCondition\s*=\s*\{", masked)
    if not cond:
        return []
    body = _block_body(clean, cond.end() - 1)
    mbody = _mask(body)
    ops: list[str] = []
    for m in re.finditer(r"([\w:]+)\s*=\s*\{", mbody):
        inner = _block_body(body, m.end() - 1)
        if '"s3:prefix"' in inner:
            ops.append(m.group(1))
    return ops


def s3_prefix_polarity_problems(text: str, label: str) -> list[str]:
    problems: list[str] = []
    for block in _jsonencode_statement_blocks(text):
        clean = _strip_comments(block)
        sid = (re.search(r'Sid\s*=\s*"([^"]*)"', clean) or [None, "?"])[1]
        effect = (re.search(r'Effect\s*=\s*"([^"]*)"', clean) or [None, "Allow"])[1]
        for op in _s3_prefix_operators(block):
            m = _OPERATOR.match(op)
            if not m:
                continue
            negated = m.group("base") in _NEGATED
            if effect == "Deny" and not negated:
                problems.append(f"{label}: Deny {sid} carries s3:prefix under positive {op}")
            if effect == "Allow" and negated:
                problems.append(f"{label}: Allow {sid} carries s3:prefix under negated {op}")
    return problems


def _all_problems(texts: dict[str, str]) -> list[str]:
    return [p for name, text in texts.items() for p in s3_prefix_polarity_problems(text, name)]


def _real() -> dict[str, str]:
    return {f: (_ROOT / "terraform" / "bootstrap" / f).read_text(encoding="utf-8") for f in _FILES}


def test_no_s3_prefix_in_negative_context() -> None:
    assert _all_problems(_real()) == []


def test_positive_deny_is_red() -> None:
    bad_text = "\n".join(
        [
            "x = jsonencode({ Statement = [ {",
            '  Sid = "D" Effect = "Deny" Action = ["s3:ListBucketVersions"] Resource = ["a"]',
            '  Condition = { StringLike = { "s3:prefix" = ["state/*"] } }',
            "} ] })",
        ]
    )
    assert any("positive StringLike" in p for p in s3_prefix_polarity_problems(bad_text, "fx"))


def test_negated_allow_is_red() -> None:
    bad_text = "\n".join(
        [
            "x = jsonencode({ Statement = [ {",
            '  Sid = "A" Effect = "Allow" Action = ["s3:ListBucketVersions"] Resource = ["a"]',
            '  Condition = { StringNotLike = { "s3:prefix" = ["ducklake/*"] } }',
            "} ] })",
        ]
    )
    assert any("negated StringNotLike" in p for p in s3_prefix_polarity_problems(bad_text, "fx"))


def test_negated_deny_and_positive_allow_are_clean() -> None:
    ok = "\n".join(
        [
            "x = jsonencode({ Statement = [ {",
            '  Sid = "D" Effect = "Deny" Action = ["s3:ListBucketVersions"] Resource = ["a"]',
            '  Condition = { StringNotLike = { "s3:prefix" = ["ducklake/*"] } }',
            "}, {",
            '  Sid = "A" Effect = "Allow" Action = ["s3:ListBucketVersions"] Resource = ["a"]',
            '  Condition = { StringLike = { "s3:prefix" = ["ducklake/*"] } }',
            "} ] })",
        ]
    )
    assert s3_prefix_polarity_problems(ok, "fx") == []
