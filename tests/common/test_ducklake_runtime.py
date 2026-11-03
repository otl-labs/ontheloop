"""MIRROR for src/common/ducklake_runtime.py -- the facade (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_runtime.py monolith: the genuine runtime-owned
surface (assert_duckdb_version, fetch_dsn/libpq_conninfo, open_connection -- dev INSTALL vs baked
LOAD, with the _patch_duckdb helper) plus the facade RE-EXPORT tests that exercise symbols owned
by ducklake_scd2_schema but surfaced through rt.* (field-semantics loading, schema_gate loud-fail,
resolve_table_spec / schema_gate-ops / _PY_TYPE_FOR_SQL / _build_merge_* / ops_table_names, and
the split-invariant byte-identical / re-export-identity / no-import-cycle tests). scd2_schema
keeps its own dedicated tests/test_ducklake_scd2_schema.py (not in scope here).
"""

from __future__ import annotations

import json
import types

# boto3 is imported at MODULE scope even though the tests reference it only via a LAZY
# `import boto3` inside test_fetch_dsn_success / test_fetch_dsn_missing_key_raises. This makes the
# file's heavy-dep requirement visible to the fast tier's cheap `--collect-only` pass so
# pr-validate defers it PROACTIVELY to the full post-merge tier, instead of catching it
# REACTIVELY. boto3 is deliberately excluded from requirements-fast.txt. See
# scripts/checks/_scaffolding.py::partition_changed_tests_by_collectability.
import boto3  # noqa: F401
import pytest

from src.common import ducklake_runtime as rt
from src.common import ducklake_scd2_schema as schema
from src.common.ducklake_version import pinned_duckdb_version
from tests.fixtures.ducklake_fakes import _SEMANTICS, FakeCon

pytestmark = pytest.mark.unit

_DSN = {
    "host": "ep-test-123.eu-west-2.aws.neon.tech",
    "dbname": "ducklake_ops",
    "username": "ducklake_ops",
    "password": "secret-pw",  # pragma: allowlist secret -- fake fixture value
    "sslmode": "require",
    "meta_schema": "ducklake_ops",
}


def test_assert_duckdb_version_match():
    pin = pinned_duckdb_version()
    fake = types.SimpleNamespace(__version__=pin)
    assert rt.assert_duckdb_version(fake) == pin


def test_assert_duckdb_version_mismatch_raises():
    fake = types.SimpleNamespace(__version__=pinned_duckdb_version() + "-mismatch")
    with pytest.raises(rt.VersionMismatchError, match="version mismatch"):
        rt.assert_duckdb_version(fake)


def test_assert_duckdb_version_resolves_default(monkeypatch):
    pin = pinned_duckdb_version()
    monkeypatch.setattr(rt.ducklake_spike, "_require_duckdb", lambda: types.SimpleNamespace(__version__=pin))
    assert rt.assert_duckdb_version() == pin


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
    out = rt.fetch_dsn(profile="agent_platform")
    assert out["host"] == _DSN["host"]
    assert captured["parameter_name"] == rt.DSN_PARAMETER_NAME
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
        rt.fetch_dsn()


def test_libpq_conninfo_explicit_and_default_sslmode():
    assert "sslmode=require" in rt.libpq_conninfo(_DSN)
    no_ssl = {k: v for k, v in _DSN.items() if k != "sslmode"}
    out = rt.libpq_conninfo(no_ssl)
    assert "sslmode=require" in out
    assert "host=ep-test-123.eu-west-2.aws.neon.tech" in out


def test_libpq_conninfo_default_connect_timeout(monkeypatch):
    monkeypatch.delenv("DUCKLAKE_CONNECT_TIMEOUT_S", raising=False)
    out = rt.libpq_conninfo(_DSN)
    assert "connect_timeout=10" in out


def test_libpq_conninfo_honours_connect_timeout_env(monkeypatch):
    monkeypatch.setenv("DUCKLAKE_CONNECT_TIMEOUT_S", "30")
    out = rt.libpq_conninfo(_DSN)
    assert "connect_timeout=30" in out


