"""Tests for src/common/ducklake_scd2_schema.py -- pure schema layer.

Verifies the extracted module in isolation: spec resolution, DDL/MERGE SQL builders, schema gate,
and field-semantics loading. No live catalog or DuckDB connection is required.

VP1 invariant: DDL/MERGE SQL produced here must be byte-identical to what ducklake_runtime
re-exports. Asserted via the identity check in test_builders_re_exported_via_runtime below.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common import ducklake_runtime as rt
from src.common import ducklake_scd2_schema as schema

pytestmark = pytest.mark.unit

_SEMANTICS = {
    "fields": {
        "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
        "rec_id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
        "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
        "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
        "payload": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
    },
    "partition_transforms": {
        "history": "year(created_timestamp), month(created_timestamp), day(created_timestamp)",
        "current": "bucket(8, rec_id)",
    },
}


# ---------------------------------------------------------------------------
# VP1 invariant: re-exports are the same objects (byte-identical SQL guaranteed)
# ---------------------------------------------------------------------------


def test_builders_re_exported_via_runtime():
    """The schema module's builders are the exact functions re-exported by the runtime."""
    assert rt._build_merge_history_sql is schema._build_merge_history_sql
    assert rt._build_merge_current_sql is schema._build_merge_current_sql
    assert rt._build_select_existing_created_sql is schema._build_select_existing_created_sql
    assert rt._column_ddl is schema._column_ddl
    assert rt.schema_gate is schema.schema_gate
    assert rt.resolve_table_spec is schema.resolve_table_spec


# ---------------------------------------------------------------------------
# No circular import: runtime -> schema (schema must NOT import runtime)
# ---------------------------------------------------------------------------


def test_no_circular_import():
    """ducklake_scd2_schema must not import ducklake_runtime (one-directional dependency)."""
    import importlib
    import sys

    mod = importlib.import_module("src.common.ducklake_scd2_schema")
    source = getattr(mod, "__file__", "") or ""
    runtime_mod_names = {k for k in sys.modules if "ducklake_runtime" in k}
    assert all("ducklake_scd2_schema" not in k for k in runtime_mod_names)
    for attr in vars(mod).values():
        mod_name = getattr(attr, "__module__", "") or ""
        assert "ducklake_runtime" not in mod_name, f"schema re-imports from runtime: {attr!r}"
    _ = source  # suppress lint


@pytest.mark.parametrize("module", ["ducklake_named_reads", "ducklake_write_verbs"])
def test_extracted_module_imports_on_its_own_without_the_facade(module):
    """Each extracted module imports first and alone (fresh interpreter): neither has a module-scope
    import of the facade, which would be a cycle (the facade imports them)."""
    import subprocess
    import sys

    code = f"import sys, src.common.{module}; assert 'src.common.ducklake_scd2_schema' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


# Every module-level name of the pre-split ducklake_scd2_schema (captured BEFORE the split, public and
# private, including the imports a test patches or reads through the module).
_PRE_SPLIT_SURFACE = frozenset(
    {
        "Any", "AppendOnlyUpdateError", "CATALOG_ALIAS", "DuckLakeRuntimeError", "NAMED_READS", "NAMED_READS_VERSION",
        "NamedRead", "PartitionSpecError", "Path", "ReferentialError", "SMOKE_CURRENT_TABLE", "SMOKE_HISTORY_TABLE",
        "STATUS_TRANSITIONS", "ScdTableSpec", "SchemaGateError", "StatusTransitionError", "VERB_REGISTRY", "WriteIdentity",
        "WriteResult", "WriteVerb", "_ACTIVE_REC_STATUSES", "_DEFAULT_FIELD_SEMANTICS_PATH", "_DERIVED_LEAD", "_DERIVED_TAIL",
        "_ENFORCED_REC_STATUSES", "_FIELD_SEMANTICS_ENV", "_PY_TYPE_FOR_SQL", "_RESOLVED_REC_STATUSES",
        "_build_merge_current_sql", "_build_merge_history_sql", "_build_select_existing_created_sql", "_column_ddl",
        "_field_semantics_path", "_load_field_semantics_cached", "_order_columns", "_params_schema", "_write_params",
        "annotations", "check_append_only_guard", "check_rec_status_transition", "dataclass", "datetime",
        "describe_named_reads", "describe_write_verbs", "load_field_semantics", "lru_cache", "ops_table_names", "os",
        "resolve_partition_block", "resolve_table_spec", "schema_gate", "yaml",
    }
)  # fmt: skip


