"""CONCERN: facade completeness + Decision-104 module-object interception (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith:
test_facade_reexports_identity_equal_to_owning_submodule (with the _PACKAGE_SUBMODULES tuple,
scanning THIS file's own source for smoke.<name> references -- narrower post-move than the
original whole-monolith scan; still green since every referenced name is a valid re-export, see
OPEN RISK 2 in the plan) plus FOUR of the five module-object interception tests (core/ec_gates,
core/maintenance_gates, core/ops_gates, and lambda_ec_gates.lambda_churn via ops_churn_regate).
The fifth (canary family) moved to test_canary.py: it depends on the canary-owned
_stub_canary_rehearsal helper, which the no-cross-test-import guard forbids importing here.
"""

from __future__ import annotations

import pathlib
import re

import scripts.ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import (
    canary,
    core,
    direct_gates,
    lambda_ec_gates,
    lambda_maintenance_gates,
    lambda_ops_gates,
)
from tests.fixtures.ducklake_smoke_fakes import _Resp

_PACKAGE_SUBMODULES = (core, direct_gates, lambda_ec_gates, lambda_maintenance_gates, lambda_ops_gates, canary)


def test_facade_reexports_identity_equal_to_owning_submodule():
    """Every smoke.<name> this suite references resolves on the facade and is identity-equal to
    the owning scripts.ducklake_smoke submodule's attribute.

    Membership is derived from THIS FILE's own source (re-scanned each run), not a hardcoded list
    (tests/CLAUDE.md, growth-safe): a future test that adds a new `smoke.<name>` reference is
    automatically covered next run. A shallow `hasattr(smoke, name)` check would pass even if the
    facade re-exported a STALE/separate copy (e.g. captured before a submodule reload); the
    identity (`is`) check is what actually proves a moved body and its test double share one
    binding -- the failure mode VP step 1's fix_if calls out (silent no-op mock).
    """
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"smoke\.([A-Za-z_][A-Za-z0-9_]*)", src)))
    assert referenced, "no smoke.<name> references found -- regex or fixture broken"
    checked = 0
    for name in referenced:
        assert hasattr(smoke, name), f"facade is missing re-export: smoke.{name}"
        facade_val = getattr(smoke, name)
        for owner in _PACKAGE_SUBMODULES:
            if hasattr(owner, name):
                assert getattr(owner, name) is facade_val, (
                    f"smoke.{name} is not identity-equal to {owner.__name__}.{name} -- "
                    "facade re-export is a stale/separate copy"
                )
                checked += 1
    assert checked > 0


def test_core_sigv4_invoke_interception_lambda_ec_gates_family(monkeypatch, capsys):
    """Module-object interception: patching core._sigv4_invoke/_function_url (not smoke.*) intercepts
    a lambda_ec_gates gate, proving the Decision 104 convention actually wires through core.<name>."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")

    def fake_invoke(url, payload, **kw):
        if payload["action"] == "read_current":
            return _Resp(200, {"row_count": 1})
        return _Resp(200, {"write_denied": True})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    monkeypatch.setattr(lambda_ec_gates, "_telemetry_read_checks", lambda **kw: None)
    monkeypatch.setattr(lambda_ec_gates, "dq_check_gate", lambda **kw: None)
    monkeypatch.setattr(lambda_ec_gates, "retired_actions_gate", lambda **kw: None)
    smoke.lambda_reader()
    assert "READER OK rows=1 write_denied=true" in capsys.readouterr().out


def test_core_sigv4_invoke_interception_lambda_maintenance_gates_family(monkeypatch, capsys):
    """Module-object interception: patching core._sigv4_invoke intercepts a lambda_maintenance_gates gate."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200, {"ok": True, "files_before": 1, "files_after_merge": 1})
    )
    smoke.lambda_maintenance_merge()
    assert "MAINTENANCE_MERGE OK" in capsys.readouterr().out


def test_core_sigv4_invoke_interception_lambda_ops_gates_family(monkeypatch, capsys):
    """Module-object interception: patching core._sigv4_invoke intercepts a lambda_ops_gates gate."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200, {"ok": True, "phase_reached": "done"}))
    smoke.connect_probe()
    out = capsys.readouterr().out
    assert "CONNECT_PROBE reader=" in out
    assert "CONNECT_PROBE writer=" in out


def test_lambda_ec_gates_lambda_churn_interception_via_ops_churn_regate(monkeypatch, capsys):
    """Module-object interception: patching lambda_ec_gates.lambda_churn (not smoke.lambda_churn)
    intercepts ops_churn_regate's cross-module delegation call."""
    called = {}

    def fake_lambda_churn(*, profile=None, region="eu-west-2"):
        called["invoked"] = (profile, region)

    monkeypatch.setattr(lambda_ec_gates, "lambda_churn", fake_lambda_churn)
    smoke.ops_churn_regate(profile="agent_platform", region="eu-west-2")
    assert called["invoked"] == ("agent_platform", "eu-west-2")
    assert "OPS_CHURN_REGATE OK" in capsys.readouterr().out
