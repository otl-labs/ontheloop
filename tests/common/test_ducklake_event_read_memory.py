"""The 7-day root-sessions read fits the telemetry reader's own memory and spill limits (rec-5491, Decision 207).

Module-level importorskip: the fast tier installs no duckdb, so these nodes run in the full tier. The four journals are written
as Parquet files and viewed under the reader's alias, so render_sql's output runs unchanged with no ducklake extension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast-tier venv")

from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common import ducklake_runtime as rt  # noqa: E402
from tests.fixtures import duckdb_connect  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402

_TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
_ROOTS = 3000
_OBSERVATIONS = 300_000
_TRANSCRIPTS = 150_000
_AGENTS = 1500
_ROW_GROUP = 20_000
_START = "TIMESTAMPTZ '2026-01-01 01:00:00+00'"
_PLANTED = {("telemetry_observations", 10), ("telemetry_observations", 20), ("telemetry_transcripts", 5)}


def _sid(expr: str) -> str:
    return f"'s' || lpad(CAST(({expr}) % {_ROOTS} AS VARCHAR), 5, '0')"


def _select(table: str, count: int, overrides: dict[str, str], extra_where: str = "") -> str:
    cols = ", ".join(
        f"{overrides[name]} AS {name}" if name in overrides else f"CAST(NULL AS {sql_type}) AS {name}"
        for name, sql_type in db.table_columns(table).items()
    )
    return f"SELECT {cols} FROM range({count}) t(n) {extra_where}"


def _common(prefix: str) -> dict[str, str]:
    return {
        "producer": "'claude_code'",
        "parser_version": "1",
        "tenant_id": f"'{_TENANT}'",
        "project_id": f"'{_PROJECT}'",
        "event_id": f"'{prefix}' || lpad(CAST(n AS VARCHAR), 9, '0')",
        "session_id": _sid("n"),
        "session_started_at": f"{_START} + to_days(CAST(n % {_ROOTS} % 7 AS INTEGER))",
        "event_timestamp": f"{_START} + to_days(CAST(n % {_ROOTS} % 7 AS INTEGER)) + to_seconds(n % 3000)",
        "created_timestamp": f"{_START}",
    }


def _write(con: Any, table: str, selects: list[str], path: Path) -> None:
    con.execute(f"COPY ({' UNION ALL '.join(selects)}) TO '{path}' (FORMAT PARQUET, ROW_GROUP_SIZE {_ROW_GROUP})")


@pytest.fixture(scope="module")
def reader_con(tmp_path_factory: pytest.TempPathFactory) -> Any:
    root = tmp_path_factory.mktemp("reader_memory")
    writer = duckdb_connect.connect()
    writer.execute("SET TimeZone='UTC'")
    writer.execute(f"SET memory_limit='{ev.TELEMETRY_MEMORY_LIMIT}'")
    writer.execute(f"SET temp_directory='{root / 'write-spill'}'")
    files = {t: root / f"{t}.parquet" for t in db.TABLES}

    sessions = {**_common("ses"), "event_kind": "'open'", "event_id": "'ses' || lpad(CAST(n AS VARCHAR), 9, '0')"}
    sessions["session_id"] = _sid("n")
    _write(writer, "telemetry_sessions", [_select("telemetry_sessions", _ROOTS, sessions)], files["telemetry_sessions"])

    obs = {
        **_common("obs"),
        "observation_type": "'tool_call'",
        "observation_id": "'o' || CAST(n AS VARCHAR)",
        "metadata": "repeat('x', 790) || lpad(CAST(n AS VARCHAR), 10, '0')",
    }
    planted_obs = [10, 20]
    obs_conflict = {**obs, "metadata": "'conflicting'"}
    _write(
        writer,
        "telemetry_observations",
        [
            _select("telemetry_observations", _OBSERVATIONS, obs),
            _select("telemetry_observations", _OBSERVATIONS, obs_conflict, f"WHERE n IN ({', '.join(map(str, planted_obs))})"),
        ],
        files["telemetry_observations"],
    )

    trans = {**_common("trn"), "transcript_id": "'t' || CAST(n AS VARCHAR)", "content_sha256": "'h' || CAST(n AS VARCHAR)"}
    trans_conflict = {**trans, "content_sha256": "'other'"}
    _write(
        writer,
        "telemetry_transcripts",
        [
            _select("telemetry_transcripts", _TRANSCRIPTS, trans),
            _select("telemetry_transcripts", _TRANSCRIPTS, trans_conflict, "WHERE n = 5"),
        ],
        files["telemetry_transcripts"],
    )

    agents = {**_common("agt"), "agent_run_id": "'r' || CAST(n AS VARCHAR)", "host_run_ref": "'run-' || CAST(n AS VARCHAR)"}
    _write(writer, "telemetry_agents", [_select("telemetry_agents", _AGENTS, agents)], files["telemetry_agents"])
    writer.close()

    con = duckdb_connect.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"ATTACH ':memory:' AS {rt.TELEMETRY_CATALOG_ALIAS}")
    for table, path in files.items():
        con.execute(f"CREATE VIEW {rt.TELEMETRY_CATALOG_ALIAS}.{table} AS SELECT * FROM read_parquet('{path}')")
    projects = rt.resolve_table_spec("ops_projects").current_table
    ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in db.projects_columns())
    con.execute(f"CREATE TABLE {rt.TELEMETRY_CATALOG_ALIAS}.{projects} ({ddl})")
    db.insert_projects(con, [{"ulid": "p1", "project_id": _PROJECT, "tenant_id": _TENANT, "purpose": "real"}])
    con.execute(f"SET memory_limit='{ev.TELEMETRY_MEMORY_LIMIT}'")
    con.execute(f"SET temp_directory='{root / 'spill'}'")
    con.execute(f"SET max_temp_directory_size='{ev.TELEMETRY_MAX_TEMP_SIZE}'")
    yield con
    con.close()


def test_root_sessions_week_fits_the_reader_memory(reader_con: Any) -> None:
    rows = ev.execute(reader_con, verb="telemetry_root_sessions", params={"day_from": "2026-01-01", "day_to": "2026-01-07"})
    assert len(rows) == _ROOTS
    conflicted = {r["session_id"]: r for r in rows if r["derivation_conflicted"]}
    expected = {_session(i): (table, i) for table, i in _PLANTED}
    assert set(conflicted) == set(expected)
    for sid, (table, i) in expected.items():
        keys = {(k["table_name"], k["event_id"]) for k in conflicted[sid]["conflicted_grain_keys"]}
        prefix = "obs" if table == "telemetry_observations" else "trn"
        assert keys == {(table.removeprefix("telemetry_"), f"{prefix}{i:09d}")} or keys == {(table, f"{prefix}{i:09d}")}


def _session(i: int) -> str:
    return f"s{i % _ROOTS:05d}"