def test_facade_reexports_pre_split_surface():
    """The facade's names are a superset of the pre-split surface, plus exactly the named additions."""
    names = {n for n in vars(schema) if not n.startswith("__")}
    assert _PRE_SPLIT_SURFACE <= names, sorted(_PRE_SPLIT_SURFACE - names)
    assert names - _PRE_SPLIT_SURFACE == {"is_event_table", "table_write_boundary"}


def test_facade_reexports_are_the_extracted_modules_objects():
    from src.common import ducklake_named_reads as nr
    from src.common import ducklake_write_verbs as wv

    assert schema.NAMED_READS is nr.NAMED_READS and schema.NamedRead is nr.NamedRead
    assert schema.describe_named_reads is nr.describe_named_reads
    assert schema.VERB_REGISTRY is wv.VERB_REGISTRY and schema.WriteVerb is wv.WriteVerb
    assert schema.describe_write_verbs is wv.describe_write_verbs


# ---------------------------------------------------------------------------
# tests for the schema constants CATALOG_ALIAS and the SMOKE_* tables
# ---------------------------------------------------------------------------


def test_constants_present():
    assert schema.CATALOG_ALIAS == "ops_catalog"
    assert schema.SMOKE_HISTORY_TABLE == "ducklake_smoke_history"
    assert schema.SMOKE_CURRENT_TABLE == "ducklake_smoke_current"


# ---------------------------------------------------------------------------
# _order_columns
# ---------------------------------------------------------------------------


def test_order_columns_ulid_lead_timestamps_tail():
    ordered = schema._order_columns(_SEMANTICS["fields"], "rec_id")
    names = [c for c, _ in ordered]
    assert names[0] == "ulid"
    assert names[1] == "rec_id"  # merge key first among inputs
    assert names[-2:] == ["created_timestamp", "last_updated_timestamp"]


def test_order_columns_other_inputs_middle():
    ordered = schema._order_columns(_SEMANTICS["fields"], "rec_id")
    names = [c for c, _ in ordered]
    assert "payload" in names
    payload_idx = names.index("payload")
    assert payload_idx > 1  # after ulid + merge key
    assert payload_idx < len(names) - 2  # before timestamps


def test_order_columns_keeps_writer_derived_columns():
    """A non-envelope role: derived column is physical (after the inputs, before the envelope tail) and
    schema_gate still refuses it from a caller; every current non-event registry entry orders exactly
    ulid, merge key, inputs, envelope tail, as before."""
    fields = {
        **_SEMANTICS["fields"],
        "purpose": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
    }
    names = [c for c, _ in schema._order_columns(fields, "rec_id")]
    assert names == ["ulid", "rec_id", "payload", "purpose", "created_timestamp", "last_updated_timestamp"]
    with pytest.raises(schema.SchemaGateError, match="derived"):
        schema.schema_gate({"rec_id": "x", "purpose": "drill"}, {**_SEMANTICS, "fields": fields})
    envelope = {"ulid", "created_timestamp", "last_updated_timestamp"}
    semantics = schema.load_field_semantics()
    for table, entry in semantics["ops_tables"].items():
        if schema.is_event_table(table, semantics) or entry.get("write_mode") == "control":
            continue
        columns = entry["columns"]
        derived_outside = [
            c for c, s in columns.items() if s["role"] == "derived" and c not in envelope and c != entry["merge_key"]
        ]
        ordered = [c for c, _ in schema.resolve_table_spec(table, semantics).ordered_columns]
        assert (
            ordered[0] == "ulid"
            and ordered[1] == entry["merge_key"]
            and ordered[-2:] == ["created_timestamp", "last_updated_timestamp"]
        )
        assert [c for c in ordered if c in derived_outside] == derived_outside


