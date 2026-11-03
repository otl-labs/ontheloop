"""Unit tests for the ducklake_telemetry_writer stub handler (no network)."""

from __future__ import annotations

import base64
import importlib
import json
import logging
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.common import ducklake_runtime as rt
from src.common import ducklake_telemetry_registration as reg
from src.common import ducklake_telemetry_txn as txn
from src.common.ducklake_scd2_schema import SchemaGateError
from src.common.ducklake_telemetry_blobstore import BlobGrantSelfCheckError, BlobIntegrityError, BlobStoreAccessError
from src.common.ducklake_telemetry_txn import TelemetryRequestError
from src.common.ducklake_write_rules import RowRuleViolationError
from src.common.ducklake_writes import OCCRetryExhaustedError
from src.lambdas.ducklake_telemetry_writer import handler as h
from src.telemetry.gate import GateError, GrainConflictError, RowRuleGateError

ACCOUNT = "acct-under-test"
USER_ARN = "arn:aws:sts::acct-under-test:assumed-role/Some/secret-session"
SECRET_ID = "/secret/agent-platform-ducklake-telemetry-writer-dsn"
VERB_NAMES = ["provision_telemetry", "register_project", "register_tenant", "telemetry_probe"]
_PW = "pass" + "word=hunter2"
ALL_ACTIONS = sorted(["append_telemetry", "attach_check", "begin_blobs", "describe", *VERB_NAMES])


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", "ducklake_smoke")
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.SMOKE_DATA_PATH)
    monkeypatch.setenv("TELEMETRY_DSN_PARAMETER", SECRET_ID)
    monkeypatch.setenv("DUCKLAKE_EXTENSION_DIRECTORY", "/opt/ext")
    h._CATALOG_LOGIN.clear()


def _event(body: Any, *, identity: bool = True, b64: bool = False) -> dict[str, Any]:
    raw = body if isinstance(body, str) else json.dumps(body)
    ev: dict[str, Any] = {"body": base64.b64encode(raw.encode()).decode() if b64 else raw, "isBase64Encoded": b64}
    if identity:
        ev["requestContext"] = {"authorizer": {"iam": {"accountId": ACCOUNT, "userArn": USER_ARN}}}
    return ev


def _body(resp: dict[str, Any]) -> dict[str, Any]:
    return json.loads(resp["body"])


@pytest.mark.parametrize("action", ["describe", "attach_check", "nope"])
@pytest.mark.parametrize(
    "ctx",
    [
        None,
        {},
        {"authorizer": {}},
        {"authorizer": {"iam": "x"}},
        {"authorizer": {"iam": {"accountId": ACCOUNT}}},
        {"authorizer": {"iam": {"userArn": USER_ARN}}},
        {"authorizer": {"iam": {"accountId": "", "userArn": ""}}},
    ],
)
def test_missing_or_partial_identity_is_403(action: str, ctx: Any) -> None:
    ev = _event({"action": action}, identity=False)
    if ctx is not None:
        ev["requestContext"] = ctx
    resp = h.handler(ev)
    assert resp["statusCode"] == 403
    assert _body(resp)["error_type"] == "missing_caller_identity"