def _patch_duckdb(monkeypatch, con):
    fake_duckdb = types.SimpleNamespace(connect=lambda: con, __version__=pinned_duckdb_version())
    monkeypatch.setattr(rt.ducklake_spike, "_require_duckdb", lambda: fake_duckdb)
    monkeypatch.setattr(rt.ducklake_spike, "_set_s3_credentials", lambda c, profile=None: None)


def test_open_connection_dev_mode_installs(monkeypatch):
    con = FakeCon()
    _patch_duckdb(monkeypatch, con)
    out = rt.open_connection(dsn=_DSN, data_path="s3://x/y/")
    assert out is con
    sqls = [s for s, _ in con.executed]
    assert any("INSTALL ducklake" in s for s in sqls)
    assert any(s.startswith("ATTACH 'ducklake:postgres:") for s in sqls)
    assert any("META_SCHEMA 'ducklake_ops'" in s for s in sqls)
    assert any("ducklake_default_data_inlining_row_limit=0" in s for s in sqls)
    assert any(s == "SET threads=1" for s in sqls)


def test_open_connection_pins_utc_timezone(monkeypatch):
    """rec-4068: every DuckLake session is pinned to UTC, issued BEFORE the ATTACH (partition
    transforms evaluate in the connection's TimeZone, Decision 96)."""
    con = FakeCon()
    _patch_duckdb(monkeypatch, con)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/")
    sqls = [s for s, _ in con.executed]
    assert "SET TimeZone='UTC'" in sqls
    tz_index = sqls.index("SET TimeZone='UTC'")
    attach_index = next(i for i, s in enumerate(sqls) if s.startswith("ATTACH 'ducklake:postgres:"))
    assert tz_index < attach_index


def test_open_connection_meta_schema_param_relocates_smoke(monkeypatch):
    """Smoke attaches its OWN meta-schema (rec-2099): passing meta_schema overrides the ducklake_ops default."""
    con = FakeCon()
    _patch_duckdb(monkeypatch, con)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", meta_schema=rt.SMOKE_META_SCHEMA)
    sqls = [s for s, _ in con.executed]
    assert rt.SMOKE_META_SCHEMA == "ducklake_smoke"
    assert any("META_SCHEMA 'ducklake_smoke'" in s for s in sqls)
    assert not any("META_SCHEMA 'ducklake_ops'" in s for s in sqls)


def test_open_connection_baked_mode_failclosed(monkeypatch):
    con = FakeCon()
    _patch_duckdb(monkeypatch, con)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", extension_directory="/opt/duckdb_extensions")
    sqls = [s for s, _ in con.executed]
    assert any("extension_directory=" in s for s in sqls)
    assert any("autoinstall_known_extensions=false" in s for s in sqls)
    assert any("autoload_known_extensions=false" in s for s in sqls)
    assert any("custom_extension_repository=''" in s for s in sqls)
    assert any(s == "LOAD postgres" for s in sqls)
    assert not any("INSTALL" in s for s in sqls)  # fail-closed: no network INSTALL
    assert any(s == "SET threads=1" for s in sqls)  # vCPU-starvation fix applies on the baked path too


def test_open_connection_with_shared_creds(monkeypatch):
    con = FakeCon()
    fake_duckdb = types.SimpleNamespace(connect=lambda: con, __version__=pinned_duckdb_version())
    monkeypatch.setattr(rt.ducklake_spike, "_require_duckdb", lambda: fake_duckdb)
    creds = ("AKIA", "secret", "token", "eu-west-2")  # pragma: allowlist secret
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", _creds=creds)
    sqls = [s for s, _ in con.executed]
    assert any("s3_access_key_id=" in s for s in sqls)
    assert any("s3_session_token=" in s for s in sqls)


