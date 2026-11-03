"""CONCERN: scripts/ducklake_smoke/lambda_telemetry_gates.py (T2.36 slice 2a-2, deploy wiring).

The --lambda-telemetry-smoke checks against the twin (the heavy gate since the production flip), each with a red case,
and the Decision 101 leak guards. The URL never appears. The production writer's light gate is in
test_lambda_telemetry_production_gate.py.
"""

from __future__ import annotations

import re
import threading

# boto3 is patched by string below; the import makes the heavy-dep requirement visible to the fast tier's collect pass.
import boto3  # noqa: F401
import pytest
from botocore.exceptions import ClientError

from scripts.ducklake_smoke import core, lambda_telemetry_gates
from tests.fixtures.ducklake_smoke_fakes import _Resp
from tests.fixtures.telemetry_append_fakes import LiveFake

FAKE_ACCT = "123456789012"
FAKE_ARN = "arn:" + f"aws:lambda:eu-west-2:{FAKE_ACCT}:function:agent-platform-ducklake-telemetry-writer"
FAKE_HOST = "abcdefghij" + ".lambda-url.eu-west-2.on.aws"
SUCCESS_LINE = (
    "LAMBDA_TELEMETRY OK unsigned=403 describe=200 attach_check=ok unknown_action=400 direct_invoke=denied target=twin"
)

DESCRIBE_OK = {
    "ok": True,
    "actions": ["attach_check", "describe"],
    "catalog": {"meta_schema": core.META_SCHEMA, "data_path_is_smoke": True},
    "blob_protocol": {
        "version": 1,
        "inline_threshold_bytes": 65536,
        "cap_bytes": 8388608,
        "max_items": 200,
        "grant_ttl_seconds": 900,
    },
    "schema": {
        t: {"caller_known_columns": ["c"], "not_null": [], "accepted_values": {}, "fingerprint": "f"}
        for t in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")
    },
}
ATTACH_OK = {
    "ok": True,
    "meta_schema": core.META_SCHEMA,
    "data_path_is_smoke": True,
    "catalog_login": "ducklake_telemetry_writer",
}


TENANT_ID = "01HTENANT" + "0" * 17
PROJECT_ID = "01HPRQJEKT" + "0" * 16
TABLES = [f"t{i}" for i in range(9)]
PROBE_OK = {
    "ok": True,
    "meta_schema": core.META_SCHEMA,
    "data_path_is_smoke": True,
    "connect_reused": True,
    "catalog_login": "ducklake_telemetry_writer",
    "catalog_backend": "neon",
    "catalog_sessions": 3,
    "can_create": False,
    "tables": {t: {"present": True, "partitioned_as_declared": True} for t in TABLES},
    "counters": {"ops_tenants": 2, "ops_projects": 5},
    "counter_rows": {"ops_tenants": 1, "ops_projects": 1},
    "current_counts": {"ops_tenants": 1, "ops_projects": 5},
}
MARKERS = (
    "TELEMETRY_PROVISION_OK",
    "TELEMETRY_TENANT_STABLE",
    "TELEMETRY_PROJECT_CONVERGED",
    "TELEMETRY_PROBE_OK",
    "TELEMETRY_SELF_ONLY_OK",
    "catalog_backend=neon",
    "catalog_sessions=3",
)


class _Client:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"StatusCode": 204}


def _denied(code="AccessDeniedException", message="x"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "Invoke")