def test_identity_never_in_response_or_logs(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    caplog.set_level(logging.DEBUG)
    for ev in (_event({"action": "describe"}), _event({"action": "bogus"}), _event("{bad")):
        resp = h.handler(ev)
        assert ACCOUNT not in resp["body"] and USER_ARN not in resp["body"] and "secret-session" not in resp["body"]
    out = capsys.readouterr()
    assert ACCOUNT not in caplog.text + out.out + out.err


def test_malformed_body_is_400() -> None:
    resp = h.handler(_event("{not json"))
    assert resp["statusCode"] == 400 and _body(resp)["error_type"] == "bad_request"


@pytest.mark.parametrize("raw", ["[1, 2]", '"str"', "3"])
def test_non_object_body_is_400(raw: str) -> None:
    resp = h.handler(_event(raw))
    assert resp["statusCode"] == 400 and _body(resp)["error_type"] == "bad_request"


def test_bad_base64_is_400() -> None:
    ev = _event("x")
    ev["body"], ev["isBase64Encoded"] = "!!!notb64", True
    assert h.handler(ev)["statusCode"] == 400


def test_base64_body_is_decoded() -> None:
    resp = h.handler(_event({"action": "describe"}, b64=True))
    assert resp["statusCode"] == 200


def test_no_direct_invoke_fallback() -> None:
    resp = h.handler({"action": "describe", "requestContext": _event({})["requestContext"]})
    assert resp["statusCode"] == 400
    assert _body(resp)["error_type"] == "unknown_action"


@pytest.mark.parametrize("payload", [{}, {"action": "x"}, {"action": 5}])
def test_unknown_action_is_400_and_lists_actions(payload: dict[str, Any]) -> None:
    resp = h.handler(_event(payload))
    body = _body(resp)
    assert resp["statusCode"] == 400 and body["error_type"] == "unknown_action"
    assert body["actions"] == ALL_ACTIONS


def test_describe_is_connectionless(monkeypatch: pytest.MonkeyPatch) -> None:
    boom = MagicMock(side_effect=AssertionError("no connection allowed"))
    monkeypatch.setattr(rt, "get_warm_connection", boom)
    monkeypatch.setattr(rt, "open_connection", boom)
    monkeypatch.setattr(rt, "fetch_dsn", boom)
    resp = h.handler(_event({"action": "describe", "meta_schema": "ducklake", "data_path": "s3://x/"}))
    body = _body(resp)
    assert resp["statusCode"] == 200
    assert body["function"] == "ducklake_telemetry_writer"
    assert body["actions"] == ALL_ACTIONS
    assert body["catalog"] == {"meta_schema": "ducklake_smoke", "data_path_is_smoke": True}
    assert "s3://" not in resp["body"]


def test_describe_flags_non_smoke_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_DATA_PATH", "s3://other/ducklake/")
    assert _body(h.handler(_event({"action": "describe"})))["catalog"]["data_path_is_smoke"] is False


def test_describe_trailing_slash_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.SMOKE_DATA_PATH.rstrip("/"))
    assert _body(h.handler(_event({"action": "describe"})))["catalog"]["data_path_is_smoke"] is True


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, MagicMock]:
    dsn = {
        "host": "db.example.invalid",
        "dbname": "d",
        "username": "ducklake_telemetry_writer",
        "password": "pw-value-x",  # pragma: allowlist secret
    }
    fetch = MagicMock(return_value=dsn)
    opened = MagicMock(return_value=MagicMock())
    con = MagicMock()
    state: dict[str, bool] = {"warm": False}

    def warm(*, opener: Any, **kw: Any) -> tuple[Any, dict[str, Any]]:
        warm.kwargs = kw  # type: ignore[attr-defined]
        if state["warm"]:
            return con, {"reused": True, "reopened": False, "connect_ms": 0.0}
        opener()
        state["warm"] = True
        return con, {"reused": False, "reopened": False, "connect_ms": 12.5}

    warm_mock = MagicMock(side_effect=warm)
    monkeypatch.setattr(rt, "fetch_dsn", fetch)
    monkeypatch.setattr(rt, "open_connection", opened)
    monkeypatch.setattr(rt, "get_warm_connection", warm_mock)
    return fetch, opened, warm_mock


