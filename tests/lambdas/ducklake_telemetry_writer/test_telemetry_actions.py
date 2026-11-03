"""Coverage home for src/lambdas/ducklake_telemetry_writer/telemetry_actions.py (fakes, no network)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.common import ducklake_telemetry_provision as prov
from src.common import ducklake_telemetry_registration as reg
from src.common.ducklake_scd2_schema import load_field_semantics
from src.common.ducklake_telemetry_txn import TelemetryRequestError
from src.lambdas.ducklake_telemetry_writer import handler as h
from src.lambdas.ducklake_telemetry_writer import telemetry_actions as actions
from tests.fixtures.telemetry_blob_fakes import FakeBlobStore

ACCOUNT = "acct-under-test"
SENTINEL = "sentinel-value-7"
SEM = load_field_semantics()
PRINCIPAL = reg.CallerPrincipal("aws_account", ACCOUNT, "SomeRole")
CONTRACT = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "ducklake_telemetry_writer.yaml"


class _Cur:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class RecordingCon:
    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.sql: list[str] = []
        self.params: list[Any] = []
        self.answers = answers or {}

    def execute(self, sql: str, params: Any = None) -> _Cur:
        self.sql.append(sql)
        self.params.append(params)
        for needle, rows in self.answers.items():
            if needle in sql:
                if isinstance(rows, Exception):
                    raise rows
                return _Cur(rows)
        return _Cur([])


def _deps(**kw: Any) -> actions.Deps:
    base: dict[str, Any] = {
        "semantics": SEM,
        "metric_sink": None,
        "meta_schema": "ducklake_smoke",
        "data_path_is_smoke": True,
        "catalog_login": "ducklake_telemetry_writer",
        "catalog_backend": "neon",
        "connect_reused": True,
        "sleep": lambda _s: None,
        "blob_store": FakeBlobStore(),
    }
    return actions.Deps(**{**base, **kw})


def _req(action: str, **body: Any) -> actions.TelemetryRequest:
    return actions.TelemetryRequest(action, {"action": action, **body}, PRINCIPAL, 100)


def _call(action: str, con: Any = None, deps: actions.Deps | None = None, **body: Any) -> dict[str, Any]:
    return actions.VERBS[action](_req(action, **body), con if con is not None else RecordingCon(), deps or _deps())


@pytest.mark.parametrize("key", ["principal_kind", "principal_ref", "tenant_id", "confirm", "force_recreate"])
def test_register_tenant_refuses_principal_parameters(key: str) -> None:
    con = RecordingCon()
    with pytest.raises(TelemetryRequestError) as info:
        _call("register_tenant", con, **{key: SENTINEL})
    assert info.value.status == 400 and info.value.error_type == "bad_request"
    assert "allowed_keys" in info.value.fields and SENTINEL not in json.dumps(info.value.fields) and not con.sql


def test_register_tenant_response_never_carries_the_principal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    seen: list[Any] = []

    def fake(con: Any, principal: Any, **kw: Any) -> reg.Registration:
        seen.append((principal, kw))
        return reg.Registration("TENANT", True, 0, 1.0, "TENANT")

    monkeypatch.setattr(actions.reg, "register_tenant", fake)
    out = _call("register_tenant", component="ci")
    assert out == {"ok": True, "tenant_id": "TENANT", "created": True}
    assert seen[0][0] is PRINCIPAL and ACCOUNT not in json.dumps(out) + caplog.text


def test_register_project_rejects_unknown_keys_but_accepts_component(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake(con: Any, principal: Any, project_ref: str, **kw: Any) -> reg.Registration:
        calls.append((project_ref, kw["attributes"]))
        return reg.Registration("PROJECT", False, 1, 2.0, "TENANT")

    monkeypatch.setattr(actions.reg, "register_project", fake)
    out = _call("register_project", project_ref="synthetic:x", name="N", remote="R", component="ci")
    assert out == {"ok": True, "project_id": "PROJECT", "created": False, "purpose": "synthetic", "registration": "registered"}
    assert calls == [("synthetic:x", {"name": "N", "remote": "R"})]
    _call("register_project", project_ref="github.com/o/r")
    assert calls[1] == ("github.com/o/r", {})
    with pytest.raises(TelemetryRequestError):
        _call("register_project", project_ref="p", purpose="real")
    with pytest.raises(TelemetryRequestError) as info:
        _call("register_project")
    assert info.value.fields == {"field": "project_ref"}
    with pytest.raises(TelemetryRequestError) as info:
        _call("register_project", project_ref="")
    assert info.value.fields == {"field": "project_ref"}


_PAIRS = [
    ("provision_telemetry", "component"),
    ("register_tenant", "component"),
    ("register_project", "component"),
    ("register_project", "project_ref"),
    ("register_project", "name"),
    ("register_project", "remote"),
    ("telemetry_probe", "component"),
]


@pytest.mark.parametrize(("verb", "field"), _PAIRS)
@pytest.mark.parametrize(
    "bad",
    [f"a\x00{SENTINEL}", f"a\ud83d{SENTINEL}", f"a\ude00{SENTINEL}", 12345, None, [SENTINEL]],
    ids=["nul", "high-surrogate", "low-surrogate", "int", "null", "list"],
)
def test_nul_or_lone_surrogate_in_any_string_is_400_without_the_value(
    verb: str, field: str, bad: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    con = RecordingCon()
    body = {"project_ref": "p", field: bad} if verb == "register_project" else {field: bad}
    with pytest.raises(TelemetryRequestError) as info:
        _call(verb, con, **body)
    err = info.value
    assert (err.status, err.error_type, err.fields) == (400, "bad_request", {"field": field})
    assert not con.sql and SENTINEL not in str(err) + json.dumps(err.fields) + caplog.text


def test_surrogate_pair_decoded_from_json_is_not_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    ref = json.loads('{"project_ref": "emoji-\\ud83d\\ude00"}')["project_ref"]
    assert ref.endswith("\U0001f600")
    monkeypatch.setattr(actions.reg, "register_project", lambda *a, **k: reg.Registration("P", True, 0, 0.0, "T"))
    assert _call("register_project", project_ref=ref)["ok"] is True


def test_register_project_requires_a_registered_tenant() -> None:
    con = RecordingCon()
    with pytest.raises(TelemetryRequestError) as info:
        _call("register_project", con, project_ref="github.com/o/r")
    assert (info.value.status, info.value.error_type, info.value.fields) == (
        403,
        "tenant_unresolved",
        {"remedy": "register_tenant"},
    )
    assert any(s == "ROLLBACK" for s in con.sql) and "COMMIT" not in con.sql


def test_provision_refuses_force_recreate_keys() -> None:
    for key in ("force_recreate", "confirm", "confirm_force_recreate", "meta_schema", "data_path"):
        con = RecordingCon()
        with pytest.raises(TelemetryRequestError) as info:
            _call("provision_telemetry", con, **{key: True})
        assert info.value.error_type == "bad_request" and not con.sql


def test_provision_runs_in_one_transaction_with_the_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics: list[str] = []
    monkeypatch.setattr(
        actions.prov,
        "provision_telemetry_catalog",
        lambda con, semantics: prov.ProvisionResult(("a", "b"), {"ops_tenants": 0, "ops_projects": 2}, ("a",)),
    )
    con = RecordingCon()
    out = _call("provision_telemetry", con, _deps(metric_sink=lambda n, v: metrics.append(n)))
    assert out == {"ok": True, "tables": ["a", "b"], "counters": {"ops_tenants": 0, "ops_projects": 2}}
    assert con.sql == ["BEGIN TRANSACTION", "COMMIT"] and metrics == ["OccRetryCount", "CommitLatencyMs"]


def test_catalog_backend_is_classified_without_echoing_the_host() -> None:
    assert actions.classify_backend("ep-example.neon.tech") == "neon"
    assert actions.classify_backend("EP-EXAMPLE.REGION.NEON.TECH.") == "neon"
    assert actions.classify_backend("db.example.rds.amazonaws.com") == "rds"
    assert actions.classify_backend("rds.amazonaws.com") == "rds"
    for other in ("notneon.tech", "neon.tech.evil.example", "localhost", ""):
        assert actions.classify_backend(other) == "other"


def _probe_con(**answers: Any) -> RecordingCon:
    base: dict[str, Any] = {
        "has_schema_privilege": [(False,)],
        "FROM duckdb_databases": [("meta_db",)],
        "postgres_query": [(4,)],
        "WHERE counter_name": [(1,)],
        "SELECT count(*) FROM ops_catalog": [(3,)],
    }
    return RecordingCon({**base, **answers})


def test_telemetry_probe_reports_binding_reuse_backend_and_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = prov.target_tables(SEM)
    monkeypatch.setattr(actions.prov, "existing_tables", lambda con: set(targets))
    monkeypatch.setattr(actions.prov, "inspect_partitions", lambda con, existing, sem: {t: t != targets[0] for t in targets})
    con = _probe_con()
    out = _call("telemetry_probe", con)
    assert out["ok"] is True and out["meta_schema"] == "ducklake_smoke" and out["data_path_is_smoke"] is True
    assert out["connect_reused"] is True and out["catalog_login"] == "ducklake_telemetry_writer"
    assert out["catalog_backend"] == "neon" and out["catalog_sessions"] == 4 and out["catalog_sessions_reason"] is None
    assert list(out["tables"]) == list(targets) and len(out["tables"]) == 9
    assert out["tables"][targets[0]] == {"present": True, "partitioned_as_declared": False}
    assert out["tables"][targets[1]] == {"present": True, "partitioned_as_declared": True}
    assert out["counter_rows"] == {"ops_tenants": 1, "ops_projects": 1}
    assert out["counters"] == {"ops_tenants": 1, "ops_projects": 1}
    assert out["current_counts"] == {"ops_tenants": 3, "ops_projects": 3}
    assert "meta_db" not in json.dumps(out)


def test_telemetry_probe_before_provisioning_reports_absent_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions.prov, "existing_tables", lambda con: set())
    monkeypatch.setattr(actions.prov, "inspect_partitions", lambda con, existing, sem: {})
    out = _call("telemetry_probe", _probe_con())
    assert all(t == {"present": False, "partitioned_as_declared": False} for t in out["tables"].values())
    assert out["counters"] == {"ops_tenants": None, "ops_projects": None}
    assert out["counter_rows"] == {"ops_tenants": 0, "ops_projects": 0}
    assert out["current_counts"] == {"ops_tenants": None, "ops_projects": None}


def test_telemetry_probe_reports_can_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(actions.prov, "existing_tables", lambda con: set())
    monkeypatch.setattr(actions.prov, "inspect_partitions", lambda con, existing, sem: {})
    out = _call("telemetry_probe", _probe_con())
    assert out["can_create"] is False and out["can_create_reason"] is None and out["catalog_sessions"] == 4
    con = _probe_con()
    assert any("has_schema_privilege(current_user, ''ducklake_smoke'', ''CREATE'')" in s for s in _call_sql(con))
    assert _call("telemetry_probe", _probe_con(has_schema_privilege=[(True,)]))["can_create"] is True
    unreadable = _call("telemetry_probe", _probe_con(has_schema_privilege=RuntimeError(f"host {SENTINEL} refused")))
    assert unreadable["can_create"] is None and unreadable["can_create_reason"] == "RuntimeError"
    assert SENTINEL not in json.dumps(unreadable)


def _call_sql(con: RecordingCon) -> list[str]:
    _call("telemetry_probe", con)
    return con.sql


def test_telemetry_probe_counter_row_count_other_than_one_reports_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = prov.target_tables(SEM)
    monkeypatch.setattr(actions.prov, "existing_tables", lambda con: set(targets))
    monkeypatch.setattr(actions.prov, "inspect_partitions", lambda con, existing, sem: dict.fromkeys(targets, True))
    out = _call("telemetry_probe", _probe_con(**{"WHERE counter_name": [(2,)]}))
    assert out["counter_rows"] == {"ops_tenants": 2, "ops_projects": 2} and out["counters"] == {
        "ops_tenants": None,
        "ops_projects": None,
    }


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ({"FROM duckdb_databases": []}, "no_postgres_catalog"),
        ({"postgres_query": RuntimeError(f"connection to {SENTINEL} failed")}, "RuntimeError"),
    ],
)
def test_catalog_sessions_is_a_measurement_never_a_failure(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any], reason: str
) -> None:
    monkeypatch.setattr(actions.prov, "existing_tables", lambda con: set())
    monkeypatch.setattr(actions.prov, "inspect_partitions", lambda con, existing, sem: {})
    out = _call("telemetry_probe", _probe_con(**answers))
    assert out["ok"] is True and out["catalog_sessions"] is None and out["catalog_sessions_reason"] == reason
    assert SENTINEL not in json.dumps(out)


def test_contract_verbs_match_the_dispatched_production_verbs() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    production = set(h._all_actions()) - {"attach_check", "telemetry_probe"}
    assert set(contract["verbs"]) == production
    assert production == {
        "describe",
        "append_telemetry",
        "begin_blobs",
        "provision_telemetry",
        "register_tenant",
        "register_project",
    }
    assert contract["contract"]["id"] == "ducklake_telemetry_writer" and contract["contract"]["class"] == "B"
    for verb in ("provision_telemetry", "register_tenant", "register_project"):
        text = contract["verbs"][verb]["payload_schema_ref"]
        assert "U+0000" in text and "U+D800-U+DFFF" in text
        assert "unknown" in contract["verbs"][verb]["typed_errors"]["bad_request"]
        assert "never the value" in contract["verbs"][verb]["typed_errors"]["bad_request"]
    assert "U+0000" not in contract["verbs"]["describe"]["payload_schema_ref"]


_TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"


def _session_probe(monkeypatch: pytest.MonkeyPatch, registered: bool = True) -> RecordingCon:
    targets = prov.target_tables(SEM)
    monkeypatch.setattr(actions.prov, "existing_tables", lambda con: set(targets))
    monkeypatch.setattr(actions.prov, "inspect_partitions", lambda con, existing, sem: dict.fromkeys(targets, True))
    monkeypatch.setattr(actions.reg, "lookup_tenant", lambda con, kind, ref, semantics=None: _TENANT)
    found = (_PROJECT, "registered") if registered else None
    monkeypatch.setattr(actions.reg, "lookup_project", lambda con, tenant, ref, semantics=None: found)
    return RecordingCon(
        {
            "FROM duckdb_databases": [("meta_db",)],
            "postgres_query": [(4,)],
            "WHERE counter_name": [(1,)],
            "ducklake_list_files": [(2, 4096)],
            "SELECT DISTINCT": [(2,)],
            "SELECT count(*) FROM ops_catalog": [(3,)],
        }
    )


_SESSION = {"project_ref": "repo:example", "session_ref": "sess", "session_started_at": "2026-01-01T12:34:56.789Z"}


def test_probe_reports_grain_counts_for_one_session_day(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from src.telemetry.identity import derive_entity_key

    con = _session_probe(monkeypatch)
    out = _call("telemetry_probe", con, **_SESSION)
    events = {t for t in prov.target_tables(SEM) if t.startswith("telemetry_")}
    assert out["project_registered"] is True and set(out["grain_counts"]) == events
    assert all(v == {"rows": 3, "keys": 2} for v in out["grain_counts"].values())
    assert set(out["data_files"]) == events and all(v == {"files": 2, "bytes": 4096} for v in out["data_files"].values())
    started = datetime(2026, 1, 1, 12, 34, 56, 789000, tzinfo=timezone.utc)
    session_id = derive_entity_key("telemetry_sessions:session_id", _TENANT, _PROJECT, "sess", started)
    bound = [p for p in con.params if p and p[0] == session_id]
    assert bound and all(
        p[1:] == [datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)] for p in bound
    )
    assert "conflicted" not in json.dumps(out) and "repo:example" not in json.dumps(out)
    none = _call("telemetry_probe", _session_probe(monkeypatch, registered=False), **_SESSION)
    assert none["grain_counts"] is None and none["project_registered"] is False and set(none["data_files"]) == events
    assert "grain_counts" not in _call("telemetry_probe", _session_probe(monkeypatch))


def test_probe_counts_blob_objects_for_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from src.common import ducklake_telemetry_blobs as blobs

    started = datetime(2026, 1, 1, 12, 34, 56, 789000, tzinfo=timezone.utc)
    store = FakeBlobStore()
    store.objects[blobs.session_prefix(_TENANT, _PROJECT, "sess", started)] = {"count": 1, "bytes": 70000}
    out = _call("telemetry_probe", _session_probe(monkeypatch), _deps(blob_store=store), **_SESSION)
    assert out["blob_objects"] == {"count": 1, "bytes": 70000} and "repo:example" not in json.dumps(out)
    assert "blob_objects" not in _call("telemetry_probe", _session_probe(monkeypatch))


def test_probe_inputs_refused_before_any_catalog_read() -> None:
    def refused(**body: Any) -> TelemetryRequestError:
        con = RecordingCon()
        with pytest.raises(TelemetryRequestError) as info:
            _call("telemetry_probe", con, **body)
        assert not con.sql, "a catalog read happened before the refusal"
        assert SENTINEL not in json.dumps(info.value.fields)
        return info.value

    for field in ("project_ref", "session_ref"):
        for bad in (f"{SENTINEL}\x00", f"{SENTINEL}\ud800"):
            err = refused(**{**_SESSION, field: bad})
            assert (err.status, err.error_type, err.fields) == (400, "bad_request", {"field": field})
    for given in ({"project_ref": "p"}, {"session_ref": "s", "session_started_at": _SESSION["session_started_at"]}):
        err = refused(**given)
        assert (err.status, err.error_type) == (400, "bad_request") and "requires" in err.fields
    for bad in ("2026-01-01T12:00:00Z", "nope", 7, "1960-01-01T00:00:00.000Z"):
        err = refused(**{**_SESSION, "session_started_at": bad})
        assert (err.status, err.error_type, err.fields) == (
            422,
            "schema_gate",
            {"column": "session_started_at", "reason": "wire_timestamp"},
        )
    err = refused(**{**_SESSION, "session_ref": ""})
    assert (err.status, err.fields) == (400, {"field": "session_ref"})
