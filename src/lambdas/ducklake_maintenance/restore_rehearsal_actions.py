"""restore_rehearsal verb (rec-5023, Decision 251): runs only on ephemeral -restore-rehearsal-ephemeral functions.

Contract: docs/contracts/restore-rehearsal.yaml. The verb refuses, before any connection, anywhere else. Responses carry
counts, checksums, booleans, durations and names only; every error beneath a step is returned as step, error class and
a value-free code, never its message (a driver message can name a host).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any

from src.common import catalog_dr
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance._shared import EXTENSION_DIRECTORY
from src.lambdas.ducklake_maintenance.restore_rehearsal_file_catalog import checksum_sql, quote_ident, run_duckdb_copy

EPHEMERAL_SUFFIX = "-restore-rehearsal-ephemeral"
META_SCHEMA = "ducklake_ops"
STEPS = ("restore", "rewrite", "verify", "prod_checksum", "duckdb_copy", "write_probe", "teardown_scratch")
_RUN_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_BUCKET = rt.SMOKE_DATA_PATH.split("/")[2]
_PROD_OPS_DATA_PATH = f"s3://{_BUCKET}/ducklake/"
_PATH_TABLES = ("ducklake_schema", "ducklake_table", "ducklake_data_file", "ducklake_delete_file")


class RestoreRehearsalRefused(RuntimeError):
    """The verb's own refusal (a fixed reason, never a value)."""


def scratch_prefix(run_id: str) -> str:
    return f"{rt.SMOKE_DATA_PATH}restore-rehearsal/{run_id}/"


def parse_target_dsn(uri: str) -> dict[str, str]:
    """Parse the target DSN URI into the dict form run_pg_restore and open_connection take (percent-decoded)."""
    parts = urllib.parse.urlsplit(uri)
    query = urllib.parse.parse_qs(parts.query)
    return {
        "host": parts.hostname or "",
        "dbname": parts.path.lstrip("/"),
        "username": urllib.parse.unquote(parts.username or ""),
        "password": urllib.parse.unquote(parts.password or ""),
        "sslmode": (query.get("sslmode") or ["require"])[0],
    }


def rewrite_data_path_sql(meta_schema: str, new_path: str) -> tuple[str, tuple[str, ...]]:
    """The one rewrite statement: only the global data_path row of the named meta-schema."""
    return (
        f"UPDATE {quote_ident(meta_schema)}.ducklake_metadata SET value = %s WHERE key = 'data_path' AND scope IS NULL",
        (new_path,),
    )


def _guard(event: dict[str, Any]) -> tuple[str, str, dict[str, str] | None]:
    step = event.get("step")
    if step not in STEPS:
        raise RestoreRehearsalRefused("unknown step")
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "").endswith(EPHEMERAL_SUFFIX):
        raise RestoreRehearsalRefused("not an ephemeral rehearsal function")
    token = os.environ.get("DUCKLAKE_RESTORE_REHEARSAL")
    if not token or event.get("confirm") != token:
        raise RestoreRehearsalRefused("confirm token missing or wrong")
    run_id = event.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise RestoreRehearsalRefused("invalid run id")
    target = os.environ.get("DUCKLAKE_RESTORE_TARGET_DSN")
    dsn: dict[str, str] | None = None
    if step == "prod_checksum":
        if target:
            raise RestoreRehearsalRefused("prod_checksum refuses a function holding a target")
    else:
        if not target:
            raise RestoreRehearsalRefused("no rehearsal target")
        dsn = parse_target_dsn(target)
        if not dsn["username"].startswith("restore_rehearsal_") or not dsn["dbname"].startswith("restore_rehearsal_"):
            raise RestoreRehearsalRefused("target is not a rehearsal login")
    prefix = scratch_prefix(run_id)
    for key in ("data_path", "scratch_prefix"):
        value = event.get(key)
        if value is not None and (not isinstance(value, str) or not value.startswith(prefix) or ".." in value):
            raise RestoreRehearsalRefused("path outside the rehearsal scratch prefix")
    return step, prefix, dsn


def _code(exc: BaseException) -> int | str | None:
    for attr in ("pgcode", "returncode", "errno"):
        value = getattr(exc, attr, None)
        if value is not None:
            return value
    return None


def _connect(dsn: dict[str, str]) -> Any:
    import psycopg2  # noqa: PLC0415

    conn = psycopg2.connect(rt.libpq_conninfo(dsn))
    conn.autocommit = True
    return conn


