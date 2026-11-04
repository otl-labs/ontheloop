"""host_run_ref's required_when over the GENERATED field_semantics projection (PLAN-telemetry-claude-p-capture-adapter).

The projection is what the deployed DuckLake writer enforces: a top-level telemetry_agents row (a top-level trigger) without a
host_run_ref is refused 422 row_rule required_when; a sub-agent row and the deploy smoke vector (no trigger) pass without one.
A new module, so test_append.py and tests/row_rules/test_rules.py are not grown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.telemetry.append import EventTableSpec, prepare_batch
from src.telemetry.gate import RowRuleGateError
from tests.fixtures.turn_capture_corpus import PROJECT_REF, load_case, mem_tree
from tests.fixtures.turn_capture_v8_cases import ci_rca_hook_pin_and_host_run_files

TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
GENERATED = Path(__file__).resolve().parents[2] / "config" / "lambda" / "ducklake" / "field_semantics.yaml"


@pytest.fixture(scope="module")
def spec() -> EventTableSpec:
    tables = yaml.safe_load(GENERATED.read_text(encoding="utf-8"))["ops_tables"]
    return EventTableSpec.from_projection("telemetry_agents", tables["telemetry_agents"])


def _agent_rows(files: dict[str, str]) -> list[dict[str, Any]]:
    from src.turn_capture.record_turn import record_turn

    return record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True).agents


def _prepare(spec: EventTableSpec, row: dict[str, Any]) -> None:
    prepare_batch(spec, [row], tenant_id=TENANT, project_id=PROJECT)


def test_the_generated_projection_carries_the_seven_top_level_triggers() -> None:
    projection = yaml.safe_load(GENERATED.read_text(encoding="utf-8"))["ops_tables"]["telemetry_agents"]
    required = projection["columns"]["host_run_ref"]["required_when"]
    assert required == {
        "trigger": ["workflow_run", "workflow_dispatch", "push", "schedule", "eventbridge", "manual", "smoke_test"]
    }


def test_a_top_level_row_without_host_run_ref_is_refused_and_with_one_accepted(spec: EventTableSpec) -> None:
    opener, closer = _agent_rows(ci_rca_hook_pin_and_host_run_files())
    assert opener["trigger"] == closer["trigger"] == "workflow_run"
    for row in (opener, closer):
        _prepare(spec, row)
        bare = {k: v for k, v in row.items() if k != "host_run_ref"}
        with pytest.raises(RowRuleGateError) as raised:
            _prepare(spec, bare)
        assert raised.value.rule == "required_when" and raised.value.column == "host_run_ref"


def test_a_sub_agent_row_and_the_smoke_vector_pass_without_it(spec: EventTableSpec) -> None:
    files, _ = load_case("subagent_sync.json")
    rows = _agent_rows(files)
    assert rows and all(r["trigger"] == "subagent_sync" and "host_run_ref" not in r for r in rows)
    for row in rows:
        _prepare(spec, row)
    smoke = {k: v for k, v in rows[0].items() if k != "trigger"}
    _prepare(spec, smoke)
