"""CONCERN: the facade CLI main() dispatch (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith: all nine test_main_*
tests exercising scripts/ducklake_neon_smoke_test.py's own main() -- direct-gate dispatch,
churn-incontainer dispatch, lambda-attach dispatch, lambda-gate loud-fail, and canary dispatch.
main() is defined in the facade module itself (not in any scripts/ducklake_smoke/ submodule).
"""

from __future__ import annotations

import pytest

import scripts.ducklake_neon_smoke_test as smoke


def test_main_attach(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "attach_roundtrip", lambda profile=None: 1)
    assert smoke.main(["--attach"]) == 0
    assert "ATTACH OK rows=1" in capsys.readouterr().out


def test_main_churn_gate(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "churn_gate", lambda profile=None: {"collision_rate": 0.1, "p95_latency_ms": 12.0})
    assert smoke.main(["--churn-gate"]) == 0
    assert "CHURN_GATE PASS" in capsys.readouterr().out


def test_main_restore_drill(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "restore_drill", lambda profile=None: True)
    assert smoke.main(["--restore-drill"]) == 0
    assert "RESTORE_OK read-your-write verified" in capsys.readouterr().out


def test_main_loud_fail_returns_1(monkeypatch, capsys):
    def _boom(profile=None):
        raise smoke.SmokeTestFailure("CHURN_GATE FAIL: over budget")

    monkeypatch.setattr(smoke, "churn_gate", _boom)
    assert smoke.main(["--churn-gate"]) == 1
    assert "CHURN_GATE FAIL" in capsys.readouterr().err


def test_main_requires_a_mode():
    with pytest.raises(SystemExit):
        smoke.main([])


def test_main_lambda_churn_incontainer_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(
        smoke, "lambda_churn_incontainer", lambda profile=None, region="eu-west-2": print("CHURN_INCONTAINER ok")
    )
    assert smoke.main(["--lambda-churn-incontainer"]) == 0
    assert "CHURN_INCONTAINER" in capsys.readouterr().out


def test_main_lambda_attach_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "lambda_attach", lambda profile=None, region="eu-west-2": print("LAMBDA_ATTACH OK stub"))
    assert smoke.main(["--lambda-attach"]) == 0
    assert "LAMBDA_ATTACH OK" in capsys.readouterr().out


def test_main_lambda_gate_loud_fail_returns_1(monkeypatch, capsys):
    def _boom(profile=None, region="eu-west-2"):
        raise smoke.SmokeTestFailure("READER FAIL: boundary")

    monkeypatch.setattr(smoke, "lambda_reader", _boom)
    assert smoke.main(["--lambda-reader"]) == 1
    assert "READER FAIL" in capsys.readouterr().err


def test_main_canary_rehearsal_dispatch(monkeypatch, capsys):
    """--canary-rehearsal CLI flag dispatches to canary_rehearsal()."""
    monkeypatch.setattr(
        smoke,
        "canary_rehearsal",
        lambda profile=None, region="eu-west-2", json_output=False: print("CANARY_REHEARSAL OK"),
    )
    assert smoke.main(["--canary-rehearsal"]) == 0
    assert "CANARY_REHEARSAL OK" in capsys.readouterr().out


def test_main_ops_read_your_write_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "ops_read_your_write", lambda profile=None, region="eu-west-2": print("OPS_RYW OK"))
    assert smoke.main(["--ops-read-your-write"]) == 0
    assert "OPS_RYW OK" in capsys.readouterr().out


def test_main_migrate_ops_recs_columns_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(
        smoke, "migrate_ops_recs_columns", lambda profile=None, region="eu-west-2": print("MIGRATE_COLUMNS OK")
    )
    assert smoke.main(["--migrate-ops-recs-columns"]) == 0
    assert "MIGRATE_COLUMNS OK" in capsys.readouterr().out


def test_main_ops_churn_regate_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "ops_churn_regate", lambda profile=None, region="eu-west-2": print("OPS_CHURN_REGATE OK"))
    assert smoke.main(["--ops-churn-regate"]) == 0
    assert "OPS_CHURN_REGATE OK" in capsys.readouterr().out


