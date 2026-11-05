"""PLAN-terraform-company-emails: the company successor alerts subscription sits beside the predecessor on the alerts
topic, from a no-default tfvars variable and with no meta-argument (text-level guard over sns_alerts.tf).

Transitional: PLAN-terraform-company-emails-forget replaces this test when the predecessor is forgotten.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.checks.contracts.validate_terraform_cc_web_operations import _declared_no_default_variables
from tests.fixtures.terraform_hcl_blocks import find_resource_block

pytestmark = pytest.mark.unit

_DIR = Path(__file__).resolve().parents[3] / "terraform" / "personal"
_SNS = _DIR / "sns_alerts.tf"
_META_ARGUMENT = re.compile(r"^\s*(?:count|for_each|lifecycle)\b", re.MULTILINE)


def test_successor_subscription_beside_predecessor() -> None:
    text = _SNS.read_text(encoding="utf-8")
    for name in ("alerts_email", "company_alerts_email"):
        block = find_resource_block(text, "aws_sns_topic_subscription", name)
        assert re.search(r"^\s*topic_arn\s*=\s*aws_sns_topic\.alerts\.arn\s*$", block, re.MULTILINE), name
        assert re.search(r'^\s*protocol\s*=\s*"email"\s*$', block, re.MULTILINE), name
        assert re.search(rf"^\s*endpoint\s*=\s*var\.{name}\s*$", block, re.MULTILINE), name
        assert not _META_ARGUMENT.search(block), f"{name}: the trusted-plan screen refuses count and for_each"
    assert "company_alerts_email" in _declared_no_default_variables(_DIR), "company_alerts_email must declare no default"
    assert "@" not in text, "sns_alerts.tf must carry no e-mail address"
