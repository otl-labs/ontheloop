"""Data quality compile path: YAML/tombstones manifest -> Check objects (a dq_check kind and bound params, never SQL)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.data_quality_models import _TOMBSTONES_PATH, Check

# ---------------------------------------------------------------------------
# Tombstone resurrection checks
# ---------------------------------------------------------------------------


def load_tombstones(path: Path = _TOMBSTONES_PATH) -> list[dict]:
    """Load the list of hard-deleted record IDs from dq_tombstones.yaml."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    return spec.get("tombstones", []) if spec else []


def build_tombstone_checks(
    tombstones: list[dict],
    table_filter: str | None = None,
    database: str = "agent_platform",
) -> list[Check]:
    """Generate tombstone_resurrection Check objects from the tombstones manifest."""
    checks: list[Check] = []
    for entry in tombstones:
        table = entry.get("table", "")
        rec_id = entry.get("id", "")
        if not table or not rec_id:
            continue
        if table_filter and table != table_filter:
            continue
        view_name = f"{table}_current" if table == "ops_recommendations" else table
        checks.append(
            Check(
                table=table,
                column="id",
                test_type="tombstone_resurrection",
                kind="key_absent",
                params={"column": "id", "value": rec_id},
                description=f"{table}: tombstoned record {rec_id} must not exist in {view_name}",
                severity="error",
                enforced=True,
            )
        )
    return checks


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_checks(
    yaml_path: Path,
    table_filter: str | None = None,
) -> tuple[list[Check], dict[str, Any]]:
    """Load and compile checks from a YAML file.

    Returns (checks, metadata) where metadata carries the database name.
    """
    with open(yaml_path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    metadata = {"database": spec.get("database", "agent_platform")}

    checks: list[Check] = []
    for table_name, table_def in spec.get("tables", {}).items():
        if table_filter and table_name != table_filter:
            continue

        # Table-level checks. Their exclude_before is carried on the Check but never applied to the count.
        if "row_count" in table_def:
            rc = table_def["row_count"]
            min_rows = rc.get("min", 1)
            checks.append(
                Check(
                    table=table_name,
                    column=None,
                    test_type="row_count",
                    kind="row_count_min",
                    params={"min": min_rows},
                    description=f"{table_name}: must have >= {min_rows} rows",
                    severity=rc.get("severity", "error"),
                    enforced=rc.get("enforced", True),
                    exclude_before=rc.get("exclude_before"),
                )
            )

        if "recency" in table_def:
            rec = table_def["recency"]
            col = rec["column"]
            error_h = rec.get("error_after_hours", 168)
            checks.append(
                Check(
                    table=table_name,
                    column=col,
                    test_type="recency",
                    kind="recency_hours",
                    params={"column": col, "hours": error_h},
                    description=f"{table_name}.{col}: most recent value must be within {error_h}h of now",
                    severity="error",
                    enforced=rec.get("enforced", True),
                    exclude_before=rec.get("exclude_before"),
                )
            )

        for col_name, col_def in table_def.get("columns", {}).items():
            for test in col_def.get("tests", []):
                compiled = _compile_column_test(table_name, table_name, col_name, test)
                if compiled:
                    checks.append(compiled)

    return checks, metadata


def _gate(kind_params: dict[str, Any], exclude_before: str | None) -> dict[str, Any]:
    return {**kind_params, "exclude_before": exclude_before} if exclude_before else kind_params


def _compile_column_test(_query_table: str, table_name: str, col_name: str, test: str | dict) -> Check | None:
    """Compile a single column test definition to a Check (a dq_check kind and params).

    `_query_table` is the retired SQL FROM-target argument, kept so the compiler's callers and graduated guards keep their
    call shape; it is never read.
    """
    if isinstance(test, str):
        test_type, opts = test, {}
    elif isinstance(test, dict):
        test_type = next(iter(test))
        opts = test[test_type]
    else:
        return None

    # accepted_values may be written as a bare list of values.
    meta = opts if isinstance(opts, dict) else {}
    severity = meta.get("severity", "error")
    enforced = meta.get("enforced", True)
    eb = meta.get("exclude_before")

    def make(kind: str | None, params: dict[str, Any], description: str, *, gated: bool = True) -> Check:
        return Check(
            table=table_name,
            column=col_name,
            test_type=test_type,
            kind=kind,
            params=_gate(params, eb) if gated else params,
            description=f"{table_name}.{col_name}: {description}" if kind else description,
            severity=severity,
            enforced=enforced,
            exclude_before=eb,
        )

    if test_type == "not_null":
        return make("not_null", {"column": col_name}, "must not be NULL")
    if test_type == "unique":
        columns = list(meta.get("columns") or [col_name])
        projection = meta.get("projection", "current")
        return make("unique", {"columns": columns, "projection": projection}, "must be unique")
    if test_type == "accepted_values":
        values = list(opts if isinstance(opts, list) else meta.get("values", []))
        return make("accepted_values", {"column": col_name, "values": values}, f"values must be in [{', '.join(values)}]")
    if test_type == "pattern":
        return make("pattern", {"column": col_name, "value": meta["value"]}, f"must match {meta['value']}")
    if test_type == "min_value":
        return make("min_value", {"column": col_name, "value": meta["value"]}, f"must be >= {meta['value']}")
    if test_type == "history_not_ahead_of_current":
        column = meta.get("column", col_name)
        return make(
            "history_not_ahead_of_current",
            {"column": column},
            f"history {column} must never exceed current {column}",
            gated=False,
        )
    if test_type == "relationships":
        if not isinstance(opts, dict):
            return None
        to_table, to_column = opts.get("to_table", ""), opts.get("to_column", "")
        desc = f"{table_name}.{col_name} -> {to_table}.{to_column}: FK must resolve"
        return make(None, {}, desc)
    raise ValueError(f"{table_name}.{col_name}: unrecognised test type {test_type!r} (not SQL-compilable)")


def build_clause8_checks(spec_yaml: dict, database: str, table_filter: str | None = None) -> list[Check]:
    """Generate the CD.33 clause-8 DuckLake checks (ULID-history uniqueness, current merge-key uniqueness).

    Driven by the ops.yaml `ducklake.clause8_checks` declaration + the field_semantics merge keys.
    Referential integrity is enforced in-writer (L1); the relationships checks in ops.yaml are the L2
    backstop, so no separate referential check is generated here. DuckLake backend only.
    """
    from src.common.ducklake_runtime import resolve_table_spec  # noqa: PLC0415

    cfg = (spec_yaml.get("ducklake") or {}).get("clause8_checks") or {}
    checks: list[Check] = []
    for table in cfg.get("tables", []):
        if table_filter and table != table_filter:
            continue
        spec = resolve_table_spec(table)
        if cfg.get("ulid_history_unique"):
            checks.append(
                Check(
                    table=table,
                    column="ulid",
                    test_type="ulid_history_unique",
                    kind="unique",
                    params={"columns": ["ulid"], "projection": "history"},
                    description=f"{spec.history_table}.ulid: history ULID unique (idempotency PK)",
                    backend="ducklake",
                )
            )
        if cfg.get("current_merge_key_unique"):
            mk = spec.merge_key
            checks.append(
                Check(
                    table=table,
                    column=mk,
                    test_type="current_merge_key_unique",
                    kind="unique",
                    params={"columns": [mk], "projection": "current"},
                    description=f"{spec.current_table}.{mk}: current has one row per merge key",
                    backend="ducklake",
                )
            )
    return checks
