"""The one closed table behind the catalog owner roles and scoped logins (Decision 230 cl.2, cl.3, cl.4).

Every name a catalog verb uses comes from the tables here: no verb takes caller SQL, a role name or a secret name. The SQL
templates bundled beside this file are formatted ONLY with values from these tables, after an identifier guard, so a name
outside the closed tables raises before any SQL is built. Pure: it reads the bundled template files and does no other I/O.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.common import ducklake_runtime as rt

OWNER_ROLES: Mapping[str, str] = MappingProxyType(
    {rt.SMOKE_META_SCHEMA: "ducklake_smoke_owner", rt.TELEMETRY_PROD_META_SCHEMA: "ducklake_telemetry_owner"}
)
DATA_PATHS: Mapping[str, str] = MappingProxyType(
    {rt.SMOKE_META_SCHEMA: rt.SMOKE_DATA_PATH, rt.TELEMETRY_PROD_META_SCHEMA: rt.TELEMETRY_PROD_DATA_PATH}
)
OWNER_MODES = ("adopt", "release")

_HERE = Path(__file__).parent
_OWNER_TEMPLATE = "ducklake_catalog_owner.sql"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SECTION = re.compile(r"^-- section: (\w+)$", re.MULTILINE)

_SCOPE_FALSE = {
    "ops_usage": False,
    "ops_create": False,
    "smoke_usage": False,
    "smoke_create": False,
    "telemetry_usage": False,
    "telemetry_create": False,
    "public_create": False,
    "database_create": False,
    "smoke_owner_member": False,
    "telemetry_owner_member": False,
    "read_only": False,
    "snapshot_insert": False,
}


@dataclass(frozen=True)
class Login:
    name: str
    parameter_name: str
    template: str
    schema: str
    owner_role: str
    connection_limit: int
    kind: str
    expected_scope: Mapping[str, Any]


def _scope(login: str, schema: str, **true_entries: Any) -> Mapping[str, Any]:
    scope: dict[str, Any] = {**_SCOPE_FALSE, "current_user": login, "session_user": login}
    scope.update({f"{'smoke' if schema == rt.SMOKE_META_SCHEMA else 'telemetry'}_usage": True})
    scope.update(true_entries)
    return MappingProxyType(scope)


def _login(name: str, parameter_name: str, template: str, schema: str, limit: int, kind: str, **scope_entries: Any) -> Login:
    return Login(
        name, parameter_name, template, schema, OWNER_ROLES[schema], limit, kind, _scope(name, schema, **scope_entries)
    )


_WRITER = "ducklake_telemetry_writer_role.sql"
_READER = "ducklake_telemetry_reader_role.sql"
_MAINTAINER = "ducklake_catalog_maintainer_role.sql"

# Limits: the smoke writer serves the writer AND its smoke twin while both bind smoke (2 x (5 + 5) reserved concurrency); the
# production writer and the reader are 2 x their reserved concurrency of 5; the maintainer's is sized from the measured peak
# sessions of one invocation of each smoke action (test_maintainer_peak_sessions_across_the_five_smoke_actions).
LOGINS: Mapping[str, Login] = MappingProxyType(
    {
        login.name: login
        for login in (
            _login(
                "ducklake_telemetry_writer",
                "/secret/agent-platform-ducklake-telemetry-writer-dsn",
                _WRITER,
                rt.SMOKE_META_SCHEMA,
                20,
                "writer",
                connection_limit=20,
                snapshot_insert=True,
            ),
            _login(
                "ducklake_telemetry_writer_prod",
                "/secret/agent-platform-ducklake-telemetry-writer-prod-dsn",
                _WRITER,
                rt.TELEMETRY_PROD_META_SCHEMA,
                10,
                "writer",
                connection_limit=10,
                snapshot_insert=True,
            ),
            _login(
                "ducklake_telemetry_reader",
                "/secret/agent-platform-ducklake-telemetry-reader-dsn",
                _READER,
                rt.TELEMETRY_PROD_META_SCHEMA,
                10,
                "reader",
                connection_limit=10,
                read_only=True,
            ),
            _login(
                "ducklake_smoke_maintainer",
                "/secret/agent-platform-ducklake-smoke-maintainer-dsn",
                _MAINTAINER,
                rt.SMOKE_META_SCHEMA,
                4,
                "maintainer",
                connection_limit=4,
                smoke_create=True,
                smoke_owner_member=True,
                snapshot_insert=True,
                current_user="ducklake_smoke_owner",
            ),
        )
    }
)


def _guard(value: str, kind: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        raise rt.DuckLakeRuntimeError(f"unsafe {kind} refused before any SQL is built")
    return value


def _read(template: str) -> bytes:
    return (_HERE / template).read_bytes()


def render_login_sql(name: str) -> str:
    """The login's template with only its own table values formatted in; any other name raises before any SQL is built."""
    login = LOGINS.get(name) if isinstance(name, str) else None
    if login is None:
        raise rt.DuckLakeRuntimeError("not a login in the closed catalog login table")
    return (
        _read(login.template)
        .decode("utf-8")
        .format(
            login=_guard(login.name, "login"),
            schema=_guard(login.schema, "schema"),
            owner_role=_guard(login.owner_role, "owner role"),
            connection_limit=int(login.connection_limit),
        )
    )


def render_owner_sql(meta_schema: str, mode: str) -> str:
    """The owner template's section for *mode*, formatted for *meta_schema* and its owner role (closed tables only)."""
    owner_role = OWNER_ROLES.get(meta_schema) if isinstance(meta_schema, str) else None
    if owner_role is None:
        raise rt.DuckLakeRuntimeError("not a catalog in the closed owner role table")
    if mode not in OWNER_MODES:
        raise rt.DuckLakeRuntimeError("not a mode of the closed owner template (adopt or release)")
    text = _read(_OWNER_TEMPLATE).decode("utf-8")
    parts = _SECTION.split(text)
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    return sections[mode].format(schema=_guard(meta_schema, "schema"), owner_role=_guard(owner_role, "owner role"))


def template_sha256(name: str) -> str:
    """The sha256 of the template FILE bytes (what the procedure's check compares against the merged file)."""
    if name == "owner":
        return hashlib.sha256(_read(_OWNER_TEMPLATE)).hexdigest()
    login = LOGINS.get(name) if isinstance(name, str) else None
    if login is None:
        raise rt.DuckLakeRuntimeError("not a login in the closed catalog login table")
    return hashlib.sha256(_read(login.template)).hexdigest()
