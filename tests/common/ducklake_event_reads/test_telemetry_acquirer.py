"""telemetry_acquirer and the production pin: every event acquire names its pin and may bind a declared secret."""

from __future__ import annotations

from typing import Any

import pytest

from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_event_read_fakes import NO_OPS, RecordingCon

_PAYLOAD = {"verb": "telemetry_health_count", "params": {}}


def _production_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", rt.TELEMETRY_PROD_META_SCHEMA)
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.TELEMETRY_PROD_DATA_PATH)


def _spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"fetch": [], "open": [], "warm": [], "con": RecordingCon()}

    def fetch_dsn(*args: Any, **kwargs: Any) -> dict[str, str]:
        seen["fetch"].append((args, kwargs))
        return {}

    def open_connection(**kwargs: Any) -> RecordingCon:
        seen["open"].append(kwargs)
        return seen["con"]

    real = rt.get_warm_connection

    def get_warm_connection(**kwargs: Any) -> Any:
        seen["warm"].append({k: kwargs[k] for k in ("slot", "alias", "probe_sql", "meta_schema", "data_path")})
        return real(**kwargs)

    monkeypatch.setattr(rt, "fetch_dsn", fetch_dsn)
    monkeypatch.setattr(rt, "open_connection", open_connection)
    monkeypatch.setattr(rt, "get_warm_connection", get_warm_connection)
    return seen


def test_acquirer_requires_a_declared_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy(monkeypatch)
    with pytest.raises(TypeError):
        ev.acquirer_for("named_read", _PAYLOAD, NO_OPS, "/e")  # type: ignore[call-arg]
    assert seen["fetch"] == [] and seen["open"] == []
    _production_pin(monkeypatch)
    acquire = ev.acquirer_for("named_read", _PAYLOAD, NO_OPS, "/e", pin=ev.require_production_pin)
    acquire()
    (opened,) = seen["open"]
    assert opened["alias"] == rt.TELEMETRY_CATALOG_ALIAS and opened["meta_schema"] == rt.TELEMETRY_PROD_META_SCHEMA
    assert opened["data_path"] == rt.TELEMETRY_PROD_DATA_PATH and seen["warm"][0]["slot"] == ev.TELEMETRY_SLOT


def test_acquirer_takes_a_declared_pin_secret_and_read_only_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_pin(monkeypatch)
    seen = _spy(monkeypatch)
    order: list[str] = []

    def pin() -> tuple[str, str]:
        order.append("pin")
        return ev.require_production_pin()

    acquire = ev.acquirer_for("named_read", _PAYLOAD, NO_OPS, "/e", pin=pin, dsn_secret_id="reader-secret", read_only=True)
    assert order == ["pin"] and seen["open"] == []
    acquire()
    assert seen["fetch"] == [(("reader-secret",), {})]
    (opened,) = seen["open"]
    assert opened["read_only"] is True and opened["meta_schema"] == rt.TELEMETRY_PROD_META_SCHEMA
    assert [sql for sql, _ in seen["con"].calls][0].startswith("SET memory_limit=")
    ev._INTERRUPTED_SLOTS.add(ev.TELEMETRY_SLOT)
    acquire()
    assert "__ducklake_metadata_" in seen["warm"][1]["probe_sql"]


def test_require_production_pin_refuses_smoke_ops_and_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for meta_schema, data_path in (
        (rt.SMOKE_META_SCHEMA, rt.SMOKE_DATA_PATH),
        ("ducklake_ops", rt.TELEMETRY_PROD_DATA_PATH),
        (rt.TELEMETRY_PROD_META_SCHEMA, rt.SMOKE_DATA_PATH),
        ("", ""),
    ):
        monkeypatch.setenv("TELEMETRY_META_SCHEMA", meta_schema)
        monkeypatch.setenv("TELEMETRY_DATA_PATH", data_path)
        with pytest.raises(rt.DuckLakeRuntimeError) as raised:
            ev.require_production_pin()
        assert meta_schema not in str(raised.value) or meta_schema == ""
    monkeypatch.delenv("TELEMETRY_META_SCHEMA")
    monkeypatch.delenv("TELEMETRY_DATA_PATH")
    with pytest.raises(rt.DuckLakeRuntimeError):
        ev.require_production_pin()
    _production_pin(monkeypatch)
    assert ev.require_production_pin() == (rt.TELEMETRY_PROD_META_SCHEMA, rt.TELEMETRY_PROD_DATA_PATH)
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.TELEMETRY_PROD_DATA_PATH.rstrip("/"))
    assert ev.require_production_pin()[0] == rt.TELEMETRY_PROD_META_SCHEMA