@pytest.fixture
def wired(monkeypatch):
    """Happy-path doubles; tests override entries of `state` to break exactly one check."""
    state = {
        "unsigned": _Resp(403, None, text='{"Message":"Forbidden"}'),
        "describe": _Resp(200, DESCRIBE_OK),
        "attach": _Resp(200, ATTACH_OK),
        "unknown": _Resp(400, {"ok": False, "error_type": "unknown_action"}),
        "client": _Client(_denied()),
        "sent": [],
        "provision": _Resp(200, {"ok": True, "tables": TABLES, "counters": {"ops_tenants": 0, "ops_projects": 0}}),
        "tenant_ids": [TENANT_ID, TENANT_ID],
        "project_ids": lambda n: PROJECT_ID,
        "probe": _Resp(200, PROBE_OK),
        "self_only": _Resp(400, {"ok": False, "error_type": "bad_request"}),
        "project_purpose": "synthetic",
        "live": LiveFake(),
    }
    lock = threading.Lock()
    counts = {"tenant": 0, "project": 0}

    def fake_invoke(url, payload, **kw):
        with lock:
            state["sent"].append((payload, kw.get("sign", True)))
        if kw.get("sign", True) is False:
            return state["unsigned"]
        action = payload["action"]
        if action == "register_tenant" and "principal_ref" in payload:
            return state["self_only"]
        if action == "register_tenant":
            with lock:
                n = counts["tenant"] = counts["tenant"] + 1
            return _Resp(200, {"ok": True, "tenant_id": state["tenant_ids"][min(n, 2) - 1], "created": n == 1})
        if action in ("append_telemetry", "begin_blobs", "named_read") or (
            action == "telemetry_probe" and "project_ref" in payload
        ):
            return state["live"].invoke(url, payload)
        if action == "register_project":
            with lock:
                n = counts["project"] = counts["project"] + 1
            state["live"].register(payload["project_ref"], PROJECT_ID)
            body = {"ok": True, "project_id": state["project_ids"](n), "created": n == 1}
            return _Resp(200, {**body, "purpose": state["project_purpose"], "registration": "registered"})
        return {
            "describe": state["describe"],
            "attach_check": state["attach"],
            "no_such_action": state["unknown"],
            "provision_telemetry": state["provision"],
            "telemetry_probe": state["probe"],
        }[action]

    class Session:
        def __init__(self, profile_name=None):
            pass

        def client(self, name, region_name=None):
            return state["client"]

    state["live"].activate(monkeypatch)
    monkeypatch.setattr(core, "_function_url", lambda role: "https://fake-url.invalid")
    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    monkeypatch.setattr("boto3.Session", Session)
    return state


def test_happy_path_prints_fixed_line(wired, capsys):
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == SUCCESS_LINE
    assert "fake-url" not in out
    assert wired["client"].calls == [
        {"FunctionName": "agent-platform-ducklake-telemetry-writer-smoke", "InvocationType": "DryRun", "Payload": b"{}"}
    ]


def test_attach_check_sends_decoy_keys(wired):
    lambda_telemetry_gates.lambda_telemetry_smoke()
    payload = next(p for p, _ in wired["sent"] if p["action"] == "attach_check")
    assert payload["meta_schema"] == "ducklake_ops"
    assert payload["data_path"].startswith("s3://")


def test_check1_url_failure_propagates(monkeypatch):
    seen = []

    def boom(role):
        seen.append(role)
        raise core.SmokeTestFailure("no url")

    monkeypatch.setattr(core, "_function_url", boom)
    with pytest.raises(core.SmokeTestFailure, match="no url"):
        lambda_telemetry_gates.lambda_telemetry_smoke()
    assert seen == ["telemetry_writer_smoke"]


