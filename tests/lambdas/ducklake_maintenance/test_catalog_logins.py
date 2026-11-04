"""MIRROR for src/lambdas/ducklake_maintenance/catalog_logins.py -- the closed owner role and login table (Decision 230).

Fast-tier text and table proofs of the four rendered login templates and the owner template. The real-engine proofs are in
tests/common/test_ducklake_catalog_owner_roles.py and test_ducklake_telemetry_writer_least_privilege.py.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import catalog_logins as cl

pytestmark = pytest.mark.unit

_DIR = Path(cl.__file__).parent
_OTHER_SCHEMAS = {rt.META_SCHEMA, rt.SMOKE_META_SCHEMA, rt.TELEMETRY_PROD_META_SCHEMA}


def _statements(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


def test_closed_table_names_exactly_four_logins_and_two_owner_roles():
    assert set(cl.LOGINS) == {
        "ducklake_telemetry_writer",
        "ducklake_telemetry_writer_prod",
        "ducklake_telemetry_reader",
        "ducklake_smoke_maintainer",
    }
    assert dict(cl.OWNER_ROLES) == {
        "ducklake_smoke": "ducklake_smoke_owner",
        "ducklake_telemetry": "ducklake_telemetry_owner",
    }
    assert set(cl.DATA_PATHS) == set(cl.OWNER_ROLES)
    assert cl.DATA_PATHS["ducklake_telemetry"] == "s3://agent-platform-data-lake/ducklake-telemetry/"
    secrets = [login.parameter_name for login in cl.LOGINS.values()]
    assert len(secrets) == len(set(secrets))
    for login in cl.LOGINS.values():
        assert login.name in login.parameter_name.replace("-", "_").replace("agent_platform_", "")
        assert login.owner_role == cl.OWNER_ROLES[login.schema]
    assert cl.LOGINS["ducklake_telemetry_writer"].connection_limit == 20
    assert cl.LOGINS["ducklake_telemetry_writer_prod"].connection_limit == 10
    assert cl.LOGINS["ducklake_telemetry_reader"].connection_limit == 10
    assert cl.LOGINS["ducklake_smoke_maintainer"].connection_limit >= 2


def test_every_login_renders_grants_on_its_own_schema_only():
    for login in cl.LOGINS.values():
        text = _statements(cl.render_login_sql(login.name))
        assert "{" not in text and "}" not in text
        for other in _OTHER_SCHEMAS - {login.schema}:
            assert not re.search(rf"SCHEMA {other}\b|\b{other}\.|'{other}'", text), (login.name, other)
        assert f"CONNECTION LIMIT {login.connection_limit}" in text
        if login.kind == "maintainer":
            continue
        assert f"USAGE ON SCHEMA {login.schema} TO {login.name}" in text
        assert not re.search(r"\bpublic\b", text)
        assert f"ALTER DEFAULT PRIVILEGES FOR ROLE {login.owner_role} IN SCHEMA {login.schema}" in text


def test_writer_template_grants_no_update_or_delete_on_catalog_structure():
    text = _statements(cl.render_login_sql("ducklake_telemetry_writer_prod"))
    grants = re.findall(r"GRANT ([A-Z, ]+) ON ducklake_telemetry\.(\w+) TO ducklake_telemetry_writer_prod;", text)
    by_table = {table: set(privs.replace(" ", "").split(",")) for privs, table in grants}
    for structural in ("ducklake_table", "ducklake_schema", "ducklake_column", "ducklake_snapshot"):
        assert by_table[structural] - {"SELECT", "INSERT"} == set() and "DELETE" not in by_table[structural]
    assert "UPDATE" not in by_table["ducklake_snapshot"] and by_table["ducklake_table"] == {"SELECT"}
    assert not any("DELETE" in privs for privs in by_table.values())
    assert {t for t, privs in by_table.items() if "INSERT" in privs} == {
        "ducklake_data_file",
        "ducklake_file_column_stats",
        "ducklake_file_partition_value",
        "ducklake_snapshot",
        "ducklake_snapshot_changes",
        "ducklake_table_column_stats",
        "ducklake_table_stats",
    }
    assert {t for t, privs in by_table.items() if "UPDATE" in privs} == {
        "ducklake_data_file",
        "ducklake_table_column_stats",
        "ducklake_table_stats",
    }
    assert "GRANT ALL" not in text and not re.search(r"GRANT [A-Z, ]*(DELETE|TRUNCATE)", text)
    assert "ON ALL TABLES IN SCHEMA ducklake_telemetry TO ducklake_telemetry_writer_prod" not in text.replace(
        "REVOKE ALL ON ALL TABLES IN SCHEMA ducklake_telemetry FROM", ""
    )


def test_writer_template_grants_inlined_data_tables_by_class():
    for name in ("ducklake_telemetry_writer", "ducklake_telemetry_writer_prod"):
        login = cl.LOGINS[name]
        text = _statements(cl.render_login_sql(name))
        assert "starts_with(c.relname, 'ducklake_inlined_data_')" in text
        assert f"n.nspname = '{login.schema}'" in text
        assert "GRANT SELECT ON %I.%I TO %I" in text
        assert (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA {login.schema} GRANT SELECT ON TABLES TO {name}" in text
        )
        assert f"ALTER DEFAULT PRIVILEGES FOR ROLE {login.owner_role} IN SCHEMA {login.schema} GRANT SELECT ON TABLES" in text
        assert (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_ops IN SCHEMA {login.schema} REVOKE ALL ON TABLES FROM {name}" in text
        )
        assert "ducklake_inlined_data_1" not in text


def test_reader_template_is_select_only_and_read_only():
    text = _statements(cl.render_login_sql("ducklake_telemetry_reader"))
    assert "SET default_transaction_read_only = on" in text and "statement_timeout = '100s'" in text
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA ducklake_telemetry TO ducklake_telemetry_reader" in text
    granted = re.findall(r"GRANT ([A-Z, ]+) ON", text)
    assert set(granted) == {"CONNECT", "USAGE", "SELECT"}
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE SCHEMA)\b", text.replace("CREATE ROLE", ""))
    assert "NOINHERIT" in text and "ducklake_smoke" not in text


def test_maintainer_template_joins_only_its_catalog_owner_role():
    text = _statements(cl.render_login_sql("ducklake_smoke_maintainer"))
    assert "GRANT ducklake_smoke_owner TO ducklake_smoke_maintainer WITH INHERIT TRUE, SET TRUE;" in text
    assert "ALTER ROLE ducklake_smoke_maintainer SET role = 'ducklake_smoke_owner';" in text
    assert len(re.findall(r"^GRANT \w+ TO ", text, re.MULTILINE)) == 1
    assert (
        "ducklake_telemetry" not in text and "SUPERUSER" not in text and "CREATEROLE" not in text.replace("NOCREATEROLE", "")
    )


def test_render_refuses_any_name_outside_the_closed_table():
    for bad in ("ducklake_ops", "x'; DROP ROLE y; --", "", None, 7):
        with pytest.raises(rt.DuckLakeRuntimeError):
            cl.render_login_sql(bad)
        with pytest.raises(rt.DuckLakeRuntimeError):
            cl.template_sha256(bad)
    for schema, mode in (("ducklake_ops", "adopt"), ("x'; --", "adopt"), ("ducklake_smoke", "drop"), ("ducklake_smoke", None)):
        with pytest.raises(rt.DuckLakeRuntimeError):
            cl.render_owner_sql(schema, mode)
    with pytest.raises(rt.DuckLakeRuntimeError, match="unsafe"):
        cl._guard("Not-An-Identifier", "login")


def test_owner_sql_creates_or_adopts_and_normalises_every_relation():
    for schema, role in cl.OWNER_ROLES.items():
        text = cl.render_owner_sql(schema, "adopt")
        assert f"CREATE ROLE {role} NOLOGIN" in text
        assert f"GRANT {role} TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;" in text
        assert "createrole_self_grant = 'set, inherit'" in text
        assert "CREATE SCHEMA %I AUTHORIZATION %I" in text
        assert "GRANT CREATE ON DATABASE %I TO %I" in text and "REVOKE CREATE ON DATABASE %I FROM %I" in text
        assert "ALTER SCHEMA %I OWNER TO %I" in text
        assert "c.relkind IN ('r', 'p', 'S', 'v', 'm')" in text and "c.relowner <>" in text
        assert "ALTER %s %I.%I OWNER TO %I" in text
        assert f"'{schema}'" in text and "section" not in text
        assert "DROP" not in text and "TRUNCATE" not in text


def test_owner_sql_release_section_returns_smoke_to_current_user_only():
    text = cl.render_owner_sql("ducklake_smoke", "release")
    assert "ALTER SCHEMA %I OWNER TO CURRENT_USER" in text and "OWNER TO CURRENT_USER" in text
    assert "CREATE ROLE" not in text and "CREATE SCHEMA" not in text and "DROP" not in text
    assert "'ducklake_smoke'" in text and "ducklake_telemetry" not in text
    assert cl.render_owner_sql("ducklake_smoke", "adopt") != text


def test_every_rendered_template_sets_a_local_lock_timeout():
    rendered = [cl.render_login_sql(name) for name in cl.LOGINS]
    rendered += [cl.render_owner_sql(schema, mode) for schema in cl.OWNER_ROLES for mode in cl.OWNER_MODES]
    for text in rendered:
        assert _statements(text).lstrip().startswith("SET LOCAL lock_timeout = '5s';")


def test_expected_scope_is_true_only_on_objects_the_preconditions_require():
    for login in cl.LOGINS.values():
        scope = dict(login.expected_scope)
        prefix = "smoke" if login.schema == "ducklake_smoke" else "telemetry"
        assert scope[f"{prefix}_usage"] is True
        other = "telemetry" if prefix == "smoke" else "smoke"
        assert (
            scope[f"{other}_usage"] is False and scope[f"{other}_create"] is False and scope[f"{other}_owner_member"] is False
        )
        assert scope["ops_usage"] is False and scope["ops_create"] is False
        assert scope["public_create"] is False and scope["database_create"] is False
        assert scope["connection_limit"] == login.connection_limit
        assert scope["session_user"] == login.name
        if login.kind != "maintainer":
            assert scope[f"{prefix}_create"] is False and scope[f"{prefix}_owner_member"] is False
        assert scope["read_only"] is (login.kind == "reader")
        assert scope["snapshot_insert"] is (login.kind != "reader")


def test_maintainer_expected_scope_names_the_owner_role_as_current_user():
    scope = cl.LOGINS["ducklake_smoke_maintainer"].expected_scope
    assert scope["current_user"] == "ducklake_smoke_owner" and scope["session_user"] == "ducklake_smoke_maintainer"
    assert scope["smoke_owner_member"] is True and scope["smoke_create"] is True
    for name in ("ducklake_telemetry_writer", "ducklake_telemetry_writer_prod", "ducklake_telemetry_reader"):
        assert cl.LOGINS[name].expected_scope["current_user"] == name
        assert cl.LOGINS[name].expected_scope["smoke_owner_member"] is False


def test_template_sha256_hashes_the_file_bytes():
    for name, login in cl.LOGINS.items():
        assert cl.template_sha256(name) == hashlib.sha256((_DIR / login.template).read_bytes()).hexdigest()
    assert cl.template_sha256("owner") == hashlib.sha256((_DIR / "ducklake_catalog_owner.sql").read_bytes()).hexdigest()
