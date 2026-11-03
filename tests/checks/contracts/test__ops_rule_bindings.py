"""Unit cases for scripts/checks/contracts/_ops_rule_bindings.py: ops.yaml has no write-time role and is never stricter
than the contract (one-way), and the status vocabulary legs (rec-4158)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.contracts import _ops_rule_bindings as bindings
from scripts.checks.contracts import _rule_grammar as grammar

_REPO = Path(__file__).resolve().parents[3]
_OPS = "config/agent/data_quality/ops.yaml"
_EARLIER_NN = "exclude_before is earlier than the contract's not_null date"
_EARLIER_AV = "exclude_before is earlier than the contract's accepted_values"
_SOURCES = (_OPS, "src/common/ducklake_scd2_schema.py", "scripts/executor/jsonl_store.py")


@pytest.fixture
def recs() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    fields, intents, _ = grammar.read_table("ops_recommendations", _REPO / "docs/contracts", _REPO)
    return fields, intents


def _root(tmp_path: Path) -> Path:
    for rel in _SOURCES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REPO / rel, tmp_path / rel)
    return tmp_path


def _ops_with(tmp_path: Path, column: str, tests: list[Any]) -> Path:
    (tmp_path / _OPS).parent.mkdir(parents=True, exist_ok=True)
    doc = {"tables": {"ops_recommendations": {"columns": {column: {"tests": tests}}}}}
    (tmp_path / _OPS).write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path


def test_the_real_tree_agrees(recs) -> None:
    assert bindings.binding_errors(_REPO, *recs) == []


def test_ops_yaml_has_no_write_time_role_and_is_never_stricter(tmp_path: Path, recs) -> None:
    fields, intents = recs
    assert "write_time" not in (_REPO / _OPS).read_text(encoding="utf-8")
    assert bindings._ops_yaml_errors(_REPO, intents) == []
    # a write_time key anywhere is refused
    doc = {"tables": {"ops_recommendations": {"columns": {"title": {"tests": [{"not_null": {"write_time": False}}]}}}}}
    (tmp_path / _OPS).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / _OPS).write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert any("`write_time` key remains" in e for e in bindings._ops_yaml_errors(tmp_path, intents))


@pytest.mark.parametrize(
    ("column", "tests", "message"),
    [
        ("risk", [{"not_null": {"exclude_before": "2026-05-01"}}], None),
        ("risk", [{"not_null": {"exclude_before": "2026-06-01"}}], None),
        (
            "risk",
            [{"not_null": {"exclude_before": "2026-01-01"}}],
            _EARLIER_NN,
        ),
        ("risk", [{"not_null": {}}], _EARLIER_NN),
        ("risk", [{"not_null": {"enforced": False}}], None),
        ("ghost", [{"not_null": {}}], "column the contract does not declare"),
        ("ghost", [{"expression": {"sql": "1 = 1"}}], None),
        ("risk", [{"accepted_values": {"values": ["low"], "exclude_before": "2026-05-01"}}], None),
        ("risk", [{"accepted_values": {"values": ["low", "extreme"], "exclude_before": "2026-05-01"}}], "not a subset"),
        ("risk", [{"accepted_values": {"values": ["low"]}}], _EARLIER_AV),
        ("title", ["a_bare_name_is_not_a_rule", {}], "test entry {} must be a name or a single-key mapping"),
        ("risk", ["not_null"], "exclude_before"),
        ("risk", [{"expression": {"sql": "1 = 1"}}], None),
    ],
)
def test_ops_yaml_is_never_stricter_than_the_contract(tmp_path: Path, recs, column, tests: list[Any], message) -> None:
    errors = bindings._ops_yaml_errors(_ops_with(tmp_path, column, tests), recs[1])
    if message is None:
        assert errors == []
    else:
        assert any(message in e for e in errors), errors


def test_not_null_with_no_declared_contract_leg_is_stricter(tmp_path: Path, recs) -> None:
    _, intents = recs
    no_leg = {**intents, "risk": {k: v for k, v in intents["risk"].items() if k != "not_null"}}
    errors = bindings._ops_yaml_errors(_ops_with(tmp_path, "risk", [{"not_null": {}}]), no_leg)
    assert any("ops.yaml risk.not_null: stricter than the contract" in e for e in errors), errors


def test_status_sources_fail_closed(tmp_path: Path, recs) -> None:
    _, intents = recs
    root = _root(tmp_path)
    assert bindings._status_errors(root, intents) == []
    (root / "scripts/executor/jsonl_store.py").write_text("OTHER = 1\n", encoding="utf-8")
    errors = bindings._status_errors(root, intents)
    assert any("could not read the jsonl_store._VALID_STATUSES" in e for e in errors)
    assert any("could not read the Recommendation status Literal" in e for e in errors)
    (root / "scripts/executor/jsonl_store.py").write_text("_VALID_STATUSES = frozenset(compute())\n", encoding="utf-8")
    assert any("could not read the jsonl_store._VALID_STATUSES" in e for e in bindings._status_errors(root, intents))
    (root / "src/common/ducklake_scd2_schema.py").write_text("STATUS_TRANSITIONS = {}\n", encoding="utf-8")
    assert any("STATUS_TRANSITIONS enforced set" in e for e in bindings._status_errors(root, intents))
    (root / "src/common/ducklake_scd2_schema.py").write_text("STATUS_TRANSITIONS = build()\n", encoding="utf-8")
    assert any("STATUS_TRANSITIONS enforced set" in e for e in bindings._status_errors(root, intents))
    drifted = {**intents, "status": {"accepted_values": {"values": ["open"]}}}
    assert any(
        "status vocabulary: the contract's ['open'] differs" in e
        for e in bindings._status_errors(_root(tmp_path / "b"), drifted)
    )


def test_unreadable_inputs_raise_for_the_entry_to_report(tmp_path: Path, recs) -> None:
    (tmp_path / _OPS).parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(OSError):
        bindings.binding_errors(tmp_path, *recs)
    root = _root(tmp_path / "b")
    (root / "scripts/executor/jsonl_store.py").write_text("_VALID_STATUSES = []\nx = (\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        bindings.binding_errors(root, *recs)
