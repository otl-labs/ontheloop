"""CONCERN: scripts/ducklake_smoke/lambda_telemetry_reader_gates.py (slice 2c, Decision 236 cl.2-3).

The reader's deploy gate on a consistent fake, each of its eight red cases, the session and verification-verb checks
(moved here from the shared reader's gate, which serves no event verb since slice 2c's cleanup), and the Decision 101 leak
guard. The URL and every response body stay out of the output.
"""

from __future__ import annotations

import json

# boto3 is patched by string below; the import makes the heavy-dep requirement visible to the fast tier's collect pass.
import boto3  # noqa: F401
import pytest
from botocore.exceptions import ClientError

from scripts.ducklake_smoke import core, lambda_telemetry_reader_gates
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_reader_client import VERB_FIELDS
from tests.fixtures.ducklake_smoke_fakes import _Resp

SUCCESS_LINE = (
    "LAMBDA_TELEMETRY_READER OK unsigned=403 describe=200 attach_check=ok query_ops=400 content=400 direct_invoke=denied"
)
SERVED = sorted(set(EVENT_READS) - {"transcript_content"})
TABLES = ["telemetry_agents", "telemetry_observations", "telemetry_sessions", "telemetry_transcripts"]
FAKE_URL_HOST = "abcdefghij" + ".lambda-url.eu-west-2.on.aws"
LEAKY = "secret-body " + FAKE_URL_HOST

DESCRIBE_OK = {
    "ok": True,
    "function": "telemetry_reader",
    "catalog": {"meta_schema": "ducklake_telemetry", "data_path_is_smoke": False},
    "verbs": {name: {"kind": "event_journal"} for name in SERVED},
}
ATTACH_OK = {
    "ok": True,
    "meta_schema": "ducklake_telemetry",
    "data_path_is_smoke": False,
    "catalog_login": "ducklake_telemetry_reader",
    "read_only": True,
}
HEALTH_OK = {"ok": True, "rows": [{"table_name": t, "record_count": 0} for t in TABLES]}
EMPTY_OK = {"ok": True, "rows": [], "row_count": 0}
INPUT_400 = {"ok": False, "error_type": "named_read_input", "error": "session_id is not canonical"}
MARKER = "MARKER"
USAGE_VERBS = ("model_usage_rollup", "tool_use_rollup", "session_startup_baseline")


def _j(status, payload):
    return _Resp(status, payload, text=json.dumps(payload))


class _Client:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"StatusCode": 204}


def _denied(code="AccessDeniedException"):
    return ClientError({"Error": {"Code": code, "Message": LEAKY}}, "Invoke")


@pytest.fixture
def wired(monkeypatch):
    state = {
        "unsigned": _Resp(403, None, text='{"Message":"Forbidden"}'),
        "describe": _Resp(200, DESCRIBE_OK),
        "attach": _Resp(200, ATTACH_OK),
        "query_ops": _Resp(400, {"ok": False, "error": "unknown action"}),
        "content": _Resp(400, {"ok": False, "error_type": "unknown_verb"}),
        "ops_verb": _Resp(400, {"ok": False, "error_type": "unknown_verb"}),
        "health": _Resp(200, HEALTH_OK),
        "window": _j(200, EMPTY_OK),
        "usage": {name: _j(200, EMPTY_OK) for name in USAGE_VERBS},
        "absent": _j(200, EMPTY_OK),
        "bad_session": _j(400, INPUT_400),
        "tree": _j(200, EMPTY_OK),
        "bad_tree": _j(400, INPUT_400),
        "verification_window": _j(200, EMPTY_OK),
        "client": _Client(_denied()),
        "sent": [],
    }

    def fake_invoke(url, payload, **kw):
        state["sent"].append((payload, kw.get("sign", True)))
        if kw.get("sign", True) is False:
            return state["unsigned"]
        action = payload["action"]
        if action == "named_read":
            verb = payload["verb"]
            if verb == "transcript_content":
                return state["content"]
            if verb == "open_recs_tally":
                return state["ops_verb"]
            if verb in USAGE_VERBS:
                return state["usage"][verb]
            params = payload.get("params") or {}
            refused = MARKER in str(params.get("session_id", "")) or params.get("max_rows") == 0
            if verb == "session_state_and_duration":
                return state["bad_session"] if refused else state["absent"]
            if verb == "session_tree_rows":
                return state["bad_tree"] if refused else state["tree"]
            if verb in ("telemetry_orphan_counts", "telemetry_root_sessions"):
                return state["verification_window"]
            return state["health"] if verb == "telemetry_health_count" else state["window"]
        return {"describe": state["describe"], "attach_check": state["attach"], "query_ops": state["query_ops"]}[action]

    class Session:
        def __init__(self, profile_name=None):
            pass

        def client(self, name, region_name=None):
            return state["client"]

    monkeypatch.setattr(core, "_function_url", lambda role: "https://fake-url.invalid")
    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    monkeypatch.setattr("boto3.Session", Session)
    return state


