"""Decision 213 cl.5 / Decision 230 cl.3 provisioning verb for the closed catalog logins.

One OPERATIONAL, human-invoked action on the ducklake_maintenance function -- invoked over 443 via `aws lambda invoke`
(never a public Function URL, never reachable from CI or PlatformDev), by PlatformAdmin or by an ADMIN-container agent only
under explicit human direction, presenting each mutation (Decision 213 cl.5). No CC-web container has TCP/5432 egress, so
the migration, the password set and the reach check all run here.

  - action_provision_telemetry_login: closed, no caller-supplied SQL, role or secret name; confirm must name one of the
    four logins in catalog_logins.LOGINS. As the shared owner login it checks the login's owner role and schema exist,
    applies the login's password-free template (rendered from the closed table), then sets the role's password from THAT
    login's secret, and finally connects AS the login to probe its reach against the table's expected scope. The reader
    login is additionally attached READ_ONLY and read once. Idempotent: a re-run is rotation, and the RDS swap's
    re-provision.

Every psycopg2.Error is re-raised as a DuckLakeRuntimeError carrying only the exception class and its SQLSTATE, because a
connect error message names the catalog host. Neither the response nor any log line carries the host, the DSN or the
password.
"""

from __future__ import annotations

from typing import Any

from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import catalog_logins as logins
from src.lambdas.ducklake_maintenance._shared import EXTENSION_DIRECTORY

SMOKE_WRITER = logins.LOGINS["ducklake_telemetry_writer"]
TELEMETRY_LOGIN = SMOKE_WRITER.name
TELEMETRY_DSN_SECRET_ID = SMOKE_WRITER.secret_id
_NEON_SUPERUSER_QUERY = (
    "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neon_superuser') "
    "THEN pg_has_role(current_user, 'neon_superuser', 'member') END"
)
_PRECONDITION_QUERY = "SELECT to_regrole(%s) IS NOT NULL, to_regnamespace(%s) IS NOT NULL"
_SCOPE_KEYS = (
    "current_user",
    "session_user",
    "ops_usage",
    "ops_create",
    "smoke_usage",
    "smoke_create",
    "telemetry_usage",
    "telemetry_create",
    "public_create",
    "database_create",
    "connection_limit",
    "smoke_owner_member",
    "telemetry_owner_member",
    "read_only",
    "snapshot_insert",
)


def _schema_privilege(schema: str, kind: str) -> str:
    return f"COALESCE(has_schema_privilege(current_user, to_regnamespace('{schema}'), '{kind}'), false)"


def _scope_query(schema: str) -> str:
    """The probe, with every role and schema resolved through to_regrole / to_regnamespace so an absent one reads false."""
    smoke_owner = logins.OWNER_ROLES[rt.SMOKE_META_SCHEMA]
    telemetry_owner = logins.OWNER_ROLES[rt.TELEMETRY_PROD_META_SCHEMA]
    return (
        "SELECT current_user::text, session_user::text, "
        f"{_schema_privilege(rt.META_SCHEMA, 'USAGE')}, {_schema_privilege(rt.META_SCHEMA, 'CREATE')}, "
        f"{_schema_privilege(rt.SMOKE_META_SCHEMA, 'USAGE')}, {_schema_privilege(rt.SMOKE_META_SCHEMA, 'CREATE')}, "
        f"{_schema_privilege(rt.TELEMETRY_PROD_META_SCHEMA, 'USAGE')}, "
        f"{_schema_privilege(rt.TELEMETRY_PROD_META_SCHEMA, 'CREATE')}, "
        f"{_schema_privilege('public', 'CREATE')}, "
        "has_database_privilege(current_user, current_database(), 'CREATE'), "
        "(SELECT rolconnlimit FROM pg_roles WHERE rolname = session_user), "
        f"COALESCE(pg_has_role(session_user, to_regrole('{smoke_owner}'), 'MEMBER'), false), "
        f"COALESCE(pg_has_role(session_user, to_regrole('{telemetry_owner}'), 'MEMBER'), false), "
        "current_setting('default_transaction_read_only') = 'on', "
        f"COALESCE(has_table_privilege(current_user, to_regclass('{schema}.ducklake_snapshot'), 'INSERT'), false)"
    )


def _scoped_dsn(login: logins.Login, owner: dict[str, str]) -> dict[str, str]:
    """Fetch the login's secret and refuse unless it names this role on the owner's host over TLS."""
    try:
        scoped = rt.fetch_dsn(login.secret_id)
    except RuntimeError:
        raise rt.DuckLakeRuntimeError("provision_telemetry_login: the scoped login secret is missing required keys") from None
    wrong = [
        field
        for field, expected in (
            ("username", login.name),
            ("host", owner["host"]),
            ("dbname", owner["dbname"]),
            ("sslmode", "require"),
        )
        if scoped.get(field) != expected
    ]
    if wrong:
        raise rt.DuckLakeRuntimeError(
            f"provision_telemetry_login: the scoped login secret differs from what is required in: {', '.join(wrong)}"
        )
    return scoped


