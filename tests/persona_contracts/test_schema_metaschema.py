"""Full 2020-12 metaschema check of every persona schema (the fast tier checks only the keyword subset)."""

from __future__ import annotations

from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema", reason="env: jsonschema absent (a direct requirement since rec-3795)")

from scripts.persona_contracts import load_index  # noqa: E402
from scripts.persona_contracts.schema import load_yaml  # noqa: E402
from tests.persona_contracts.fixture_tree import build_tree, read_contract  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _check(contract: dict) -> None:
    for key in ("input_schema", "output_schema"):
        jsonschema.Draft202012Validator.check_schema(contract[key])


def test_fixture_contract_schemas_pass_the_metaschema(tmp_path: Path) -> None:
    _check(read_contract(build_tree(tmp_path)))


def test_every_registered_contract_passes_the_metaschema() -> None:
    for row in load_index().personas:
        _check(load_yaml(ROOT / row.contract))


def test_fixture_success_validates_and_every_failure_envelope_does_not(tmp_path: Path) -> None:
    contract = read_contract(build_tree(tmp_path))
    validator = jsonschema.Draft202012Validator(contract["output_schema"])
    assert validator.is_valid({"verdict": "PROCEED"})
    for failure in contract["failure_outputs"]:
        assert not validator.is_valid({"outcome": "failure", "failure": failure["id"], "detail": "x"})
