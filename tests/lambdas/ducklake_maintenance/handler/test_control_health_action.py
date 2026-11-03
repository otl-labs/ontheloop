"""action_control_health concern for src/lambdas/ducklake_maintenance/handler.py (T2.26
control-table-class-and-counter-conformance).

Relocated here from tests/test_ducklake_maintenance.py so this dispatch/wiring concern has its
dedicated coverage home (this is the concern-split test PACKAGE for handler.py, Decision 104) --
the control_health() invariant-assertion logic itself is covered separately in
tests/test_ducklake_control_health.py (src/common/ducklake_control_health.py's own home).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from tests.fixtures.ducklake_maintenance_handler import _response_body

pytestmark = pytest.mark.unit


def test_handler_catalog_stats_listed_in_actions():
    """catalog_stats must appear in the actions list returned on an unknown action."""
    body = _response_body(h.handler({"action": "bad"}))
    assert "catalog_stats" in body["actions"]


def test_handler_dispatches_control_health_without_a_connection_arg():
    """The handler never pre-opens a connection -- control_health receives con=None too."""
    action_mock = MagicMock(return_value={"ok": True})
    with patch.dict(h._ACTIONS, {"control_health": action_mock}):
        r = h.handler({"action": "control_health", "meta_schema": "ducklake_ops"})
    assert r["statusCode"] == 200
    assert action_mock.call_args.args[1] is None