# ---------------------------------------------------------------------------
# tests for resolve_table_spec on smoke (None) and on ops_recommendations
# ---------------------------------------------------------------------------


def test_resolve_smoke_spec():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    assert spec.table is None
    assert spec.history_table == schema.SMOKE_HISTORY_TABLE
    assert spec.current_table == schema.SMOKE_CURRENT_TABLE
    assert spec.merge_key == "rec_id"
    cols = [c for c, _ in spec.ordered_columns]
    assert cols == ["ulid", "rec_id", "payload", "created_timestamp", "last_updated_timestamp"]


def test_resolve_ops_recommendations():
    spec = schema.resolve_table_spec("ops_recommendations")
    assert spec.merge_key == "id"
    cols = [c for c, _ in spec.ordered_columns]
    assert cols[0] == "ulid"
    assert cols[1] == "id"
    assert cols[-2:] == ["created_timestamp", "last_updated_timestamp"]
    assert "bucket(8, id)" == spec.partition_current


def test_resolve_unknown_table_raises():
    with pytest.raises(schema.SchemaGateError, match="unknown ops table"):
        schema.resolve_table_spec("ops_does_not_exist")


def test_resolve_table_spec_rejects_control_class_table():
    """resolve_table_spec directed-raises for a control-class table (T2.26) -- never widened to
    represent one; ducklake_control_tables.resolve_control_spec is the real resolution path."""
    with pytest.raises(schema.SchemaGateError, match="ducklake_control_tables"):
        schema.resolve_table_spec("ops_entity_counters")


# ---------------------------------------------------------------------------
# _column_ddl
# ---------------------------------------------------------------------------


def test_column_ddl_not_null_and_nullable():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    ddl = schema._column_ddl(spec)
    assert "ulid VARCHAR NOT NULL" in ddl
    assert "rec_id VARCHAR NOT NULL" in ddl
    assert "payload VARCHAR" in ddl
    assert "payload VARCHAR NOT NULL" not in ddl  # nullable column has no NOT NULL


# ---------------------------------------------------------------------------
# _build_merge_history_sql
# ---------------------------------------------------------------------------


def test_build_merge_history_sql_structure():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    sql = schema._build_merge_history_sql(spec)
    assert f"MERGE INTO {schema.CATALOG_ALIAS}.{schema.SMOKE_HISTORY_TABLE} AS t" in sql
    assert "ON t.ulid = s.ulid" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    col_count = len(spec.ordered_columns)
    assert sql.count("?") == col_count  # one placeholder per column


def test_build_merge_history_sql_ops_recommendations():
    spec = schema.resolve_table_spec("ops_recommendations")
    sql = schema._build_merge_history_sql(spec)
    assert "ops_recommendations_history" in sql
    assert "ON t.ulid = s.ulid" in sql


# ---------------------------------------------------------------------------
# _build_merge_current_sql
# ---------------------------------------------------------------------------


def test_build_merge_current_sql_structure():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    sql = schema._build_merge_current_sql(spec)
    assert f"MERGE INTO {schema.CATALOG_ALIAS}.{schema.SMOKE_CURRENT_TABLE} AS t" in sql
    assert "ON t.rec_id = s.rec_id" in sql
    assert "WHEN MATCHED THEN UPDATE SET" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    # created_timestamp must NOT appear in UPDATE SET (carried, never re-stamped)
    update_portion = sql.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
    assert "created_timestamp = s.created_timestamp" not in update_portion
    assert "rec_id = s.rec_id" not in update_portion  # merge key also not in UPDATE SET


def test_build_merge_current_sql_ops_recommendations():
    spec = schema.resolve_table_spec("ops_recommendations")
    sql = schema._build_merge_current_sql(spec)
    assert "ops_recommendations_current" in sql
    assert "ON t.id = s.id" in sql


# ---------------------------------------------------------------------------
# _build_select_existing_created_sql
# ---------------------------------------------------------------------------