def test_open_connection_shared_creds_no_token(monkeypatch):
    con = FakeCon()
    fake_duckdb = types.SimpleNamespace(connect=lambda: con, __version__=pinned_duckdb_version())
    monkeypatch.setattr(rt.ducklake_spike, "_require_duckdb", lambda: fake_duckdb)
    creds = ("AKIA", "secret", None, "eu-west-2")  # pragma: allowlist secret
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", _creds=creds)
    sqls = [s for s, _ in con.executed]
    assert not any("s3_session_token=" in s for s in sqls)


def test_field_semantics_path_env_override(monkeypatch):
    monkeypatch.setenv(rt._FIELD_SEMANTICS_ENV, "/custom/fs.yaml")
    assert str(rt._field_semantics_path()) == "/custom/fs.yaml"


def test_field_semantics_path_default(monkeypatch):
    monkeypatch.delenv(rt._FIELD_SEMANTICS_ENV, raising=False)
    assert rt._field_semantics_path() == rt._DEFAULT_FIELD_SEMANTICS_PATH


def test_load_field_semantics_explicit_path(tmp_path):
    p = tmp_path / "fs.yaml"
    p.write_text("fields:\n  rec_id:\n    role: input\n", encoding="utf-8")
    out = rt.load_field_semantics(p)
    assert out["fields"]["rec_id"]["role"] == "input"


def test_load_field_semantics_real_contract(monkeypatch):
    monkeypatch.delenv(rt._FIELD_SEMANTICS_ENV, raising=False)
    out = rt.load_field_semantics()
    assert "ulid" in out["fields"]
    assert out["fields"]["ulid"]["role"] == "derived"


def test_schema_gate_accepts_valid_record():
    rt.schema_gate({"rec_id": "rec-1", "payload": "x"}, _SEMANTICS)
    rt.schema_gate({"rec_id": "rec-1"}, _SEMANTICS)  # payload nullable
    rt.schema_gate({"rec_id": "rec-1", "payload": None}, _SEMANTICS)


def test_schema_gate_raises_on_unknown_field():
    with pytest.raises(rt.SchemaGateError, match="unknown field"):
        rt.schema_gate({"rec_id": "rec-1", "bogus": "x"}, _SEMANTICS)


def test_schema_gate_raises_on_supplied_derived_field():
    with pytest.raises(rt.SchemaGateError, match="derived"):
        rt.schema_gate({"rec_id": "rec-1", "ulid": "01ABC"}, _SEMANTICS)


def test_schema_gate_raises_on_missing_required():
    with pytest.raises(rt.SchemaGateError, match="missing or null"):
        rt.schema_gate({"payload": "x"}, _SEMANTICS)


def test_schema_gate_raises_on_null_required():
    with pytest.raises(rt.SchemaGateError, match="missing or null"):
        rt.schema_gate({"rec_id": None, "payload": "x"}, _SEMANTICS)


def test_schema_gate_raises_on_mistyped_field():
    with pytest.raises(rt.SchemaGateError, match="expected str"):
        rt.schema_gate({"rec_id": 123}, _SEMANTICS)


def test_schema_gate_raises_on_empty_required():
    with pytest.raises(rt.SchemaGateError, match="empty"):
        rt.schema_gate({"rec_id": ""}, _SEMANTICS)


def test_schema_gate_loads_default_semantics(monkeypatch):
    monkeypatch.delenv(rt._FIELD_SEMANTICS_ENV, raising=False)
    rt.schema_gate({"rec_id": "rec-1", "payload": "x"})  # uses the real contract


def test_resolve_table_spec_smoke_is_backcompat():
    """table=None resolves the smoke pair with the historical column order (T2.17 back-compat)."""
    spec = rt.resolve_table_spec(None)
    assert spec.history_table == rt.SMOKE_HISTORY_TABLE
    assert spec.current_table == rt.SMOKE_CURRENT_TABLE
    assert spec.merge_key == "rec_id"
    assert [c for c, _ in spec.ordered_columns] == [
        "ulid",
        "rec_id",
        "payload",
        "created_timestamp",
        "last_updated_timestamp",
    ]


