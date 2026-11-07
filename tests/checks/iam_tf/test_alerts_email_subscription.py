"""PLAN-terraform-company-emails-forget: the founder's alerts subscription is forgotten with destroy = false, never
destroyed, and the company successor stays on the alerts topic from a no-default tfvars variable with no meta-argument
(text-level guard over sns_alerts.tf).

Transitional: PLAN-terraform-company-emails-closing replaces this test and the removed block it checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.checks.contracts.validate_terraform_cc_web_operations import _declared_no_default_variables
from tests.checks.iam_tf._platform_security_hcl import _block_body, _mask, _resource_body, _strip_comments

pytestmark = pytest.mark.unit

_DIR = Path(__file__).resolve().parents[3] / "terraform" / "personal"
_SNS = _DIR / "sns_alerts.tf"
_META_ARGUMENT = re.compile(r"^\s*(?:count|for_each|lifecycle)\b", re.MULTILINE)
_PREDECESSOR = "aws_sns_topic_subscription.alerts_email"


def _removed_destroy_flags(text: str, address: str) -> list[bool | None]:
    """destroy flag (None when undeclared) of every `removed { from = <address> ... }` block, comments ignored."""
    flags: list[bool | None] = []
    for m in re.finditer(r"\bremoved\s*\{", _mask(text)):
        body = _block_body(text, m.end() - 1)
        fm = re.search(r"^\s*from\s*=\s*([\w.]+)\s*$", _strip_comments(body), re.M)
        if fm is None or fm.group(1) != address:
            continue
        destroy: bool | None = None
        lcm = re.search(r"\blifecycle\s*\{", _mask(body))
        if lcm is not None:
            dm = re.search(r"\bdestroy\s*=\s*(true|false)\b", _strip_comments(_block_body(body, lcm.end() - 1)))
            if dm is not None:
                destroy = dm.group(1) == "true"
        flags.append(destroy)
    return flags


def test_predecessor_forgotten_not_destroyed() -> None:
    text = _SNS.read_text(encoding="utf-8")
    assert _resource_body(text, "aws_sns_topic_subscription", "alerts_email") is None, "the founder's resource block stays"
    assert _removed_destroy_flags(text, _PREDECESSOR) == [False], f"{_PREDECESSOR}: exactly one removed block, destroy = false"

    block = _resource_body(text, "aws_sns_topic_subscription", "company_alerts_email")
    assert block is not None, "the company successor subscription is gone"
    body = _strip_comments(block)
    assert re.search(r"^\s*topic_arn\s*=\s*aws_sns_topic\.alerts\.arn\s*$", body, re.MULTILINE)
    assert re.search(r'^\s*protocol\s*=\s*"email"\s*$', body, re.MULTILINE)
    assert re.search(r"^\s*endpoint\s*=\s*var\.company_alerts_email\s*$", body, re.MULTILINE)
    assert not _META_ARGUMENT.search(body), "the trusted-plan screen refuses count and for_each"
    assert "company_alerts_email" in _declared_no_default_variables(_DIR), "company_alerts_email must declare no default"
    assert "@" not in text, "sns_alerts.tf must carry no e-mail address"
