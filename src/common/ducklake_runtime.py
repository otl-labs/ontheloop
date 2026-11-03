"""DuckLake operational-lakehouse runtime facade (T2.17 / CD.33, Decision 81).

Single ATTACH/connection authority: this module owns the warm-connection cache and the one
ATTACH implementation shared by the ducklake_writer/ducklake_reader Lambdas and the Neon smoke
test (dev/smoke installs extensions over the network; the Lambda path loads them from a baked
layer; `extension_directory` selects the mode).

Design invariants (CD.33):
  - Idempotent append: the history table is keyed by a monotonic ULID minted ONCE per write,
    OUTSIDE the OCC-retry loop, and reused on every retry. MERGE-on-ULID insert-if-not-matched
    de-duplicates, so a retried write never double-appends (no engine PK; the ULID is a logical
    key enforced by MERGE).
  - Schema gate + OCC exhaustion LOUD-FAIL (Decision 55): a rejected field or an exhausted
    retry budget raises; there is never a silent drop or a fallback backend.
  - Version lockstep (OQ.12): DuckDB is pinned to PINNED_DUCKDB_VERSION; a runtime assert fails
    loudly on mismatch.

Split invariant (PLAN-sloc-ducklake-layer): the write/table-DDL/read/metrics primitives now live
in ducklake_writes / ducklake_tables / ducklake_reads / ducklake_metrics; the control-class table
surface (T2.26) lives in ducklake_control_tables. This module re-exports every symbol so
`from src.common.ducklake_runtime import X`, `rt.X`, and
`patch("src.common.ducklake_runtime.X")` call sites keep binding to real module attributes at the
original path. The pure schema layer (spec/SQL builders/gate) lives in ducklake_scd2_schema, whose
re-export block below is unchanged. Dependency is strictly one-directional: this facade imports
the sub-modules; none of them import it back (Decision 80 acyclic-import discipline).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from src.common import ducklake_spike
from src.common.ducklake_control_tables import (  # noqa: F401 -- re-exported facade surface (T2.26)
    ControlTableSpec,
    bootstrap_entity_counter,
    control_table_names,
    ensure_entity_counters_table,
    is_control_table,
    resolve_control_spec,
)
from src.common.ducklake_metrics import (  # noqa: F401 -- re-exported facade surface (PLAN-sloc-ducklake-layer)
    CLOUDWATCH_NAMESPACE,
    emit_metric,
    make_metric_sink,
)
from src.common.ducklake_named_reads import read_version  # noqa: F401 -- re-exported for the reader handler's per-verb stamp
from src.common.ducklake_reads import (  # noqa: F401 -- re-exported facade surface (PLAN-sloc-ducklake-layer)
    named_read,
    read_current,
    read_history,
)
from src.common.ducklake_scd2_schema import (
    _DEFAULT_FIELD_SEMANTICS_PATH,  # noqa: F401 -- re-exported for backward compat
    _FIELD_SEMANTICS_ENV,  # noqa: F401 -- re-exported for backward compat
    _PY_TYPE_FOR_SQL,  # noqa: F401 -- re-exported for backward compat
    CATALOG_ALIAS,
    NAMED_READS,  # noqa: F401 -- re-exported; own use (named_read) moved to ducklake_reads
    NAMED_READS_VERSION,  # noqa: F401 -- re-exported for the reader handler response envelope
    SMOKE_CURRENT_TABLE,  # noqa: F401 -- re-exported for backward compat
    SMOKE_HISTORY_TABLE,  # noqa: F401 -- re-exported for backward compat
    STATUS_TRANSITIONS,  # noqa: F401 -- re-exported for tests/clients introspecting the DAG
    VERB_REGISTRY,  # noqa: F401 -- re-exported for tests/clients introspecting the write-verb registry
    AppendOnlyUpdateError,  # noqa: F401 -- re-exported for clients catching write-once violations
    DuckLakeRuntimeError,
    NamedRead,  # noqa: F401 -- re-exported for tests/clients introspecting the registry
    ReferentialError,  # noqa: F401 -- re-exported; own use (write_scd2) moved to ducklake_writes
    ScdTableSpec,  # noqa: F401 -- re-exported for backward compat
    SchemaGateError,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    StatusTransitionError,  # noqa: F401 -- re-exported for clients catching illegal status reactivation
    WriteIdentity,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    WriteResult,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    WriteVerb,  # noqa: F401 -- re-exported for tests/clients introspecting the write-verb registry
    _build_merge_current_sql,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    _build_merge_history_sql,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    _build_select_existing_created_sql,  # noqa: F401 -- re-exported; own use (write_scd2) moved to ducklake_writes
    _column_ddl,  # noqa: F401 -- re-exported; own use (create_scd2_tables) moved to ducklake_tables
    _field_semantics_path,  # noqa: F401 -- re-exported for backward compat
    _load_field_semantics_cached,  # noqa: F401 -- re-exported for backward compat
    _order_columns,  # noqa: F401 -- re-exported for backward compat
    _write_params,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    check_append_only_guard,  # noqa: F401 -- re-exported; own use (write_scd2) moved to ducklake_writes
    check_rec_status_transition,  # noqa: F401 -- re-exported; own use (write_scd2) moved to ducklake_writes
    describe_named_reads,  # noqa: F401 -- re-exported for the reader handler's `describe` action
    describe_write_verbs,  # noqa: F401 -- re-exported for the writer handler's `describe` action
    is_event_table,  # noqa: F401 -- re-exported for both handlers' write_boundary refusal and control_health
    load_field_semantics,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    ops_table_names,  # noqa: F401 -- re-exported for backward compat
    resolve_table_spec,  # noqa: F401 -- re-exported; own use moved to ducklake_writes/tables/reads
    schema_gate,  # noqa: F401 -- re-exported; own use (write_scd2/file_scd2) moved to ducklake_writes
    table_write_boundary,  # noqa: F401 -- re-exported for both handlers' write_boundary refusal
)
from src.common.ducklake_tables import (  # noqa: F401 -- re-exported facade surface
    create_control_table,
    create_scd2_tables,
    reconcile_table_columns,
)
from src.common.ducklake_version import pinned_duckdb_version as _pinned_duckdb_version
from src.common.ducklake_write_rules import (
    RowRuleViolationError,  # noqa: F401 -- re-exported for the writer handler's 422 row_rule
)
from src.common.ducklake_writes import (  # noqa: F401 -- re-exported facade surface (PLAN-sloc-ducklake-layer)
    CHURN_WRITERS,
    CHURN_WRITES_PER_WRITER,
    COMMIT_LATENCY_BUDGET_MS,
    ENTITY_COUNTERS_TABLE,
    OCC_COLLISION_RATE_BUDGET,
    OCC_MAX_BACKOFF_S,
    OCCRetryExhaustedError,
    _advance_entity_counter,
    _allocate_entity_id,
    _emit_write_metrics,
    _occ_backoff,
    _safe_rollback,
    file_scd2,
    is_occ_collision,
    mint_write_identity,
    write_scd2,
)

# ---------------------------------------------------------------------------
# Constants -- connection authority
# ---------------------------------------------------------------------------

_PINNED_DUCKDB_VERSION: str | None = None  # resolved lazily via module __getattr__ -> _pinned_duckdb_version()

DSN_PARAMETER_NAME = "/secret/ducklake-neon-catalog-dsn"
META_SCHEMA = "ducklake_ops"

# Smoke uses a DEDICATED meta-schema so it can never collide with the production catalog. rec-2099
# root cause: T2.17 smoke initialized `ducklake_ops` at the smoke DATA_PATH, and DATA_PATH is pinned
# per meta-schema, so a later production ATTACH at `ducklake/` fails. Each meta-schema is an
# independent DuckLake catalog in the same Neon Postgres -- production attaches `ducklake_ops`, smoke
# attaches `ducklake_smoke` -- so their DATA_PATH pins never conflict.
SMOKE_META_SCHEMA = "ducklake_smoke"

# The reader's second catalog (telemetry event journal) attaches under its own alias, so a statement that reaches the
# wrong slot fails loud (catalog missing) instead of silently reading the other catalog.
TELEMETRY_CATALOG_ALIAS = "telemetry_catalog"
OPS_SLOT = "ops"

# Representative SCD2 smoke-table pair (real ops_* business schema is T2.19).
SMOKE_DATA_PATH = "s3://agent-platform-data-lake/ducklake-neon-smoke/"

# Production telemetry is its own catalog. Its data prefix is a SIBLING of ducklake/, never inside it, so the ops catalog's
# catalog-wide orphan cleanup never sees telemetry Parquet.
TELEMETRY_PROD_META_SCHEMA = "ducklake_telemetry"
TELEMETRY_PROD_DATA_PATH = "s3://agent-platform-data-lake/ducklake-telemetry/"

# Baked extensions in the Lambda layer: (LOAD/INSTALL name, on-disk file stem). DuckDB publishes
# the Postgres extension binary as `postgres_scanner.duckdb_extension` even though it INSTALLs and
# LOADs under the name `postgres` (verified against the pinned duckdb / config/lambda/ducklake/version.yaml).
BAKED_EXTENSIONS: tuple[tuple[str, str], ...] = (
    ("ducklake", "ducklake"),
    ("httpfs", "httpfs"),
    ("postgres", "postgres_scanner"),
)

# Default location DuckDB looks for baked extensions inside the Lambda (the layer unpacks to /opt).
LAMBDA_EXTENSION_DIRECTORY = "/opt/duckdb_extensions"


def __getattr__(name: str):
    """PEP 562 lazy module attribute: expose PINNED_DUCKDB_VERSION without import-time I/O."""
    if name == "PINNED_DUCKDB_VERSION":
        return _pinned_duckdb_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Exceptions -- loud-fail conditions specific to the connection layer
# ---------------------------------------------------------------------------


class VersionMismatchError(DuckLakeRuntimeError):
    """Raised when the live DuckDB version differs from the pinned lockstep version (OQ.12)."""


# ---------------------------------------------------------------------------
# Version assertion (OQ.12 lockstep)
# ---------------------------------------------------------------------------


def assert_duckdb_version(duckdb_module: Any = None) -> str:
    """Assert the live DuckDB equals the pinned version. Loud-fail on mismatch (OQ.12).

    Returns the asserted version string. Pass `duckdb_module` to inject a fake in tests.
    """
    duckdb_module = duckdb_module if duckdb_module is not None else ducklake_spike._require_duckdb()
    actual = getattr(duckdb_module, "__version__", None)
    target = _pinned_duckdb_version()
    if actual != target:
        raise VersionMismatchError(
            f"DuckDB version mismatch: runtime has {actual!r}, pinned {target!r}. "
            "DuckLake v1.0 is lockstep with the version in config/lambda/ducklake/version.yaml (OQ.12). "
            "Follow the clone-rehearsal version-bump policy in docs/runbooks/ducklake-catalog-operations.md."
        )
    return actual


# ---------------------------------------------------------------------------
# DSN fetch + conninfo (moved here from the smoke test -- single implementation)
# ---------------------------------------------------------------------------


def fetch_dsn(parameter_name: str = DSN_PARAMETER_NAME, *, profile: str | None = None) -> dict[str, str]:
    """Fetch + parse the Neon DSN JSON from an SSM SecureString parameter (Decision 37 runtime-fetch).

    Returns a dict with at least host / dbname / username / password (sslmode optional, defaults
    to require). Raises RuntimeError if the document is missing a required key, and ValueError for a
    name outside /secret/ (a stale Secrets Manager id fails loudly). Store errors propagate unwrapped.
    """
    import boto3  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    if not parameter_name.startswith("/secret/"):
        raise ValueError(f"DSN parameter name {parameter_name!r} is not under /secret/")
    session = boto3.Session(profile_name=resolve_aws_profile(profile))
    client = session.client("ssm")
    resp = client.get_parameter(Name=parameter_name, WithDecryption=True)
    payload = _parse_dsn_document(resp["Parameter"]["Value"])
    missing = [k for k in ("host", "dbname", "username", "password") if not payload.get(k)]
    if missing:
        raise RuntimeError(f"DSN parameter {parameter_name!r} is missing required keys: {missing}")
    return payload


def _parse_dsn_document(document: str) -> dict[str, str]:
    """Parse the DSN parameter's JSON value into a dict."""
    import json  # noqa: PLC0415

    return json.loads(document)


