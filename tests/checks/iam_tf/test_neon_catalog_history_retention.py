"""Decision 214 cl.3: the Neon catalog keeps 7 days of point-in-time history (text-level guard over the .tf)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.fixtures.terraform_hcl_blocks import find_resource_block

pytestmark = pytest.mark.unit

_TF = Path(__file__).resolve().parents[3] / "terraform" / "personal" / "neon_ducklake_catalog.tf"


def test_history_retention_is_seven_days():
    block = find_resource_block(_TF.read_text(encoding="utf-8"), "neon_project", "ducklake_catalog")
    match = re.search(r"^\s*history_retention_seconds\s*=\s*(\d+)\s*$", block, re.MULTILINE)
    assert match is not None, "history_retention_seconds is not set on neon_project.ducklake_catalog"
    assert int(match.group(1)) == 604800
