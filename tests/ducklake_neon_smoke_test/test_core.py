"""CONCERN: scripts/ducklake_smoke/core.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith: fetch_dsn (delegates to
ducklake_runtime), _libpq_conninfo, _dsn_uri (delegates to catalog_dr -- grouped here with the
other DSN-formatting tests per the original monolith's ordering), _open_attached (delegates to
ducklake_runtime.open_connection, exercised with the smoke FakeCon), and the lambda-invoke
primitives also owned by core (_function_url, _sigv4_invoke, _ok_json). All calls go through the
smoke facade (scripts.ducklake_neon_smoke_test); no test here needs a bare `core`/`direct_gates`
module reference, so only the facade is imported.
"""

from __future__ import annotations

import json
import types

# boto3 and requests are imported at MODULE scope even though the tests reference them only via
# LAZY imports inside test_fetch_dsn_success / test_fetch_dsn_missing_key_raises / test_sigv4_invoke_signs
# / test_sigv4_invoke_unsigned. This makes the file's heavy-dep requirement visible to the fast
# tier's cheap `--collect-only` pass so pr-validate defers it PROACTIVELY to the full post-merge
# tier, instead of catching it REACTIVELY. Both are deliberately excluded from requirements-fast.txt.
# See scripts/checks/_scaffolding.py::partition_changed_tests_by_collectability.
import boto3  # noqa: F401
import pytest
import requests  # noqa: F401

import scripts.ducklake_neon_smoke_test as smoke
from src.common.ducklake_version import pinned_duckdb_version
from tests.fixtures.ducklake_smoke_fakes import _DSN, FakeCon, _Resp


def test_fetch_dsn_success(monkeypatch):
    captured = {}

    class _Client:
        def get_parameter(self, Name, WithDecryption):
            captured["parameter_name"] = Name
            captured["decrypt"] = WithDecryption
            return {"Parameter": {"Value": json.dumps(_DSN)}}

    class _Session:
        def __init__(self, profile_name=None):
            captured["profile"] = profile_name

        def client(self, name):
            captured["client"] = name
            return _Client()

    monkeypatch.setattr(boto3, "Session", _Session)
    out = smoke.fetch_dsn(profile="agent_platform")
    assert out["host"] == _DSN["host"]
    assert captured["parameter_name"] == smoke.DSN_PARAMETER_NAME
    assert captured["decrypt"] is True
    assert captured["client"] == "ssm"


def test_fetch_dsn_missing_key_raises(monkeypatch):
    bad = {k: v for k, v in _DSN.items() if k != "password"}

    class _Client:
        def get_parameter(self, Name, WithDecryption):
            return {"Parameter": {"Value": json.dumps(bad)}}

    class _Session:
        def __init__(self, profile_name=None):
            pass

        def client(self, name):
            return _Client()

    monkeypatch.setattr(boto3, "Session", _Session)
    with pytest.raises(RuntimeError, match="missing required keys"):
        smoke.fetch_dsn()


def test_libpq_conninfo_uses_explicit_sslmode():
    out = smoke._libpq_conninfo(_DSN)
    assert "sslmode=require" in out
    assert "host=ep-test-123.eu-west-2.aws.neon.tech" in out


def test_libpq_conninfo_defaults_sslmode_when_absent():
    dsn = {k: v for k, v in _DSN.items() if k != "sslmode"}
    assert "sslmode=require" in smoke._libpq_conninfo(dsn)


def test_dsn_uri_default_and_explicit_sslmode():
    assert smoke._dsn_uri(_DSN).startswith("postgresql://ducklake_ops:secret-pw@")  # pragma: allowlist secret
    no_ssl = {k: v for k, v in _DSN.items() if k != "sslmode"}
    assert smoke._dsn_uri(no_ssl).endswith("?sslmode=require")


def test_open_attached_delegates_to_runtime(monkeypatch):
    """_open_attached now delegates to ducklake_runtime.open_connection (single ATTACH impl)."""
    con = FakeCon()
    captured = {}

    def fake_open(**kwargs):
        captured.update(kwargs)
        return con

    monkeypatch.setattr(smoke.ducklake_runtime, "open_connection", fake_open)
    out = smoke._open_attached(_DSN, profile="agent_platform")
    assert out is con
    assert captured["dsn"] is _DSN
    assert captured["extension_directory"] is None  # dev/smoke mode = network INSTALL
    assert captured["profile"] == "agent_platform"
    assert captured["data_path"] == smoke.SMOKE_DATA_PATH


