"""prepare_batch (the pure half of append_events): rules, typed errors, compare exclusions from the projection and
intra-batch collapse honouring representation-only columns. duckdb-free so the fast tier runs it."""

from __future__ import annotations

import functools
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.row_rules.rules import RowRuleError
from src.telemetry.append import EventTableSpec, PreparedBatch, prepare_batch
from src.telemetry.gate import AppendError, GateError, GrainConflictError, RowRuleGateError
from src.telemetry.identity import IdentityError
from tests.fixtures.turn_capture_ducklake import load_specs

TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(seconds=30, microseconds=999_999)


def transcript(ref: str = "u1#0", payload: str = "hello", **over: Any) -> dict[str, Any]:
    data = payload.encode()
    row: dict[str, Any] = {
        "event_kind": "point",
        "event_timestamp": T0 + timedelta(seconds=5),
        "session_started_at": T0,
        "external_ref": ref,
        "entity_ref": ref,
        "session_ref": "sess",
        "observation_ref": "turn-1",
        "producer": "claude_code",
        "producer_version": "t",
        "parser_version": 2,
        "purpose": "prompt",
        "origin": "human",
        "content": payload,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "content_bytes": len(data),
        "content_truncated": False,
    }
    row.update(over)
    return {k: v for k, v in row.items() if v is not None}


@functools.lru_cache(maxsize=1)
def specs() -> dict[str, EventTableSpec]:
    return load_specs()


def prepare(rows, table="telemetry_transcripts", **kw) -> PreparedBatch:
    return prepare_batch(specs()[table], rows, tenant_id=TENANT, project_id=PROJECT, now=kw.pop("now", NOW))


def test_prepare_batch_applies_rules_and_derivation() -> None:
    prepared = prepare([transcript(), transcript("u2#0", "second")])
    assert (prepared.submitted, prepared.collapsed, len(prepared.rows)) == (2, 0, 2)
    row = prepared.rows[0]
    assert len(row["event_id"]) == 26 and len(row["transcript_id"]) == 26 and row["tenant_id"] == TENANT
    assert row["created_timestamp"] == datetime(2026, 1, 1, 12, 0, 30, 999000, tzinfo=timezone.utc)
    assert prepare([]).rows == []

    def rejected(over, rule, column):
        with pytest.raises(GateError) as raised:
            prepare([transcript(**over)])
        message = str(raised.value)
        assert "telemetry_transcripts" in message and rule in message and column in message, message

    rejected({"event_timestamp": T0 - timedelta(seconds=1)}, "not_before", "event_timestamp")
    rejected({"event_timestamp": NOW + timedelta(seconds=301)}, "max_after_write_seconds", "event_timestamp")
    rejected({"purpose": "nonsense"}, "accepted_values", "purpose")
    rejected({"content_sha256": "0" * 64}, "integrity", "content")
    rejected({"content_sha256": "NOT-HEX"}, "pattern", "content_sha256")
    rejected({"content": None, "content_sha256": "0" * 64}, "exactly_one_of", "content")
    rejected({"origin": None}, "not_null", "origin")
    with pytest.raises(GateError, match="BIGINT"):
        prepare([transcript(content_bytes=True)])
    with pytest.raises(GateError, match="unknown column"):
        prepare([transcript(surprise=1)])
    with pytest.raises(GateError, match="derived"):
        prepare([transcript(event_id="x")])
    with pytest.raises(GateError, match="required ref field"):
        prepare([{k: v for k, v in transcript().items() if k != "observation_ref"}])
    with pytest.raises(GateError, match="session_started_at"):
        prepare([{k: v for k, v in transcript().items() if k != "session_started_at"}])
    with pytest.raises(GateError, match="external_ref and event_timestamp"):
        prepare([{k: v for k, v in transcript().items() if k != "external_ref"} | {"entity_ref": "e"}])
    with pytest.raises(AppendError, match="tz-aware"):
        prepare([transcript()], now=datetime(2026, 1, 1))


