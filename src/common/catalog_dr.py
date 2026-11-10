"""DuckLake catalog disaster-recovery primitives (T2.18 FP-B / CD.34, Decision 82).

Scheduled daily pipeline: pg_dump --format=custom --serializable-deferrable -> engine-version-tagged
S3 object -> CatalogDumpSuccess CloudWatch metric.

Design invariants:
  - LOUD-FAIL on any non-zero pg_dump exit (raises CatalogDrError BEFORE emitting the success metric).
    A failed dump MUST NOT be recorded as a success in CloudWatch (Decision 55).
  - --format=custom: compressed, supports selective/parallel restore via pg_restore.
    Diverges from the T2.16b restore-drill format (plain SQL / --no-owner) intentionally; the
    T2.19 restore-drill gate must be updated to use pg_restore. Flagged as a T2.19 carry item.
  - --serializable-deferrable: clean consistent snapshot without blocking writers.
  - Engine-version tags in BOTH the S3 key and the object metadata so a restore can be matched
    to a compatible engine (OQ.12 lockstep; CD.34 O-2).

Note on format divergence from ducklake_neon_smoke_test._consistent_pg_dump:
  The smoke test uses PLAIN-SQL format (--no-owner; restores via psql). The scheduled DR here
  uses --format=custom (restores via pg_restore). These deliberately diverge; dsn_uri() is
  exported from this module and re-used by both callers to eliminate a second DSN-URI call site.
"""

from __future__ import annotations

import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common.ducklake_runtime import PINNED_DUCKDB_VERSION, emit_metric

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DR_CLOUDWATCH_NAMESPACE = "DuckLakeCatalogDR"
PINNED_PG_VERSION = "16"
DR_METRIC_NAME = "CatalogDumpSuccess"

# Default pg_dump/pg_restore binary paths inside the Lambda pgclient layer (/opt/bin/).
LAMBDA_PG_DUMP_PATH = "/opt/bin/pg_dump"
LAMBDA_PG_RESTORE_PATH = "/opt/bin/pg_restore"


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class CatalogDrError(RuntimeError):
    """Loud-fail for any catalog DR failure (pg_dump non-zero exit, upload error).

    Raised before emitting the success metric -- never swallowed (Decision 55). ``returncode`` carries
    pg_restore's exit status (None for other failures) so a caller can report a value-free code.
    """

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


# ---------------------------------------------------------------------------
# DSN helpers (exported so ducklake_neon_smoke_test can import, eliminating drift)
# ---------------------------------------------------------------------------


def dsn_uri(dsn: dict[str, str]) -> str:
    """Assemble a libpq URI from a DSN dict (sslmode defaults to require)."""
    sslmode = dsn.get("sslmode") or "require"
    user = urllib.parse.quote(dsn["username"], safe="")
    password = urllib.parse.quote(dsn["password"], safe="")
    return f"postgresql://{user}:{password}@{dsn['host']}/{dsn['dbname']}?sslmode={sslmode}"


# ---------------------------------------------------------------------------
# Command / key / metadata builders (testable without subprocess)
# ---------------------------------------------------------------------------


def build_pg_dump_cmd(
    dsn_uri_str: str,
    out_path: str,
    *,
    pg_dump_path: str = LAMBDA_PG_DUMP_PATH,
    schema: str | None = None,
) -> list[str]:
    """Return the pg_dump argv list for a custom-format consistent dump.

    Flags:
      --format=custom          compressed, supports selective/parallel restore (pg_restore)
      --serializable-deferrable  single-txn consistent snapshot, non-blocking
      --schema=<s>             (optional) dump ONLY this schema -- used by the restore drill to dump a
                               scratch meta-schema in isolation, so the drill never touches production
      --file=<out>             write to local path (not stdout)
    """
    cmd = [pg_dump_path, "--format=custom", "--serializable-deferrable"]
    if schema is not None:
        cmd.append(f"--schema={schema}")
    cmd += ["--file", out_path, dsn_uri_str]
    return cmd


def build_pg_restore_cmd(
    dump_path: str,
    target_dsn_uri: str,
    *,
    pg_restore_path: str = LAMBDA_PG_RESTORE_PATH,
    clean: bool = True,
    no_acl: bool = False,
    schema: str | None = None,
) -> list[str]:
    """Return the pg_restore argv for restoring a --format=custom dump into *target_dsn_uri*.

    The FP-B daily dumps are --format=custom (run_catalog_dump / build_pg_dump_cmd), so the T2.19
    restore-drill restores via pg_restore -- NOT the plain-SQL psql path the T2.16b drill used.
    Flags:
      --dbname=<uri>   restore directly into the target database (drives the connection)
      --no-owner       do not restore ownership (the scratch role differs from the dump's owner)
      --clean --if-exists  drop existing objects first so the drill is idempotent on a reused scratch db
      --exit-on-error  loud-fail on the first restore error (Decision 55 -- no partial restore)
      --no-acl         (opt-in) skip GRANTs, so a restore never depends on roles that exist only in PROD
      --schema=<s>     (opt-in) restore only this schema (the rehearsal restores ducklake_ops, never telemetry)
    """
    cmd = [pg_restore_path, "--no-owner", "--exit-on-error", "--dbname", target_dsn_uri]
    if clean:
        cmd[1:1] = ["--clean", "--if-exists"]
    if no_acl:
        cmd.append("--no-acl")
    if schema is not None:
        cmd.append(f"--schema={schema}")
    cmd.append(dump_path)
    return cmd