def test_happy_path_prints_the_fixed_line(wired, capsys):
    lambda_telemetry_reader_gates.lambda_telemetry_reader()
    out = capsys.readouterr().out
    assert out.strip().splitlines() == [SUCCESS_LINE]
    assert "fake-url" not in out
    assert wired["client"].calls == [
        {"FunctionName": "agent-platform-ducklake-telemetry-reader", "InvocationType": "DryRun", "Payload": b"{}"}
    ]
    verbs = [p.get("verb") for p, _ in wired["sent"] if p["action"] == "named_read"]
    assert verbs == [
        "transcript_content",
        "open_recs_tally",
        "telemetry_health_count",
        "sessions_window",
        "model_usage_rollup",
        "tool_use_rollup",
        "session_startup_baseline",
        "session_state_and_duration",
        "session_state_and_duration",
        "session_tree_rows",
        "session_tree_rows",
        "session_tree_rows",
        "telemetry_orphan_counts",
        "telemetry_root_sessions",
    ]


def _extra_verb():
    verbs = {**DESCRIBE_OK["verbs"], "transcript_content": {"kind": "event_journal"}}
    return {"describe": _Resp(200, {**DESCRIBE_OK, "verbs": verbs})}


_RED = {
    "smoke_binding": (
        {"describe": _Resp(200, {**DESCRIBE_OK, "catalog": {"meta_schema": "ducklake_smoke", "data_path_is_smoke": True}})}
    ),
    "wrong_login": ({"attach": _Resp(200, {**ATTACH_OK, "catalog_login": "ducklake_telemetry_writer"})}),
    "read_only_false": ({"attach": _Resp(200, {**ATTACH_OK, "read_only": False})}),
    "query_ops_200": ({"query_ops": _Resp(200, {"ok": True})}),
    "content_200": ({"content": _Resp(200, {"ok": True, "rows": []})}),
    "dry_run_success": ({"client": _Client()}),
    "missing_event_table": ({"health": _Resp(200, {"ok": True, "rows": HEALTH_OK["rows"][:3]})}),
    "extra_served_verb": _extra_verb(),
}


@pytest.mark.parametrize("case", sorted(_RED))
def test_each_red_case_fails_the_gate(wired, capsys, case):
    wired.update(_RED[case])
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_reader_gates.lambda_telemetry_reader()
    assert str(info.value).startswith("LAMBDA_TELEMETRY_READER FAIL:")
    assert SUCCESS_LINE not in capsys.readouterr().out


def test_failure_text_never_carries_a_body_or_url(wired, capsys):
    wired["attach"] = _Resp(500, {"ok": False, "error_type": "unmapped", "error": LEAKY}, text=LEAKY)
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_reader_gates.lambda_telemetry_reader()
    wired["attach"] = _Resp(200, ATTACH_OK)
    wired["client"] = _Client(_denied("ThrottlingException"))
    with pytest.raises(core.SmokeTestFailure) as info2:
        lambda_telemetry_reader_gates.lambda_telemetry_reader()
    captured = capsys.readouterr()
    for text in (str(info.value), str(info2.value), captured.out, captured.err):
        assert "secret-body" not in text
        assert "lambda-url" not in text
        assert "fake-url" not in text
    assert "status=500" in str(info.value)
    assert "ThrottlingException" in str(info2.value)


