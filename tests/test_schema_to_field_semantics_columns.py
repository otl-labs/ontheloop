"""Column-level projection tests moved out of tests/test_schema_to_field_semantics.py (Decision 128: that file sat at its
SLOC cap): the role derivation rule and the lasting rule that every projected per_write column is one its contract declares,
and the reverse (SCD2 tables only; true before and after the column add of plan C2)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
_MODULE_PATH = _ROOT / "scripts" / "schema_to_field_semantics.py"
_spec = importlib.util.spec_from_file_location("schema_to_field_semantics", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_project_role = _mod._project_role
generate = _mod.generate


# ---------------------------------------------------------------------------
# Role derivation rule
# ---------------------------------------------------------------------------
class TestProjectRole:
    def test_no_derivation_is_input(self) -> None:
        assert _project_role(None) == "input"

    def test_empty_derivation_is_input(self) -> None:
        assert _project_role({}) == "input"

    def test_realized_true_is_derived(self) -> None:
        assert _project_role({"realized": True}) == "derived"

    def test_realized_false_is_input(self) -> None:
        assert _project_role({"realized": False}) == "input"

    def test_realized_missing_is_input(self) -> None:
        assert _project_role({"formula": "some formula"}) == "input"

    def test_derived_requires_exact_true(self) -> None:
        assert _project_role({"realized": 1}) == "input"  # 1 != True (strict check)
        assert _project_role({"realized": "true"}) == "input"  # string "true" is not True


def test_projected_per_write_equals_contract_declared() -> None:
    for table, entry in generate()["ops_tables"].items():
        path = _ROOT / "docs" / "contracts" / f"{table}.yaml"
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if str((raw.get("governance") or {}).get("table_class") or "scd2").lower() != "scd2":
            continue
        declared = {n for n, f in (raw.get("fields") or {}).items() if f.get("per_write") is True}
        projected = {n for n, c in (entry.get("columns") or {}).items() if c.get("per_write") is True}
        assert projected == declared, table
