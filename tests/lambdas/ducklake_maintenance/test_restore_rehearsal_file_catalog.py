"""restore_rehearsal_file_catalog (rec-5023): the DuckDB-file copy stays in /tmp, is deleted, reports mismatches, never raises."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.lambdas.ducklake_maintenance import restore_rehearsal_file_catalog as fc

pytestmark = pytest.mark.unit

DSN = {"host": "h", "dbname": "d", "username": "u", "password": "p"}
DATA = "s3://b/ducklake-neon-smoke/restore-rehearsal/r1/ducklake/"


class _Con:
    """Mock DuckDB connection: pg catalog tables, a dst table set, and per-alias checksum rows."""

    def __init__(self, tmp_holder, *, digests=None, fail_copy=False, median=5):
        self.holder, self.digests, self.fail_copy, self.median = tmp_holder, digests or {}, fail_copy, median
        self.sql, self.closed, self._res = [], False, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if self.fail_copy and sql.startswith("ATTACH 'postgres:"):
            raise RuntimeError("host=secret")
        if sql.startswith("ATTACH 'ducklake:") and " AS init " in sql:
            path = sql.split("'")[1].removeprefix("ducklake:")
            self.holder["path"] = path
            with open(path, "wb") as fh:
                fh.write(b"x" * 10)
        if "information_schema.tables" in sql:
            self._res = [("ducklake_snapshot",), ("ducklake_table",), ("not_in_dst",)]
        elif "duckdb_tables()" in sql:
            self._res = [("ducklake_snapshot",), ("ducklake_table",)]
        elif sql.startswith("SELECT count(*) FROM dst"):
            self._res = [(4,)]
        elif "quantile_disc" in sql:
            self._res = [(self.median,)]
        elif "sum(hash(t))" in sql:
            alias = sql.split(" FROM ")[1].split(".")[0].strip('"')
            version = int(sql.rsplit("=> ", 1)[1].rstrip(")"))
            key = (alias, sql.split('"."')[-1].split('"')[0], version)
            value = self.digests.get(key, (2, "77"))
            if isinstance(value, Exception):
                raise value
            self._res = [value]
        else:
            self._res = []
        return self

    def fetchone(self):
        return self._res[0]

    def fetchall(self):
        return self._res

    def close(self):
        self.closed = True


def _run(con, tables=(("main", "a"),)):
    with patch.object(fc.rt, "open_connection", return_value=con) as opener:
        out = fc.run_duckdb_copy(DSN, meta_schema="ducklake_ops", data_path=DATA, tables=list(tables), snapshot_id=9)
    return out, opener


def test_checksum_sql_is_one_expression_with_alias_before_at():
    sql = fc.checksum_sql("src", "main", "t", 9)
    assert sql == 'SELECT count(*), sum(hash(t))::VARCHAR FROM "src"."main"."t" t AT (VERSION => 9)'


def test_quote_ident_escapes_quotes():
    assert fc.quote_ident('a"b') == '"a""b"'


def test_copy_matches_and_deletes_the_tmp_file():
    holder: dict = {}
    out, opener = _run(_Con(holder))
    assert out["tables"] == [{"table": "main.a", "match_restored": True, "match_median": True}]
    assert out["catalog_rows"] == 8 and out["file_bytes"] == 10 and out["copy_ms"] >= 0
    assert holder["path"].startswith(("/tmp", os.path.realpath("/tmp"))) or "rr-file-catalog-" in holder["path"]
    assert not os.path.exists(holder["path"])
    assert opener.call_args.kwargs["read_only"] is True


def test_copy_only_copies_tables_present_in_the_file_catalog():
    holder: dict = {}
    con = _Con(holder)
    _run(con)
    inserts = [s for s in con.sql if s.startswith("INSERT INTO dst")]
    assert len(inserts) == 2 and not any("not_in_dst" in s for s in inserts)
    assert "DETACH dst" in con.sql and con.closed


def test_mismatch_at_either_snapshot_is_false_not_raised():
    holder: dict = {}
    digests = {("cp", "a", 9): (2, "other"), ("cp", "b", 5): (9, "z")}
    out, _ = _run(_Con(holder, digests=digests), tables=(("main", "a"), ("main", "b")))
    assert out["tables"] == [
        {"table": "main.a", "match_restored": False, "match_median": True},
        {"table": "main.b", "match_restored": True, "match_median": False},
    ]


def test_read_error_is_a_false_match():
    holder: dict = {}
    out, _ = _run(_Con(holder, digests={("src", "a", 5): RuntimeError("boom")}))
    assert out["tables"][0] == {"table": "main.a", "match_restored": True, "match_median": False}


def test_init_or_copy_error_returns_all_false_within_the_contract_fields():
    holder: dict = {}
    con = _Con(holder, fail_copy=True)
    out, _ = _run(con, tables=(("main", "a"), ("main", "b")))
    assert set(out) == {"copy_ms", "catalog_rows", "file_bytes", "tables"}
    assert out["catalog_rows"] == 0 and out["file_bytes"] == 0
    assert all(not r["match_restored"] and not r["match_median"] for r in out["tables"])
    assert "secret" not in str(out) and con.closed
    assert not os.path.exists(holder["path"])


def test_open_failure_is_also_all_false():
    with patch.object(fc.rt, "open_connection", side_effect=RuntimeError("host=secret")):
        out = fc.run_duckdb_copy(DSN, meta_schema="ducklake_ops", data_path=DATA, tables=[("main", "a")], snapshot_id=1)
    assert out["tables"] == [{"table": "main.a", "match_restored": False, "match_median": False}]