def test_attach_check_pins_env_and_ignores_event(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch, opened, warm = _patch_runtime(monkeypatch)
    resp = h.handler(_event({"action": "attach_check", "data_path": "s3://evil/", "meta_schema": "ducklake"}))
    body = _body(resp)
    assert resp["statusCode"] == 200 and body["ok"] is True
    fetch.assert_called_once_with(parameter_name=SECRET_ID)
    kwargs = opened.call_args.kwargs
    assert kwargs["data_path"] == rt.SMOKE_DATA_PATH and kwargs["meta_schema"] == "ducklake_smoke"
    assert kwargs["extension_directory"] == "/opt/ext"
    assert warm.call_args.kwargs["data_path"] == rt.SMOKE_DATA_PATH
    assert warm.call_args.kwargs["meta_schema"] == "ducklake_smoke"
    assert body["meta_schema"] == "ducklake_smoke" and body["data_path_is_smoke"] is True
    assert body["catalog_login"] == "ducklake_telemetry_writer"
    assert body["connect_ms"] == 12.5 and body["reused"] is False
    assert "pw-value-x" not in resp["body"] and "db.example.invalid" not in resp["body"]


def test_attach_check_reuse_returns_cached_login(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch, _opened, _warm = _patch_runtime(monkeypatch)
    h.handler(_event({"action": "attach_check"}))
    body = _body(h.handler(_event({"action": "attach_check"})))
    assert body["reused"] is True and body["catalog_login"] == "ducklake_telemetry_writer"
    assert fetch.call_count == 1


@pytest.mark.parametrize("name", ["TELEMETRY_META_SCHEMA", "TELEMETRY_DATA_PATH", "TELEMETRY_DSN_PARAMETER"])
def test_unset_env_is_500(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.delenv(name)
    resp = h.handler(_event({"action": "attach_check"}))
    assert resp["statusCode"] == 500 and _body(resp)["error_type"] == "telemetry_env_unset"


def test_describe_with_env_unset_is_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEMETRY_META_SCHEMA")
    assert _body(h.handler(_event({"action": "describe"})))["error_type"] == "telemetry_env_unset"


def test_import_with_env_unset_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TELEMETRY_META_SCHEMA", "TELEMETRY_DATA_PATH", "TELEMETRY_DSN_PARAMETER", "DUCKLAKE_EXTENSION_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(h)


def test_runtime_error_is_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "get_warm_connection", MagicMock(side_effect=rt.DuckLakeRuntimeError("boom")))
    resp = h.handler(_event({"action": "attach_check"}))
    assert resp["statusCode"] == 500 and _body(resp)["error_type"] == "runtime"


def test_unexpected_exception_is_a_typed_500(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(rt, "get_warm_connection", MagicMock(side_effect=KeyError("unmapped")))
    resp = h.handler(_event({"action": "attach_check"}))
    body = _body(resp)
    assert resp["statusCode"] == 500
    assert body["error_type"] == "unmapped" and body["error_class"] == "KeyError" and body["action"] == "attach_check"
    assert body["request_id"] is None and "elapsed_ms" in body
    line = json.loads(next(ln for ln in capsys.readouterr().out.splitlines() if "ducklake_unmapped_error" in ln))
    assert line["function"] == "telemetry-writer" and line["error_class"] == "KeyError" and line["action"] == "attach_check"


class _FakeDuckDBIOException(Exception):
    pass


_FakeDuckDBIOException.__name__ = "IOException"
_FakeDuckDBIOException.__module__ = "_duckdb"


@pytest.mark.parametrize("raised", [Exception, KeyError, _FakeDuckDBIOException])
@pytest.mark.parametrize("site", ["warm_connection", "verb"])
def test_unmapped_answer_never_echoes_the_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], raised: type[Exception], site: str
) -> None:
    acct = "1234" + "56789012"
    secret = f"ATTACH failed host=h.example {_PW} s3://bucket/key arn:aws:iam::{acct}:role/r"
    if site == "warm_connection":
        monkeypatch.setattr(rt, "get_warm_connection", MagicMock(side_effect=raised(secret)))
        action = "attach_check"
    else:
        _patch_runtime(monkeypatch)
        _stub_verbs(monkeypatch, fn=lambda *_: (_ for _ in ()).throw(raised(secret)))
        action = "register_tenant"
    resp = h.handler(_event({"action": action}))
    assert resp["statusCode"] == 500
    assert _body(resp)["error_class"] == raised.__name__
    for text in (resp["body"], capsys.readouterr().out):
        assert "hunter2" not in text and acct not in text and "arn:aws" not in text


def test_dead_connection_at_connect_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rt, "get_warm_connection", MagicMock(side_effect=RuntimeError("server closed the connection unexpectedly"))
    )
    resp = h.handler(_event({"action": "attach_check"}))
    body = _body(resp)
    assert resp["statusCode"] == 503
    assert body["error_type"] == "catalog_unavailable" and body["reopened"] is False
    assert "server closed" not in resp["body"]


