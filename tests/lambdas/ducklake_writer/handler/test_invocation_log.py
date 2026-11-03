"""One ducklake_invocation line per writer invocation on every path (Decision 214)."""

from __future__ import annotations

import json

import pytest

import src.lambdas.ducklake_writer.handler as h
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_writer_handler import FakeCon

pytestmark = pytest.mark.unit

_FIXED = {"event", "function", "action", "status", "connect_ms", "connect_reused", "reopened", "elapsed_ms"}


def _lines(out: str) -> list[dict]:
    return [json.loads(x) for x in out.splitlines() if x.startswith("{") and '"ducklake_invocation"' in x]


def _one(capsys, event) -> dict:
    capsys.readouterr()
    h.handler(event)
    lines = _lines(capsys.readouterr().out)
    assert len(lines) == 1 and set(lines[0]) == _FIXED and lines[0]["function"] == "writer"
    return lines[0]


def _raising(exc: Exception):
    def _fn(payload, con):
        raise exc

    return _fn


def test_one_invocation_line_per_path(monkeypatch, capsys):
    monkeypatch.setattr(h, "_open_writer_connection", lambda: FakeCon())
    monkeypatch.setitem(h._ACTIONS, "write", lambda payload, con: {"ok": True})

    ok = _one(capsys, {"action": "write", "payload": "SECRET-PAYLOAD-VALUE"})
    assert (ok["action"], ok["status"], ok["connect_reused"], ok["reopened"]) == ("write", 200, False, False)
    assert _one(capsys, {"action": "write"})["connect_reused"] is True

    bad = _one(capsys, {"action": "SECRET-ACTION-NAME"})
    assert (bad["action"], bad["status"], bad["connect_ms"]) == ("unknown", 400, None)
    capsys.readouterr()
    h.handler({"action": "SECRET-ACTION-NAME"})
    assert "SECRET" not in capsys.readouterr().out

    row_rule = rt.RowRuleViolationError("ops_x", "not_blank", "col")
    monkeypatch.setitem(h._ACTIONS, "write", _raising(row_rule))
    assert _one(capsys, {"action": "write"})["status"] == 422
    monkeypatch.setitem(h._ACTIONS, "write", _raising(rt.OCCRetryExhaustedError("occ")))
    assert _one(capsys, {"action": "write"})["status"] == 503
    monkeypatch.setitem(h._ACTIONS, "write", _raising(rt.DuckLakeRuntimeError("boom")))
    assert _one(capsys, {"action": "write"})["status"] == 500

    monkeypatch.setattr(rt, "reset_warm_connection", lambda: None)
    conless = _one(capsys, {"action": "reset_warm_connection"})
    assert (conless["status"], conless["connect_ms"], conless["connect_reused"]) == (200, None, None)


def test_reopen_paths_are_marked(monkeypatch, capsys):
    monkeypatch.setattr(h, "_open_writer_connection", lambda: FakeCon())
    calls = {"n": 0}

    def flaky(payload, con):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("server closed the connection unexpectedly")
        return {"ok": True}

    monkeypatch.setitem(h._ACTIONS, "write", flaky)
    assert _one(capsys, {"action": "write"})["reopened"] is True

    monkeypatch.setitem(h._ACTIONS, "write", lambda payload, con: {"ok": True})
    monkeypatch.setattr(
        h,
        "_warm_writer_connection",
        lambda force_reopen=False: (FakeCon(), {"connect_ms": 7.0, "reused": False, "reopened": True}),
    )
    line = _one(capsys, {"action": "write"})
    assert line["reopened"] is True and line["connect_ms"] == 7.0
