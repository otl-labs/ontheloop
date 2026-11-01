"""The rendered R4/R5 dedupe block and conflict report on in-memory DuckDB (Decision 207).

Module-level importorskip: the fast tier installs no duckdb, so these nodes run in the full tier (pre-handoff and on main),
deliberately not integration-marked. The block under test is the SAME text the verbs render (compose / dedupe_ctes /
conflict_select), with the table names, the scope and the compared columns swapped for the vector tables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

duckdb = pytest.importorskip("duckdb")

from src.common import ducklake_event_read_sql as sql  # noqa: E402

_VECTORS = yaml.safe_load((Path(__file__).parents[1] / "telemetry" / "fixtures" / "dedupe_vectors.yaml").read_text("utf-8"))[
    "vectors"
]
_TABLES = {"sessions": "t_sessions", "observations": "t_observations", "agents": "t_agents", "transcripts": "t_transcripts"}
_COLUMNS = (
    "producer VARCHAR, event_id VARCHAR, parser_version BIGINT, session_id VARCHAR, event_kind VARCHAR, "
    "created_timestamp TIMESTAMPTZ, content VARCHAR, observation_id VARCHAR, observation_type VARCHAR, "
    "event_timestamp TIMESTAMPTZ, session_started_at TIMESTAMPTZ"
)
_BODY = (
    "SELECT 'o' AS t, event_id, parser_version FROM o UNION ALL SELECT 'a', event_id, parser_version FROM a UNION ALL "
    "SELECT 's', event_id, parser_version FROM s"
)
_SURVIVORS = sql.compose((sql.SESSIONS, sql.OBSERVATIONS, sql.AGENTS), _BODY)
_CONFLICTS = sql.compose(
    (sql.SESSIONS, sql.OBSERVATIONS, sql.AGENTS), "SELECT table_name, producer, event_id, parser_version FROM conflicts"
)


def _render(text: str, *, mutate: tuple[str, str] | None = None) -> str:
    if mutate is not None:
        assert mutate[0] in text, f"mutant target {mutate[0]!r} not in the rendered block"
        text = text.replace(*mutate)
    text = text.replace("{scope}", "TRUE")
    for placeholder, table in _TABLES.items():
        text = text.replace("{" + placeholder + "}", table).replace("{cmp_" + placeholder + "}", "content")
    return text


def _load(rows_by_table: dict[str, list[dict[str, Any]]]) -> Any:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    for table in _TABLES.values():
        con.execute(f"CREATE TABLE {table} ({_COLUMNS})")
    short = {"telemetry_sessions": "t_sessions", "telemetry_observations": "t_observations", "telemetry_agents": "t_agents"}
    for name, rows in rows_by_table.items():
        for row in rows:
            observation_type = "model_call" if "observation_id" in row else "tool_call"
            con.execute(
                f"INSERT INTO {short[name]} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    row["producer"],
                    row["event_id"],
                    row["parser_version"],
                    row["session_id"],
                    row.get("event_kind"),
                    row["created_timestamp"],
                    row.get("content"),
                    row.get("observation_id"),
                    observation_type,
                    row.get("event_timestamp"),
                    row["created_timestamp"],
                ],
            )
    return con


def _survivors(con: Any, **kw: Any) -> dict[str, set[tuple[str, int]]]:
    out: dict[str, set[tuple[str, int]]] = {"o": set(), "a": set(), "s": set()}
    for table, event_id, version in con.execute(_render(_SURVIVORS, **kw)).fetchall():
        out[table].add((event_id, version))
    return out


def _conflicts(con: Any) -> set[tuple[str, str, str, int]]:
    return {tuple(r) for r in con.execute(_render(_CONFLICTS)).fetchall()}


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["name"] for v in _VECTORS])
def test_rendered_dedupe_passes_every_vector(vector: dict[str, Any]) -> None:
    con = _load(vector["rows"])
    survivors = _survivors(con)
    conflicts = _conflicts(con)
    short = {"telemetry_observations": "o", "telemetry_agents": "a"}
    for table, expected in vector["expected"].items():
        assert sorted(e for e, _ in survivors[short[table]]) == sorted(expected["surviving_event_ids"]), vector["name"]
        wanted = {(table, *key) for key in expected["conflicted_grain_keys"]}
        assert {c for c in conflicts if c[0] == table} == wanted, vector["name"]


_BUMP = {
    "telemetry_sessions": [
        {
            "producer": "claude_code",
            "event_id": "open",
            "parser_version": 1,
            "session_id": "s1",
            "event_kind": "open",
            "created_timestamp": "2026-09-25T10:00:00+00:00",
            "content": "v1",
        },
        {
            "producer": "claude_code",
            "event_id": "open",
            "parser_version": 2,
            "session_id": "s1",
            "event_kind": "open",
            "created_timestamp": "2026-09-25T11:00:00+00:00",
            "content": "v2",
        },
    ],
    "telemetry_observations": [
        {
            "producer": "claude_code",
            "event_id": "step-1",
            "parser_version": 1,
            "session_id": "s1",
            "created_timestamp": "2026-09-25T10:01:00+00:00",
            "content": "A",
        },
        {
            "producer": "claude_code",
            "event_id": "step-1",
            "parser_version": 2,
            "session_id": "s1",
            "created_timestamp": "2026-09-25T11:01:00+00:00",
            "content": "B",
        },
    ],
}
_TWO_VERSIONS = {
    "telemetry_observations": [
        {
            "producer": "litellm",
            "event_id": "e",
            "parser_version": 1,
            "session_id": "s3",
            "created_timestamp": "2026-09-25T10:01:00+00:00",
            "content": "A",
        },
        {
            "producer": "litellm",
            "event_id": "e",
            "parser_version": 2,
            "session_id": "s3",
            "created_timestamp": "2026-09-25T11:01:00+00:00",
            "content": "B",
        },
    ]
}
_NON_REPLAYABLE = {
    "telemetry_sessions": [
        {
            "producer": "litellm",
            "event_id": "c1",
            "parser_version": 1,
            "session_id": "s2",
            "event_kind": "close",
            "created_timestamp": "2026-09-25T10:00:00+00:00",
            "content": "x",
        },
        {
            "producer": "litellm",
            "event_id": "c2",
            "parser_version": 2,
            "session_id": "s2",
            "event_kind": "close",
            "created_timestamp": "2026-09-25T11:00:00+00:00",
            "content": "y",
        },
    ],
    "telemetry_observations": [
        {
            "producer": "litellm",
            "event_id": "obs-a",
            "parser_version": 1,
            "session_id": "s2",
            "created_timestamp": "2026-09-25T10:01:00+00:00",
            "content": "a",
        },
        {
            "producer": "litellm",
            "event_id": "obs-b",
            "parser_version": 2,
            "session_id": "s2",
            "created_timestamp": "2026-09-25T11:01:00+00:00",
            "content": "b",
        },
    ],
}


def test_dedupe_catches_keep_lowest_and_non_open_generation_mutants() -> None:
    """The vectors compare surviving event_ids only, so these two mutants pass them: survivors are compared by version here."""
    assert _survivors(_load(_BUMP))["o"] == {("step-1", 2)}
    bump = _load(_TWO_VERSIONS)
    assert _survivors(bump)["o"] == {("e", 2)}
    assert _survivors(bump, mutate=("parser_version DESC, created_timestamp", "parser_version ASC, created_timestamp"))[
        "o"
    ] != {("e", 2)}, "keep-lowest-version mutant must be distinguishable"

    non_replayable = _load(_NON_REPLAYABLE)
    assert _survivors(non_replayable)["o"] == {("obs-a", 1), ("obs-b", 2)}
    mutant = _survivors(non_replayable, mutate=("AND event_kind = 'open' GROUP BY", "GROUP BY"))
    assert mutant["o"] != {("obs-a", 1), ("obs-b", 2)}, "V* from non-open rows must be distinguishable"


def test_conflicted_keys_are_reported_never_resolved() -> None:
    race = {
        "telemetry_observations": [
            {
                "producer": "claude_code",
                "event_id": "e1",
                "parser_version": 1,
                "session_id": "s1",
                "created_timestamp": "2026-09-25T10:00:00+00:00",
                "content": "x",
            },
            {
                "producer": "claude_code",
                "event_id": "e1",
                "parser_version": 1,
                "session_id": "s1",
                "created_timestamp": "2026-09-25T10:00:01+00:00",
                "content": None,
            },
            {
                "producer": "claude_code",
                "event_id": "e2",
                "parser_version": 1,
                "session_id": "s1",
                "created_timestamp": "2026-09-25T10:00:00+00:00",
                "content": "same",
            },
            {
                "producer": "claude_code",
                "event_id": "e2",
                "parser_version": 1,
                "session_id": "s1",
                "created_timestamp": "2026-09-25T10:00:09+00:00",
                "content": "same",
            },
        ]
    }
    con = _load(race)
    assert _conflicts(con) == {("telemetry_observations", "claude_code", "e1", 1)}
    assert sorted(e for e, _ in _survivors(con)["o"]) == ["e1", "e2"]