def test_request_id_comes_from_the_lambda_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "get_warm_connection", MagicMock(side_effect=KeyError("x")))
    ctx = MagicMock(aws_request_id="7c2a9f4e-1b3d-4e6a-8f50-3a9d2c7e1b64")
    assert _body(h.handler(_event({"action": "attach_check"}), ctx))["request_id"] == ctx.aws_request_id


def test_missing_identity_stays_403_ahead_of_the_terminal_answer() -> None:
    resp = h.handler(_event("{not json", identity=False))
    assert resp["statusCode"] == 403 and _body(resp)["error_type"] == "missing_caller_identity"


def test_deeply_nested_body_is_400() -> None:
    resp = h.handler(_event("[" * 100000))
    assert resp["statusCode"] == 400 and _body(resp)["error_type"] == "bad_request"


def _stub_verbs(monkeypatch: pytest.MonkeyPatch, fn: Any = None) -> list[Any]:
    seen: list[Any] = []

    def stub(request: Any, con: Any, deps: Any) -> dict[str, Any]:
        seen.append((request, con, deps))
        if fn is not None:
            return fn(request, con, deps)
        return {"ok": True, "echo": request.action}

    for name in VERB_NAMES:
        monkeypatch.setitem(h.actions.VERBS, name, stub)
    return seen


def test_dispatch_includes_the_registration_verbs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    seen = _stub_verbs(monkeypatch)
    assert sorted(h.actions.VERBS) == VERB_NAMES
    for name in VERB_NAMES:
        resp = h.handler(_event({"action": name}))
        assert resp["statusCode"] == 200 and _body(resp) == {"ok": True, "echo": name}
    assert [r.action for r, _c, _d in seen] == VERB_NAMES
    assert _body(h.handler(_event({"action": "describe"})))["actions"] == ALL_ACTIONS


def test_invalid_request_is_refused_before_any_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    _fetch, _opened, warm = _patch_runtime(monkeypatch)
    resp = h.handler(_event({"action": "register_project", "project_ref": "bad\u0000sentinel-value"}))
    body = _body(resp)
    assert resp["statusCode"] == 400 and body["error_type"] == "bad_request" and body["field"] == "project_ref"
    assert "sentinel-value" not in resp["body"] and not warm.called


@pytest.mark.parametrize(
    ("exc", "status", "error_type"),
    [
        (RowRuleViolationError("ops_projects", "not_null", "tenant_id"), 422, "row_rule"),
        (SchemaGateError("unknown field"), 422, "schema_gate"),
        (OCCRetryExhaustedError("budget"), 503, "occ_exhausted"),
        (rt.DuckLakeRuntimeError("boom"), 500, "runtime"),
    ],
)
def test_error_map_rows_for_registration_errors(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, status: int, error_type: str
) -> None:
    _patch_runtime(monkeypatch)

    def boom(*_a: Any) -> dict[str, Any]:
        raise exc

    _stub_verbs(monkeypatch, boom)
    resp = h.handler(_event({"action": "register_tenant"}))
    body = _body(resp)
    assert resp["statusCode"] == status and body["error_type"] == error_type and body["ok"] is False
    if error_type == "row_rule":
        assert "ops_projects" in body["error"] and "not_null" in body["error"] and "tenant_id" in body["error"]


def test_request_error_maps_to_its_own_status_and_value_free_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)

    def refuse(*_a: Any) -> dict[str, Any]:
        raise TelemetryRequestError(403, "tenant_unresolved", {"remedy": "register_tenant"})

    _stub_verbs(monkeypatch, refuse)
    resp = h.handler(_event({"action": "register_project", "project_ref": "p"}))
    body = _body(resp)
    assert resp["statusCode"] == 403 and body["error_type"] == "tenant_unresolved" and body["remedy"] == "register_tenant"