def _require_preconditions(cur: Any, login: logins.Login) -> None:
    cur.execute(_PRECONDITION_QUERY, (login.owner_role, login.schema))
    role_exists, schema_exists = cur.fetchone()
    missing = [
        name
        for name, found in ((f"owner role {login.owner_role}", role_exists), (f"schema {login.schema}", schema_exists))
        if not found
    ]
    if missing:
        raise rt.DuckLakeRuntimeError(
            f"provision_telemetry_login: {', '.join(missing)} does not exist yet; run provision_catalog_owner first"
        )


def _apply_as_owner(psycopg2: Any, login: logins.Login, owner: dict[str, str]) -> tuple[bool | None, str, dict[str, str]]:
    """Owner session: record neon_superuser membership, check preconditions, run the template, then set the password."""
    sql_text = logins.render_login_sql(login.name)
    conn = psycopg2.connect(rt.libpq_conninfo(owner))
    try:
        with conn.cursor() as cur:
            cur.execute(_NEON_SUPERUSER_QUERY)
            member = cur.fetchone()[0]
            _require_preconditions(cur, login)
            cur.execute(sql_text)
        conn.commit()
        scoped = _scoped_dsn(login, owner)
        with conn.cursor() as cur:
            # Neon accepts only a plaintext password here; psycopg2 binds it client-side, so it is never
            # concatenated into SQL text by this module and never logged.
            cur.execute(f"ALTER ROLE {login.name} PASSWORD %s", (scoped["password"],))
        conn.commit()
    finally:
        conn.close()
    return member, logins.template_sha256(login.name), scoped


def _probe_as_scoped(psycopg2: Any, login: logins.Login, scoped: dict[str, str]) -> tuple[dict[str, Any], bool]:
    """Scoped session: report the login's privileges and whether it can read its catalog's metadata."""
    conn = psycopg2.connect(rt.libpq_conninfo(scoped))
    try:
        with conn.cursor() as cur:
            cur.execute(_scope_query(login.schema))
            scope = dict(zip(_SCOPE_KEYS, cur.fetchone(), strict=True))
        readable = True
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {login.schema}.ducklake_metadata")
                cur.fetchone()
        except psycopg2.Error:
            conn.rollback()
            readable = False
    finally:
        conn.close()
    return scope, readable


def _read_only_attach(login: logins.Login, scoped: dict[str, str]) -> dict[str, str]:
    """Attach the catalog READ_ONLY as the reader login and read one count; report the outcome, never raise for it."""
    try:
        con = rt.open_connection(
            dsn=scoped,
            data_path=logins.DATA_PATHS[login.schema],
            meta_schema=login.schema,
            extension_directory=EXTENSION_DIRECTORY,
            read_only=True,
        )
        try:
            con.execute(f"SELECT count(*) FROM {rt.CATALOG_ALIAS}.telemetry_sessions").fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 -- the message can name the host: class only
        return {"read_only_attach": "refused", "read_only_attach_error": type(exc).__name__}
    return {"read_only_attach": "accepted"}


def action_provision_telemetry_login(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL, human-invoked: provision one closed catalog login and report its reach (Decision 213 cl.5, 230 cl.3)."""
    confirm = event.get("confirm")
    login = logins.LOGINS.get(confirm) if isinstance(confirm, str) else None
    if login is None:
        raise rt.DuckLakeRuntimeError(f"provision_telemetry_login requires confirm to name one of {sorted(logins.LOGINS)}")
    import psycopg2  # noqa: PLC0415

    try:
        member, migration_sha256, scoped = _apply_as_owner(psycopg2, login, rt.fetch_dsn())
        scope, readable = _probe_as_scoped(psycopg2, login, scoped)
    except psycopg2.Error as exc:
        raise rt.DuckLakeRuntimeError(
            f"provision_telemetry_login database error: {type(exc).__name__} sqlstate={exc.pgcode}"
        ) from None
    response: dict[str, Any] = {
        "ok": True,
        "login": login.name,
        "migration_applied": True,
        "migration_sha256": migration_sha256,
        "password_set": True,
        "owner_is_neon_superuser_member": member,
        "scope": scope,
        "metadata_readable": readable,
    }
    if login.kind == "reader":
        response.update(_read_only_attach(login, scoped))
    return response