def test_build_select_existing_created_sql():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    sql = schema._build_select_existing_created_sql(spec)
    assert f"SELECT created_timestamp FROM {schema.CATALOG_ALIAS}.{schema.SMOKE_CURRENT_TABLE}" in sql
    assert "WHERE rec_id = ?" in sql
    assert "status" not in sql


def test_build_select_existing_created_sql_include_status():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    sql = schema._build_select_existing_created_sql(spec, include_status=True)
    assert sql.startswith("SELECT created_timestamp, status FROM")
    assert "WHERE rec_id = ?" in sql


# ---------------------------------------------------------------------------
# _write_params
# ---------------------------------------------------------------------------


def test_write_params_ordering():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    identity = schema.WriteIdentity(ulid="01TESTULID12345678901234", timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc))
    created_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record = {"rec_id": "rec-42", "payload": "hello"}
    params = schema._write_params(spec, record, identity, created_ts)
    names = [c for c, _ in spec.ordered_columns]
    assert params[names.index("ulid")] == identity.ulid
    assert params[names.index("rec_id")] == "rec-42"
    assert params[names.index("payload")] == "hello"
    assert params[names.index("created_timestamp")] == created_ts
    assert params[names.index("last_updated_timestamp")] == identity.timestamp


# ---------------------------------------------------------------------------
# schema_gate
# ---------------------------------------------------------------------------


def test_schema_gate_accepts_valid_smoke_record():
    schema.schema_gate({"rec_id": "rec-1", "payload": "x"}, _SEMANTICS)
    schema.schema_gate({"rec_id": "rec-1"}, _SEMANTICS)  # payload nullable


def test_schema_gate_raises_unknown_field():
    with pytest.raises(schema.SchemaGateError, match="unknown field"):
        schema.schema_gate({"rec_id": "rec-1", "bogus": "x"}, _SEMANTICS)


def test_schema_gate_raises_derived_field():
    with pytest.raises(schema.SchemaGateError, match="derived"):
        schema.schema_gate({"rec_id": "rec-1", "ulid": "01ABC"}, _SEMANTICS)


def test_schema_gate_raises_missing_required():
    with pytest.raises(schema.SchemaGateError, match="missing or null"):
        schema.schema_gate({"payload": "x"}, _SEMANTICS)


def test_schema_gate_raises_mistyped_field():
    with pytest.raises(schema.SchemaGateError, match="expected str"):
        schema.schema_gate({"rec_id": 123}, _SEMANTICS)


def test_schema_gate_raises_empty_required():
    with pytest.raises(schema.SchemaGateError, match="empty"):
        schema.schema_gate({"rec_id": ""}, _SEMANTICS)


def test_schema_gate_ops_table():
    schema.schema_gate(
        {"id": "rec-1", "title": "a valid title", "status": "open", "automatable": True}, table="ops_recommendations"
    )


def test_schema_gate_ops_rejects_mistyped_bool():
    with pytest.raises(schema.SchemaGateError, match="expected bool"):
        schema.schema_gate(
            {"id": "rec-1", "title": "a valid title", "status": "open", "automatable": "yes"}, table="ops_recommendations"
        )


# ---------------------------------------------------------------------------
# _PY_TYPE_FOR_SQL coverage
# ---------------------------------------------------------------------------


def test_py_type_map_covers_ops_types():
    assert schema._PY_TYPE_FOR_SQL["BIGINT"] is int
    assert schema._PY_TYPE_FOR_SQL["BOOLEAN"] is bool
    assert schema._PY_TYPE_FOR_SQL["VARCHAR[]"] is list
    assert schema._PY_TYPE_FOR_SQL["BIGINT[]"] is list


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_exception_hierarchy():
    assert issubclass(schema.SchemaGateError, schema.DuckLakeRuntimeError)
    assert issubclass(schema.ReferentialError, schema.DuckLakeRuntimeError)
    assert issubclass(schema.AppendOnlyUpdateError, schema.DuckLakeRuntimeError)
    assert issubclass(schema.DuckLakeRuntimeError, RuntimeError)