def test_the_retry_of_a_passing_batch_is_judged_against_a_later_clock() -> None:
    row = transcript(event_timestamp=NOW + timedelta(seconds=299))
    first = prepare([row])
    later = prepare([row], now=NOW + timedelta(minutes=5))
    assert first.rows[0]["event_id"] == later.rows[0]["event_id"]


def test_compare_excluded_comes_from_projection() -> None:
    loaded = specs()
    structural = frozenset({"created_timestamp", "producer_version"})
    assert loaded["telemetry_transcripts"].compare_excluded == structural | {"content", "content_uri"}
    for table in ("telemetry_sessions", "telemetry_observations", "telemetry_agents"):
        assert loaded[table].compare_excluded == structural, table
    direct = EventTableSpec(table="telemetry_sessions", columns={"event_id": "VARCHAR"})
    assert direct.compare_excluded == structural and direct.rules.not_null == frozenset()
    assert loaded["telemetry_transcripts"].rules.content is not None
    assert not loaded["telemetry_transcripts"].rules.exemptions
    sets = loaded["telemetry_observations"].rules
    assert (
        sets.at_most
        == {
            "reasoning_tokens": "tokens_output",
            "cache_creation_1h_tokens": "tokens_cache_creation",
            "cache_creation_5m_tokens": "tokens_cache_creation",
        }
        and sets.not_before["event_timestamp"] == "session_started_at"
    )


def test_bundled_projection_refuses_inline_above_the_threshold() -> None:
    threshold = specs()["telemetry_transcripts"].rules.content.threshold
    assert prepare([transcript(payload="x" * threshold)]).submitted == 1
    with pytest.raises(GateError) as caught:
        prepare([transcript(payload="x" * (threshold + 1))])
    assert isinstance(caught.value, RowRuleGateError) and caught.value.rule == "inline_within_threshold"


def test_intra_batch_representation_change_collapses() -> None:
    # a representation change needs a threshold change, which one projection cannot hold: exempt the leg in this spec only
    base = specs()["telemetry_transcripts"]
    exempt = replace(base, rules=replace(base.rules, exemptions={"inline_within_threshold": ("test", "test")}))

    def prepare(rows):
        return prepare_batch(exempt, rows, tenant_id=TENANT, project_id=PROJECT, now=NOW)

    payload = "z" * 70_000
    digest = hashlib.sha256(payload.encode()).hexdigest()
    inline = transcript(payload=payload)
    spilled = transcript(payload=payload, content=None, content_uri=f"t/p/s/{digest}")
    prepared = prepare([inline, spilled])
    assert (prepared.submitted, prepared.collapsed, len(prepared.rows)) == (2, 1, 1)
    other = transcript(payload="y" * 70_000, content=None, content_uri="t/p/s/other")
    with pytest.raises(GrainConflictError, match="content_sha256"):
        prepare([inline, other])
    differing = transcript(payload=payload, purpose="response")
    with pytest.raises(GrainConflictError, match="purpose"):
        prepare([inline, differing])
    stamp_only = transcript(payload=payload, producer_version="other")
    assert prepare([inline, stamp_only]).collapsed == 1
    with pytest.raises(GateError, match="uri_above_threshold"):
        prepare([transcript(content=None, content_uri="t/p/s/x")])


def test_identifier_validation_rejects_unsafe_names() -> None:
    spec = EventTableSpec(table="bad name", columns={"event_id": "VARCHAR"})
    with pytest.raises(AppendError, match="safe SQL identifier"):
        prepare_batch(spec, [], tenant_id=TENANT, project_id=PROJECT)
    spec = EventTableSpec(table="telemetry_sessions", columns={"bad col": "VARCHAR"})
    with pytest.raises(AppendError, match="safe SQL identifier"):
        prepare_batch(spec, [], tenant_id=TENANT, project_id=PROJECT)


