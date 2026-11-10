"""restore_rehearsal dispatch concern for src/lambdas/ducklake_maintenance/handler.py (rec-5023, Decision 251)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from src.lambdas.ducklake_maintenance import restore_rehearsal_actions as rra

pytestmark = pytest.mark.unit


def test_restore_rehearsal_is_registered():
    assert h._ACTIONS["restore_rehearsal"] is rra.action_restore_rehearsal


def test_refusal_maps_to_400_with_fixed_reason_only(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "agent-platform-ducklake-maintenance")
    resp = h.handler({"action": "restore_rehearsal", "step": "restore", "confirm": "x", "run_id": "r1"})
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body == {"ok": False, "error_type": "refused", "error": "restore_rehearsal refused"}


def test_refusal_never_carries_exception_text(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "agent-platform-ducklake-maintenance")
    boom = rra.RestoreRehearsalRefused("s3://secret-bucket/path postgresql://u:pw@host/db")
    with patch.dict(h._ACTIONS, {"restore_rehearsal": lambda e, c: (_ for _ in ()).throw(boom)}):
        resp = h.handler({"action": "restore_rehearsal"})
    assert resp["statusCode"] == 400
    assert "secret-bucket" not in resp["body"] and "postgresql" not in resp["body"]


@pytest.mark.parametrize("action", ["catalog_reinit", "gc_ops", "restore_drill", "unknown_thing"])
def test_ephemeral_function_refuses_every_other_action_before_any_connection(monkeypatch, action):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "maintenance-restore-rehearsal-ephemeral")
    with patch.object(h.rt, "open_connection", side_effect=AssertionError("connected")):
        resp = h.handler({"action": action, "meta_schema": "x", "confirm": "x", "data_path": "s3://b/p/"})
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error_type"] == "refused"


def test_ephemeral_function_runs_restore_rehearsal(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "maintenance-restore-rehearsal-ephemeral")
    with patch.dict(h._ACTIONS, {"restore_rehearsal": lambda e, c: {"ok": True}}):
        resp = h.handler({"action": "restore_rehearsal"})
    assert resp["statusCode"] == 200