@pytest.mark.parametrize(
    ("key", "resp", "needle"),
    [
        ("unsigned", _Resp(200, {"ok": True}), "unsigned describe status=200"),
        ("unsigned", _Resp(403, {"ok": False, "error_type": "missing_caller_identity"}), "not AWS_IAM"),
        ("describe", _Resp(200, {**DESCRIBE_OK, "actions": ["describe"]}), "missing describe or attach_check"),
        (
            "describe",
            _Resp(200, {**DESCRIBE_OK, "catalog": {"meta_schema": "ducklake_ops", "data_path_is_smoke": True}}),
            "meta_schema is not the smoke schema",
        ),
        (
            "describe",
            _Resp(200, {**DESCRIBE_OK, "catalog": {"meta_schema": core.META_SCHEMA, "data_path_is_smoke": False}}),
            "data_path_is_smoke",
        ),
        ("attach", _Resp(200, {**ATTACH_OK, "ok": False}), "attach_check status=200"),
        ("attach", _Resp(200, {**ATTACH_OK, "meta_schema": "ducklake_ops"}), "event keys honoured"),
        ("attach", _Resp(200, {**ATTACH_OK, "data_path_is_smoke": False}), "data_path_is_smoke"),
        ("attach", _Resp(200, {**ATTACH_OK, "catalog_login": "ducklake_ops"}), "scoped telemetry login"),
        ("unknown", _Resp(200, {"ok": True}), "unknown action status=200"),
        ("unknown", _Resp(400, {"error_type": "bad_request"}), "error_type=bad_request"),
    ],
)
def test_red_cases(wired, key, resp, needle):
    wired[key] = resp
    with pytest.raises(core.SmokeTestFailure, match=needle):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_dryrun_success_means_guard_missing(wired):
    wired["client"] = _Client()
    with pytest.raises(core.SmokeTestFailure, match="URL-only guard missing: direct invoke allowed"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_dryrun_other_code_is_inconclusive(wired):
    wired["client"] = _Client(_denied("ThrottlingException"))
    with pytest.raises(core.SmokeTestFailure, match="direct-invoke probe inconclusive: ThrottlingException"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_client_error_message_never_leaks(wired, capsys):
    leaky = f"User {FAKE_ACCT} is not authorized on {FAKE_ARN} via https://{FAKE_HOST}/"
    wired["client"] = _Client(_denied("ThrottlingException", leaky))
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_gates.lambda_telemetry_smoke()
    captured = capsys.readouterr()
    for text in (str(info.value), captured.out, captured.err):
        assert FAKE_ACCT not in text
        assert "arn:" not in text
        assert "lambda-url" not in text


_REQUEST_ID = "7c2a9f4e-1b3d-4e6a-8f50-3a9d2c7e1b64"


def test_failure_text_carries_error_class_and_request_id(wired):
    wired["attach"] = _Resp(500, {"ok": False, "error_type": "unmapped", "error_class": "KeyError", "request_id": _REQUEST_ID})
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_gates.lambda_telemetry_smoke()
    assert f"error_type=unmapped error_class=KeyError request_id={_REQUEST_ID}" in str(info.value)


def test_bodyless_502_failure_text_carries_the_runtime_request_id(wired):
    wired["attach"] = _Resp(502, None, text="Internal Server Error", headers={"x-amzn-RequestId": _REQUEST_ID})
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_gates.lambda_telemetry_smoke()
    assert f"error_type=none error_class=none request_id={_REQUEST_ID}" in str(info.value)


def test_off_shape_diagnostics_print_none(wired):
    wired["attach"] = _Resp(500, {"ok": False, "error_class": f"Bad Class {FAKE_ARN}", "request_id": FAKE_ACCT})
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_gates.lambda_telemetry_smoke()
    assert "error_class=none request_id=none" in str(info.value)
    assert FAKE_ACCT not in str(info.value) and "arn:" not in str(info.value)


def test_http_body_never_leaks(wired, capsys):
    wired["attach"] = _Resp(500, {"ok": False, "error": f"{FAKE_ACCT} {FAKE_ARN} {FAKE_HOST}"}, text=FAKE_ARN)
    with pytest.raises(core.SmokeTestFailure) as info:
        lambda_telemetry_gates.lambda_telemetry_smoke()
    captured = capsys.readouterr()
    for text in (str(info.value), captured.out, captured.err):
        assert FAKE_ACCT not in text
        assert "arn:" not in text
        assert "lambda-url" not in text


def _register_project_calls(wired):
    return [p for p, _ in wired["sent"] if p["action"] == "register_project"]


def test_registration_vectors_pass_on_a_consistent_writer(wired, capsys):
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    for marker in MARKERS:
        assert marker in out, marker
    lines = out.strip().splitlines()
    assert lines.index("catalog_sessions=3") < next(
        i for i, ln in enumerate(lines) if ln.startswith("TELEMETRY_TENANT_STABLE")
    )
    calls = _register_project_calls(wired)
    assert len(calls) == 4 and len({c["project_ref"] for c in calls}) == 1
    assert calls[0]["project_ref"].startswith("synthetic:deploy-smoke-") and len(calls[0]["project_ref"]) == 23 + 32
    assert [p["action"] for p, _ in wired["sent"] if "project_ref" not in p].count("telemetry_probe") == 5
    assert [p["action"] for p, _ in wired["sent"]].count("provision_telemetry") == 2


def test_register_tenant_id_drift_fails(wired):
    wired["tenant_ids"] = [TENANT_ID, "01HDRAFT" + "0" * 18]
    with pytest.raises(core.SmokeTestFailure, match="not stable across calls"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_register_tenant_non_ulid_fails(wired):
    wired["tenant_ids"] = ["not-a-ulid", "not-a-ulid"]
    with pytest.raises(core.SmokeTestFailure, match="canonical uppercase ULID"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_concurrent_register_project_divergent_ids_fail(wired):
    wired["project_ids"] = lambda n: f"01HDVRGNT{n:017d}"
    with pytest.raises(core.SmokeTestFailure, match="Decision 200 reversal condition"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"project_purpose": "real"}, "purpose or registration"),
        ({"project_ids": lambda n: "lowercase-not-a-ulid"}, "canonical uppercase ULID"),
    ],
)
def test_register_project_shape_failures(wired, override, needle):
    wired.update(override)
    with pytest.raises(core.SmokeTestFailure, match=needle):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_every_concurrent_call_claiming_created_fails(wired, monkeypatch):
    original = core._sigv4_invoke

    def all_created(url, payload, **kw):
        resp = original(url, payload, **kw)
        if payload["action"] == "register_project":
            return _Resp(200, {**resp.json(), "created": True})
        return resp

    monkeypatch.setattr(core, "_sigv4_invoke", all_created)
    with pytest.raises(core.SmokeTestFailure, match="created 4 projects"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_provision_unstable_table_set_fails(wired):
    wired["provision"] = _Resp(200, {"ok": True, "tables": TABLES[:8], "counters": {}})
    with pytest.raises(core.SmokeTestFailure, match="one stable set of nine tables"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_probe_below_counter_floor_fails(wired):
    wired["probe"] = _Resp(200, {**PROBE_OK, "counters": {"ops_tenants": 2, "ops_projects": 4}})
    with pytest.raises(core.SmokeTestFailure, match="ops_projects counter is below its table count"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_probe_counter_row_count_fails(wired):
    wired["probe"] = _Resp(200, {**PROBE_OK, "counter_rows": {"ops_tenants": 2, "ops_projects": 1}})
    with pytest.raises(core.SmokeTestFailure, match="ops_tenants counter does not have exactly one row"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


@pytest.mark.parametrize(
    "override",
    [
        {"meta_schema": "ducklake_ops"},
        {"data_path_is_smoke": False},
    ],
)
def test_probe_non_smoke_binding_fails(wired, override):
    wired["probe"] = _Resp(200, {**PROBE_OK, **override})
    with pytest.raises(core.SmokeTestFailure, match="not bound to the smoke catalog"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"connect_reused": False}, "connect_reused is not true"),
        ({"catalog_login": "ducklake_ops"}, "scoped telemetry login"),
        ({"tables": {"t": {"present": True, "partitioned_as_declared": True}}}, "nine physical tables"),
        ({"counters": None}, "omitted the counter reads"),
    ],
)
def test_probe_other_failures(wired, override, needle):
    wired["probe"] = _Resp(200, {**PROBE_OK, **override})
    with pytest.raises(core.SmokeTestFailure, match=needle):
        lambda_telemetry_gates.lambda_telemetry_smoke()


@pytest.mark.parametrize("value", [True, None])
def test_probe_can_create_true_fails(wired, value):
    wired["probe"] = _Resp(200, {**PROBE_OK, "can_create": value})
    with pytest.raises(core.SmokeTestFailure, match="can_create is not false"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_probe_can_create_null_fails(wired):
    probe = {k: v for k, v in PROBE_OK.items() if k != "can_create"}
    wired["probe"] = _Resp(200, probe)
    with pytest.raises(core.SmokeTestFailure, match="can_create is not false"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_probe_undeclared_partition_fails(wired):
    tables = {**PROBE_OK["tables"], "t3": {"present": True, "partitioned_as_declared": False}, "t4": {"present": False}}
    wired["probe"] = _Resp(200, {**PROBE_OK, "tables": tables})
    with pytest.raises(core.SmokeTestFailure, match="1 tables absent, 2 not partitioned as declared"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_self_only_violation_fails(wired):
    wired["self_only"] = _Resp(200, {"ok": True, "tenant_id": TENANT_ID, "created": False})
    with pytest.raises(core.SmokeTestFailure, match="self-only register_tenant status=200"):
        lambda_telemetry_gates.lambda_telemetry_smoke()


def test_unrecognised_measurements_print_a_neutral_value(wired, capsys):
    wired["probe"] = _Resp(200, {**PROBE_OK, "catalog_backend": f"{FAKE_HOST}", "catalog_sessions": None})
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    assert "catalog_backend=unrecognised" in out and "catalog_sessions=unavailable" in out and FAKE_HOST not in out


def test_gate_output_never_contains_an_account_id(wired, capsys):
    wired["probe"] = _Resp(200, {**PROBE_OK, "leak": f"{FAKE_ACCT} {FAKE_ARN} {FAKE_HOST}"})
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    assert FAKE_ACCT not in out and "arn:" not in out and "lambda-url" not in out and "fake-url" not in out
    assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])", out)


def test_append_vectors_run_after_registration_with_the_registered_ref(wired, capsys):
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    sent = [p for p, _ in wired["sent"]]
    registered = _register_project_calls(wired)[0]["project_ref"]
    last_registration = max(i for i, p in enumerate(sent) if p["action"] == "register_project")
    first_append = next(i for i, p in enumerate(sent) if p["action"] == "append_telemetry")
    last_self_only = max(i for i, p in enumerate(sent) if p["action"] == "register_tenant")
    assert last_registration < first_append and last_self_only < first_append
    assert sent[first_append]["project_ref"] == registered and registered.startswith("synthetic:deploy-smoke-")
    for marker in ("TELEMETRY_APPEND_OK", "TELEMETRY_RESEND_NOOP", "TELEMETRY_AUTOREGISTER_OK", "TELEMETRY_ROWCAP_OK"):
        assert marker in out, marker
    assert registered not in out and registered.split(":", 1)[1] not in out
    assert out.strip().splitlines()[-1] == SUCCESS_LINE


def test_blob_vectors_run_after_the_append_vectors(wired, capsys):
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    sent = [p for p, _ in wired["sent"]]
    registered = _register_project_calls(wired)[0]["project_ref"]
    last_append = max(i for i, p in enumerate(sent) if p["action"] == "append_telemetry" and p["project_ref"] != registered)
    first_blob = next(i for i, p in enumerate(sent) if p["action"] == "begin_blobs")
    assert sent[first_blob]["project_ref"] == registered and first_blob < last_append + 1000
    first_append = next(i for i, p in enumerate(sent) if p["action"] == "append_telemetry")
    assert first_append < first_blob
    for marker in (
        "TELEMETRY_BLOB_PROTOCOL_OK",
        "TELEMETRY_BLOB_AUTOREGISTER_OK",
        "blob_grant_ms=",
        "blob_put_ms=",
        "blob_append_ms=",
    ):
        assert marker in out, marker
    assert "READBACK" not in out and "blob_read_ms" not in out
    assert registered not in out and out.strip().splitlines()[-1] == SUCCESS_LINE


def _record_roles(monkeypatch):
    roles = []

    def url(role):
        roles.append(role)
        return "https://fake-url.invalid"

    monkeypatch.setattr(core, "_function_url", url)
    return roles


def test_twin_gate_targets_the_twin_function(wired, monkeypatch, capsys):
    roles = _record_roles(monkeypatch)
    lambda_telemetry_gates.lambda_telemetry_smoke()
    out = capsys.readouterr().out
    assert roles[0] == "telemetry_writer_smoke" and "telemetry_writer" not in roles
    assert out.strip().splitlines()[-1] == SUCCESS_LINE
    assert wired["client"].calls == [
        {"FunctionName": "agent-platform-ducklake-telemetry-writer-smoke", "InvocationType": "DryRun", "Payload": b"{}"}
    ]
