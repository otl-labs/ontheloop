"""The litellm producer (rec-5396): rules LM-1..LM-8 over hand-built events plus the golden corpus end to end."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.persona_backends import events as ev
from src.telemetry.append import prepare_batch
from src.turn_capture import litellm_producer as lp
from tests.fixtures.telemetry_blob_fakes import as_written
from tests.fixtures.turn_capture_corpus import PROJECT, TENANT, golden_digest, normalise
from tests.fixtures.turn_capture_ducklake import load_specs
from tests.fixtures.turn_capture_litellm_cases import (
    FB,
    HOST_RUN,
    HOST_RUN_LOCAL,
    LITELLM_BUILDERS,
    T0,
    call,
    litellm_case_names,
    load_litellm_case,
    rec,
)

ROOT = Path(__file__).resolve().parents[2]
PIN = T0 * 1000
HOST = HOST_RUN.encode()


def _map(events, host=HOST) -> lp.Mapped:
    mapped = lp.map_run(events, host)
    assert isinstance(mapped, lp.Mapped)
    return mapped


def _record(**over):
    import dataclasses

    return dataclasses.asdict(rec(**over))


def _attempt(**over):
    import dataclasses

    return ev.attempt_dict(dataclasses.replace(call(), **over))


def _turn(n=1, *, attempts=None, record="default", message=None, probe=None, known=True, started=PIN + 10, ended=PIN + 200):
    return ev.turn_record(
        turn=n,
        role="primary",
        attempts=[_attempt()] if attempts is None else attempts,
        attempts_known=known,
        probe_turn=0,
        record=_record() if record == "default" else record,
        failed_over=False,
        finish_reason="stop",
        tool_calls=[],
        tool_result_bytes=[],
        kind="terminal_valid",
        revision=0,
        message=message,
        started_at_ms=started,
        ended_at_ms=ended,
        probe_record=probe,
    )


def _events(turns, end="default", started=PIN):
    head = ev.header("c.yaml", "fixture_persona", 3, "d" * 64, "e" * 64, started)
    final = ev.end_record("success", None, len(turns), 0, PIN + 500) if end == "default" else end
    return {"header": head, "turns": turns, "end": final}


def _calls(mapped):
    return dict(mapped.batches)["telemetry_observations"]


def test_failed_attempt_emits_row():
    for outcome in ("outage", "credit_exhausted", "provider_error", "malformed_output", "empty_output"):
        turn = _turn(attempts=[_attempt(outcome=outcome), _attempt(attempt=2)])
        rows = _calls(_map(_events([turn]), HOST))
        loss, good = rows
        assert loss["outcome"] == "error" and loss["usage_final"] is False and "usage_source" not in loss
        assert json.loads(loss["metadata"]) == {"error": outcome, "loss": "usage_not_recorded"}
        assert [loss[c] for c in ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation")] == [0] * 4
        assert loss["reasoning_visibility"] == "none" and "reasoning_tokens" not in loss and "reasoning_effort" not in loss
        assert loss["name"] == "primary" and loss["attempt"] == 1 and loss["source_ordinal"] == 1
        assert good["outcome"] == "success" and good["source_ordinal"] == 2


@pytest.mark.parametrize(
    ("message", "tokens", "expected"),
    [
        ({"thinking_blocks": [{"type": "thinking", "thinking": "text"}]}, 5, "summarized"),
        ({"thinking_blocks": [{"type": "redacted_thinking", "data": "x"}], "reasoning_content": "r"}, 5, "redacted"),
        ({"thinking_blocks": [{"type": "thinking", "thinking": ""}]}, None, "redacted"),
        ({"reasoning_content": "raw"}, 5, "full"),
        ({"content": "x"}, 5, "omitted"),
        ({"content": "x"}, 0, "none"),
        ({"content": "x"}, None, "none"),
        (None, 7, "omitted"),
    ],
)
def test_reasoning_visibility_matrix(message, tokens, expected):
    assert lp.reasoning_visibility(message, tokens) == expected
    row = _calls(_map(_events([_turn(record=_record(reasoning_tokens=tokens), message=message)]), HOST))[0]
    assert row["reasoning_visibility"] == expected
    assert ("reasoning_tokens" in row) == (tokens is not None)


def test_probe_record_reasoning_visibility_ignores_the_message():
    turn = _turn(attempts=[_attempt(role=FB)], message={"reasoning_content": "raw"}, probe=_record(role=FB, response_id=None))
    probe = _calls(_map(_events([turn]), HOST))[0]
    assert probe["name"] == "cold_start_probe" and probe["reasoning_visibility"] == "omitted" and "attempt" not in probe


def test_rows_pass_kernel():
    specs = load_specs()
    content_rule = specs["telemetry_transcripts"].rules.content
    assert content_rule is not None
    threshold = content_rule.threshold
    for name in litellm_case_names():
        files, _ = load_litellm_case(name)
        mapped = _map(json.loads(files["events.json"]), files["host-run.json"].encode())
        assert isinstance(mapped, lp.Mapped), name
        for table, rows in mapped.batches:
            prepare_batch(specs[table], as_written(rows, threshold), tenant_id=TENANT, project_id=PROJECT)


def test_usage_final_follows_usage_absent():
    absent = _calls(_map(_events([_turn(record=_record(usage_absent=("tokens_output",)))]), HOST))[0]
    full = _calls(_map(_events([_turn()]), HOST))[0]
    assert absent["usage_final"] is False and full["usage_final"] is True
    assert absent["usage_source"] == full["usage_source"] == "record"


def test_lm1_refused_schema_and_deferred_inputs():
    old = _events([_turn()])
    old["header"]["schema"] = 1
    assert isinstance(lp.map_run(old, HOST), lp.Refused)
    assert isinstance(lp.map_run([], HOST), lp.Refused)
    assert isinstance(lp.map_run(_events([_turn()]), None), lp.Deferred)
    assert isinstance(lp.map_run(_events([_turn()]), b"{}"), lp.Deferred)
    no_pin = _events([_turn()])
    no_pin["header"]["started_at_ms"] = None
    assert isinstance(lp.map_run(no_pin, HOST), lp.Deferred)
    no_pin["header"]["started_at_ms"] = float(PIN)
    assert isinstance(lp.map_run(no_pin, HOST), lp.Deferred)


def _agents(mapped):
    return dict(mapped.batches)["telemetry_agents"]


def _sessions(mapped):
    return dict(mapped.batches)["telemetry_sessions"]


@pytest.mark.parametrize(
    ("end", "session", "run", "reason"),
    [
        (ev.end_record("success", None, 1, 0, PIN + 500), "success", "success", None),
        (ev.end_record("failure", "budget_exhausted", 1, 0, PIN + 500), "failed", "timeout", "budget_exhausted"),
        (ev.end_record("failure", "schema_invalid", 1, 0, PIN + 500), "failed", "failed", "schema_invalid"),
        (ev.end_record("aborted", None, 1, 0, PIN + 500, "transport_exception"), "failed", "failed", "transport_exception"),
    ],
)
def test_lm2_lm3_outcome_tables(end, session, run, reason):
    mapped = _map(_events([_turn()], end), HOST)
    open_row, close = _sessions(mapped)
    assert open_row["external_ref"].endswith("#0/open") and open_row["source_ordinal"] == 0
    assert open_row["workflow"] == "ci_rca" and open_row["execution_attempt"] == 1 and "outcome" not in open_row
    assert close["external_ref"].endswith("/end#0/close") and close["outcome"] == session
    assert close.get("failure_reason") == reason
    run_open, run_close = _agents(mapped)
    assert run_open["external_ref"].endswith("/run#0/open") and "model" not in run_open
    assert run_close["external_ref"].endswith("/end#0/run_close") and run_close["outcome"] == run
    assert run_close.get("error") == reason and run_close["model"] == "deepseek-v4-flash"
    assert run_open["definition_sha256"] == run_close["definition_sha256"] == "d" * 64
    assert run_open["agent_type"] == "ci_rca" and run_open["version"] == "3" and run_open["trigger"] == "workflow_run"
    assert run_open["host_run_ref"] == "gha:123456789/1/rca/1" and run_open["workflow_run_id"] == "123456789"
    assert run_open["entity_ref"].endswith("/run") and run_open["session_ref"] == open_row["entity_ref"]


def test_run_close_model_falls_back_to_requested_model_and_none():
    record = _record(model=None)
    assert _agents(_map(_events([_turn(record=record)]), HOST))[1]["model"] == "deepseek/deepseek-flash"
    assert "model" not in _agents(_map(_events([_turn(record=None, attempts=[])]), HOST))[1]


def test_lm4_probe_placement_and_attempts_unknown():
    probe = _record(role=FB, response_id=None, at=300)
    before = [
        _attempt(outcome="outage"),
        _attempt(role=FB, attempt=1),
    ]
    rows = _calls(_map(_events([_turn(attempts=before, probe=probe, record=_record(role=FB))]), HOST))
    assert [r["name"] for r in rows] == ["primary", "cold_start_probe", FB]
    after = [_attempt(outcome="outage")]
    rows = _calls(_map(_events([_turn(attempts=after, record=None, probe=probe)]), HOST))
    assert [r["name"] for r in rows] == ["primary", "cold_start_probe"]
    mapped = _map(_events([_turn(known=False, attempts=[]), _turn(2, record=None)]), HOST)
    assert _calls(mapped) == [] and mapped.diagnostics == {"attempts_unknown": 1, "ok_without_record": 1}


def test_lm5_both_ref_forms():
    with_id = _calls(_map(_events([_turn(record=_record(response_id="resp_1"))]), HOST))[0]
    assert with_id["entity_ref"] == "resp_1" and with_id["external_ref"] == "resp_1/model_call"
    without = _calls(_map(_events([_turn(2, record=_record(response_id=None))]), HOST))[0]
    assert without["entity_ref"] == "litellm:gha:123456789/1/rca/1/call/2/1"
    assert without["external_ref"] == without["entity_ref"] + "/model_call"
    assert without["session_ref"] == "litellm:gha:123456789/1/rca/1" and "parent_observation_ref" not in without


def test_lm7_cancelled_row():
    flight = _attempt(outcome="cancelled", latency_ms=0)
    row = _calls(_map(_events([_turn(attempts=[flight], record=None)]), HOST))[0]
    assert row["outcome"] == "interrupted" and json.loads(row["metadata"]) == {"loss": "cancelled"}
    assert row["usage_final"] is False and row["tokens_output"] == 0 and row["provider"] == "deepseek"
    probe = _attempt(outcome="cancelled", role=FB, attempt=0)
    assert "attempt" not in _calls(_map(_events([_turn(attempts=[probe], record=None)]), HOST))[0]
    no_clock = _attempt(outcome="outage", latency_ms=None, started_at_ms=None)
    stamp = _calls(_map(_events([_turn(attempts=[no_clock], record=None, started=PIN + 77)]), HOST))[0]
    assert stamp["event_timestamp"] == lp._at(PIN + 77)


def test_lm8_prefix_rule_and_halt():
    turns = [_turn(1), _turn(2, record=_record(at=300, response_id="resp_b"))]
    final = _map(_events(turns), HOST)
    prefix = _map(_events(turns[:1], None), HOST)
    assert [r["external_ref"] for r in _calls(prefix)] == [r["external_ref"] for r in _calls(final)][:1]
    assert _calls(prefix)[0] == _calls(final)[0]
    assert len(_sessions(prefix)) == len(_agents(prefix)) == 1
    assert [r["source_ordinal"] for r in _calls(final)] == [1, 2]
    assert _sessions(final)[1]["source_ordinal"] == _agents(final)[1]["source_ordinal"] == 3
    assert [t for t, _ in final.batches] == ["telemetry_observations", "telemetry_agents", "telemetry_sessions"]
    early = _record(started_at_ms=PIN - 5, responded_at_ms=PIN - 1)
    with pytest.raises(lp.ProducerHalt):
        _map(_events([_turn(record=early)]), HOST)


def test_cases_match_builders():
    for name in litellm_case_names():
        assert load_litellm_case(name)[0] == LITELLM_BUILDERS[name](), name


def test_golden_rows():
    rows_by = {}
    for name in litellm_case_names():
        files, expected = load_litellm_case(name)
        mapped = _map(json.loads(files["events.json"]), files["host-run.json"].encode())
        rows_by[name] = normalise(mapped.batches)
        assert rows_by[name] == expected, name
    assert len(golden_digest(rows_by, list(rows_by))) == 64


def test_golden_cases_hold_the_declared_shapes():
    def rows(name):
        return load_litellm_case(name)[1]

    assert {r["outcome"] for r in rows("deadline_cancelled.json") if r["table"] == "telemetry_observations"} == {
        "error",
        "interrupted",
    }
    assert [r["name"] for r in rows("fail_over_probe.json") if r["table"] == "telemetry_observations"].count(
        "cold_start_probe"
    ) == 1
    assert not [r for r in rows("aborted_transport.json") if r["table"] == "telemetry_observations"]
    assert not [r for r in rows("partial_no_end.json") if r["event_kind"] == "close"]
    assert {r["reasoning_visibility"] for r in rows("single_call.json") if "reasoning_visibility" in r} == {"full"}
    anthropic = [r for r in rows("anthropic_thinking.json") if "cache_creation_1h_tokens" in r]
    assert anthropic and {r["reasoning_visibility"] for r in anthropic} == {"summarized", "none"}


def test_imports_stay_inside_turn_capture():
    tree = ast.parse((ROOT / "src/turn_capture/litellm_producer.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert all(
        n in sys.stdlib_module_names or n.split(".")[0] in sys.stdlib_module_names or n.startswith("src.turn_capture")
        for n in names
    ), names
    banned = (
        "src.telemetry.append",
        "src.telemetry.gate",
        "src.telemetry_client",
        "boto3",
        "botocore",
        "urllib.request",
        "http.client",
        "ssl",
        "socket",
    )
    code = (
        "import sys, json\nsys.path.insert(0, %r)\nimport src.turn_capture.litellm_producer\n"
        "print(json.dumps([m for m in %r if m in sys.modules]))" % (str(ROOT), banned)
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    assert json.loads(out.stdout) == []


def test_run_persona_never_imports_the_producer():
    for rel in ("scripts/run_persona.py", "src/persona_backends"):
        path = ROOT / rel
        for source in [path] if path.is_file() else sorted(path.glob("*.py")):
            assert "litellm_producer" not in source.read_text(encoding="utf-8"), source


def test_local_root_session_and_run_rows() -> None:
    events = json.loads(load_litellm_case("local_container_run.json")[0]["events.json"])
    mapped = _map(events, HOST_RUN_LOCAL.encode())
    tables = dict(mapped.batches)
    assert {r["execution_attempt"] for r in tables["telemetry_sessions"]} == {1}
    assert {(r["trigger"], r.get("workflow_run_id")) for r in tables["telemetry_agents"]} == {("manual", None)}
    assert {r["parser_version"] for rows in tables.values() for r in rows} == {2}
