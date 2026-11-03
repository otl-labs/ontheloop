"""Derivation tests for the telemetry event-journal verbs on in-memory DuckDB (rec-4291).

Module-level importorskip and NOT integration-marked: the fast tier installs no duckdb (so it collects nothing here) and the
full tier, pre-handoff and on main, runs these. The four journals and ops_projects are built from the field_semantics event
projection under the telemetry catalog alias; golden cases are authored from the contract formulas in
tests/telemetry/fixtures/derivation_cases.yaml.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

duckdb = pytest.importorskip("duckdb")

from src.common import ducklake_event_read_verbs as verbs  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common.ducklake_reader_client import VERB_FIELDS  # noqa: E402
from src.telemetry.identity import derive_entity_key  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402

_FIXTURES = Path(__file__).parents[1] / "telemetry" / "fixtures"
_CASES = yaml.safe_load((_FIXTURES / "derivation_cases.yaml").read_text("utf-8"))
_FRICTION = yaml.safe_load((_FIXTURES / "friction_vectors.yaml").read_text("utf-8"))["vectors"]
_TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
_SUBAGENT_CASES = {"c11_sub_agents_take_state_and_duration_from_the_agent_run_pair", "c12_async_sub_agent_without_a_close"}
_ROW_KEYS = {"session", "at", "so", "created"}


def _session_ids(case: dict[str, Any]) -> dict[str, str]:
    return {
        name: derive_entity_key(
            "telemetry_sessions:session_id",
            _TENANT,
            _PROJECT,
            f"{case['id']}:{name}",
            datetime.fromisoformat(spec["started_at"]),
        )
        for name, spec in case["sessions"].items()
    }


def _load(case: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    ids = _session_ids(case)
    con = db.open_memory_catalog()
    db.insert_projects(
        con,
        [
            {"project_id": f"proj-{name}", "tenant_id": _TENANT, "project_ref": name, "purpose": spec["purpose"]}
            for name, spec in _CASES["projects"].items()
        ],
    )
    for table, rows in case["rows"].items():
        stored: list[dict[str, Any]] = []
        for row in rows:
            session = case["sessions"][row["session"]]
            out = db.base_row(
                **{k: v for k, v in row.items() if k not in _ROW_KEYS},
                session_id=ids[row["session"]],
                session_started_at=datetime.fromisoformat(session["started_at"]),
                event_timestamp=row["at"],
                source_ordinal=row.get("so"),
                project_id=f"proj-{session.get('project', 'real')}",
            )
            if "created" in row:
                out["created_timestamp"] = row["created"]
            if table == "telemetry_sessions" and session.get("parent"):
                out["parent_session_id"] = ids[session["parent"]]
            out.setdefault("entity_ref", f"{case['id']}:{row['event_id']}")
            stored.append(out)
        db.insert_rows(con, table, stored)
    return con, ids


def _resolve(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, dict) and set(value) == {"session"}:
        return ids[value["session"]]
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


def _same(actual: Any, expected: Any, key: str) -> bool:
    if key in {"as_of", "last_event_timestamp", "session_started_at"} and isinstance(expected, str):
        return actual == datetime.fromisoformat(expected)
    return actual == expected


def _run(case: dict[str, Any], expect: dict[str, Any]) -> None:
    con, ids = _load(case)
    params = dict(expect["params"])
    if "session" in expect:
        params["session_id"] = ids[expect["session"]]
    actual = ev.execute(con, verb=expect["verb"], params=params)
    wanted = _resolve(expect["rows"], ids)
    assert len(actual) == len(wanted), f"{case['id']}/{expect['verb']}: {len(actual)} rows, expected {len(wanted)}: {actual}"
    for got, want in zip(actual, wanted):
        for key, value in want.items():
            assert _same(got[key], value, key), f"{case['id']}/{expect['verb']}/{key}: {got[key]!r} != {value!r}"


def _expects(verb_names: set[str], *, subagents: bool | None = None) -> list[Any]:
    out = []
    for case in _CASES["cases"]:
        if subagents is not None and (case["id"] in _SUBAGENT_CASES) != subagents:
            continue
        for index, expect in enumerate(case["expect"]):
            if expect["verb"] in verb_names:
                out.append(pytest.param(case, expect, id=f"{case['id']}-{expect['verb']}-{index}"))
    return out


@pytest.mark.parametrize(("case", "expect"), _expects({"session_state_and_duration"}, subagents=False))
def test_session_state_golden_cases(case: dict[str, Any], expect: dict[str, Any]) -> None:
    _run(case, expect)


@pytest.mark.parametrize(("case", "expect"), _expects({"session_state_and_duration"}, subagents=True))
def test_sub_agent_state_from_the_agent_run_pair(case: dict[str, Any], expect: dict[str, Any]) -> None:
    _run(case, expect)


_SESSION_END_PREFIXES = ("c_at_rest", "c_mid_turn", "c_producer_close", "c_turn_", "c_blocked_turn", "c_duration_is")


def test_session_end_reads_the_latest_turn() -> None:
    cases = [c for c in _CASES["cases"] if c["id"].startswith(_SESSION_END_PREFIXES)]
    assert len(cases) >= 10
    for case in cases:
        for expect in case["expect"]:
            _run(case, expect)


@pytest.mark.parametrize(("case", "expect"), _expects({"session_process_event_rollup"}))
def test_process_event_rollup_golden_cases(case: dict[str, Any], expect: dict[str, Any]) -> None:
    _run(case, expect)


@pytest.mark.parametrize(("case", "expect"), _expects({"agent_run_state_and_duration", "agent_run_token_rollup"}))
def test_agent_run_state_and_tokens_golden_cases(case: dict[str, Any], expect: dict[str, Any]) -> None:
    _run(case, expect)


def _model_call(event_id: str, observation_id: str, at: str, so: int, **tokens: Any) -> dict[str, Any]:
    return {
        "session": "root",
        "event_id": event_id,
        "event_kind": "point",
        "observation_id": observation_id,
        "observation_type": "model_call",
        "at": at,
        "so": so,
        **tokens,
    }


def _rollup_case(case_id: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": case_id,
        "sessions": {"root": {"started_at": "2026-09-25T10:00:00+00:00"}},
        "rows": {
            "telemetry_sessions": [
                {"session": "root", "event_id": "e1", "event_kind": "open", "at": "2026-09-25T10:00:00+00:00", "so": 0}
            ],
            "telemetry_observations": observations,
        },
    }


def _rollup(case: dict[str, Any]) -> list[dict[str, Any]]:
    con, ids = _load(case)
    return ev.execute(con, verb="agent_run_token_rollup", params={"session_id": ids["root"]})


def test_agent_run_token_rollup_carries_cache_classes() -> None:
    plain = {"tokens_input": 100, "tokens_output": 10, "tokens_cache_read": 30, "tokens_cache_creation": 4}
    winner = {"tokens_input": 200, "tokens_output": 20, "tokens_cache_read": 50, "tokens_cache_creation": 9}
    loser = {"tokens_input": 999, "tokens_output": 99, "tokens_cache_read": 70, "tokens_cache_creation": 2}
    observations = [
        _model_call("m1", "m1", "2026-09-25T10:10:00+00:00", 1, **plain),
        _model_call("m1", "m1", "2026-09-25T10:10:00+00:00", 1, **plain),
        _model_call("m2", "m2", "2026-09-25T10:30:00+00:00", 2, **winner),
        {**_model_call("m2l", "m2", "2026-09-25T10:31:00+00:00", 2, **loser), "producer": "litellm"},
    ]
    (row,) = _rollup(_rollup_case("cache_classes", observations))
    assert row["tokens_input_total"] == 300
    assert row["tokens_output_total"] == 30
    assert row["tokens_cache_read_total"] == 80
    assert row["tokens_cache_creation_total"] == 13
    assert row["model_call_count"] == 2


def test_agent_run_token_rollup_empty_session_shares_one_convention() -> None:
    (row,) = _rollup(_rollup_case("cache_empty", []))
    totals = {
        row[k] for k in ("tokens_input_total", "tokens_output_total", "tokens_cache_read_total", "tokens_cache_creation_total")
    }
    assert len(totals) == 1 and totals <= {None, 0}


@pytest.mark.parametrize(("case", "expect"), _expects({"sessions_window", "sessions_window_all_purposes"}))
def test_sessions_window_real_projects_only_and_ordered(case: dict[str, Any], expect: dict[str, Any]) -> None:
    _run(case, expect)


def test_sessions_window_matches_the_per_session_verb_and_refuses_an_eight_day_window() -> None:
    case = next(c for c in _CASES["cases"] if c["id"].startswith("c16"))
    con, ids = _load(case)
    window = ev.execute(
        con,
        verb="sessions_window_all_purposes",
        params={"day_from": "2026-09-25", "day_to": "2026-09-26", "as_of": "2026-09-26T12:00:00+00:00"},
    )
    for row in window:
        single = ev.execute(
            con,
            verb="session_state_and_duration",
            params={"session_id": row["session_id"], "as_of": "2026-09-26T12:00:00+00:00"},
        )
        assert single == [row]
    assert [r["session_id"] for r in window] == [ids["first"], ids["drill_run"], ids["later"]]
    with pytest.raises(ev.EventReadInputError):
        ev.execute(
            con,
            verb="sessions_window",
            params={"day_from": "2026-09-25", "day_to": "2026-10-02", "as_of": "2026-09-26T12:00:00+00:00"},
        )
    assert (
        len(
            ev.execute(
                con,
                verb="sessions_window",
                params={"day_from": "2026-09-25", "day_to": "2026-10-01", "as_of": "2026-10-02T00:00:00+00:00"},
                limit=1,
            )
        )
        == 1
    )


@pytest.mark.parametrize(("case", "expect"), _expects({"model_call_cost_estimate"}))
def test_cost_source_never_a_false_zero(case: dict[str, Any], expect: dict[str, Any]) -> None:
    _run(case, expect)
    con, _ = _load(case)
    costs = ev.execute(con, verb="model_call_cost_estimate", params={"session_id": _session_ids(case)["root"]})
    assert all(r["cost_usd"] != 0 for r in costs)


@pytest.mark.parametrize("vector", _FRICTION, ids=[v["id"] for v in _FRICTION])
def test_friction_vectors(vector: dict[str, Any]) -> None:
    """All 17 committed vectors: the (ref, label) facts and the verb's totals from the one shared rule text."""
    session = derive_entity_key(
        "telemetry_sessions:session_id", _TENANT, _PROJECT, vector["id"], datetime.fromisoformat("2026-09-25T10:00:00+00:00")
    )
    con = db.open_memory_catalog()
    rows = []
    for obs_id, obs_type, kind, name, severity, outcome, parent, t in vector["rows"]:
        rows.append(
            db.base_row(
                session_id=session,
                session_started_at=datetime.fromisoformat("2026-09-25T10:00:00+00:00"),
                event_id=f"ev-{obs_id}-{kind}",
                event_kind=kind,
                event_timestamp=datetime.fromisoformat("2026-09-25T10:00:00+00:00").replace(second=t),
                source_ordinal=t,
                observation_id=obs_id,
                observation_type=obs_type,
                name=name,
                severity=severity,
                outcome=outcome,
                parent_observation_id=parent,
            )
        )
    db.insert_rows(con, "telemetry_observations", rows)
    from src.common import ducklake_event_read_sql as sql  # noqa: PLC0415

    facts = ev.execute_entry(
        con,
        ev.EventRead(
            verb="friction_facts",
            tables=(),
            sql=sql.FRICTION_FACTS_SQL,
            params=("session_id",),
            binding="session",
            data=verbs.FRICTION_RULES,
        ),
        {"session_id": session},
    )
    assert sorted((r["ref"], r["label"]) for r in facts) == sorted(tuple(e) for e in vector["expected"]), vector["id"]
    classes = {r["class"] for r in facts}
    rollup = ev.execute(con, verb="session_friction_rollup", params={"session_id": session})
    if not rows or not vector["expected"]:
        assert rollup == [] or (rollup[0]["rework_total"] == 0 and rollup[0]["exception_total"] == 0)
        return
    assert len(rollup) == 1
    assert rollup[0]["rework_total"] == sum(1 for r in facts if r["class"] == "rework")
    assert rollup[0]["exception_total"] == sum(1 for r in facts if r["class"] == "exception")
    assert sum(item["n"] for item in rollup[0]["label_counts"]) == len(facts)
    assert rollup[0]["classifier_version"] == 1
    assert classes <= {"rework", "exception"}


