"""restore_rehearsal_actions (rec-5023, Decision 251): refusals, field allow-list, value-free errors. All seams mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.common import catalog_dr
from src.lambdas.ducklake_maintenance import restore_rehearsal_actions as rra

pytestmark = pytest.mark.unit

RUN = "20261109t193000_a1b2c3"
FN = "agent-platform-maint-restore-rehearsal-ephemeral"
TARGET_URI = "postgresql://restore_rehearsal_x:p%2Fw%40@ep-fake.example.invalid/restore_rehearsal_x?sslmode=require"  # pragma: allowlist secret -- fake fixture value
SCRATCH = f"s3://agent-platform-data-lake/ducklake-neon-smoke/restore-rehearsal/{RUN}/"
FAKE_HOST = "ep-secret-host.example.invalid"
FAKE_PW = "hunter2-fake"  # pragma: allowlist secret -- fake fixture value


def _env(monkeypatch, *, fn=FN, token="tok", target=TARGET_URI):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", fn)
    if token is None:
        monkeypatch.delenv("DUCKLAKE_RESTORE_REHEARSAL", raising=False)
    else:
        monkeypatch.setenv("DUCKLAKE_RESTORE_REHEARSAL", token)
    if target is None:
        monkeypatch.delenv("DUCKLAKE_RESTORE_TARGET_DSN", raising=False)
    else:
        monkeypatch.setenv("DUCKLAKE_RESTORE_TARGET_DSN", target)


def _ev(step, **extra):
    return {"action": "restore_rehearsal", "step": step, "confirm": "tok", "run_id": RUN, **extra}


class _Cur:
    def __init__(self, results, rowcount=1):
        self.results, self.executed, self.rowcount = list(results), [], rowcount
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        self._last = self.results.pop(0) if self.results else [(0,)]

    def fetchone(self):
        return self._last[0]

    def fetchall(self):
        return self._last


class _Conn:
    def __init__(self, cur):
        self.cur, self.closed = cur, False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def _no_connection():
    return patch.object(rra, "_connect", side_effect=AssertionError("connected"))


# ---------------------------------------------------------------- refusals (before any connection)


def test_refuses_production_function_name(monkeypatch):
    _env(monkeypatch, fn="agent-platform-ducklake-maintenance")
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("restore"), None)


@pytest.mark.parametrize("token", [None, ""])
def test_refuses_missing_token(monkeypatch, token):
    _env(monkeypatch, token=token)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("restore"), None)


def test_refuses_wrong_token(monkeypatch):
    _env(monkeypatch)
    ev = _ev("restore")
    ev["confirm"] = "nope"
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(ev, None)


@pytest.mark.parametrize("run_id", [None, "Bad-Id", "", "a" * 65])
def test_refuses_bad_run_id(monkeypatch, run_id):
    _env(monkeypatch)
    ev = _ev("restore")
    ev["run_id"] = run_id
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(ev, None)


def test_refuses_unknown_step(monkeypatch):
    _env(monkeypatch)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("catalog_reinit"), None)


def test_prod_checksum_refuses_when_target_dsn_set(monkeypatch):
    _env(monkeypatch)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("prod_checksum", snapshot_id=1, tables=[]), None)


@pytest.mark.parametrize("step", [s for s in rra.STEPS if s != "prod_checksum"])
def test_other_steps_refuse_when_target_dsn_absent(monkeypatch, step):
    _env(monkeypatch, target=None)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev(step), None)


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://neondb_owner:p@h/restore_rehearsal_x",
        "postgresql://restore_rehearsal_x:p@h/ducklake",
    ],
)
def test_non_rehearsal_target_refused(monkeypatch, uri):
    _env(monkeypatch, target=uri)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("verify", snapshot_id=1, data_path=SCRATCH), None)


@pytest.mark.parametrize("key", ["data_path", "scratch_prefix"])
@pytest.mark.parametrize(
    "value", ["s3://agent-platform-data-lake/ducklake/", rra.rt.SMOKE_DATA_PATH, 5, f"{SCRATCH}../../ducklake/"]
)
def test_path_outside_scratch_refused(monkeypatch, key, value):
    _env(monkeypatch)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("rewrite", **{key: value}), None)


def test_rewrite_target_must_be_inside_scratch(monkeypatch):
    _env(monkeypatch)
    with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
        rra.action_restore_rehearsal(_ev("rewrite"), None)


def test_restore_refuses_non_s3_dump_url(monkeypatch):
    _env(monkeypatch)
    for url in ("http://x.s3.amazonaws.com/d", "https://evil.example.com/d", "https://bucket.amazonaws.com/d"):
        with _no_connection(), pytest.raises(rra.RestoreRehearsalRefused):
            rra.action_restore_rehearsal(_ev("restore", dump_url=url), None)


# ---------------------------------------------------------------- pure helpers


def test_target_dsn_uri_parses_with_percent_decoding():
    dsn = rra.parse_target_dsn(TARGET_URI)
    assert dsn == {
        "host": "ep-fake.example.invalid",
        "dbname": "restore_rehearsal_x",
        "username": "restore_rehearsal_x",
        "password": "p/w@",  # pragma: allowlist secret -- fake fixture value
        "sslmode": "require",
    }
    assert rra.parse_target_dsn("postgresql://u:p@h/d")["sslmode"] == "require"
    assert rra.parse_target_dsn("postgresql:///d") == {
        "host": "",
        "dbname": "d",
        "username": "",
        "password": "",
        "sslmode": "require",
    }


def test_encoded_password_round_trips_into_pg_restore_argv():
    dsn = rra.parse_target_dsn(TARGET_URI)
    seen = {}

    def _runner(cmd, **kw):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    catalog_dr.run_pg_restore("/tmp/x.dump", dsn, runner=_runner, clean=False)
    assert "p%2Fw%40" in " ".join(seen["cmd"])


def test_rewrite_sql_names_only_the_global_data_path_row_in_the_named_schema():
    sql, params = rra.rewrite_data_path_sql("ducklake_ops", SCRATCH)
    assert sql == "UPDATE \"ducklake_ops\".ducklake_metadata SET value = %s WHERE key = 'data_path' AND scope IS NULL"
    assert params == (SCRATCH,)


def test_manifest_includes_both_rehearsal_modules():
    manifest = yaml.safe_load(Path("src/lambdas/ducklake_maintenance/manifest.yaml").read_text())
    base = "src/lambdas/ducklake_maintenance/"
    assert base + "restore_rehearsal_actions.py" in manifest["includes"]
    assert base + "restore_rehearsal_file_catalog.py" in manifest["includes"]


def test_scratch_prefix():
    assert rra.scratch_prefix(RUN) == SCRATCH


@pytest.mark.parametrize(
    "exc,expected",
    [
        (type("E", (Exception,), {"pgcode": "42P01"})(), "42P01"),
        (catalog_dr.CatalogDrError("x", returncode=2), 2),
        (OSError(28, "x"), 28),
        (ValueError("x"), None),
    ],
)
def test_code_is_value_free(exc, expected):
    assert rra._code(exc) == expected


def test_connect_uses_conninfo_and_autocommit():
    fake = MagicMock()
    with patch.dict("sys.modules", {"psycopg2": MagicMock(connect=MagicMock(return_value=fake))}) as _:
        conn = rra._connect({"host": "h", "dbname": "d", "username": "u", "password": "p"})
    assert conn is fake and fake.autocommit is True


def test_scalar():
    cur = _Cur([[(7,)]])
    assert rra._scalar(cur, "SELECT 1") == 7


# ---------------------------------------------------------------- restore


def test_restore_ops_schema_creates_schema_then_restores_ops_only():
    cur = _Cur([])
    conn = _Conn(cur)
    dsn = rra.parse_target_dsn(TARGET_URI)
    with (
        patch.object(rra, "_connect", return_value=conn),
        patch.object(rra.catalog_dr, "run_pg_restore") as restore,
    ):
        rra.restore_ops_schema("/tmp/d.dump", dsn, pg_restore_path="/usr/bin/pg_restore")
    assert cur.executed[0][0] == "CREATE SCHEMA ducklake_ops" and conn.closed
    restore.assert_called_once_with(
        "/tmp/d.dump", dsn, pg_restore_path="/usr/bin/pg_restore", clean=False, no_acl=True, schema="ducklake_ops"
    )


def test_download_streams_s3_url_to_file(tmp_path):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.side_effect = [b"abc", b""]
    with patch.object(rra.urllib.request, "urlopen", return_value=resp):
        rra._download("https://bkt.s3.eu-west-2.amazonaws.com/k?X=1", str(tmp_path / "d"))
    assert (tmp_path / "d").read_bytes() == b"abc"


def _measure_cursor():
    return _Cur(
        [
            [(42,)],  # snapshot
            [(0,)],
            [(0,)],
            [(0,)],
            [(0,)],  # four path tables
            [(0,)],  # foreign relations
            [(0,)],  # foreign schema owner
            [("some_role",), ("PUBLIC",), ("some_role",)],  # acl entries
        ]
    )


def test_measure_counts_over_ducklake_ops_namespace_only():
    cur = _measure_cursor()
    with patch.object(rra, "_connect", return_value=_Conn(cur)):
        out = rra._measure({})
    assert out == {
        "snapshot_id": 42,
        "absolute_paths": 0,
        "foreign_owned": 0,
        "foreign_acl": 3,
        "foreign_acl_grantees": ["PUBLIC", "some_role"],
    }
    sqls = " ".join(s for s, _ in cur.executed)
    assert (
        "ducklake_schema" in sqls
        and "ducklake_table" in sqls
        and "ducklake_data_file" in sqls
        and "ducklake_delete_file" in sqls
    )
    assert all(p in (("ducklake_ops",), ("ducklake_ops", "ducklake_ops"), ()) for _, p in cur.executed)


def test_restore_step_success_deletes_local_dump_and_returns_counts(monkeypatch):
    _env(monkeypatch)
    seen = {}

    def _download(url, dest):
        Path(dest).write_bytes(b"x")
        seen["dest"] = dest

    with (
        patch.object(rra, "_download", side_effect=_download),
        patch.object(rra, "restore_ops_schema") as restore,
        patch.object(rra, "_measure", return_value={"snapshot_id": 5}),
    ):
        out = rra.action_restore_rehearsal(_ev("restore", dump_url="https://b.s3.amazonaws.com/k"), None)
    restore.assert_called_once()
    assert out["ok"] is True and out["snapshot_id"] == 5 and "restore_ms" in out
    assert not Path(seen["dest"]).exists()
    assert "dump_url" not in json.dumps(out)


def test_restore_step_error_returns_class_and_code_only(monkeypatch):
    _env(monkeypatch)
    err = catalog_dr.CatalogDrError(f"pg_restore exited 1: could not connect {FAKE_HOST} {FAKE_PW}", returncode=1)
    with (
        patch.object(rra, "_download", side_effect=lambda u, d: Path(d).write_bytes(b"x")),
        patch.object(rra, "restore_ops_schema", side_effect=err),
    ):
        out = rra.action_restore_rehearsal(_ev("restore", dump_url="https://b.s3.amazonaws.com/k"), None)
    assert out == {"ok": False, "step": "restore", "error_class": "CatalogDrError", "code": 1}
    assert FAKE_HOST not in json.dumps(out) and FAKE_PW not in json.dumps(out)


def test_restore_step_download_failure_has_no_local_dump_to_remove(monkeypatch):
    _env(monkeypatch)
    with patch.object(rra, "_download", side_effect=OSError(5, f"{FAKE_HOST}")):
        out = rra.action_restore_rehearsal(_ev("restore", dump_url="https://b.s3.amazonaws.com/k"), None)
    assert out["ok"] is False and out["code"] == 5


# ---------------------------------------------------------------- rewrite


def test_rewrite_executes_only_the_rewrite_sql(monkeypatch):
    _env(monkeypatch)
    cur = _Cur([[(0,)], [(0,)], [(0,)], [(0,)], []], rowcount=1)
    with patch.object(rra, "_connect", return_value=_Conn(cur)):
        out = rra.action_restore_rehearsal(_ev("rewrite", data_path=SCRATCH + "ducklake/"), None)
    assert out["ok"] and out["rewritten"] is True
    updates = [(s, p) for s, p in cur.executed if s.startswith("UPDATE")]
    assert updates == [(rra.rewrite_data_path_sql("ducklake_ops", SCRATCH + "ducklake/")[0], (SCRATCH + "ducklake/",))]


def test_rewrite_with_absolute_paths_runs_no_update(monkeypatch):
    _env(monkeypatch)
    cur = _Cur([[(3,)], [(0,)], [(0,)], [(0,)]])
    with patch.object(rra, "_connect", return_value=_Conn(cur)):
        out = rra.action_restore_rehearsal(_ev("rewrite", data_path=SCRATCH + "ducklake/"), None)
    assert out["rewritten"] is False
    assert not any(s.startswith("UPDATE") for s, _ in cur.executed)


# ---------------------------------------------------------------- verify / prod_checksum


class _Con:
    def __init__(self, files=None, rows=None, fail_tables=()):
        self.files, self.rows, self.fail_tables, self.closed, self.sql = files or {}, rows or {}, set(fail_tables), False, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._table = params[0] if params else None
        if "ducklake_list_files" in sql and self._table in self.fail_tables:
            raise RuntimeError(FAKE_HOST)
        if "sum(hash(t))" in sql:
            name = sql.split('"."')[-1].split('"')[0]
            if name in self.fail_tables:
                raise RuntimeError(FAKE_HOST)
            self._res = [self.rows.get(name, (1, "11"))]
        else:
            self._res = self.files.get(self._table, [])
        return self

    def fetchone(self):
        return self._res[0]

    def fetchall(self):
        return self._res

    def close(self):
        self.closed = True


_BASE = "ducklake-neon-smoke/restore-rehearsal/" + RUN + "/ducklake/"
_URI = "s3://agent-platform-data-lake/" + _BASE


def test_verify_reports_missing_file_and_false_read_without_raising(monkeypatch):
    _env(monkeypatch)
    con = _Con(
        files={
            "a": [(_URI + "a/1.parquet", None)],
            "b": [(_URI + "b/gone.parquet", _URI + "b/del.parquet")],
        },
        fail_tables={"c"},
    )
    keys = {_BASE + "a/1.parquet", _BASE + "b/del.parquet"}
    with (
        patch.object(rra, "_catalog_tables", return_value=[("main", "a"), ("main", "b"), ("main", "c")]),
        patch.object(rra.rt, "open_connection", return_value=con) as opener,
        patch.object(rra, "_scratch_keys", return_value=keys),
    ):
        out = rra.action_restore_rehearsal(_ev("verify", data_path=SCRATCH + "ducklake/", snapshot_id=9), None)
    assert out["attach_ok"] is True and out["missing_files"] == 1
    assert [r["read_ok"] for r in out["tables"]] == [True, True, False]
    assert out["tables"][2]["count"] is None
    assert opener.call_args.kwargs["read_only"] is True and "OVERRIDE" not in json.dumps(opener.call_args.kwargs)
    assert con.closed and FAKE_HOST not in json.dumps(out)


def test_verify_failed_attach_is_recorded_not_raised(monkeypatch):
    _env(monkeypatch)
    with patch.object(rra.rt, "open_connection", side_effect=RuntimeError(f"{FAKE_HOST} {FAKE_PW}")):
        out = rra.action_restore_rehearsal(_ev("verify", data_path=SCRATCH + "ducklake/", snapshot_id=9), None)
    assert out == {"ok": True, "step": "verify", "attach_ok": False, "missing_files": None, "tables": []}


def test_prod_checksum_attaches_read_only_at_production_path(monkeypatch):
    _env(monkeypatch, target=None)
    con = _Con(rows={"a": (3, "99")})
    with (
        patch.object(rra.rt, "fetch_dsn", return_value={"host": "h"}),
        patch.object(rra.rt, "open_connection", return_value=con) as opener,
    ):
        out = rra.action_restore_rehearsal(_ev("prod_checksum", snapshot_id=9, tables=[["main", "a"]]), None)
    kw = opener.call_args.kwargs
    assert kw["read_only"] is True and kw["data_path"] == "s3://agent-platform-data-lake/ducklake/"
    assert kw["meta_schema"] == "ducklake_ops"
    assert out["tables"] == [{"schema": "main", "table": "a", "read_ok": True, "count": 3, "checksum": "99"}]
    assert con.closed
    assert all("AT (VERSION => 9)" in s for s in con.sql)


def test_catalog_tables_lists_live_tables():
    cur = _Cur([[("main", "a"), ("ops", "b")]])
    conn = _Conn(cur)
    with patch.object(rra, "_connect", return_value=conn):
        assert rra._catalog_tables({}) == [("main", "a"), ("ops", "b")]
    assert conn.closed and "end_snapshot IS NULL" in cur.executed[0][0]


def test_scratch_keys_lists_under_data_path():
    pages = [{"Contents": [{"Key": "k1"}]}, {}]
    paginator = MagicMock(paginate=MagicMock(return_value=pages))
    client = MagicMock(get_paginator=MagicMock(return_value=paginator))
    with patch.dict("sys.modules", {"boto3": MagicMock(client=MagicMock(return_value=client))}):
        assert rra._scratch_keys(SCRATCH + "ducklake/") == {"k1"}
    assert paginator.paginate.call_args.kwargs["Prefix"] == _BASE


# ---------------------------------------------------------------- duckdb_copy / write_probe / teardown


def test_duckdb_copy_delegates_with_the_event_tables(monkeypatch):
    _env(monkeypatch)
    with patch.object(
        rra, "run_duckdb_copy", return_value={"copy_ms": 1, "catalog_rows": 2, "file_bytes": 3, "tables": []}
    ) as run:
        out = rra.action_restore_rehearsal(
            _ev("duckdb_copy", data_path=SCRATCH + "ducklake/", snapshot_id=4, tables=[["main", "a"]]), None
        )
    assert out["ok"] and out["catalog_rows"] == 2
    assert run.call_args.kwargs["tables"] == [("main", "a")] and run.call_args.kwargs["snapshot_id"] == 4


class _ProbeCon:
    def __init__(self, path):
        self.path, self.sql, self.closed = path, [], False

    def execute(self, sql):
        self.sql.append(sql)
        return self

    def fetchone(self):
        return (self.path,)

    def close(self):
        self.closed = True


def _probe(monkeypatch, path, keycount):
    _env(monkeypatch)
    con = _ProbeCon(path)
    client = MagicMock()
    client.list_objects_v2.return_value = {"KeyCount": keycount}
    cur = _Cur([[("1.0",)]])
    with (
        patch.object(rra.rt, "open_connection", return_value=con),
        patch.object(rra, "_connect", return_value=_Conn(cur)),
        patch.dict("sys.modules", {"boto3": MagicMock(client=MagicMock(return_value=client))}),
    ):
        out = rra.action_restore_rehearsal(_ev("write_probe", data_path=SCRATCH + "ducklake/"), None)
    return out, con


def test_write_probe_lands_in_scratch_only(monkeypatch):
    out, con = _probe(monkeypatch, SCRATCH + "ducklake/rehearsal_probe/t/f.parquet", 1)
    assert out["write_probe_in_scratch"] is True and out["write_probe_key"] == "rehearsal_probe/t/f.parquet"
    assert out["catalog_format_version"] == "1.0" and con.closed
    assert any("range(100)" in s for s in con.sql)


def test_write_probe_not_in_scratch_when_file_elsewhere(monkeypatch):
    out, _ = _probe(monkeypatch, "s3://agent-platform-data-lake/ducklake/x/f.parquet", 1)
    assert out["write_probe_in_scratch"] is False and out["write_probe_key"] is None


def test_write_probe_false_when_object_missing(monkeypatch):
    out, _ = _probe(monkeypatch, SCRATCH + "ducklake/rehearsal_probe/t/f.parquet", 0)
    assert out["write_probe_in_scratch"] is False


def test_teardown_scratch_deletes_only_under_the_scratch_prefix(monkeypatch):
    _env(monkeypatch)
    pages = [{"Contents": [{"Key": "a"}, {"Key": "b"}]}, {}]
    paginator = MagicMock(paginate=MagicMock(return_value=pages))
    client = MagicMock(get_paginator=MagicMock(return_value=paginator))
    with patch.dict("sys.modules", {"boto3": MagicMock(client=MagicMock(return_value=client))}):
        out = rra.action_restore_rehearsal(_ev("teardown_scratch"), None)
    assert out == {"ok": True, "step": "teardown_scratch", "deleted_objects": 2}
    assert paginator.paginate.call_args.kwargs["Prefix"] == f"ducklake-neon-smoke/restore-rehearsal/{RUN}/"
    client.delete_objects.assert_called_once()


def test_response_fields_match_no_forbidden_pattern(monkeypatch):
    _env(monkeypatch)
    cur = _measure_cursor()
    with (
        patch.object(rra, "_download"),
        patch.object(rra, "restore_ops_schema"),
        patch.object(rra, "_connect", return_value=_Conn(cur)),
    ):
        out = rra.action_restore_rehearsal(_ev("restore", dump_url="https://b.s3.amazonaws.com/k?X-Amz-Signature=s"), None)
    text = json.dumps(out)
    for needle in ("amazonaws", "X-Amz", "postgresql://", "agent-platform-data-lake", "s3://"):
        assert needle not in text
