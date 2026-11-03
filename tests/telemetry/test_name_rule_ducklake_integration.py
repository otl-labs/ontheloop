"""Real local DuckLake: the process_event name rule is live through append_events (rec-4176).

pytest.mark.integration. A golden hook case recorded by the v2 producer lands; in a fresh catalog the same batch with one
process_event name broken is rejected and writes nothing; DuckDB's regexp_full_match agrees with Python re on every stored
name and every shared vector. The extension helper FAILS, never skips (rec-4071).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from src.row_rules.rules import RowRuleError
from src.telemetry.append import append_events
from src.telemetry.gate import GateError
from src.turn_capture.record_turn import record_turn
from tests.fixtures.process_event_name_vectors import ACCEPT, REJECT
from tests.fixtures.turn_capture_corpus import PROJECT, PROJECT_REF, TENANT, case_cap, load_case, mem_tree
from tests.fixtures.turn_capture_ducklake import create_tables, load_specs, open_local_lake

pytestmark = pytest.mark.integration

CASE = "hook_blocked_tool_call.json"
OBSERVATIONS = "telemetry_observations"


def record() -> Any:
    files, _ = load_case(CASE)
    with case_cap(CASE):
        return record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True)


def batch(result: Any, table: str) -> list[dict[str, Any]]:
    return next(rows for name, rows in result.batches if name == table)


def append_rows(con: Any, specs: dict, table: str, rows: list[dict[str, Any]]) -> Any:
    return append_events(con, specs[table], rows, tenant_id=TENANT, project_id=PROJECT, catalog="lake")


def file_count(con: Any) -> int:
    return con.execute(f"SELECT count(*) FROM ducklake_list_files('lake', '{OBSERVATIONS}')").fetchone()[0]


def row_count(con: Any) -> int:
    return con.execute(f"SELECT count(*) FROM lake.{OBSERVATIONS}").fetchone()[0]


def stored_process_names(con: Any) -> list[str]:
    sql = f"SELECT name FROM lake.{OBSERVATIONS} WHERE observation_type = 'process_event'"
    return [r[0] for r in con.execute(sql).fetchall()]


def contract_regex() -> str:
    import yaml  # noqa: PLC0415

    path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "telemetry_observations.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["fields"]["name"]["dq_intent"]["pattern_when"][0]["pattern"]


def test_golden_hook_case_lands_with_the_rule_live_and_duckdb_agrees_with_python(tmp_path: Path) -> None:
    specs = load_specs()
    result = record()
    con = open_local_lake(tmp_path)
    create_tables(con, specs)
    for table, rows in result.batches:
        if rows:
            assert append_rows(con, specs, table, rows).inserted == len(rows), table
    names = stored_process_names(con)
    assert names and all(n.startswith("hook:") for n in names)
    pattern = contract_regex()
    regex = re.compile(pattern)
    for name in [*names, *ACCEPT, *REJECT]:
        duck = con.execute("SELECT regexp_full_match(?, ?)", [name, pattern]).fetchone()[0]
        assert duck == (regex.fullmatch(name) is not None), repr(name)
    assert all(regex.fullmatch(n) for n in ACCEPT) and not any(regex.fullmatch(n) for n in REJECT)
    con.close()


def test_a_broken_process_event_name_rejects_the_batch_and_writes_nothing(tmp_path: Path) -> None:
    specs = load_specs()
    result = record()
    con = open_local_lake(tmp_path)
    create_tables(con, specs)
    rows = [dict(r) for r in batch(result, OBSERVATIONS)]
    victim = next(r for r in rows if r["observation_type"] == "process_event")
    victim["name"] = "never_on_main"
    before = file_count(con)
    with pytest.raises(GateError) as raised:
        append_rows(con, specs, OBSERVATIONS, rows)
    cause = raised.value.__cause__
    assert isinstance(cause, RowRuleError) and cause.rule == "pattern_when" and cause.column == "name"
    assert row_count(con) == 0
    assert file_count(con) == before
    con.close()
