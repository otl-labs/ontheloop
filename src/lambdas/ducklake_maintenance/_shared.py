"""Shared identifier/path guards for the ducklake_maintenance Lambda (Decision 204 extraction; the metric emitter
and DATA_PATH joined them when control_health/catalog_stats moved to health_actions.py).

EXTENSION_DIRECTORY and _require_identifier (+ its regex) moved here out of handler.py so
partition_actions.py can use them without an import cycle: handler.py imports partition_actions
for its dispatch entries, so partition_actions.py can never import handler.py back (Decision 80
acyclic-import discipline). handler.py re-binds both moved names from here, so its existing tests
and callers are unaffected. Also carries a NEW s3:// data_path guard used only by
partition_actions.py -- handler.py's own per-action inline data_path guards are unchanged.
"""

from __future__ import annotations

import os
import re
from typing import Any

from src.common import ducklake_maintenance as maint
from src.common import ducklake_runtime as rt

EXTENSION_DIRECTORY = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY", rt.LAMBDA_EXTENSION_DIRECTORY)

# T2.26: control_health is read-mostly (asserts invariants, never mutates), so -- unlike the
# production-destructive/operational actions, which all REQUIRE an explicit event data_path
# (no-arg invokes refused, Decision 84/81) -- it may fall back to an env-pinned production default
# so a scheduled EventBridge target's static input (or a manual smoke invoke) need not repeat it.
DATA_PATH = os.environ.get("DUCKLAKE_DATA_PATH")

# A SQL identifier (meta-schema name) -- guards the few f-string-interpolated DDL sites.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_identifier(name: Any) -> str:
    """Validate *name* is a bare SQL identifier (guards the f-string-interpolated meta-schema DDL)."""
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise rt.DuckLakeRuntimeError(f"invalid SQL identifier {name!r} (expected [A-Za-z_][A-Za-z0-9_]*)")
    return name


def _require_data_path(value: Any) -> str:
    """Validate *value* is an s3:// data_path URI (guards partition_actions' event parsing)."""
    if not isinstance(value, str) or not value.startswith("s3://"):
        raise rt.DuckLakeRuntimeError(f"invalid data_path {value!r} -- expected an s3:// URI (the production DuckLake path)")
    return value


def _emit_maintenance_metric(
    name: str, value: float, *, profile: str | None = None, dimensions: dict[str, str] | None = None
) -> None:
    namespace = maint.MAINTENANCE_CLOUDWATCH_NAMESPACE
    if dimensions:
        rt.emit_metric(name, value, namespace=namespace, profile=profile, dimensions=dimensions)
    else:
        rt.emit_metric(name, value, namespace=namespace, profile=profile)


# The closed set merge_ops, gc_ops and the breaker sink emit for a non-ops catalog (cardinality bound; the ops series
# stays undimensioned and carries every name).
CATALOG_METRICS = frozenset(
    {
        "MaintenanceBreakerTrip",
        "GcReferencedMissing",
        "GcDeletedSnapshots",
        "GcDeferredFiles",
        "MergeOpsPassFailed",
        "MergeOpsTablesCount",
        "MergeOpsFilesAfterTotal",
        "MergeOpsDurationMs",
        "CatalogFileColumnStatsRows",
    }
)


def catalog_dimensions(meta_schema: Any) -> dict[str, str] | None:
    """MetaSchema dimension for a non-ops catalog; None for ducklake_ops or anything that is not an identifier."""
    if not isinstance(meta_schema, str) or not _IDENTIFIER_RE.match(meta_schema) or meta_schema == rt.META_SCHEMA:
        return None
    return {"MetaSchema": meta_schema}
