"""Mirror test for src/telemetry/request.py: every rule, its order and its value-free errors."""

from __future__ import annotations

import ast
import functools
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.telemetry.request import (
    GENERATION_MARKER_TABLE,
    AppendRequest,
    RequestError,
    TableShape,
    parse_append_request,
)
from tests.fixtures.turn_capture_ducklake import load_specs

SESSION = "2026-01-01T12:00:00.000Z"
SECRET = "SECRET-VALUE-9f3a"  # pragma: allowlist secret


@functools.lru_cache(maxsize=1)
def shapes() -> dict[str, TableShape]:
    return {name: TableShape.from_spec(spec) for name, spec in load_specs().items()}


def session_row(**over: Any) -> dict[str, Any]:
    row = {
        "event_kind": "open",
        "event_timestamp": SESSION,
        "session_started_at": SESSION,
        "external_ref": "s#0/open",
        "entity_ref": "sess",
        "producer": "claude_code",
        "producer_version": "t",
        "parser_version": 2,
        "workflow": "implement",
        "execution_attempt": 1,
    }
    return {**row, **over}


def transcript_row(**over: Any) -> dict[str, Any]:
    row = {
        "event_kind": "point",
        "event_timestamp": SESSION,
        "session_started_at": SESSION,
        "external_ref": "t#0",
        "entity_ref": "t#0",
        "session_ref": "sess",
        "observation_ref": "turn-1",
        "producer": "claude_code",
        "producer_version": "t",
        "parser_version": 2,
        "purpose": "prompt",
        "origin": "human",
        "content": "hello",
    }
    return {**row, **over}


def body(*batches: tuple[str, list[dict[str, Any]]], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": "append_telemetry",
        "project_ref": "repo:example",
        "batches": [{"table": table, "rows": rows} for table, rows in batches],
    }
    return {**base, **over}


def refused(request: dict[str, Any]) -> RequestError:
    with pytest.raises(RequestError) as caught:
        parse_append_request(request, shapes())
    assert SECRET not in str(caught.value) and SECRET not in repr(vars(caught.value))
    return caught.value


def test_allowlist_refuses_selector_and_identity_keys() -> None:
    ok = body(("telemetry_sessions", [session_row()]))
    assert isinstance(parse_append_request(ok, shapes()), AppendRequest)
    for key in ("catalog", "meta_schema", "data_path", "tenant_id", "project_id", SECRET):
        err = refused({**ok, key: "x"})
        assert (err.error_type, err.reason) == ("bad_request", "unknown_key")
        assert SECRET not in str(err)


def test_closed_table_set_each_table_once() -> None:
    row = session_row()
    err = refused(body(("not_a_table", [row])))
    assert (err.error_type, err.reason) == ("bad_request", "unknown_table")
    err = refused(body((SECRET, [row])))
    assert err.table is None
    err = refused(body(("telemetry_observations", [row]), ("telemetry_observations", [row])))
    assert (err.reason, err.table) == ("duplicate_table", "telemetry_observations")


def test_malformed_batch_shapes_are_refused() -> None:
    row = session_row()
    for batches in (
        "x",
        [],
        ["x"],
        [{"table": "telemetry_sessions"}],
        [{"table": "telemetry_sessions", "rows": [row], "extra": 1}],
        [{"table": 7, "rows": [row]}],
    ):
        assert refused(body(batches=batches)).error_type == "bad_request"
    for rows in ("x", [1], [[row]]):
        err = refused(body(("telemetry_sessions", rows)))
        assert (err.reason, err.table) == ("rows", "telemetry_sessions")
    for over in ({"project_ref": ""}, {"project_ref": 3}, {"component": "Bad Component"}, {"component": 3}):
        assert refused(body(("telemetry_sessions", [row]), **over)).error_type == "bad_request"