def test_resolve_table_spec_ops_recommendations():
    """ops_recommendations resolves merge_key=id, ulid-lead, timestamps-tail, id-bucket partition."""
    spec = rt.resolve_table_spec("ops_recommendations")
    assert spec.merge_key == "id"
    cols = [c for c, _ in spec.ordered_columns]
    assert cols[0] == "ulid"
    assert cols[1] == "id"  # merge key first among inputs
    assert cols[-2:] == ["created_timestamp", "last_updated_timestamp"]
    assert "bucket(8, id)" == spec.partition_current
    # array/int/bool columns are present with their real types
    assert spec.fields["dependencies"]["sql_type"] == "VARCHAR[]"
    assert spec.fields["automatable"]["sql_type"] == "BOOLEAN"
    assert spec.fields["execution_steps"]["sql_type"] == "BIGINT"


def test_resolve_table_spec_unknown_raises():
    with pytest.raises(rt.SchemaGateError, match="unknown ops table"):
        rt.resolve_table_spec("ops_not_a_table")


def test_py_type_map_extended_for_ops_columns():
    """The gate type map covers arrays/ints/booleans for the real ops columns (T2.19)."""
    assert rt._PY_TYPE_FOR_SQL["BIGINT"] is int
    assert rt._PY_TYPE_FOR_SQL["BOOLEAN"] is bool
    assert rt._PY_TYPE_FOR_SQL["VARCHAR[]"] is list
    assert rt._PY_TYPE_FOR_SQL["BIGINT[]"] is list


def test_schema_gate_ops_accepts_valid_record():
    """A valid ops_recommendations record (arrays, bool, int) passes the table-parameterized gate."""
    record = {
        "id": "rec-1",
        "status": "open",
        "title": "t",
        "automatable": True,
        "dependencies": ["rec-2", "rec-3"],
        "execution_steps": 4,
    }
    rt.schema_gate(record, table="ops_recommendations")  # no raise


def test_schema_gate_ops_rejects_unknown_field():
    with pytest.raises(rt.SchemaGateError, match="unknown field"):
        rt.schema_gate({"id": "rec-1", "status": "open", "bogus": "x"}, table="ops_recommendations")


def test_schema_gate_ops_rejects_derived_ulid():
    with pytest.raises(rt.SchemaGateError, match="derived"):
        rt.schema_gate({"id": "rec-1", "status": "open", "ulid": "01XYZ"}, table="ops_recommendations")


def test_schema_gate_ops_requires_merge_key_and_status():
    with pytest.raises(rt.SchemaGateError, match="missing or null"):
        rt.schema_gate({"status": "open"}, table="ops_recommendations")  # id missing


def test_schema_gate_ops_rejects_mistyped_bool():
    with pytest.raises(rt.SchemaGateError, match="expected bool"):
        rt.schema_gate(
            {"id": "rec-1", "title": "a valid title", "status": "open", "automatable": "yes"}, table="ops_recommendations"
        )


def test_schema_gate_ops_rejects_mistyped_array():
    with pytest.raises(rt.SchemaGateError, match="expected list"):
        rt.schema_gate(
            {"id": "rec-1", "title": "a valid title", "status": "open", "tags": "not-a-list"}, table="ops_recommendations"
        )


def test_build_merge_sql_ops_recommendations_uses_id_key():
    """The generated MERGE SQL targets the ops tables and keys current on id (not rec_id)."""
    spec = rt.resolve_table_spec("ops_recommendations")
    hist_sql = rt._build_merge_history_sql(spec)
    curr_sql = rt._build_merge_current_sql(spec)
    assert "ops_recommendations_history" in hist_sql
    assert "ON t.ulid = s.ulid" in hist_sql
    assert "ops_recommendations_current" in curr_sql
    assert "ON t.id = s.id" in curr_sql
    # created_timestamp is carried, never in the UPDATE SET; id (merge key) is not updated either.
    assert "created_timestamp = s.created_timestamp" not in curr_sql
    assert "id = s.id" not in curr_sql.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]