PLANTED = "PLANTED-ROW-VALUE-7731"


def _gate_fails(wired, capsys, match):
    with pytest.raises(core.SmokeTestFailure, match=match) as info:
        lambda_telemetry_reader_gates.lambda_telemetry_reader()
    captured = capsys.readouterr()
    assert SUCCESS_LINE not in captured.out
    assert PLANTED not in str(info.value) and MARKER not in str(info.value) and "fake-url" not in str(info.value)
    assert str(info.value).startswith("LAMBDA_TELEMETRY_READER FAIL:")


def test_reader_gate_covers_the_verification_verbs(wired, capsys):
    lambda_telemetry_reader_gates.lambda_telemetry_reader()
    capsys.readouterr()
    trees = [p["params"] for p, _ in wired["sent"] if p.get("verb") == "session_tree_rows"]
    assert len(trees) == 3 and {t["max_rows"] for t in trees} == {0, 5}
    window = "verification_window"
    for key, answer, match in (
        ("tree", _j(200, {"ok": True, "rows": [{"x": PLANTED}], "row_count": 1}), "did not answer zero rows"),
        ("bad_tree", _j(200, EMPTY_OK), "must answer 400 named_read_input"),
        ("bad_tree", _j(400, {**INPUT_400, "error": "not-a-session-id-MARKER"}), "without echoing it"),
        (window, _j(500, {"ok": False, "rows": [{"x": PLANTED}]}), "did not answer over today's window"),
        (window, _j(200, {"ok": False, "rows": [{"x": PLANTED}]}), "did not answer over today's window"),
    ):
        saved, wired[key] = wired[key], answer
        _gate_fails(wired, capsys, match)
        wired[key] = saved


def test_reader_gate_refuses_a_malformed_session_without_echo(wired, capsys):
    for key, answer, match in (
        ("bad_session", _j(400, {**INPUT_400, "error": "not-a-session-id-MARKER"}), "malformed session_id was echoed"),
        ("bad_session", _j(500, {"ok": False, "error_type": "unmapped"}), "malformed session status=500"),
        ("bad_session", _j(200, EMPTY_OK), "malformed session status=200"),
        ("absent", _j(200, {"ok": True, "rows": [{"x": PLANTED}], "row_count": 1}), "absent session_id returned rows"),
        ("absent", _j(500, {"ok": False, "error_type": "unmapped"}), "absent session status=500"),
    ):
        saved, wired[key] = wired[key], answer
        _gate_fails(wired, capsys, match)
        wired[key] = saved


def test_reader_gate_reads_the_usage_verbs(wired, capsys):
    lambda_telemetry_reader_gates.lambda_telemetry_reader()
    assert capsys.readouterr().out.strip().splitlines() == [SUCCESS_LINE]
    sent = [p for p, _ in wired["sent"] if p.get("verb") in USAGE_VERBS]
    assert [p["verb"] for p in sent] == list(USAGE_VERBS) and all(set(p["params"]) == {"day_from", "day_to"} for p in sent)
    for verb in USAGE_VERBS:
        columns = dict.fromkeys(VERB_FIELDS[verb])
        wired["usage"][verb] = _j(200, {"ok": True, "rows": [columns], "row_count": 1})
        lambda_telemetry_reader_gates.lambda_telemetry_reader()
        capsys.readouterr()
        short = dict.fromkeys(list(columns)[1:], PLANTED)
        for status, body, match in (
            (200, {"ok": True, "rows": [short], "row_count": 1}, f"{verb} rows do not carry exactly"),
            (500, {"ok": False, "error_type": "unmapped", "rows": [{"x": PLANTED}]}, f"{verb} status=500 error_type=unmapped"),
            (504, {"ok": False, "error_type": "read_deadline_exceeded"}, f"{verb} status=504"),
        ):
            wired["usage"][verb] = _j(status, body)
            _gate_fails(wired, capsys, match)
        wired["usage"][verb] = _j(200, EMPTY_OK)