def test_principal_port_is_built_at_the_edge_and_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    _patch_runtime(monkeypatch)
    seen = _stub_verbs(monkeypatch)
    resp = h.handler(_event({"action": "register_tenant"}))
    principal = seen[0][0].principal
    assert (principal.kind, principal.ref, principal.role_name) == ("aws_account", ACCOUNT, "Some")
    out = capsys.readouterr()
    assert ACCOUNT not in resp["body"] + caplog.text + out.out + out.err + repr(principal)
    other = _event({"action": "register_tenant"})
    other["requestContext"]["authorizer"]["iam"]["userArn"] = "arn:aws:iam::acct-under-test:user/bob"
    h.handler(other)
    assert seen[1][0].principal.role_name == "bob"
    other["requestContext"]["authorizer"]["iam"]["userArn"] = "opaque"
    h.handler(other)
    assert seen[2][0].principal.role_name == ""


def test_metric_sink_uses_the_telemetry_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    seen = _stub_verbs(monkeypatch)
    factory = MagicMock(return_value="SINK")
    monkeypatch.setattr(h, "make_metric_sink", factory)
    h.handler(_event({"action": "provision_telemetry"}))
    factory.assert_called_once_with(namespace="DuckLakeTelemetryWriter")
    assert h.METRIC_NAMESPACE == "DuckLakeTelemetryWriter" and seen[0][2].metric_sink == "SINK"


def test_request_bytes_reach_the_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    seen = _stub_verbs(monkeypatch)
    raw = json.dumps({"action": "telemetry_probe", "component": "caf\u00e9"})
    h.handler(_event(raw))
    h.handler(_event(raw, b64=True))
    expected = len(raw.encode("utf-8"))
    assert [r.request_bytes for r, _c, _d in seen] == [expected, expected]
    assert h._parse_body({}) == ({}, 0, 0)


def test_reopened_connection_clears_telemetry_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    _stub_verbs(monkeypatch)
    reg._TENANT_CACHE[("aws_account", "x")] = "T"
    monkeypatch.setattr(
        rt,
        "get_warm_connection",
        MagicMock(return_value=(MagicMock(), {"reused": True, "reopened": False, "connect_ms": 0.0})),
    )
    h.handler(_event({"action": "register_tenant"}))
    assert reg.cached_tenant_id("aws_account", "x") == "T"
    monkeypatch.setattr(
        rt,
        "get_warm_connection",
        MagicMock(return_value=(MagicMock(), {"reused": False, "reopened": True, "connect_ms": 3.0})),
    )
    h.handler(_event({"action": "register_tenant"}))
    assert reg.cached_tenant_id("aws_account", "x") is None


@pytest.mark.parametrize(
    ("host", "backend"),
    [
        ("ep-example.neon.tech", "neon"),
        ("db.example.rds.amazonaws.com", "rds"),
        ("db.example.invalid", "other"),
        ("", "other"),
    ],
)
def test_catalog_backend_class_reaches_the_verb_without_the_host(
    monkeypatch: pytest.MonkeyPatch, host: str, backend: str
) -> None:
    fetch, _opened, _warm = _patch_runtime(monkeypatch)
    fetch.return_value = {**fetch.return_value, "host": host}
    seen = _stub_verbs(monkeypatch, lambda r, c, d: {"ok": True, "backend": d.catalog_backend, "reused": d.connect_reused})
    resp = h.handler(_event({"action": "telemetry_probe"}))
    assert _body(resp)["backend"] == backend and seen[0][2].catalog_login == "ducklake_telemetry_writer"
    assert host not in resp["body"] or host == ""


class _NoTables:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str, params: Any = None) -> "_NoTables":
        self.sql.append(sql)
        return self

    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> None:
        return None


def test_provision_absent_tables_maps_to_500_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """The REAL provision_telemetry_catalog over a catalog with no tables: a typed 500, no DDL, nothing raised (rec-4294)."""
    _patch_runtime(monkeypatch)
    con = _NoTables()
    monkeypatch.setattr(rt, "get_warm_connection", MagicMock(return_value=(con, {"reused": True, "reopened": False})))
    resp = h.handler(_event({"action": "provision_telemetry"}))
    body = _body(resp)
    assert resp["statusCode"] == 500 and body["error_type"] == "runtime" and body["ok"] is False
    assert "telemetry tables absent" in body["error"] and "telemetry_sessions" in body["error"]
    assert "create_telemetry_tables" in body["error"]
    assert not [s for s in con.sql if s.startswith(("CREATE", "ALTER", "INSERT", "DELETE"))]
    assert con.sql[0] == "BEGIN TRANSACTION" and con.sql[-1] == "ROLLBACK"