def test_request_module_imports_only_timestamps() -> None:
    tree = ast.parse(Path("src/telemetry/request.py").read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module is not None
            modules.add(node.module)
    foreign = {m for m in modules if m.split(".")[0] not in sys.stdlib_module_names and m != "__future__"}
    assert foreign == {"src.telemetry.timestamps"}


def test_lone_surrogate_strings_are_refused() -> None:
    lone = "a\ud800b"
    cases = [
        body(("telemetry_sessions", [session_row(workflow=lone)])),
        body(("telemetry_sessions", [session_row(**{lone: 1})])),
        body(("telemetry_sessions", [session_row()]), project_ref=lone),
        body(("telemetry_sessions", [session_row()]), component=lone),
        {**body(("telemetry_sessions", [session_row()])), lone: 1},
    ]
    for request in cases:
        err = refused(request)
        assert (err.error_type, err.reason) == ("bad_request", "invalid_unicode")
        assert lone not in str(err)
    located = refused(body(("telemetry_sessions", [session_row(), session_row(workflow=lone)])))
    assert (located.table, located.row, located.column) == ("telemetry_sessions", 1, None)
    paired = body(("telemetry_sessions", [session_row(workflow=json.loads('"\\ud83d\\ude00"'))]))
    assert parse_append_request(paired, shapes()).batches[0][1][0]["workflow"] == "\U0001f600"


def test_nul_in_catalog_bound_strings_is_refused() -> None:
    nul = "a\x00b"
    for request in (
        body(("telemetry_sessions", [session_row(**{nul: 1})])),
        body(("telemetry_sessions", [session_row()]), project_ref=nul),
        body(("telemetry_sessions", [session_row()]), component=nul),
        body(("telemetry_sessions", [session_row(producer=nul)])),
    ):
        assert refused(request).reason == "invalid_unicode"
    free_text = body(("telemetry_sessions", [session_row(workflow=nul)]))
    assert parse_append_request(free_text, shapes()).batches[0][1][0]["workflow"] == nul


def test_range_edge_offset_timestamp_is_a_schema_gate() -> None:
    err = refused(body(("telemetry_sessions", [session_row(event_timestamp="9999-12-31T23:59:59.999-23:59")])))
    assert (err.error_type, err.reason, err.column, err.table, err.row) == (
        "schema_gate",
        "wire_timestamp",
        "event_timestamp",
        "telemetry_sessions",
        0,
    )


def test_sessions_batch_must_be_last() -> None:
    sessions = ("telemetry_sessions", [session_row()])
    transcripts = ("telemetry_transcripts", [transcript_row()])
    err = refused(body(sessions, transcripts))
    assert (err.error_type, err.table) == ("write_order", GENERATION_MARKER_TABLE)
    assert parse_append_request(body(transcripts, sessions), shapes()).batches[-1][0] == GENERATION_MARKER_TABLE
    assert parse_append_request(body(transcripts), shapes()).batches[0][0] == "telemetry_transcripts"


def test_one_producer_and_parser_version_per_request() -> None:
    sessions = ("telemetry_sessions", [session_row()])
    for column, other in (("producer", "other"), ("parser_version", 3)):
        transcripts = ("telemetry_transcripts", [transcript_row(**{column: other})])
        err = refused(body(transcripts, sessions))
        assert (err.error_type, err.reason) == ("mixed_producer", column)
    absent = transcript_row()
    del absent["producer"]
    request = parse_append_request(body(("telemetry_transcripts", [absent]), sessions), shapes())
    assert (request.producer, request.parser_version) == ("claude_code", 2)


def test_empty_request_is_refused() -> None:
    assert refused(body(("telemetry_sessions", []))).reason == "empty_request"
    assert refused(body(("telemetry_sessions", []), ("telemetry_agents", []))).error_type == "bad_request"
    only_one_side = body(("telemetry_transcripts", [transcript_row()]), ("telemetry_sessions", []))
    assert len(parse_append_request(only_one_side, shapes()).batches) == 2


def test_component_grammar_and_default() -> None:
    sessions = ("telemetry_sessions", [session_row()])
    assert parse_append_request(body(sessions), shapes()).component == "unknown"
    assert parse_append_request(body(sessions, component="hook-stop_1"), shapes()).component == "hook-stop_1"
    for bad in ("", "1abc", "Upper", "a" * 65, "a b"):
        assert refused(body(sessions, component=bad)).reason == "component"


def test_wire_timestamps_need_exactly_three_fraction_digits() -> None:
    for value in ("2026-01-01T12:00:00Z", "2026-01-01T12:00:00.1Z", "2026-01-01T12:00:00.1234Z", "nope", 7):
        err = refused(body(("telemetry_sessions", [session_row(session_started_at=value)])))
        assert (err.error_type, err.column, err.reason) == ("schema_gate", "session_started_at", "wire_timestamp")
    raw = body(("telemetry_sessions", [session_row(event_timestamp="2026-01-01T13:00:00.250+01:00")]))
    parsed = parse_append_request(raw, shapes())
    assert parsed.batches[0][1][0]["event_timestamp"].isoformat() == "2026-01-01T12:00:00.250000+00:00"
    assert raw["batches"][0]["rows"][0]["event_timestamp"] == "2026-01-01T13:00:00.250+01:00"


def test_inline_content_size_is_left_to_the_kernel_rule() -> None:
    threshold = 65536
    over = ("telemetry_transcripts", [transcript_row(), transcript_row(content="é" * (threshold // 2 + 1))])
    parsed = parse_append_request(body(over), shapes())
    assert parsed.batches[0][0] == "telemetry_transcripts" and len(parsed.batches[0][1]) == 2
    assert not hasattr(shapes()["telemetry_transcripts"], "threshold")


def test_content_uri_is_refused_as_writer_derived() -> None:
    err = refused(body(("telemetry_transcripts", [transcript_row(content_uri="s3://x", content=None)])))
    assert (err.error_type, err.reason, err.column, err.table, err.row) == (
        "bad_request",
        "writer_derived",
        "content_uri",
        "telemetry_transcripts",
        0,
    )


def test_errors_never_carry_a_value() -> None:
    cases = [
        body(("telemetry_sessions", [session_row(workflow=SECRET, **{SECRET: 1})]), **{SECRET: 1}),
        body((SECRET, [session_row()])),
        body(("telemetry_sessions", [session_row(session_started_at=SECRET)])),
        body(("telemetry_transcripts", [transcript_row(content_uri=SECRET)])),
        body(("telemetry_sessions", [session_row(workflow="a\ud800" + SECRET)])),
        body(("telemetry_sessions", [session_row(producer=SECRET + "\x00")])),
        body(("telemetry_sessions", [session_row(producer=SECRET)]), ("telemetry_agents", [session_row(producer="p")])),
    ]
    for request in cases:
        err = refused(request)
        fields = {k: v for k, v in vars(err).items() if k != "args"}
        assert all(not isinstance(v, str) or SECRET not in v for v in fields.values())