def test_health_count_reads_metadata_only() -> None:
    """Only the DuckLake metadata tables exist on this connection: a data-file read would fail with a catalog error."""
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS __ducklake_metadata_telemetry_catalog")
    meta = "__ducklake_metadata_telemetry_catalog"
    con.execute(f"CREATE TABLE {meta}.ducklake_table(table_id BIGINT, table_name VARCHAR, end_snapshot BIGINT)")
    con.execute(f"CREATE TABLE {meta}.ducklake_table_stats(table_id BIGINT, record_count BIGINT)")
    con.execute(
        f"INSERT INTO {meta}.ducklake_table VALUES (1, 'telemetry_sessions', NULL), (2, 'telemetry_observations', NULL), "
        "(3, 'telemetry_agents', NULL), (4, 'telemetry_transcripts', NULL), (5, 'telemetry_sessions', 9), (6, 'other', NULL)"
    )
    con.execute(f"INSERT INTO {meta}.ducklake_table_stats VALUES (1, 4), (2, 17), (5, 99)")
    rows = ev.execute(con, verb="telemetry_health_count", params={})
    assert [(r["table_name"], r["record_count"]) for r in rows] == [
        ("telemetry_agents", 0),
        ("telemetry_observations", 17),
        ("telemetry_sessions", 4),
        ("telemetry_transcripts", 0),
    ]
    assert {r["meta_schema"] for r in rows} == {""}
    assert "telemetry_catalog.telemetry_" not in ev.render_sql(verbs.EVENT_READS["telemetry_health_count"])