def libpq_conninfo(dsn: dict[str, str]) -> str:
    """Return a libpq keyword/value conninfo string for the DuckLake postgres backend.

    sslmode defaults to require so TLS is always enforced even if the secret omits it (the
    Terraform-written secret always sets it; this is defence in depth).
    connect_timeout is bounded (default 10s, overridable via DUCKLAKE_CONNECT_TIMEOUT_S) so a
    stale/unreachable endpoint fails fast with a precise libpq error instead of hanging to the
    120s Lambda wall.
    """
    import os  # noqa: PLC0415

    sslmode = dsn.get("sslmode") or "require"
    timeout = int(os.environ.get("DUCKLAKE_CONNECT_TIMEOUT_S", "10"))
    base = f"dbname={dsn['dbname']} host={dsn['host']} user={dsn['username']} password={dsn['password']}"
    return f"{base} sslmode={sslmode} connect_timeout={timeout}"


# ---------------------------------------------------------------------------
# Connection authority -- the single ATTACH (dev INSTALL vs Lambda baked layer)
# ---------------------------------------------------------------------------


def open_connection(
    *,
    dsn: dict[str, str],
    data_path: str = SMOKE_DATA_PATH,
    meta_schema: str = META_SCHEMA,
    extension_directory: str | None = None,
    profile: str | None = None,
    alias: str = CATALOG_ALIAS,
    read_only: bool = False,
    _creds: tuple[str, str, str | None, str] | None = None,
) -> Any:
    """Open a DuckDB connection with ducklake/httpfs/postgres loaded and the Neon catalog ATTACHed.

    extension_directory:
      - None  -> dev/smoke mode: network INSTALL + LOAD of each extension.
      - set   -> Lambda baked mode: LOAD from the directory with autoload/autoinstall DISABLED and
                 custom_extension_repository EMPTY (fail-closed: no network INSTALL at runtime).

    meta_schema selects the DuckLake catalog: production attaches `ducklake_ops` (the default
    META_SCHEMA), smoke attaches `SMOKE_META_SCHEMA` (`ducklake_smoke`). Each meta-schema is an
    independent catalog in the same Neon Postgres, so their pinned DATA_PATHs never collide (rec-2099).

    The ATTACH validates DATA_PATH against the catalog's stored value and fails loud on mismatch
    (no silent rebind). DuckLake pins the data_path at catalog-init; OVERRIDE_DATA_PATH is only a
    per-session override and does NOT persist, so relocating the catalog means reinitialising it --
    see docs/runbooks/ducklake-catalog-operations.md.

    Inlining is disabled (ducklake_default_data_inlining_row_limit=0) on every connection so S3
    Parquet is written immediately for ALL tables (EC11 / smoke #921). The ATTACH targets the Neon
    DIRECT endpoint over TLS (sslmode=require, enforced by libpq_conninfo).
    """
    duckdb = ducklake_spike._require_duckdb()
    assert_duckdb_version(duckdb)
    con = duckdb.connect()
    # Limit DuckDB's internal thread pool to 1. The churn gate runs 8 Python threads, each with its
    # own connection; DuckDB's background parallelism compounds vCPU starvation on constrained Lambda
    # allocations. Our caller provides the concurrency -- DuckDB does not need to add its own.
    con.execute("SET threads=1")

    if extension_directory is not None:
        con.execute(f"SET extension_directory={ducklake_spike._sql_str_literal(extension_directory)}")
        con.execute("SET autoinstall_known_extensions=false")
        con.execute("SET autoload_known_extensions=false")
        con.execute("SET custom_extension_repository=''")
        for load_name, _stem in BAKED_EXTENSIONS:
            con.execute(f"LOAD {load_name}")
    else:
        for load_name, _stem in BAKED_EXTENSIONS:
            con.execute(f"INSTALL {load_name}; LOAD {load_name}")

    if _creds is not None:
        ak, sk, tok, region = _creds
        con.execute(f"SET s3_region={ducklake_spike._sql_str_literal(region)}")
        con.execute(f"SET s3_access_key_id={ducklake_spike._sql_str_literal(ak)}")
        con.execute(f"SET s3_secret_access_key={ducklake_spike._sql_str_literal(sk)}")
        if tok:
            con.execute(f"SET s3_session_token={ducklake_spike._sql_str_literal(tok)}")
    else:
        ducklake_spike._set_s3_credentials(con, profile=profile)

    # Inlining off for ALL tables (CD.34 / EC11): write S3 Parquet immediately, never inline rows.
    con.execute("SET ducklake_default_data_inlining_row_limit=0")

    # Pin UTC BEFORE the ATTACH (Decision 96 intent, rec-4068): DuckLake evaluates year()/month()/
    # day()/hour() partition transforms in the connection's TimeZone, so an un-pinned Lambda
    # environment default would make the calendar-day partition boundary follow the container's
    # local TZ instead of UTC. ICU is statically linked in the pinned duckdb version, so this works
    # with autoload disabled.
    con.execute("SET TimeZone='UTC'")

    conninfo = libpq_conninfo(dsn)
    read_only_option = ", READ_ONLY" if read_only else ""
    con.execute(
        f"ATTACH 'ducklake:postgres:{conninfo}' AS {alias} "
        f"(DATA_PATH '{data_path}', META_SCHEMA '{meta_schema}'{read_only_option})"
    )
    return con


