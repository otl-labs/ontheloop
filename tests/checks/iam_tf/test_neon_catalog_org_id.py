"""rec-4543 and the Neon organisation move: the catalog's organisation id is never committed, and no plan may destroy
or replace the catalog (text-level guard over the .tf)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.fixtures.terraform_hcl_blocks import find_resource_block

pytestmark = pytest.mark.unit

_DIR = Path(__file__).resolve().parents[3] / "terraform" / "personal"
_CATALOG = _DIR / "neon_ducklake_catalog.tf"
_VARIABLES = _DIR / "variables.tf"


def _variable_block(text: str, name: str) -> str:
    match = re.search(rf'^variable\s+"{re.escape(name)}"\s*\{{.*?^\}}', text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"variable {name!r} not found"
    return match.group(0)


def _lifecycle(block: str) -> str:
    match = re.search(r"^\s*lifecycle\s*\{(.*?)^\s*\}", block, re.MULTILINE | re.DOTALL)
    assert match is not None, "no lifecycle block"
    return match.group(1)


def test_neon_org_id_has_no_committed_default() -> None:
    text = _VARIABLES.read_text(encoding="utf-8")
    block = _variable_block(text, "neon_org_id")
    assert not re.search(r"^\s*default\s*=", block, re.MULTILINE), "neon_org_id must have no default (rec-4543)"
    assert re.search(r"^\s*nullable\s*=\s*false\s*$", block, re.MULTILINE), "neon_org_id must be required"
    assert "There is no neon_org_id" not in text
    assert not re.search(r'"org-[a-z]+-[a-z]+-\d+"', "\n".join(p.read_text(encoding="utf-8") for p in _DIR.glob("*.tf")))


def test_catalog_project_ignores_org_id_and_cannot_be_destroyed() -> None:
    lifecycle = _lifecycle(find_resource_block(_CATALOG.read_text(encoding="utf-8"), "neon_project", "ducklake_catalog"))
    assert re.search(r"^\s*prevent_destroy\s*=\s*true\s*$", lifecycle, re.MULTILINE)
    ignored = re.search(r"^\s*ignore_changes\s*=\s*\[([^\]]*)\]", lifecycle, re.MULTILINE)
    assert ignored is not None and "org_id" in [s.strip() for s in ignored.group(1).split(",")]


def test_catalog_database_cannot_be_destroyed() -> None:
    lifecycle = _lifecycle(find_resource_block(_CATALOG.read_text(encoding="utf-8"), "neon_database", "ducklake_ops"))
    assert re.search(r"^\s*prevent_destroy\s*=\s*true\s*$", lifecycle, re.MULTILINE)