def test_rule_break_is_a_row_rule_gate_error_with_table_and_row() -> None:
    bad = transcript("u2#0", origin=None)
    with pytest.raises(RowRuleGateError) as caught:
        prepare([transcript(), bad])
    err = caught.value
    assert isinstance(err.__cause__, RowRuleError)
    assert (err.table, err.row, err.rule, err.column) == ("telemetry_transcripts", 1, "not_null", "origin")
    assert "hello" not in str(err)


def test_derivation_errors_are_gate_errors_with_both_reasons() -> None:
    def rejected(**over: Any) -> GateError:
        with pytest.raises(GateError) as caught:
            prepare([transcript(**over)])
        assert isinstance(caught.value.__cause__, IdentityError)
        return caught.value

    bad_ref = rejected(entity_ref="")
    assert (bad_ref.reason, bad_ref.column, bad_ref.table, bad_ref.row) == (
        "invalid_ref",
        "entity_ref",
        "telemetry_transcripts",
        0,
    )
    pre_epoch = datetime(1960, 1, 1, tzinfo=timezone.utc)
    assert (rejected(session_started_at=pre_epoch).reason, rejected(session_started_at=pre_epoch).column) == (
        "time_range",
        "session_started_at",
    )
    assert (rejected(event_timestamp=pre_epoch).reason, rejected(event_timestamp=pre_epoch).column) == (
        "time_range",
        "event_timestamp",
    )
    assert rejected(external_ref="").column == "external_ref"


def test_own_site_gate_errors_carry_column_and_reason() -> None:
    def rejected(row: dict[str, Any]) -> GateError:
        with pytest.raises(GateError) as caught:
            prepare([row])
        return caught.value

    full = transcript()
    cases = [
        ({k: v for k, v in full.items() if k != "session_started_at"}, "session_started_at", "not_null"),
        ({k: v for k, v in full.items() if k != "observation_ref"}, "observation_ref", "required_ref"),
        ({k: v for k, v in full.items() if k != "external_ref"} | {"entity_ref": "e"}, "external_ref", "not_null"),
        ({k: v for k, v in full.items() if k != "event_timestamp"}, "event_timestamp", "not_null"),
    ]
    for row, column, reason in cases:
        err = rejected(row)
        assert (err.column, err.reason, err.table, err.row) == (column, reason, "telemetry_transcripts", 0)
    kept = GateError("m", table="x", row=9)
    from src.telemetry.append import _locate

    _locate(kept, "y", 0)
    assert (kept.table, kept.row) == ("x", 9)


def test_bundled_projection_admits_billing_shape_local() -> None:
    from src.common.ducklake_scd2_schema import load_field_semantics  # noqa: PLC0415

    entry = load_field_semantics()["ops_tables"]["telemetry_observations"]
    spec = EventTableSpec.from_projection("telemetry_observations", entry)

    def model_call(ref: str, shape: str) -> dict[str, Any]:
        return {
            "event_kind": "point",
            "event_timestamp": T0 + timedelta(seconds=5),
            "session_started_at": T0,
            "external_ref": ref,
            "entity_ref": ref,
            "session_ref": "sess",
            "producer": "litellm",
            "producer_version": "t",
            "parser_version": 1,
            "observation_type": "model_call",
            "billing_shape": shape,
            "tokens_input": 1,
            "tokens_output": 1,
            "tokens_cache_read": 0,
            "tokens_cache_creation": 0,
            "reasoning_visibility": "none",
        }

    def run(rows: list[dict[str, Any]]) -> PreparedBatch:
        return prepare_batch(spec, rows, tenant_id=TENANT, project_id=PROJECT, now=NOW)

    assert run([model_call("m1", "local"), model_call("m2", "metered_marginal")]).submitted == 2
    with pytest.raises(GateError) as caught:
        run([model_call("m3", "free")])
    assert (caught.value.rule, caught.value.column) == ("accepted_values", "billing_shape")