def _c6_lines(out: str) -> list[dict[str, Any]]:
    lines = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    return [ln for ln in lines if ln.get("event") == "telemetry_request"]


def test_dispatch_includes_append_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    seen: list[Any] = []

    def stub(request: Any, con: Any, deps: Any) -> dict[str, Any]:
        seen.append((request, deps))
        return {"ok": True}

    monkeypatch.setattr(h.edge, "action_append_telemetry", stub)
    factory = MagicMock(return_value="SINK")
    monkeypatch.setattr(h, "make_metric_sink", factory)
    body = {"action": "append_telemetry", "project_ref": "p", "batches": [{"table": "t", "rows": [{}]}]}
    resp = h.handler(_event(body))
    assert resp["statusCode"] == 200 and _body(resp) == {"ok": True}
    request, deps = seen[0]
    assert request.action == "append_telemetry" and request.body == body
    assert deps.metric_sink == deps.stats.sink and not factory.called
    assert "append_telemetry" in _body(h.handler(_event({"action": "describe"})))["actions"]
    assert _body(h.handler(_event({"action": "describe"})))["verbs"]["append_telemetry"]["caps"]["max_rows_per_table"] == 2000


@pytest.mark.parametrize(
    ("exc", "status", "error_type", "expected"),
    [
        (
            RowRuleGateError("t.c: row rule r violated", rule="r", table="t", row=2, column="c"),
            422,
            "row_rule",
            {"table": "t", "row": 2, "rule": "r", "column": "c", "exclude_before": "2026-01-01"},
        ),
        (
            GateError("m", table="t", row=1, column="c", reason="type"),
            422,
            "schema_gate",
            {"table": "t", "row": 1, "column": "c", "reason": "type"},
        ),
        (
            GrainConflictError("TELEMETRY_GRAIN_CONFLICT secret", table="t", event_id="E", row=4),
            409,
            "grain_conflict",
            {"table": "t", "event_id": "E", "row": 4},
        ),
    ],
)
def test_error_map_rows_for_kernel_errors(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, status: int, error_type: str, expected: dict[str, Any]
) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(h.edge, "exclude_before", lambda table, column, rule: "2026-01-01")

    def boom(*_a: Any) -> dict[str, Any]:
        raise exc

    monkeypatch.setattr(h.edge, "action_append_telemetry", boom)
    resp = h.handler(_event({"action": "append_telemetry"}))
    body = _body(resp)
    assert resp["statusCode"] == status and body == {"ok": False, "error_type": error_type, **expected}
    assert "secret" not in resp["body"]