# ---------------------------------------------------------------------------
# WriteIdentity / WriteResult dataclasses
# ---------------------------------------------------------------------------


def test_write_identity_frozen():
    ts = datetime(2026, 6, 8, tzinfo=timezone.utc)
    wid = schema.WriteIdentity(ulid="01TESTULID12345678901234", timestamp=ts)
    assert wid.ulid == "01TESTULID12345678901234"
    assert wid.timestamp == ts
    with pytest.raises((AttributeError, TypeError)):
        wid.ulid = "changed"  # type: ignore[misc]


def test_write_result_frozen():
    ts = datetime(2026, 6, 8, tzinfo=timezone.utc)
    wr = schema.WriteResult(
        ulid="01U", rec_id="rec-1", occ_retries=0, commit_ms=5.0, created_timestamp=ts, last_updated_timestamp=ts
    )
    assert wr.rec_id == "rec-1"
    with pytest.raises((AttributeError, TypeError)):
        wr.occ_retries = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_field_semantics / _field_semantics_path
# ---------------------------------------------------------------------------


def test_load_field_semantics_from_file(tmp_path):
    p = tmp_path / "fs.yaml"
    p.write_text("fields:\n  rec_id:\n    role: input\n", encoding="utf-8")
    out = schema.load_field_semantics(p)
    assert out["fields"]["rec_id"]["role"] == "input"


def test_field_semantics_path_env_override(monkeypatch):
    monkeypatch.setenv(schema._FIELD_SEMANTICS_ENV, "/custom/fs.yaml")
    assert str(schema._field_semantics_path()) == "/custom/fs.yaml"


def test_field_semantics_path_default(monkeypatch):
    monkeypatch.delenv(schema._FIELD_SEMANTICS_ENV, raising=False)
    assert schema._field_semantics_path() == schema._DEFAULT_FIELD_SEMANTICS_PATH


def test_load_real_contract(monkeypatch):
    monkeypatch.delenv(schema._FIELD_SEMANTICS_ENV, raising=False)
    out = schema.load_field_semantics()
    assert "ulid" in out["fields"]
    assert out["fields"]["ulid"]["role"] == "derived"


# ---------------------------------------------------------------------------
# ops_table_names
# ---------------------------------------------------------------------------


def test_ops_table_names_includes_recommendations():
    names = schema.ops_table_names()
    assert "ops_recommendations" in names
    assert "ops_decisions" in names
    assert len(names) >= 2


def test_ops_table_names_excludes_retired_ops_session_log():
    """PLAN-t2-26-retire-ops-session-log (T2.26): the retired table is gone from the registry."""
    names = schema.ops_table_names()
    assert "ops_session_log" not in names


def test_resolve_table_spec_raises_for_retired_ops_session_log():
    """PLAN-t2-26-retire-ops-session-log (T2.26): resolve_table_spec refuses the retired table."""
    with pytest.raises(schema.SchemaGateError, match="unknown ops table"):
        schema.resolve_table_spec("ops_session_log")


# ---------------------------------------------------------------------------
# SQL byte-identity check: schema builders vs runtime re-exports (VP1)
# ---------------------------------------------------------------------------


def test_smoke_merge_history_sql_byte_identical_via_reexport():
    """SQL generated via schema module matches SQL from runtime re-export (byte-identical)."""
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    sql_from_schema = schema._build_merge_history_sql(spec)
    sql_from_runtime = rt._build_merge_history_sql(spec)
    assert sql_from_schema == sql_from_runtime


def test_smoke_merge_current_sql_byte_identical_via_reexport():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    sql_from_schema = schema._build_merge_current_sql(spec)
    sql_from_runtime = rt._build_merge_current_sql(spec)
    assert sql_from_schema == sql_from_runtime


def test_ops_merge_history_sql_byte_identical_via_reexport():
    """ops_recommendations merge SQL from schema == from runtime re-export."""
    spec = schema.resolve_table_spec("ops_recommendations")
    sql_from_schema = schema._build_merge_history_sql(spec)
    sql_from_runtime = rt._build_merge_history_sql(spec)
    assert sql_from_schema == sql_from_runtime


