"""Real local DuckLake: the golden cases' batches append through src.telemetry.append.append_events in write order.

pytest.mark.integration. Fixed tenant/project ULIDs stand in for writer-side resolution (Decision 200). Every case
gets a fresh catalog (the corpus reuses uuids across cases by design). The extension helper FAILS, never skips.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import pytest

from src.telemetry.gate import GrainConflictError
from src.turn_capture.record_turn import PARSER_VERSION, record_turn
from tests.fixtures.telemetry_dedupe_reference import compute_generations, resolve_table
from tests.fixtures.turn_capture_corpus import PROJECT, PROJECT_REF, TENANT, case_cap, case_names, load_case, mem_tree
from tests.fixtures.turn_capture_ducklake import TABLES, create_tables, load_specs, open_local_lake

pytestmark = pytest.mark.integration

ROUTE_PENDING_BYTES = (
    65536  # the kernel facade has no blob route; the writer's begin_blobs and HEAD-verify store larger payloads
)
ROUTE_PENDING = {"large_tool_result_over_64k.json": 1}
FKS = (
    ("telemetry_observations", "session_id", "telemetry_sessions", "session_id"),
    ("telemetry_transcripts", "session_id", "telemetry_sessions", "session_id"),
    ("telemetry_transcripts", "observation_id", "telemetry_observations", "observation_id"),
    ("telemetry_agents", "session_id", "telemetry_sessions", "session_id"),
    ("telemetry_agents", "observation_id", "telemetry_observations", "observation_id"),
    ("telemetry_observations", "parent_observation_id", "telemetry_observations", "observation_id"),
    ("telemetry_sessions", "parent_session_id", "telemetry_sessions", "session_id"),
)


def load(con: Any, specs: dict, result) -> dict[str, int]:
    """Append every non-empty batch in write order; returns inserted counts per table."""
    inserted: dict[str, int] = {}
    for table, rows in result.batches:
        if rows:
            done = append_rows(con, specs[table], rows)
            assert done.inserted == len(rows), (table, done)
            inserted[table] = done.inserted
    return inserted


def append_rows(con: Any, spec: Any, rows: list[dict[str, Any]]):
    from src.telemetry.append import append_events  # noqa: PLC0415

    return append_events(con, spec, rows, tenant_id=TENANT, project_id=PROJECT, catalog="lake")


def dangling(con: Any, child: str, column: str, parent: str, key: str) -> int:
    sql = (
        f"SELECT count(*) FROM lake.{child} c WHERE c.{column} IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM lake.{parent} p WHERE p.{key} = c.{column})"
    )
    return con.execute(sql).fetchone()[0]


def read_rows(con: Any, table: str) -> list[dict[str, Any]]:
    cur = con.execute(f"SELECT * FROM lake.{table}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def is_route_pending(row: dict[str, Any]) -> bool:
    return "content" in row and row["content_bytes"] > ROUTE_PENDING_BYTES


def split_route_pending(result: Any) -> tuple[Any, int]:
    """(result without the payloads above the inline threshold, how many were held back)."""
    kept = [r for r in result.transcripts if not is_route_pending(r)]
    return dataclasses.replace(result, transcripts=kept), len(result.transcripts) - len(kept)


def test_every_golden_case_loads_with_every_fk_resolving(tmp_path: Path) -> None:
    specs = load_specs()
    full_outputs = omissions = 0
    for name in case_names():
        files, _ = load_case(name)
        with case_cap(name):
            whole = record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True)
        result, held_back = split_route_pending(whole)
        assert held_back == ROUTE_PENDING.get(name, 0), (name, held_back)
        full_outputs += sum(r["purpose"] == "tool_output" and "content" in r for r in result.transcripts)
        omissions += sum("content_omitted_reason" in r for r in result.transcripts)
        con = open_local_lake(tmp_path / name.removesuffix(".json"))
        create_tables(con, specs)
        load(con, specs, result)
        for child, column, parent, key in FKS:
            assert dangling(con, child, column, parent, key) == 0, (name, child, column)
        starts = {
            con.execute(f"SELECT count(DISTINCT session_started_at) FROM lake.{t}").fetchone()[0]
            for t in TABLES
            if any(rows for tbl, rows in result.batches if tbl == t)
        }
        if starts:
            assert starts == {1}, name
        else:
            assert name == "no_reply_session.json" and not any(rows for _, rows in result.batches), name
        stored = con.execute("SELECT coalesce(max(strlen(content)), 0) FROM lake.telemetry_transcripts").fetchone()[0]
        assert stored <= ROUTE_PENDING_BYTES, name
        con.close()
    assert full_outputs >= 2 and omissions == 1


def test_replay_mutation_and_generation_bump_on_a_nested_tree(tmp_path: Path) -> None:
    specs = load_specs()
    files, _ = load_case("nested_subagent_depth2.json")
    result = record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True)
    con = open_local_lake(tmp_path)
    create_tables(con, specs)
    load(con, specs, result)
    for table, rows in result.batches:
        assert append_rows(con, specs[table], rows).inserted == 0, table

    payload = "a different payload"
    victim = dict(
        result.transcripts[0],
        content=payload,
        content_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        content_bytes=len(payload.encode()),
    )
    with pytest.raises(GrainConflictError, match="TELEMETRY_GRAIN_CONFLICT"):
        append_rows(con, specs["telemetry_transcripts"], [victim])

    bump = PARSER_VERSION + 1
    for table, rows in result.batches:
        bumped = [dict(row, parser_version=bump) for row in rows]
        assert append_rows(con, specs[table], bumped).inserted == len(bumped), table

    def oracle(table: str) -> list[dict[str, Any]]:
        out = []
        for row in read_rows(con, table):
            marker = repr(sorted((k, str(v)) for k, v in row.items() if k not in ("parser_version", "created_timestamp")))
            out.append(
                {**row, "content": marker, "created_timestamp": row["created_timestamp"].isoformat(),
                 "event_timestamp": row["event_timestamp"].isoformat(), "observation_id": row.get("observation_id")}
            )  # fmt: skip
        return out

    sessions = oracle("telemetry_sessions")
    generations = compute_generations(sessions)
    assert generations and set(generations.values()) == {bump}
    for table in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
        rows = oracle(table)
        survivors, conflicted = resolve_table(rows, generations)
        assert not conflicted and {r["parser_version"] for r in survivors} == {bump}
        assert len(survivors) == len(rows) // 2, table
    model_calls = [r for r in oracle("telemetry_observations") if r["observation_type"] == "model_call"]
    survivors, conflicted = resolve_table(model_calls, generations, is_model_call=True)
    assert not conflicted and {r["parser_version"] for r in survivors} == {bump}
    con.close()


def test_cursor_persisted_after_every_batch_round_trips(tmp_path: Path) -> None:
    specs = load_specs()
    files, _ = load_case("hooks_resume_and_compact.json")
    first = record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=False)
    con = open_local_lake(tmp_path)
    create_tables(con, specs)
    load(con, specs, first)
    second = record_turn(mem_tree(files), first.next_cursor, project_ref=PROJECT_REF, session_final=True)
    load(con, specs, second)
    full = record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True)
    for table, rows in full.batches:
        stored = con.execute(f"SELECT count(*) FROM lake.{table}").fetchone()[0]
        assert stored == len(rows), table
    assert dataclasses.is_dataclass(second.next_cursor)
    con.close()