def test_one_c6_line_per_answered_request(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _patch_runtime(monkeypatch)

    def ok(request: Any, con: Any, deps: Any) -> dict[str, Any]:
        deps.stats.tenant_id, deps.stats.project_id = "T" * 26, "P" * 26
        deps.stats.tables = {"telemetry_sessions": (2, 1)}
        deps.metric_sink("OccRetryCount", 1.0)
        deps.metric_sink("CommitLatencyMs", 12.4)
        return {"ok": True}

    monkeypatch.setattr(h.edge, "action_append_telemetry", ok)

    def answered(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        capsys.readouterr()
        resp = h.handler(event)
        (line,) = _c6_lines(capsys.readouterr().out)
        assert line["status"] == resp["statusCode"]
        return resp, line

    _resp, line = answered(_event({"action": "append_telemetry", "component": "hook-stop"}))
    assert (line["verb"], line["component"], line["caller_role"], line["tenant_id"]) == (
        "append_telemetry",
        "hook-stop",
        "Some",
        "T" * 26,
    )
    assert line["tables"] == {"telemetry_sessions": {"submitted": 2, "inserted": 1}}
    assert (line["occ_retries"], line["commit_ms"], line["Verb"], line["RequestCount"]) == (1, 12, "append_telemetry", 1)
    _resp, unknown = answered(_event({"action": "nope\u0001"}))
    assert (unknown["verb"], unknown["status"]) == ("unknown", 400) and "_aws" not in unknown
    assert answered(_event("{bad"))[1]["error_type"] == "bad_request"
    assert answered(_event({"action": "describe"}))[1]["verb"] == "describe"
    assert answered(_event({"action": "describe"}, identity=False))[1]["caller_role"] is None
    monkeypatch.setattr(h.rt, "get_warm_connection", MagicMock(side_effect=KeyError("x")))
    assert answered(_event({"action": "attach_check"}))[1]["error_type"] == "unmapped"


def test_invoke_bytes_is_the_escaped_body_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    seen = _stub_verbs(monkeypatch)
    raw = json.dumps({"action": "telemetry_probe", "component": 'caf\u00e9 "q" \\ <>&'})
    h.handler(_event(raw))
    encoded = _event(raw, b64=True)
    h.handler(encoded)
    assert [r.invoke_bytes for r, _c, _d in seen] == [len(json.dumps(raw)), len(json.dumps(encoded["body"]))]
    assert seen[0][0].invoke_bytes > len(raw.encode("utf-8")) - 1
    assert h._parse_body({"body": None}) == ({}, 0, 0)


@pytest.mark.parametrize(
    "raw", ["{bad", "[" * 100000, '{"action": "append_telemetry", "x": ' + "9" * 5000 + "}", '{"a":"\\ud800"', "\x00"]
)
def test_unparseable_bodies_through_handler_are_400(raw: str) -> None:
    resp = h.handler(_event(raw))
    assert resp["statusCode"] == 400 and _body(resp)["error_type"] == "bad_request"


def test_masked_exhaustion_logs_its_retry_count(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _patch_runtime(monkeypatch)
    exhausted = txn.TelemetryOCCRetryExhaustedError("budget", reason="masked_commit", masked_commit_retries=2)
    monkeypatch.setattr(h.edge, "action_append_telemetry", lambda *_a: (_ for _ in ()).throw(exhausted))
    resp = h.handler(_event({"action": "append_telemetry"}))
    (line,) = _c6_lines(capsys.readouterr().out)
    assert resp["statusCode"] == 503 and _body(resp)["error_type"] == "occ_exhausted"
    assert (line["status"], line["masked_commit_retries"], line["error_type"]) == (503, 2, "occ_exhausted")
    assert "Could not remove" not in resp["body"] + json.dumps(line) and "OccRetryCount" not in line


def test_dispatch_includes_begin_blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(h.edge, "action_begin_blobs", lambda request, con, deps: {"ok": True, "echo": request.action})
    assert _body(h.handler(_event({"action": "begin_blobs", "items": []}))) == {"ok": True, "echo": "begin_blobs"}


def test_describe_carries_the_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _body(h.handler(_event({"action": "describe"})))
    assert body["blob_protocol"]["version"] == 1 and "begin_blobs" in body["verbs"] and len(body["schema"]) == 4
    monkeypatch.setenv("TELEMETRY_BLOB_ROOT", "s3://b/telemetry-blobs/ducklake_smoke/")
    monkeypatch.setenv("AWS_REGION", "r")
    monkeypatch.setattr(h, "make_s3_client", lambda region: region)
    assert (h._blob_store()._client, h._blob_store()._root.bucket) == ("r", "b")


@pytest.mark.parametrize("fault", [BlobIntegrityError, BlobGrantSelfCheckError, BlobStoreAccessError])
def test_blob_faults_answer_a_typed_500(monkeypatch: pytest.MonkeyPatch, capsys: Any, fault: type) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(h.edge, "action_begin_blobs", MagicMock(side_effect=fault("secret-key")))
    resp = h.handler(_event({"action": "begin_blobs"}))
    line = json.loads(next(ln for ln in capsys.readouterr().out.splitlines() if '"verb":"begin_blobs"' in ln))
    want = re.sub(r"(?<!^)(?=[A-Z])", "_", fault.__name__.removesuffix("Error")).lower()  # BlobIntegrity -> blob_integrity
    assert (resp["statusCode"], _body(resp)["error_type"], line["status"], line["error_type"]) == (500, want, 500, want)
    assert "secret-key" not in resp["body"] and "secret-key" not in json.dumps(line)