# ---------------------------------------------------------------------------
# Warm-connection cache (neon-egress-reduction D2).
#
# A fresh ATTACH per Lambda invocation is the dominant Neon catalog-egress driver: DuckDB's postgres
# scanner sequential-COPYs ducklake_file_column_stats per query (ducklake #859), and re-ATTACHing
# every request pays that metadata transfer again and again. Reusing ONE connection across sequential
# warm invocations on the same container eliminates the repeated ATTACH (and its metadata egress).
#
# This is a per-container module global for SEQUENTIAL request handling (a Lambda container serves one
# invocation at a time). The 8-thread churn harness MUST NOT use it -- it opens an independent
# connection per thread via open_connection (constraint: never share the cached connection across
# threads). A dead session (Neon scale-to-zero) is handled as the ONE expected reopen condition
# (Decision 55: any other error still raises).
# ---------------------------------------------------------------------------

_WARM_CONNECTION_LOCK = threading.Lock()
_warm_connection: dict[str, Any] = {}
# Every non-ops slot (the reader's telemetry catalog): slot -> {"con", "key"}. The ops slot keeps the module-level dict
# above, which existing callers and tests read directly; a reopen or reset of one slot never touches another.
_named_slots: dict[str, dict[str, Any]] = {}


def _slot_store(slot: str) -> dict[str, Any]:
    return _warm_connection if slot == OPS_SLOT else _named_slots.setdefault(slot, {})