def test_verb_fields_match_event_verb_output_columns() -> None:
    con = db.open_memory_catalog()
    session = "01M3C03V80C6ZZ53H4MAGFS7Z4"
    meta = "__ducklake_metadata_telemetry_catalog"
    con.execute(f"ATTACH ':memory:' AS {meta}")
    con.execute(f"CREATE TABLE {meta}.ducklake_table(table_id BIGINT, table_name VARCHAR, end_snapshot BIGINT)")
    con.execute(f"CREATE TABLE {meta}.ducklake_table_stats(table_id BIGINT, record_count BIGINT)")
    for verb, entry in verbs.EVENT_READS.items():
        params = {
            "session_id": session,
            "as_of": "2026-09-26T12:00:00+00:00",
            "day_from": "2026-09-25",
            "day_to": "2026-09-25",
            "offset": 0,
            "max_rows": 10,
        }
        if entry.data.get("response") == "content":
            rows = _seeded_content_rows(con, session)
            assert tuple(rows[0]) == VERB_FIELDS[verb], verb
            continue
        sql = ev.render_sql(entry)
        values = ev.bind_values(entry, {k: v for k, v in params.items() if k in entry.params})
        used = {n for n in values if f"${n}" in sql}
        cursor = con.execute(sql, {n: values[n] for n in used})
        assert tuple(d[0] for d in cursor.description) == VERB_FIELDS[verb], verb


