"""CONCERN: lambda_telemetry_gates.lambda_telemetry_production, the production writer's light gate (production flip).

A fake production writer records every action: the gate expects the production binding and login, keeps the Decision 213
direct-invoke denial and the Decision 216 can_create check, and sends no project-registration, append or blob action.
"""

from __future__ import annotations

# boto3 is patched by string below; the import makes the heavy-dep requirement visible to the fast tier's collect pass.
import boto3  # noqa: F401
import pytest
from botocore.exceptions import ClientError

from scripts.ducklake_smoke import core, lambda_telemetry_gates
from src.common.ducklake_runtime import TELEMETRY_PROD_META_SCHEMA
from tests.fixtures.ducklake_smoke_fakes import _Resp

SUCCESS_LINE = (
    "LAMBDA_TELEMETRY_PRODUCTION OK unsigned=403 describe=200 attach_check=ok unknown_action=400 direct_invoke=denied"
)
_LOGIN = "ducklake_telemetry_writer_prod"
_TENANT = "01HTENANT" + "0" * 17
_TABLES = [f"t{i}" for i in range(9)]
_BINDING = {"meta_schema": TELEMETRY_PROD_META_SCHEMA, "data_path_is_smoke": False}
_DESCRIBE = {"ok": True, "actions": ["attach_check", "describe"], "catalog": dict(_BINDING)}
_ATTACH = {"ok": True, **_BINDING, "catalog_login": _LOGIN}
_PROBE = {
    "ok": True,
    **_BINDING,
    "catalog_login": _LOGIN,
    "connect_reused": True,
    "catalog_backend": "neon",
    "catalog_sessions": 2,
    "can_create": False,
    "tables": {t: {"present": True, "partitioned_as_declared": True} for t in _TABLES},
    "counters": {"ops_tenants": 2, "ops_projects": 5},
    "counter_rows": {"ops_tenants": 1, "ops_projects": 1},
    "current_counts": {"ops_tenants": 1, "ops_projects": 5},
}
_HEAVY = {"register_project", "append_telemetry", "begin_blobs", "named_read"}


class _Lambda:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"StatusCode": 204}


def _denied(code: str = "AccessDeniedException") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Invoke")


@pytest.fixture
def writer(monkeypatch):
    """A fake production writer; tests override `state` entries to break one check, and read `actions` afterwards."""
    state = {
        "describe": _DESCRIBE,
        "attach_check": _ATTACH,
        "telemetry_probe": _PROBE,
        "lambda": _Lambda(_denied()),
        "actions": [],
        "payloads": [],
        "roles": [],
    }
    tenants = {"n": 0}

    def fake_invoke(url, payload, **kw):
        action = payload["action"]
        state["actions"].append(action)
        state["payloads"].append(payload)
        if kw.get("sign", True) is False:
            return _Resp(403, None, text='{"Message":"Forbidden"}')
        if action == "no_such_action":
            return _Resp(400, {"ok": False, "error_type": "unknown_action"})
        if action == "provision_telemetry":
            return _Resp(200, {"ok": True, "tables": _TABLES})
        if action == "register_tenant" and "principal_ref" in payload:
            return _Resp(400, {"ok": False, "error_type": "bad_request"})
        if action == "register_tenant":
            tenants["n"] += 1
            return _Resp(200, {"ok": True, "tenant_id": _TENANT, "created": tenants["n"] == 1})
        if action in state:
            return _Resp(200, state[action])
        return _Resp(500, {"ok": False, "error_type": "unexpected_action"})

    class Session:
        def __init__(self, profile_name=None):
            pass

        def client(self, name, region_name=None):
            return state["lambda"]

    def url(role):
        state["roles"].append(role)
        return "https://fake-url.invalid"

    monkeypatch.setattr(core, "_function_url", url)
    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    monkeypatch.setattr("boto3.Session", Session)
    return state


def test_production_gate_expects_the_production_binding(writer, capsys):
    lambda_telemetry_gates.lambda_telemetry_production()
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == SUCCESS_LINE
    assert writer["roles"] == ["telemetry_writer"] and "fake-url" not in out
    assert "TELEMETRY_TENANT_STABLE" in out.splitlines() and _TENANT not in out
    smoke = {"meta_schema": core.META_SCHEMA, "data_path_is_smoke": True}
    red = [
        ("describe", {**_DESCRIBE, "catalog": smoke}, "describe meta_schema is not the production schema"),
        ("describe", {**_DESCRIBE, "catalog": {**_BINDING, "data_path_is_smoke": True}}, "data_path_is_smoke is not false"),
        ("attach_check", {**_ATTACH, **smoke}, "attach_check meta_schema is not the production schema"),
        ("attach_check", {**_ATTACH, "catalog_login": "ducklake_telemetry_writer"}, "production scoped telemetry login"),
        ("telemetry_probe", {**_PROBE, **smoke}, "telemetry_probe is not bound to the production catalog"),
        ("telemetry_probe", {**_PROBE, "catalog_login": "ducklake_telemetry_writer"}, "production scoped telemetry login"),
    ]
    for key, body, needle in red:
        good = writer[key]
        writer[key] = body
        with pytest.raises(core.SmokeTestFailure, match=needle):
            lambda_telemetry_gates.lambda_telemetry_production()
        writer[key] = good


def test_production_gate_keeps_the_denial_and_can_create_checks(writer):
    lambda_telemetry_gates.lambda_telemetry_production()
    assert writer["lambda"].calls == [
        {"FunctionName": "agent-platform-ducklake-telemetry-writer", "InvocationType": "DryRun", "Payload": b"{}"}
    ]
    for error, needle in ((None, "direct invoke allowed"), (_denied("ThrottlingException"), "inconclusive")):
        writer["lambda"] = _Lambda(error)
        with pytest.raises(core.SmokeTestFailure, match=needle):
            lambda_telemetry_gates.lambda_telemetry_production()
    writer["lambda"] = _Lambda(_denied())
    for value in (True, None):
        writer["telemetry_probe"] = {**_PROBE, "can_create": value}
        with pytest.raises(core.SmokeTestFailure, match="can_create is not false"):
            lambda_telemetry_gates.lambda_telemetry_production()


def test_production_gate_runs_no_append_or_registration_vector(writer):
    lambda_telemetry_gates.lambda_telemetry_production()
    actions = writer["actions"]
    assert not _HEAVY & set(actions), sorted(_HEAVY & set(actions))
    assert not any("project_ref" in p for p in writer["payloads"])
    assert set(actions) == {
        "describe",
        "attach_check",
        "no_such_action",
        "provision_telemetry",
        "register_tenant",
        "telemetry_probe",
    }
