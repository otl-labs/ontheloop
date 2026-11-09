"""Tests for src/common/catalog_dr.py (T2.18 FP-B / CD.34 -- 100% coverage, mocked subprocess + boto)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.common.catalog_dr import (
    DR_METRIC_NAME,
    LAMBDA_PG_DUMP_PATH,
    PINNED_PG_VERSION,
    CatalogDrError,
    build_dr_key,
    build_dr_object_metadata,
    build_pg_dump_cmd,
    dsn_uri,
    run_catalog_dump,
)
from src.common.ducklake_runtime import PINNED_DUCKDB_VERSION
from src.common.ducklake_version import pinned_duckdb_version

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 7, 3, 0, 0, tzinfo=timezone.utc)
_SAMPLE_DSN: dict[str, str] = {
    "username": "testuser",
    "password": "testpass",  # pragma: allowlist secret
    "host": "ep-test-123.eu-west-2.aws.neon.tech",
    "dbname": "neondb",
    "sslmode": "require",
}


# ---------------------------------------------------------------------------
# dsn_uri
# ---------------------------------------------------------------------------


def test_dsn_uri_basic():
    uri = dsn_uri(_SAMPLE_DSN)
    assert uri.startswith("postgresql://testuser:testpass@")  # pragma: allowlist secret
    assert "neondb" in uri
    assert "sslmode=require" in uri


def test_dsn_uri_defaults_sslmode_require():
    dsn = dict(_SAMPLE_DSN)
    dsn.pop("sslmode", None)
    uri = dsn_uri(dsn)
    assert "sslmode=require" in uri


def test_dsn_uri_custom_sslmode():
    dsn = dict(_SAMPLE_DSN, sslmode="verify-full")
    uri = dsn_uri(dsn)
    assert "sslmode=verify-full" in uri


# ---------------------------------------------------------------------------
# build_pg_dump_cmd
# ---------------------------------------------------------------------------


def test_build_pg_dump_cmd_contains_format_custom():
    cmd = build_pg_dump_cmd("postgresql://u:p@h/db", "/tmp/out.dump")
    assert "--format=custom" in cmd


def test_build_pg_dump_cmd_contains_serializable_deferrable():
    cmd = build_pg_dump_cmd("postgresql://u:p@h/db", "/tmp/out.dump")
    assert "--serializable-deferrable" in cmd


def test_build_pg_dump_cmd_contains_file_arg():
    cmd = build_pg_dump_cmd("postgresql://u:p@h/db", "/tmp/out.dump")
    assert "--file" in cmd
    assert "/tmp/out.dump" in cmd


def test_build_pg_dump_cmd_dsn_uri_last():
    dsn_str = "postgresql://u:p@h/db"
    cmd = build_pg_dump_cmd(dsn_str, "/tmp/out.dump")
    assert cmd[-1] == dsn_str


def test_build_pg_dump_cmd_custom_pg_dump_path():
    cmd = build_pg_dump_cmd("postgresql://u:p@h/db", "/tmp/out.dump", pg_dump_path="/usr/bin/pg_dump")
    assert cmd[0] == "/usr/bin/pg_dump"


def test_build_pg_dump_cmd_default_lambda_path():
    cmd = build_pg_dump_cmd("postgresql://u:p@h/db", "/tmp/out.dump")
    assert cmd[0] == LAMBDA_PG_DUMP_PATH


def test_build_pg_dump_cmd_no_plain_format():
    cmd = build_pg_dump_cmd("postgresql://u:p@h/db", "/tmp/out.dump")
    assert "--format=plain" not in cmd
    assert "--no-owner" not in cmd


# ---------------------------------------------------------------------------
# build_dr_key
# ---------------------------------------------------------------------------


def test_build_dr_key_contains_date_prefix():
    key = build_dr_key(_NOW)
    assert "2026/06/07" in key


def test_build_dr_key_contains_pg_version():
    key = build_dr_key(_NOW, pg_version="16")
    assert "pg16" in key


def test_build_dr_key_contains_duckdb_version():
    pin = pinned_duckdb_version()
    key = build_dr_key(_NOW, duckdb_version=pin)
    assert f"duckdb{pin}" in key or f"duckdb-{pin}" in key or pin in key


def test_build_dr_key_has_dump_extension():
    key = build_dr_key(_NOW)
    assert key.endswith(".dump")


def test_build_dr_key_contains_timestamp():
    key = build_dr_key(_NOW)
    assert "20260607T030000Z" in key


def test_build_dr_key_starts_with_catalog_dr():
    key = build_dr_key(_NOW)
    assert key.startswith("catalog-dr/")


def test_build_dr_key_default_versions():
    key = build_dr_key(_NOW)
    assert PINNED_PG_VERSION in key
    assert PINNED_DUCKDB_VERSION in key


# ---------------------------------------------------------------------------
# build_dr_object_metadata
# ---------------------------------------------------------------------------


def test_build_dr_object_metadata_has_pg_version():
    meta = build_dr_object_metadata(pg_version="16")
    assert meta["pg_version"] == "16"


def test_build_dr_object_metadata_has_duckdb_version():
    pin = pinned_duckdb_version()
    meta = build_dr_object_metadata(duckdb_version=pin)
    assert meta["duckdb_version"] == pin


def test_build_dr_object_metadata_default_versions():
    meta = build_dr_object_metadata()
    assert meta["pg_version"] == PINNED_PG_VERSION
    assert meta["duckdb_version"] == PINNED_DUCKDB_VERSION


# ---------------------------------------------------------------------------
# run_catalog_dump: happy path (mocked subprocess + boto)
# ---------------------------------------------------------------------------


def _make_mock_subprocess_ok(dump_bytes: int = 1024) -> Any:
    """Return a mock CompletedProcess with returncode=0 and a callable that writes a fake dump file."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    return mock_result