def test_ops_table_names_lists_every_registered_table():
    """T2.26: ops_entity_counters (control class) joins the registry -- refused at both
    boundaries via resolve_table_spec's directed raise, never removed from ops_table_names().
    ops_session_log was retired (CD.40). Membership and uniqueness, never an exact count: the
    registry grows (telemetry event tables, ops_tenants, ops_projects)."""
    names = rt.ops_table_names()
    assert "ops_recommendations" in names
    assert "ops_decisions" in names
    assert "ops_priority_queue" in names
    assert "ops_smoke_events" in names
    assert "ops_entity_counters" in names
    for registered in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
        assert registered in names
    assert "ops_tenants" in names and "ops_projects" in names
    assert "ops_session_log" not in names
    assert len(names) == len(set(names))


def test_runtime_reexports_registry_helpers():
    """Both handlers reach the schema layer only through `rt`: the helpers and read_version are the
    defining modules' own objects."""
    from src.common import ducklake_named_reads

    assert rt.is_event_table is schema.is_event_table
    assert rt.table_write_boundary is schema.table_write_boundary
    assert rt.read_version is ducklake_named_reads.read_version


def test_split_smoke_merge_history_sql_byte_identical():
    """After the schema split, runtime re-export produces byte-identical SQL to schema module."""
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    assert rt._build_merge_history_sql(spec) == schema._build_merge_history_sql(spec)


def test_split_smoke_merge_current_sql_byte_identical():
    spec = schema.resolve_table_spec(None, _SEMANTICS)
    assert rt._build_merge_current_sql(spec) == schema._build_merge_current_sql(spec)


def test_split_ops_recommendations_merge_history_sql_byte_identical():
    """ops_recommendations merge history SQL identical across the split."""
    spec = rt.resolve_table_spec("ops_recommendations")
    assert rt._build_merge_history_sql(spec) == schema._build_merge_history_sql(spec)


def test_split_ops_recommendations_merge_current_sql_byte_identical():
    spec = rt.resolve_table_spec("ops_recommendations")
    assert rt._build_merge_current_sql(spec) == schema._build_merge_current_sql(spec)


def test_split_schema_gate_is_re_exported():
    """schema_gate on rt is the same function object from the schema module."""
    assert rt.schema_gate is schema.schema_gate


def test_split_no_import_cycle():
    """Importing both modules in the same process must not raise (no circular import)."""
    import importlib

    importlib.import_module("src.common.ducklake_scd2_schema")
    importlib.import_module("src.common.ducklake_runtime")


# ---------------------------------------------------------------------------
# Named warm-connection slots (rec-4291): the reader's telemetry catalog is a second, isolated warm connection.
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_slots():
    rt.reset_warm_connection()
    yield
    rt.reset_warm_connection()


def _slot_con(alive: bool = True):
    from unittest.mock import MagicMock  # noqa: PLC0415

    con = MagicMock(name="slot_con")
    if not alive:
        con.execute.side_effect = Exception("connection already closed")
    return con


def test_named_slots_are_independent(_clean_slots):
    ops, telemetry = _slot_con(), _slot_con()
    first, _ = rt.get_warm_connection(opener=lambda: ops)
    second, meta = rt.get_warm_connection(opener=lambda: telemetry, slot="telemetry")
    assert (first, second) == (ops, telemetry) and meta["reused"] is False
    assert rt.get_warm_connection(opener=lambda: _slot_con())[0] is ops
    assert rt.get_warm_connection(opener=lambda: _slot_con(), slot="telemetry")[0] is telemetry
    ops.close.assert_not_called()
    telemetry.close.assert_not_called()


def test_dead_slot_reopens_without_evicting_the_other(_clean_slots):
    ops, dead, fresh = _slot_con(), _slot_con(alive=False), _slot_con()
    rt.get_warm_connection(opener=lambda: ops)
    rt.get_warm_connection(opener=lambda: dead, slot="telemetry")
    con, meta = rt.get_warm_connection(opener=lambda: fresh, slot="telemetry")
    assert con is fresh and meta["reopened"] is True
    dead.close.assert_called_once()
    assert rt.get_warm_connection(opener=lambda: _slot_con())[0] is ops
    ops.close.assert_not_called()
    again, meta = rt.get_warm_connection(opener=lambda: _slot_con(), slot="telemetry", force_reopen=True)
    assert meta["reopened"] is True and again is not fresh
    assert rt.get_warm_connection(opener=lambda: _slot_con())[0] is ops