def test_ops_merge_current_sql_byte_identical_via_reexport():
    spec = schema.resolve_table_spec("ops_recommendations")
    sql_from_schema = schema._build_merge_current_sql(spec)
    sql_from_runtime = rt._build_merge_current_sql(spec)
    assert sql_from_schema == sql_from_runtime


# ---------------------------------------------------------------------------
# append_only write mode -- schema-layer tests
# ---------------------------------------------------------------------------

_APPEND_ONLY_SEMANTICS: dict = {
    "fields": {
        "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
        "rec_id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
        "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
        "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
    },
    "ops_tables": {
        "ops_smoke_events": {
            "write_mode": "append_only",
            "status": "smoke",
            "merge_key": "event_id",
            "history_table": "ops_smoke_events_history",
            "partition": {"history": "year(created_timestamp), month(created_timestamp), day(created_timestamp)"},
            "columns": {
                "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "event_id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
                "event_type": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
                "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
                "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
            },
        }
    },
}


def test_resolve_table_spec_append_only_current_table_none():
    """append_only spec resolves with current_table=None and write_mode='append_only'."""
    spec = schema.resolve_table_spec("ops_smoke_events", _APPEND_ONLY_SEMANTICS)
    assert spec.write_mode == "append_only"
    assert spec.current_table is None
    assert spec.history_table == "ops_smoke_events_history"
    assert spec.merge_key == "event_id"


def test_check_append_only_guard_raises_on_require_exists():
    """check_append_only_guard raises AppendOnlyUpdateError for append_only + require_exists=True."""
    spec = schema.resolve_table_spec("ops_smoke_events", _APPEND_ONLY_SEMANTICS)
    with pytest.raises(schema.AppendOnlyUpdateError, match="append_only"):
        schema.check_append_only_guard(spec, require_exists=True)


def test_check_append_only_guard_noop_for_scd2():
    """check_append_only_guard is a no-op for scd2 tables even with require_exists=True."""
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    assert spec.write_mode == "scd2"
    schema.check_append_only_guard(spec, require_exists=True)  # no raise


def test_check_append_only_guard_noop_when_require_exists_false():
    """check_append_only_guard is a no-op for append_only + require_exists=False."""
    spec = schema.resolve_table_spec("ops_smoke_events", _APPEND_ONLY_SEMANTICS)
    schema.check_append_only_guard(spec, require_exists=False)  # no raise


def test_ops_smoke_events_in_real_ops_table_names():
    """ops_smoke_events appears in ops_table_names() once added to field_semantics.yaml."""
    names = schema.ops_table_names()
    assert "ops_smoke_events" in names


_SCD2_MISSING_CURRENT_SEMANTICS: dict = {
    "ops_tables": {
        "ops_widgets": {
            "merge_key": "id",
            "history_table": "ops_widgets_history",
            "current_table": "ops_widgets_current",
            "partition": {"history": "year(created_timestamp), month(created_timestamp), day(created_timestamp)"},
            "columns": {
                "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
                "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
                "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
            },
        }
    },
}

_SCD2_DAY_OF_MONTH_SEMANTICS: dict = {
    "ops_tables": {
        "ops_widgets": {
            "merge_key": "id",
            "history_table": "ops_widgets_history",
            "current_table": "ops_widgets_current",
            "partition": {"history": "day(created_timestamp)", "current": "bucket(8, id)"},
            "columns": {
                "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
                "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
                "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
            },
        }
    },
}


def test_resolve_table_spec_refuses_missing_or_non_calendar_partition():
    """resolve_table_spec raises SchemaGateError on a missing 'current' (scd2) or a day-of-month spec."""
    with pytest.raises(schema.SchemaGateError):
        schema.resolve_table_spec("ops_widgets", _SCD2_MISSING_CURRENT_SEMANTICS)
    with pytest.raises(schema.SchemaGateError):
        schema.resolve_table_spec("ops_widgets", _SCD2_DAY_OF_MONTH_SEMANTICS)
    # append_only resolves fine with no 'current' partition entry at all.
    spec = schema.resolve_table_spec("ops_smoke_events", _APPEND_ONLY_SEMANTICS)
    assert spec.partition_current is None