def _patch_run_catalog_dump(tmp_path: Path, *, returncode: int = 0, dump_size: int = 512):
    """Context manager patching subprocess.run + boto3 + emit_metric + TemporaryDirectory."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        mock_proc = MagicMock()
        mock_proc.returncode = returncode
        mock_proc.stderr = "pg_dump: error: connection refused" if returncode != 0 else ""

        mock_s3_client = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3_client

        def fake_run(cmd, **kwargs):
            if returncode == 0:
                out_file = Path(cmd[cmd.index("--file") + 1])
                out_file.write_bytes(b"PGDMP" + b"\x00" * (dump_size - 5))
            return mock_proc

        with (
            patch("subprocess.run", side_effect=fake_run) as mock_subproc,
            patch("boto3.Session", return_value=mock_session),
            patch("src.common.catalog_dr.emit_metric") as mock_emit,
            patch("scripts.aws_profile.resolve_aws_profile", return_value=None),
        ):
            yield mock_subproc, mock_s3_client, mock_emit

    return _ctx()


def test_run_catalog_dump_happy_path(tmp_path):
    with _patch_run_catalog_dump(tmp_path, returncode=0, dump_size=128) as (mock_subproc, mock_s3, mock_emit):
        result = run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    assert result["ok"] is True
    assert result["bucket"] == "test-dr-bucket"
    assert "s3_key" in result
    assert result["pg_version"] == PINNED_PG_VERSION
    assert result["duckdb_version"] == PINNED_DUCKDB_VERSION
    assert result["dump_bytes"] > 0


def test_run_catalog_dump_s3_key_engine_tagged(tmp_path):
    with _patch_run_catalog_dump(tmp_path, returncode=0) as (_, _, _):
        result = run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    assert PINNED_PG_VERSION in result["s3_key"]
    assert PINNED_DUCKDB_VERSION in result["s3_key"]


def test_run_catalog_dump_pg_dump_flags(tmp_path):
    with _patch_run_catalog_dump(tmp_path, returncode=0) as (mock_subproc, _, _):
        run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    cmd = mock_subproc.call_args[0][0]
    assert "--format=custom" in cmd
    assert "--serializable-deferrable" in cmd
    assert "--file" in cmd


def test_run_catalog_dump_success_metric_emitted(tmp_path):
    with _patch_run_catalog_dump(tmp_path, returncode=0) as (_, _, mock_emit):
        run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    mock_emit.assert_called_once()
    args, kwargs = mock_emit.call_args
    assert args[0] == DR_METRIC_NAME
    assert args[1] == 1.0


def test_run_catalog_dump_s3_upload_called(tmp_path):
    with _patch_run_catalog_dump(tmp_path, returncode=0) as (_, mock_s3, _):
        run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    mock_s3.upload_file.assert_called_once()
    _, kwargs = mock_s3.upload_file.call_args
    assert kwargs["Bucket"] == "test-dr-bucket"
    assert "pg_version" in kwargs["ExtraArgs"]["Metadata"]
    assert "duckdb_version" in kwargs["ExtraArgs"]["Metadata"]


# ---------------------------------------------------------------------------
# run_catalog_dump: LOUD-FAIL on non-zero pg_dump exit (Decision 55)
# ---------------------------------------------------------------------------


def test_run_catalog_dump_raises_on_nonzero_exit(tmp_path):
    with _patch_run_catalog_dump(tmp_path, returncode=1) as (_, mock_s3, mock_emit):
        with pytest.raises(CatalogDrError, match="pg_dump exited 1"):
            run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)


def test_run_catalog_dump_no_success_metric_on_failure(tmp_path):
    """CRITICAL: success metric must NOT be emitted on a failed pg_dump (Decision 55)."""
    with _patch_run_catalog_dump(tmp_path, returncode=1) as (_, mock_s3, mock_emit):
        with pytest.raises(CatalogDrError):
            run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    mock_emit.assert_not_called()


def test_run_catalog_dump_no_s3_upload_on_failure(tmp_path):
    """S3 upload must NOT happen after a failed pg_dump."""
    with _patch_run_catalog_dump(tmp_path, returncode=1) as (_, mock_s3, _):
        with pytest.raises(CatalogDrError):
            run_catalog_dump(_SAMPLE_DSN, bucket="test-dr-bucket", _now=_NOW)
    mock_s3.upload_file.assert_not_called()


# ---------------------------------------------------------------------------
# T2.19: pg_restore helper (custom-format restore-drill)
# ---------------------------------------------------------------------------


def test_build_pg_restore_cmd_clean_default():
    import src.common.catalog_dr as cdr

    cmd = cdr.build_pg_restore_cmd("/tmp/c.dump", "postgresql://u:p@h/db?sslmode=require")
    assert cmd[0] == cdr.LAMBDA_PG_RESTORE_PATH
    assert "--clean" in cmd and "--if-exists" in cmd and "--no-owner" in cmd and "--exit-on-error" in cmd
    assert cmd[-1] == "/tmp/c.dump"
    assert "--dbname" in cmd


def test_build_pg_restore_cmd_no_clean():
    import src.common.catalog_dr as cdr

    cmd = cdr.build_pg_restore_cmd("/tmp/c.dump", "uri", clean=False)
    assert "--clean" not in cmd


def test_run_pg_restore_success():
    import src.common.catalog_dr as cdr

    calls = {}

    def _runner(cmd, **kw):
        calls["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    cdr.run_pg_restore("/tmp/c.dump", {"host": "h", "dbname": "db", "username": "u", "password": "p"}, runner=_runner)
    assert calls["cmd"][0] == cdr.LAMBDA_PG_RESTORE_PATH


def test_run_pg_restore_nonzero_loud_fails():
    import src.common.catalog_dr as cdr

    def _runner(cmd, **kw):
        return type("R", (), {"returncode": 1, "stderr": "restore blew up"})()

    with pytest.raises(cdr.CatalogDrError, match="pg_restore exited 1"):
        cdr.run_pg_restore("/tmp/c.dump", {"host": "h", "dbname": "db", "username": "u", "password": "p"}, runner=_runner)


# ---------------------------------------------------------------------------
# restore-rehearsal additions: opt-in --no-acl and --schema, clean passthrough, returncode, encoded DSN
# ---------------------------------------------------------------------------

_DSN = {"host": "h", "dbname": "db", "username": "u", "password": "p"}


def test_build_pg_restore_cmd_default_argv_unchanged():
    import src.common.catalog_dr as cdr

    cmd = cdr.build_pg_restore_cmd("/tmp/c.dump", "uri")
    assert cmd == [
        cdr.LAMBDA_PG_RESTORE_PATH,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--exit-on-error",
        "--dbname",
        "uri",
        "/tmp/c.dump",
    ]


def test_build_pg_restore_cmd_no_acl_flag():
    import src.common.catalog_dr as cdr

    assert "--no-acl" in cdr.build_pg_restore_cmd("/tmp/c.dump", "uri", no_acl=True)
    assert "--no-acl" not in cdr.build_pg_restore_cmd("/tmp/c.dump", "uri")


def test_build_pg_restore_cmd_restore_schema_flag():
    import src.common.catalog_dr as cdr

    cmd = cdr.build_pg_restore_cmd("/tmp/c.dump", "uri", schema="ducklake_ops")
    assert "--schema=ducklake_ops" in cmd
    assert cmd[-1] == "/tmp/c.dump"
    assert not any(c.startswith("--schema") for c in cdr.build_pg_restore_cmd("/tmp/c.dump", "uri"))


def test_run_pg_restore_passes_restore_clean_no_acl_schema():
    import src.common.catalog_dr as cdr

    seen = {}

    def _runner(cmd, **kw):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    cdr.run_pg_restore("/tmp/c.dump", _DSN, runner=_runner, clean=False, no_acl=True, schema="ducklake_ops")
    assert "--clean" not in seen["cmd"] and "--no-acl" in seen["cmd"] and "--schema=ducklake_ops" in seen["cmd"]


def test_restore_clean_error_carries_returncode():
    import src.common.catalog_dr as cdr

    def _runner(cmd, **kw):
        return type("R", (), {"returncode": 3, "stderr": "x"})()

    with pytest.raises(cdr.CatalogDrError) as info:
        cdr.run_pg_restore("/tmp/c.dump", _DSN, runner=_runner)
    assert info.value.returncode == 3
    assert cdr.CatalogDrError("plain").returncode is None


def test_restore_clean_dsn_uri_percent_encodes_reserved_characters():
    import urllib.parse

    import src.common.catalog_dr as cdr

    uri = cdr.dsn_uri({**_DSN, "username": "u@x", "password": "p/w:@#?"})
    parsed = urllib.parse.urlsplit(uri)
    assert parsed.hostname == "h"
    assert urllib.parse.unquote(parsed.password) == "p/w:@#?"
    assert urllib.parse.unquote(parsed.username) == "u@x"
