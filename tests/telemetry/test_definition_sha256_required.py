"""definition_sha256 required_when over the GENERATED field_semantics projection (rec-4433).

The projection is what the deployed DuckLake writer enforces: a telemetry_agents row whose agent_type is a persona registry
key is refused 422 row_rule required_when without definition_sha256; every other row passes without one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ducklake_smoke.telemetry_append_vectors import agent_row
from src.row_rules.rules import RowRules, check_row
from src.telemetry.append import EventTableSpec, prepare_batch
from src.telemetry.gate import RowRuleGateError
from tests.fixtures.turn_capture_corpus import PROJECT_REF, load_case, mem_tree
from tests.fixtures.turn_capture_v8_cases import ci_rca_hook_pin_and_host_run_files

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml"
PERSONAS = ROOT / "docs" / "contracts" / "executor-personas.yaml"
TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
KEYS = ["implement-agent", "planning", "code-review", "decision-scout", "plan-critique"]


@pytest.fixture(scope="module")
def projection() -> dict[str, Any]:
    return yaml.safe_load(GENERATED.read_text(encoding="utf-8"))["ops_tables"]["telemetry_agents"]


@pytest.fixture(scope="module")
def spec(projection: dict[str, Any]) -> EventTableSpec:
    return EventTableSpec.from_projection("telemetry_agents", projection)


def _agent_rows(files: dict[str, str]) -> list[dict[str, Any]]:
    from src.turn_capture.record_turn import record_turn  # noqa: PLC0415

    return record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True).agents


def _prepare(spec: EventTableSpec, row: dict[str, Any]) -> None:
    prepare_batch(spec, [row], tenant_id=TENANT, project_id=PROJECT)


def test_the_projection_requires_the_hash_on_every_persona_key(projection: dict[str, Any]) -> None:
    required = projection["columns"]["definition_sha256"]["required_when"]
    assert required == {"agent_type": KEYS}
    contract = yaml.safe_load(PERSONAS.read_text(encoding="utf-8"))
    assert set(required["agent_type"]) == {row["registry_key"] for row in contract["personas"]}


def test_a_persona_row_without_a_hash_is_refused_and_with_one_accepted(spec: EventTableSpec) -> None:
    files, _ = load_case("subagent_persona_sync.json")
    rows = _agent_rows(files)
    assert {r["event_kind"] for r in rows} == {"open", "close"}
    for row in rows:
        _prepare(spec, row)
        bare = {k: v for k, v in row.items() if k != "definition_sha256"}
        with pytest.raises(RowRuleGateError) as raised:
            _prepare(spec, bare)
        assert raised.value.rule == "required_when" and raised.value.column == "definition_sha256"
    for key in KEYS:
        with_hash = {**rows[0], "agent_type": key}
        _prepare(spec, with_hash)
        with pytest.raises(RowRuleGateError) as raised:
            _prepare(spec, {k: v for k, v in with_hash.items() if k != "definition_sha256"})
        assert raised.value.rule == "required_when" and raised.value.column == "definition_sha256"


def test_rows_that_are_not_persona_runs_pass_without_a_hash(spec: EventTableSpec, projection: dict[str, Any]) -> None:
    sub, _ = load_case("subagent_sync.json")
    top = ci_rca_hook_pin_and_host_run_files()
    rows = _agent_rows(sub) + _agent_rows(top)
    assert rows and all("definition_sha256" not in r for r in rows)
    for row in rows:
        _prepare(spec, row)
    columns = projection["columns"]
    rules = RowRules.from_projection({name: columns[name] for name in ("agent_type", "definition_sha256")})
    smoke = agent_row("t", datetime(2026, 11, 16, tzinfo=UTC))
    check_row(
        "telemetry_agents",
        {"agent_type": smoke["agent_type"], "definition_sha256": smoke.get("definition_sha256")},
        rules,
        datetime(2026, 11, 16, tzinfo=UTC),
    )