def test_render_data_accepts_only_integers_identifiers_and_the_rules_list() -> None:
    assert verbs.render_data({"idle_seconds": 5, "run_label": "repeated_tool_failure"}) == {
        "idle_seconds": "5",
        "run_label": "repeated_tool_failure",
    }
    assert "CAST(NULL AS VARCHAR)" in verbs.render_data(dict(verbs.FRICTION_RULES))["rules"]
    for bad in ("x; DROP TABLE t", "has space", 1.5, None):
        with pytest.raises(ValueError, match="bare identifier"):
            verbs.render_data({"key": bad})


_CONTENT_EVENT = "01CRZ3NDEKTSV4RRFFQ69G5FA"


def _content_params(session: str, event_suffix: str, **over: Any) -> dict[str, Any]:
    base = {
        "session_id": session,
        "producer": "claude_code",
        "event_id": _CONTENT_EVENT + event_suffix,
        "parser_version": 3,
        "offset": 0,
        "max_bytes": 1024,
    }
    return {**base, **over}


def _transcript(session: str, started: datetime, event_suffix: str, **over: Any) -> dict[str, Any]:
    text = over.pop("text", "stored text")
    return db.base_row(
        event_id=_CONTENT_EVENT + event_suffix,
        event_kind="point",
        event_timestamp=started,
        session_id=session,
        session_started_at=started,
        parser_version=3,
        content=text,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        content_bytes=len(text.encode()),
        **over,
    )