# Connection-failure signatures treated as the expected dead-catalog-session condition (Neon
# scale-to-zero suspended the underlying Postgres session, or the cached DuckDB connection was closed).
# DELIBERATELY SPECIFIC PHRASES, not the bare word "connection": a capacity error ("too many
# connections for role", "remaining connection slots are reserved") or an auth failure ("password
# authentication failed") must FAIL LOUD, not be silently reopened+retried (Decision 55 -- the one
# expected transient is a dropped/closed session, never pool-exhaustion or auth). See
# test_non_dead_errors_do_not_match for the excluded false-positives.
_DEAD_CONNECTION_SIGNATURES = (
    "connection refused",
    "connection reset",
    "connection already closed",  # DuckDB closed-connection
    "connection timed out",
    "the connection is closed",
    "server closed",  # "server closed the connection unexpectedly"
    "terminating connection",
    "could not connect",
    "no connection to the server",
    "ssl connection has been closed",
    "database has been invalidated",
)


def is_dead_connection_error(exc: BaseException) -> bool:
    """True iff *exc* matches the expected dead-catalog-session signature (Neon scale-to-zero / closed).

    The narrow allow-list keeps the warm-connection reopen scoped to the ONE expected transient
    (Decision 55: no catch-and-relax) -- any other failure still propagates at the call site.
    """
    msg = str(exc).lower()
    return any(sig in msg for sig in _DEAD_CONNECTION_SIGNATURES)


