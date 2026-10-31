"""Tests for ScopeEntry.new_imports (PLAN-plan-lint): any schema_version, defaults empty, first-party dotted names."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from scripts.roadmap.plan_document import PlanDocument
from tests.fixtures.plan_document_helpers import _base, _base_v2


def _with_imports(base: dict, imports: object) -> dict:
    doc = copy.deepcopy(base)
    doc["scope"][0]["new_imports"] = imports
    return doc


@pytest.mark.parametrize("base", [_base, _base_v2], ids=["valid", "valid_v2"])
def test_accepted_on_any_schema_version(base) -> None:
    doc = PlanDocument.model_validate(_with_imports(base(), ["scripts.roadmap.plan_lint", "src.common.x", "tests.fixtures.y"]))
    assert doc.scope[0].new_imports == ["scripts.roadmap.plan_lint", "src.common.x", "tests.fixtures.y"]


def test_defaults_to_empty() -> None:
    assert PlanDocument.model_validate(_base()).scope[0].new_imports == []


@pytest.mark.parametrize("bad", ["scripts", "scripts/roadmap", "os.path", "scripts..x", "scripts.1x", "Scripts.x", ""])
def test_refuses_a_non_dotted_or_non_first_party_name(bad: str) -> None:
    with pytest.raises(ValidationError):
        PlanDocument.model_validate(_with_imports(_base(), [bad]))


def test_dump_omits_an_empty_field_so_scope_stays_equal_to_its_base() -> None:
    base = _base()
    assert PlanDocument.model_validate(base).model_dump()["scope"] == base["scope"]
    declared = PlanDocument.model_validate(_with_imports(base, ["scripts.x"])).model_dump()
    assert declared["scope"][0]["new_imports"] == ["scripts.x"]