def _seeded_content_rows(con: Any, session: str) -> list[dict[str, Any]]:
    entry = verbs.EVENT_READS["transcript_content"]
    values = ev.bind_values(entry, _content_params(session, "V"))
    db.insert_rows(con, "telemetry_transcripts", [_transcript(session, values["part_from"], "V")])
    return ev.execute(con, verb="transcript_content", params=_content_params(session, "V"))


def test_transcript_content_sql_resolves_on_a_real_catalog() -> None:
    started = datetime(2026, 9, 25, 10, 30, tzinfo=timezone.utc)
    session = derive_entity_key("telemetry_sessions:session_id", _TENANT, _PROJECT, "content", started)
    other = derive_entity_key("telemetry_sessions:session_id", _TENANT, _PROJECT, "other", started)
    entry = verbs.EVENT_READS["transcript_content"]
    con = db.open_memory_catalog()
    uri = "t/p/s/" + "ab" * 32
    object_row = {**_transcript(session, started, "B", text="x" * 10), "content": None, "content_uri": uri}
    db.insert_rows(
        con,
        "telemetry_transcripts",
        [
            _transcript(session, started, "A"),
            _transcript(session, started, "A", created_timestamp="2026-09-25T11:00:00+00:00"),
            object_row,
            _transcript(session, started, "C", text="one"),
            _transcript(session, started, "C", text="two"),
            _transcript(session, started, "D", model="a"),
            _transcript(session, started, "D", model="b"),
            _transcript(session, started, "E", model=None),
            _transcript(session, started, "E", model="b"),
            {**_transcript(session, started, "G"), "parser_version": 2},
            _transcript(other, started, "F"),
        ],
    )

    def resolve(suffix: str, **over: Any) -> list[dict[str, Any]]:
        params = _content_params(session, suffix, **over)
        sql, values = ev.render_sql(entry), ev.bind_values(entry, params)
        cursor = con.execute(sql, {n: values[n] for n in values if f"${n}" in sql})
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    assert resolve("Z") == []
    assert resolve("F") == []  # another session's row is outside the partition and session binding
    (a1, a2) = resolve("A")
    assert a1["compared_versions"] == a2["compared_versions"] == 1 and a1["content"] == "stored text"
    (b,) = resolve("B")
    assert b["content"] is None and b["content_uri"] == uri and b["compared_versions"] == 1
    for suffix in ("C", "D", "E"):
        rows = resolve(suffix)
        assert len(rows) == 2 and {r["compared_versions"] for r in rows} == {2}, suffix
    assert len(resolve("G", parser_version=3)) == 0 and len(resolve("G", parser_version=2)) == 1
    assert set(resolve("A")[0]) == {
        "content",
        "content_uri",
        "content_sha256",
        "content_bytes",
        "content_omitted_reason",
        "compared_versions",
    }