def _scalar(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cur.execute(sql, params)
    return cur.fetchone()[0]


def restore_ops_schema(
    dump_path: str, target_dsn: dict[str, str], *, pg_restore_path: str = catalog_dr.LAMBDA_PG_RESTORE_PATH
) -> None:
    """Create ducklake_ops as the target role (a schema-filtered pg_restore never creates it), then restore into it."""
    conn = _connect(target_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {META_SCHEMA}")
    finally:
        conn.close()
    catalog_dr.run_pg_restore(
        dump_path, target_dsn, pg_restore_path=pg_restore_path, clean=False, no_acl=True, schema=META_SCHEMA
    )


def _download(url: str, dest: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not (host.endswith(".amazonaws.com") and ".s3" in host):
        raise RestoreRehearsalRefused("dump url is not an https S3 url")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:  # noqa: S310 -- scheme checked above
        shutil.copyfileobj(resp, out)


def _measure(dsn: dict[str, str]) -> dict[str, Any]:
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            snapshot_id = _scalar(cur, f"SELECT max(snapshot_id) FROM {META_SCHEMA}.ducklake_snapshot")
            absolute = sum(
                _scalar(cur, f"SELECT count(*) FROM {META_SCHEMA}.{t} WHERE NOT path_is_relative") for t in _PATH_TABLES
            )
            rel = "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s"
            foreign = _scalar(
                cur,
                f"SELECT count(*) {rel} AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)",
                (META_SCHEMA,),
            ) + _scalar(
                cur,
                "SELECT count(*) FROM pg_namespace WHERE nspname = %s "
                "AND nspowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)",
                (META_SCHEMA,),
            )
            cur.execute(
                "SELECT CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE (SELECT rolname FROM pg_roles WHERE oid = x.grantee) END "
                f"FROM (SELECT c.relacl AS acl {rel} UNION ALL SELECT nspacl FROM pg_namespace WHERE nspname = %s) a, "
                "LATERAL aclexplode(a.acl) x WHERE x.grantee <> (SELECT oid FROM pg_roles WHERE rolname = current_user)",
                (META_SCHEMA, META_SCHEMA),
            )
            entries = [str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()
    return {
        "snapshot_id": snapshot_id,
        "absolute_paths": absolute,
        "foreign_owned": foreign,
        "foreign_acl": len(entries),
        "foreign_acl_grantees": sorted(set(entries)),
    }


def _step_restore(event: dict[str, Any], dsn: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rr-dump-") as tmp:
        dump_path = os.path.join(tmp, "catalog.dump")
        try:
            _download(str(event.get("dump_url")), dump_path)
            restore_ops_schema(dump_path, dsn)
        finally:
            if os.path.exists(dump_path):
                os.remove(dump_path)
    restore_ms = int((time.monotonic() - started) * 1000)
    return {"restore_ms": restore_ms, **_measure(dsn)}


def _step_rewrite(event: dict[str, Any], dsn: dict[str, str], prefix: str) -> dict[str, Any]:
    started = time.monotonic()
    new_path = str(event.get("data_path"))
    if not new_path.startswith(prefix):
        raise RestoreRehearsalRefused("rewrite target outside the rehearsal scratch prefix")
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            absolute = sum(
                _scalar(cur, f"SELECT count(*) FROM {META_SCHEMA}.{t} WHERE NOT path_is_relative") for t in _PATH_TABLES
            )
            rewritten = False
            if absolute == 0:
                sql, params = rewrite_data_path_sql(META_SCHEMA, new_path)
                cur.execute(sql, params)
                rewritten = cur.rowcount == 1
    finally:
        conn.close()
    return {"rewritten": rewritten, "rewrite_ms": int((time.monotonic() - started) * 1000)}


def _catalog_tables(dsn: dict[str, str]) -> list[tuple[str, str]]:
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT s.schema_name, t.table_name FROM {META_SCHEMA}.ducklake_table t "
                f"JOIN {META_SCHEMA}.ducklake_schema s USING (schema_id) "
                "WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL ORDER BY 1, 2"
            )
            return [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        conn.close()


def _scratch_keys(prefix_path: str) -> set[str]:
    import boto3  # noqa: PLC0415

    key_prefix = prefix_path.split("/", 3)[3]
    keys: set[str] = set()
    for page in boto3.client("s3").get_paginator("list_objects_v2").paginate(Bucket=_BUCKET, Prefix=key_prefix):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def _table_checks(
    con: Any, tables: list[tuple[str, str]], snapshot_id: int, present: set[str] | None
) -> tuple[list[dict], int]:
    rows: list[dict[str, Any]] = []
    missing = 0
    for schema, table in tables:
        row: dict[str, Any] = {"schema": schema, "table": table, "read_ok": False, "count": None, "checksum": None}
        try:
            if present is not None:
                files = con.execute(
                    f"SELECT data_file, delete_file FROM ducklake_list_files('{rt.CATALOG_ALIAS}', ?, schema => ?, "
                    f"snapshot_version => {int(snapshot_id)})",
                    [table, schema],
                ).fetchall()
                missing += sum(1 for pair in files for f in pair if f and f.split("/", 3)[3] not in present)
            count, checksum = con.execute(checksum_sql(rt.CATALOG_ALIAS, schema, table, snapshot_id)).fetchone()
            row.update(read_ok=True, count=count, checksum=checksum)
        except Exception:  # noqa: BLE001 -- recorded, never raised: a missing file fails its read
            pass
        rows.append(row)
    return rows, missing


def _step_verify(event: dict[str, Any], dsn: dict[str, str]) -> dict[str, Any]:
    data_path, snapshot_id = str(event.get("data_path")), int(event["snapshot_id"])
    try:
        con = rt.open_connection(
            dsn=dsn, data_path=data_path, meta_schema=META_SCHEMA, extension_directory=EXTENSION_DIRECTORY, read_only=True
        )
    except Exception:  # noqa: BLE001 -- a failed ATTACH is recorded, not raised
        return {"attach_ok": False, "missing_files": None, "tables": []}
    try:
        rows, missing = _table_checks(con, _catalog_tables(dsn), snapshot_id, _scratch_keys(data_path))
    finally:
        con.close()
    return {"attach_ok": True, "missing_files": missing, "tables": rows}


def _step_prod_checksum(event: dict[str, Any]) -> dict[str, Any]:
    tables = [(str(s), str(t)) for s, t in event.get("tables", [])]
    con = rt.open_connection(
        dsn=rt.fetch_dsn(),
        data_path=_PROD_OPS_DATA_PATH,
        meta_schema=META_SCHEMA,
        extension_directory=EXTENSION_DIRECTORY,
        read_only=True,
    )
    try:
        rows, _ = _table_checks(con, tables, int(event["snapshot_id"]), None)
    finally:
        con.close()
    return {"tables": rows}


def _step_duckdb_copy(event: dict[str, Any], dsn: dict[str, str]) -> dict[str, Any]:
    tables = [(str(s), str(t)) for s, t in event.get("tables", [])]
    return run_duckdb_copy(
        dsn,
        meta_schema=META_SCHEMA,
        data_path=str(event.get("data_path")),
        tables=tables,
        snapshot_id=int(event["snapshot_id"]),
    )


def _step_write_probe(event: dict[str, Any], dsn: dict[str, str], prefix: str) -> dict[str, Any]:
    import boto3  # noqa: PLC0415

    data_path = str(event.get("data_path"))
    con = rt.open_connection(dsn=dsn, data_path=data_path, meta_schema=META_SCHEMA, extension_directory=EXTENSION_DIRECTORY)
    try:
        alias = rt.CATALOG_ALIAS
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.rehearsal_probe")
        con.execute(f"CREATE TABLE {alias}.rehearsal_probe.t (id BIGINT)")
        con.execute(f"INSERT INTO {alias}.rehearsal_probe.t SELECT * FROM range(100)")
        path = con.execute(
            f"SELECT data_file FROM ducklake_list_files('{alias}', 't', schema => 'rehearsal_probe')"
        ).fetchone()[0]
    finally:
        con.close()
    in_scratch = path.startswith(prefix)
    relative = path[len(data_path) :] if path.startswith(data_path) else None
    exists = False
    if in_scratch:
        found = boto3.client("s3").list_objects_v2(Bucket=_BUCKET, Prefix=path.split("/", 3)[3], MaxKeys=1)
        exists = found.get("KeyCount", 0) > 0
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            version = _scalar(
                cur, f"SELECT value FROM {META_SCHEMA}.ducklake_metadata WHERE key = 'version' AND scope IS NULL"
            )
    finally:
        conn.close()
    return {
        "write_probe_key": relative,
        "write_probe_in_scratch": bool(in_scratch and exists),
        "catalog_format_version": version,
    }


def _step_teardown_scratch(prefix: str) -> dict[str, Any]:
    import boto3  # noqa: PLC0415

    client, deleted = boto3.client("s3"), 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=_BUCKET, Prefix=prefix.split("/", 3)[3]):
        batch = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if batch:
            client.delete_objects(Bucket=_BUCKET, Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch)
    return {"deleted_objects": deleted}


def action_restore_rehearsal(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """Run one rehearsal step. Refusals raise before any connection; any error beneath a step is value-free."""
    step, prefix, dsn = _guard(event)
    try:
        if step == "prod_checksum":
            body = _step_prod_checksum(event)
        else:
            assert dsn is not None  # noqa: S101 -- _guard guarantees a target for every other step
            body = {
                "restore": lambda: _step_restore(event, dsn),
                "rewrite": lambda: _step_rewrite(event, dsn, prefix),
                "verify": lambda: _step_verify(event, dsn),
                "duckdb_copy": lambda: _step_duckdb_copy(event, dsn),
                "write_probe": lambda: _step_write_probe(event, dsn, prefix),
                "teardown_scratch": lambda: _step_teardown_scratch(prefix),
            }[step]()
    except RestoreRehearsalRefused:
        raise
    except Exception as exc:  # noqa: BLE001 -- the message can name a host; return class and code only
        return {"ok": False, "step": step, "error_class": type(exc).__name__, "code": _code(exc)}
    return {"ok": True, "step": step, **body}