# ---------------------------------------------------------------------------
# Event-class entries and write_boundary (telemetry table registration)
# ---------------------------------------------------------------------------

_EVENT_SEMANTICS = {
    "ops_tables": {
        "evt": {
            "table_class": "event",
            "write_mode": "append_only",
            "write_boundary": "telemetry_append",
            "history_table": "evt",
        },
        "dim": {"write_boundary": "registration", "merge_key": "k", "id_scheme": "ulid"},
        "plain": {"merge_key": "k", "columns": {}, "history_table": "plain_h", "current_table": "plain_c", "partition": {}},
        "smoke_like": {"write_mode": "append_only", "merge_key": "k"},
    }
}


def test_resolve_table_spec_directs_event_tables_elsewhere():
    with pytest.raises(schema.SchemaGateError, match="event-class .*EventTableSpec.from_projection"):
        schema.resolve_table_spec("evt", _EVENT_SEMANTICS)
    real = schema.load_field_semantics()
    with pytest.raises(schema.SchemaGateError, match="table_class=event"):
        schema.resolve_table_spec("telemetry_sessions", real)


def test_table_write_boundary_reads_entry():
    assert schema.table_write_boundary("evt", _EVENT_SEMANTICS) == "telemetry_append"
    assert schema.table_write_boundary("dim", _EVENT_SEMANTICS) == "registration"
    assert schema.table_write_boundary("plain", _EVENT_SEMANTICS) is None
    assert schema.table_write_boundary("absent", _EVENT_SEMANTICS) is None
    assert schema.table_write_boundary(None) is None
    assert schema.table_write_boundary("ops_projects") == "registration"  # default: the real registry


def test_is_event_table_is_marker_based_not_write_mode_based():
    assert schema.is_event_table("evt", _EVENT_SEMANTICS)
    assert not schema.is_event_table("smoke_like", _EVENT_SEMANTICS)  # append_only with a merge key is not an event table
    assert not schema.is_event_table("absent", _EVENT_SEMANTICS)
    assert not schema.is_event_table(None)
    assert schema.is_event_table("telemetry_agents")


def test_scd_spec_id_scheme_defaults_to_serial_and_reads_ulid():
    assert schema.resolve_table_spec("ops_recommendations").id_scheme == "serial"  # null in the registry -> serial
    assert schema.resolve_table_spec("ops_tenants").id_scheme == "ulid"
    assert schema.resolve_table_spec(None).id_scheme == "serial"


# ---------------------------------------------------------------------------
# check_rec_status_transition / StatusTransitionError (T1.16 c3, Decision 103)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "existing,new",
    [
        ("failed", "open"),  # executor restart
        ("open", "superseded"),
        ("closed", "superseded"),
        ("failed", "declined"),
        ("open", "declined"),
        ("open", "closed"),
        ("closed", "closed"),  # same-status write
        ("banana", "open"),  # unrecognised vocab -- permissive skip
        ("open", "banana"),
    ],
)
def test_check_rec_status_transition_allows_live_edges(existing, new):
    schema.check_rec_status_transition("ops_recommendations", existing, new)  # must not raise


@pytest.mark.parametrize("existing", ["closed", "declined", "superseded"])
def test_check_rec_status_transition_rejects_resolved_reactivation(existing):
    with pytest.raises(schema.StatusTransitionError, match="illegal status transition"):
        schema.check_rec_status_transition("ops_recommendations", existing, "open")


def test_check_rec_status_transition_noop_for_undeclared_table():
    """A table absent from STATUS_TRANSITIONS has no DAG -- always a permissive no-op."""
    schema.check_rec_status_transition("ops_decisions", "closed", "open")  # must not raise


def test_status_transition_error_is_ducklake_runtime_error():
    assert issubclass(schema.StatusTransitionError, schema.DuckLakeRuntimeError)