def run_pg_restore(
    dump_path: str,
    target_dsn: dict[str, str],
    *,
    pg_restore_path: str = LAMBDA_PG_RESTORE_PATH,
    runner: Any = None,
    clean: bool = True,
    no_acl: bool = False,
    schema: str | None = None,
) -> None:
    """Restore *dump_path* (custom format) into *target_dsn* via pg_restore. Loud-fail on non-zero.

    `runner` defaults to subprocess.run; injectable for tests. Raises CatalogDrError on a non-zero
    exit so the restore-drill gate (ducklake_neon_smoke_test --catalog-restore-drill) stops the
    cutover rather than recording a half-restored catalog as healthy.
    """
    run = runner if runner is not None else subprocess.run
    cmd = build_pg_restore_cmd(
        dump_path, dsn_uri(target_dsn), pg_restore_path=pg_restore_path, clean=clean, no_acl=no_acl, schema=schema
    )
    result = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise CatalogDrError(
            f"pg_restore exited {result.returncode}: {result.stderr.strip()[:500]}", returncode=result.returncode
        )


def build_dr_key(
    now: datetime,
    pg_version: str = PINNED_PG_VERSION,
    duckdb_version: str = PINNED_DUCKDB_VERSION,
) -> str:
    """Engine-version-tagged S3 key for a DR dump object.

    Format: catalog-dr/{YYYY}/{MM}/{DD}/ducklake-catalog-pg{PG}-duckdb{DUCKDB}-{ts}.dump
    """
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    date_prefix = now.strftime("%Y/%m/%d")
    return f"catalog-dr/{date_prefix}/ducklake-catalog-pg{pg_version}-duckdb{duckdb_version}-{ts}.dump"


def build_dr_object_metadata(
    pg_version: str = PINNED_PG_VERSION,
    duckdb_version: str = PINNED_DUCKDB_VERSION,
) -> dict[str, str]:
    """S3 object metadata dict encoding the engine versions.

    Stored on the S3 object so a restore operator can verify engine compatibility without
    parsing the key (OQ.12 lockstep; CD.34 O-2).
    """
    return {"pg_version": pg_version, "duckdb_version": duckdb_version}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_catalog_dump(
    dsn: dict[str, str],
    *,
    bucket: str,
    pg_version: str = PINNED_PG_VERSION,
    duckdb_version: str = PINNED_DUCKDB_VERSION,
    profile: str | None = None,
    region: str = "eu-west-2",
    pg_dump_path: str = LAMBDA_PG_DUMP_PATH,
    metric_namespace: str = DR_CLOUDWATCH_NAMESPACE,
    _now: datetime | None = None,
) -> dict[str, Any]:
    """Orchestrate pg_dump -> S3 upload -> emit CatalogDumpSuccess metric.

    Steps:
      1. Build pg_dump command (--format=custom, --serializable-deferrable).
      2. Run pg_dump into a /tmp file; raise CatalogDrError on non-zero exit (loud-fail).
         The success metric is NOT emitted on failure.
      3. Upload the dump to S3 with an engine-version-tagged key and object metadata.
      4. Emit CatalogDumpSuccess=1 to CloudWatch (best-effort; a metrics failure does not fail the dump).

    Returns a stats dict describing the completed dump.
    """
    import boto3  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    now = _now or datetime.now(timezone.utc)
    s3_key = build_dr_key(now, pg_version, duckdb_version)
    metadata = build_dr_object_metadata(pg_version, duckdb_version)

    with tempfile.TemporaryDirectory(prefix="catalog-dr-") as tmp:
        dump_path = str(Path(tmp) / "catalog.dump")
        cmd = build_pg_dump_cmd(dsn_uri(dsn), dump_path, pg_dump_path=pg_dump_path)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise CatalogDrError(f"pg_dump exited {result.returncode}: {result.stderr.strip()[:500]}")

        dump_bytes = Path(dump_path).stat().st_size

        session = boto3.Session(profile_name=resolve_aws_profile(profile), region_name=region)
        s3 = session.client("s3")
        s3.upload_file(
            Filename=dump_path,
            Bucket=bucket,
            Key=s3_key,
            ExtraArgs={"Metadata": metadata},
        )

    emit_metric(DR_METRIC_NAME, 1.0, namespace=metric_namespace, unit="Count", profile=profile)

    return {
        "ok": True,
        "s3_key": s3_key,
        "bucket": bucket,
        "dump_bytes": dump_bytes,
        "pg_version": pg_version,
        "duckdb_version": duckdb_version,
    }
