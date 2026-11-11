"""Index and contract models, schema-dialect checker, capability parser; no jsonschema in the fast tier."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.persona_contracts import check_schema_dialect, load_index, parse_capability
from scripts.persona_contracts.schema import PersonaRow

ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = ["type", "properties", "required", "additionalProperties", "items", "enum", "description"]


def test_real_index_parses() -> None:
    index = load_index()
    assert index.contract["id"] == "executor-personas"
    assert {c.id for c in index.capability_vocabulary.capabilities} >= {"read_input", "edit_repo", "warehouse_read"}


def test_no_persona_module_imports_jsonschema() -> None:
    paths = list((ROOT / "scripts" / "persona_contracts").glob("*.py"))
    paths += list((ROOT / "scripts" / "checks" / "contracts").glob("validate_persona_contract*.py"))
    assert paths
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            assert not any(n.split(".")[0] == "jsonschema" for n in names), path


def test_dialect_accepts_a_closed_object() -> None:
    schema = {"type": "object", "additionalProperties": False, "required": [], "properties": {"a": {"type": "string"}}}
    assert check_schema_dialect(schema, KEYWORDS) == []


def test_dialect_refuses_off_subset_keyword() -> None:
    schema = {"type": "object", "additionalProperties": False, "required": [], "oneOf": []}
    assert any("oneOf" in e for e in check_schema_dialect(schema, KEYWORDS))


def test_dialect_refuses_missing_additional_properties_false() -> None:
    assert any("additionalProperties" in e for e in check_schema_dialect({"type": "object", "required": []}, KEYWORDS))


def test_dialect_refuses_missing_required_list() -> None:
    assert any("required" in e for e in check_schema_dialect({"type": "object", "additionalProperties": False}, KEYWORDS))


def test_dialect_recurses_into_properties_and_items() -> None:
    nested = {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {"a": {"type": "array", "items": {"type": "object", "required": []}}},
    }
    assert any("items" in e and "additionalProperties" in e for e in check_schema_dialect(nested, KEYWORDS))


def test_parse_capability_splits_ids_and_parameters() -> None:
    assert parse_capability("read_input:diff") == ("read_input", "diff")
    assert parse_capability("read_repo") == ("read_repo", None)


def _row(**extra: object) -> dict[str, object]:
    return {
        "persona": "p",
        "kind": "verdict",
        "contract": "c",
        "rendered_prompt": "r",
        "implementation": {"status": "pending"},
        **extra,
    }


def test_persona_row_registry_key_is_a_string() -> None:
    assert PersonaRow.model_validate(_row(registry_key="code-review")).registry_key == "code-review"
    with pytest.raises(ValidationError):
        PersonaRow.model_validate(_row(registry_key=5))


def test_persona_row_registry_key_may_be_absent() -> None:
    assert PersonaRow.model_validate(_row()).registry_key is None