def test_open_attached_real_attach_composition(monkeypatch):
    """Through the real runtime: dev-mode INSTALL + a single ATTACH with META_SCHEMA."""
    con = FakeCon()
    fake_duckdb = types.SimpleNamespace(connect=lambda: con, __version__=pinned_duckdb_version())
    monkeypatch.setattr(smoke.ducklake_runtime.ducklake_spike, "_require_duckdb", lambda: fake_duckdb)
    monkeypatch.setattr(smoke.ducklake_runtime.ducklake_spike, "_set_s3_credentials", lambda c, profile=None: None)
    smoke._open_attached(_DSN, profile=None)
    attach_sql = [s for s in con.executed if s.startswith("ATTACH")]
    assert len(attach_sql) == 1
    assert "ducklake:postgres:" in attach_sql[0]
    # Smoke attaches its OWN meta-schema (rec-2099) -- never the production ducklake_ops catalog.
    assert "META_SCHEMA 'ducklake_smoke'" in attach_sql[0]
    assert any("INSTALL postgres" in s for s in con.executed)


def test_function_url_from_env(monkeypatch):
    monkeypatch.setenv(smoke.WRITER_URL_ENV, "https://writer.lambda-url/")
    assert smoke._function_url("writer") == "https://writer.lambda-url"


def test_function_url_from_terraform(monkeypatch):
    monkeypatch.delenv(smoke.READER_URL_ENV, raising=False)
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="https://reader.url\n", stderr=""),
    )
    assert smoke._function_url("reader") == "https://reader.url"


def test_function_url_missing_raises(monkeypatch):
    monkeypatch.delenv(smoke.WRITER_URL_ENV, raising=False)
    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(smoke.SmokeTestFailure, match="cannot reach"):
        smoke._function_url("writer")


def test_function_url_maintenance_smoke_from_env(monkeypatch):
    """T2.18 c9 split: the maintenance smoke gates resolve a DISTINCT env var from the admin
    maintenance function's MAINTENANCE_URL_ENV."""
    monkeypatch.setenv(smoke.MAINTENANCE_SMOKE_URL_ENV, "https://maintenance-smoke.lambda-url/")
    assert smoke._function_url("maintenance_smoke") == "https://maintenance-smoke.lambda-url"


def test_function_url_maintenance_smoke_terraform_output_name(monkeypatch):
    """Falls back to `terraform output ducklake_maintenance_smoke_function_url` (matches the
    terraform/personal/ducklake_maintenance_smoke.tf output name)."""
    monkeypatch.delenv(smoke.MAINTENANCE_SMOKE_URL_ENV, raising=False)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="https://maintenance-smoke.url\n", stderr="")

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    assert smoke._function_url("maintenance_smoke") == "https://maintenance-smoke.url"
    assert captured["cmd"][-1] == "ducklake_maintenance_smoke_function_url"


def test_sigv4_invoke_signs(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _Resp(200, {"ok": True})

    monkeypatch.setattr(requests, "post", fake_post)

    class _FC:
        access_key = "ak"  # pragma: allowlist secret
        secret_key = "sk"  # pragma: allowlist secret
        token = None

    class _Session:
        def __init__(self, profile_name=None):
            pass

        def get_credentials(self):
            return types.SimpleNamespace(get_frozen_credentials=lambda: _FC())

    import boto3

    monkeypatch.setattr(boto3, "Session", _Session)
    resp = smoke._sigv4_invoke("https://x", {"action": "attach_check"}, sign=True)
    assert resp.status_code == 200
    assert "Authorization" in captured["headers"]  # SigV4 added an Authorization header


def test_sigv4_invoke_unsigned(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _Resp(403)

    monkeypatch.setattr(requests, "post", fake_post)
    resp = smoke._sigv4_invoke("https://x", {"action": "attach_check"}, sign=False)
    assert resp.status_code == 403
    assert "Authorization" not in captured["headers"]


def test_ok_json_status_mismatch_raises():
    with pytest.raises(smoke.SmokeTestFailure, match="unexpected status"):
        smoke._ok_json(_Resp(500, text="boom"))