def test_main_catalog_restore_drill_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "catalog_restore_drill", lambda profile=None, region="eu-west-2": print("CATALOG_RESTORE OK"))
    assert smoke.main(["--catalog-restore-drill"]) == 0
    assert "CATALOG_RESTORE OK" in capsys.readouterr().out


def test_main_connect_probe_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "connect_probe", lambda profile=None, region="eu-west-2": print("CONNECT_PROBE OK"))
    assert smoke.main(["--connect-probe"]) == 0
    assert "CONNECT_PROBE OK" in capsys.readouterr().out


def test_main_lambda_warm_reuse_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(
        smoke,
        "lambda_warm_reuse",
        lambda profile=None, region="eu-west-2", json_output=False: print("WARM_REUSE OK"),
    )
    assert smoke.main(["--lambda-warm-reuse"]) == 0
    assert "WARM_REUSE OK" in capsys.readouterr().out


def test_main_lambda_warm_reuse_writer_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(
        smoke,
        "lambda_warm_reuse_writer",
        lambda profile=None, region="eu-west-2", json_output=False: print("WARM_REUSE_WRITER OK"),
    )
    assert smoke.main(["--lambda-warm-reuse-writer"]) == 0
    assert "WARM_REUSE_WRITER OK" in capsys.readouterr().out


def test_main_lambda_row_rules_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "lambda_row_rules", lambda profile=None, region="eu-west-2": print("LAMBDA_ROW_RULES OK stub"))
    assert smoke.main(["--lambda-row-rules"]) == 0
    assert "LAMBDA_ROW_RULES OK" in capsys.readouterr().out


def test_main_lambda_telemetry_production_dispatch(monkeypatch, capsys):
    seen = {}

    def fake(profile=None, region="eu-west-2"):
        seen.update(profile=profile, region=region)
        print("LAMBDA_TELEMETRY_PRODUCTION OK stub")

    assert "lambda_telemetry_production" in smoke._LAMBDA_GATES
    monkeypatch.setattr(smoke, "lambda_telemetry_production", fake)
    assert smoke.main(["--lambda-telemetry-production", "--profile", "p", "--region", "r"]) == 0
    assert seen == {"profile": "p", "region": "r"}
    assert "LAMBDA_TELEMETRY_PRODUCTION OK" in capsys.readouterr().out


def test_main_lambda_telemetry_flag_is_retired(monkeypatch):
    called = []
    for attr in ("lambda_telemetry_production", "lambda_telemetry_smoke", "lambda_telemetry_reader"):
        monkeypatch.setattr(smoke, attr, lambda attr=attr, **kw: called.append(attr))
    assert not hasattr(smoke, "lambda_telemetry") and "lambda_telemetry" not in smoke._LAMBDA_GATES
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--lambda-telemetry"])
    assert exc.value.code == 2
    assert called == []


def test_create_telemetry_flag_dispatches_the_gate(monkeypatch):
    from scripts.ducklake_smoke import lambda_maintenance_gates

    assert smoke.lambda_maintenance_create_telemetry is lambda_maintenance_gates.lambda_maintenance_create_telemetry
    assert "lambda_maintenance_create_telemetry" in smoke._LAMBDA_GATES
    seen = []
    monkeypatch.setattr(smoke, "lambda_maintenance_create_telemetry", lambda **kw: seen.append(kw))
    assert smoke.main(["--lambda-maintenance-create-telemetry"]) == 0
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("flag", "attr"),
    [("--lambda-telemetry-smoke", "lambda_telemetry_smoke"), ("--lambda-telemetry-reader", "lambda_telemetry_reader")],
)
def test_telemetry_twin_and_reader_flags_dispatch_their_gates(monkeypatch, flag, attr):
    seen = {}

    def fake(profile=None, region="eu-west-2"):
        seen.update(profile=profile, region=region)

    assert attr in smoke._LAMBDA_GATES
    monkeypatch.setattr(smoke, attr, fake)
    assert smoke.main([flag, "--profile", "p", "--region", "r"]) == 0
    assert seen == {"profile": "p", "region": "r"}