def _probe_connection_alive(con: Any, probe_sql: str) -> bool:
    """Cheap liveness probe. Returns False when the probe raises (closed/dead connection)."""
    try:
        con.execute(probe_sql).fetchall()
        return True
    except Exception:  # noqa: BLE001 -- any probe failure means reopen; the reopen itself loud-fails
        return False


def reset_warm_connection(slot: str | None = None) -> None:
    """Close + clear one warm slot (test teardown / explicit drop); slot=None drops every slot."""
    with _WARM_CONNECTION_LOCK:
        stores = [_slot_store(slot)] if slot is not None else [_warm_connection, *_named_slots.values()]
        cons = [store.pop("con", None) for store in stores]
        for store in stores:
            store.pop("key", None)
    for con in cons:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


def get_warm_connection(
    *,
    dsn: dict[str, str] | None = None,
    dsn_factory: Callable[[], dict[str, str]] | None = None,
    opener: Callable[[], Any] | None = None,
    data_path: str = SMOKE_DATA_PATH,
    meta_schema: str = META_SCHEMA,
    extension_directory: str | None = None,
    profile: str | None = None,
    _creds: tuple[str, str, str | None, str] | None = None,
    probe_sql: str = "SELECT 1",
    force_reopen: bool = False,
    slot: str = OPS_SLOT,
    alias: str = CATALOG_ALIAS,
) -> tuple[Any, dict[str, Any]]:
    """Return a per-container cached DuckLake connection, reusing the ATTACH across SEQUENTIAL warm
    invocations (neon-egress-reduction D2).

    The first call in a container opens + ATTACHes and caches the connection; subsequent invocations
    on the same warm container reuse it -- no re-ATTACH, so no per-invocation re-COPY of
    ducklake_file_column_stats (ducklake #859) and therefore no repeated Neon metadata egress. The
    cached connection is validated by a cheap liveness probe; a dead session (Neon scale-to-zero) or a
    cross-catalog key change triggers a transparent reopen.

    *dsn* / *dsn_factory* / *opener*: supply the catalog DSN directly, a DSN factory, or a full
    connection opener (zero-arg). All are invoked ONLY when (re)opening, so the warm-reuse path does
    not re-fetch Secrets Manager or re-ATTACH. *opener* takes precedence (the reader/writer handlers
    pass their existing _open_*_connection so the open seam stays single).

    Returns (connection, meta) where meta = {"reused": bool, "reopened": bool, "connect_ms": float}.
    connect_ms is 0.0 on reuse (no ATTACH) and the real open cost on a (re)open -- so warm reuse is
    observable in the handler response.

    *slot* names an independent cache entry (the reader's telemetry catalog uses its own); each slot has its own key,
    liveness probe, reopen and connect meta, and reopening one never closes another. *alias* is the ATTACH alias when
    this function opens the connection itself.

    CONCURRENCY: per-container module global for SEQUENTIAL handling only. The churn harness opens its
    own per-thread connections via open_connection and MUST NOT call this.
    """
    key = (data_path, meta_schema, extension_directory)
    with _WARM_CONNECTION_LOCK:
        store = _slot_store(slot)
        cached = store.get("con")
        cached_key = store.get("key")
        had_cached = cached is not None
        if not force_reopen and cached is not None and cached_key == key and _probe_connection_alive(cached, probe_sql):
            return cached, {"reused": True, "reopened": False, "connect_ms": 0.0}

        # (Re)open: discard a stale / dead / cross-catalog cached connection first.
        if cached is not None:
            try:
                cached.close()
            except Exception:  # noqa: BLE001
                pass
            store.pop("con", None)
            store.pop("key", None)

        t0 = time.perf_counter()
        if opener is not None:
            con = opener()
        else:
            resolved_dsn = dsn if dsn is not None else (dsn_factory() if dsn_factory is not None else None)
            if resolved_dsn is None:
                raise DuckLakeRuntimeError("get_warm_connection requires a dsn, dsn_factory, or opener")
            con = open_connection(
                dsn=resolved_dsn,
                data_path=data_path,
                meta_schema=meta_schema,
                extension_directory=extension_directory,
                profile=profile,
                alias=alias,
                _creds=_creds,
            )
        connect_ms = (time.perf_counter() - t0) * 1000.0
        store["con"] = con
        store["key"] = key
        # reopened=True means a previously-cached (dead/stale) connection was replaced (not a cold start).
        return con, {"reused": False, "reopened": had_cached, "connect_ms": round(connect_ms, 2)}