def test_reset_drops_one_slot_or_all(_clean_slots):
    ops, telemetry = _slot_con(), _slot_con()
    rt.get_warm_connection(opener=lambda: ops)
    rt.get_warm_connection(opener=lambda: telemetry, slot="telemetry")
    rt.reset_warm_connection("telemetry")
    telemetry.close.assert_called_once()
    ops.close.assert_not_called()
    assert rt._named_slots["telemetry"] == {}
    assert rt._warm_connection["con"] is ops
    rt.get_warm_connection(opener=lambda: _slot_con(), slot="telemetry")
    rt.reset_warm_connection()
    ops.close.assert_called_once()
    assert rt._warm_connection == {} and rt._named_slots["telemetry"] == {}
    broken = _slot_con()
    broken.close.side_effect = Exception("already gone")
    rt.get_warm_connection(opener=lambda: broken, slot="telemetry")
    rt.reset_warm_connection()


def test_open_connection_attaches_under_the_given_alias(monkeypatch):
    con = FakeCon()
    _patch_duckdb(monkeypatch, con)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", alias=rt.TELEMETRY_CATALOG_ALIAS)
    attach = next(s for s, _ in con.executed if s.startswith("ATTACH 'ducklake:postgres:"))
    assert f" AS {rt.TELEMETRY_CATALOG_ALIAS} (" in attach
    default = FakeCon()
    _patch_duckdb(monkeypatch, default)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/")
    assert any(f" AS {rt.CATALOG_ALIAS} (" in s for s, _ in default.executed)
    opened: list[str] = []
    monkeypatch.setattr(rt, "open_connection", lambda **kw: opened.append(kw["alias"]) or _slot_con())
    rt.get_warm_connection(dsn=_DSN, slot="telemetry", alias=rt.TELEMETRY_CATALOG_ALIAS)
    assert opened == [rt.TELEMETRY_CATALOG_ALIAS]
    rt.reset_warm_connection()


def test_ops_slot_storage_is_unchanged(_clean_slots):
    """The ops slot keeps the module-level dict with con/key entries that test_ducklake_runtime_warm_connection.py reads."""
    con = _slot_con()
    rt.get_warm_connection(opener=lambda: con, data_path="s3://x/", meta_schema="m", extension_directory="/e")
    assert rt._warm_connection["con"] is con
    assert rt._warm_connection["key"] == ("s3://x/", "m", "/e")
    assert "ops" not in rt._named_slots


def test_open_connection_read_only_adds_the_attach_option(monkeypatch):
    plain = FakeCon()
    _patch_duckdb(monkeypatch, plain)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", meta_schema="ducklake_telemetry")
    plain_attach = next(s for s, _ in plain.executed if s.startswith("ATTACH 'ducklake:postgres:"))
    assert plain_attach.endswith("(DATA_PATH 's3://x/y/', META_SCHEMA 'ducklake_telemetry')")
    read_only = FakeCon()
    _patch_duckdb(monkeypatch, read_only)
    rt.open_connection(dsn=_DSN, data_path="s3://x/y/", meta_schema="ducklake_telemetry", read_only=True)
    attach = next(s for s, _ in read_only.executed if s.startswith("ATTACH 'ducklake:postgres:"))
    assert attach.endswith("(DATA_PATH 's3://x/y/', META_SCHEMA 'ducklake_telemetry', READ_ONLY)")
    assert rt.TELEMETRY_PROD_META_SCHEMA == "ducklake_telemetry"
    assert rt.TELEMETRY_PROD_DATA_PATH == "s3://agent-platform-data-lake/ducklake-telemetry/"
    assert not rt.TELEMETRY_PROD_DATA_PATH.startswith("s3://agent-platform-data-lake/ducklake/")
