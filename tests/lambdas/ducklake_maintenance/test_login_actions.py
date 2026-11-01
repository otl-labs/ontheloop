"""Decision 213 cl.5: the human-invoked provision_telemetry_login verb (src/lambdas/ducklake_maintenance/login_actions.py).

psycopg2 is replaced by a local stub (the fast CI tier installs no native driver, and the plan's VP step is
hermetic), and the Secrets Manager DSN fetch is patched at the boundary; fixture hosts and passwords are
composed at runtime so no credential-shaped literal lands in the tree.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
import src.lambdas.ducklake_maintenance.login_actions as la
from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.lambdas.ducklake_maintenance import catalog_logins as cl

pytestmark = pytest.mark.unit


class Error(Exception):
    def __init__(self, message: str = "", pgcode: str | None = None) -> None:
        super().__init__(message)
        self.pgcode = pgcode


class OperationalError(Error):
    pass


class InsufficientPrivilege(Error):
    def __init__(self, message: str = "") -> None:
        super().__init__(message, "42501")


_PG = types.ModuleType("psycopg2")
_PG.Error = Error  # type: ignore[attr-defined]
_PG.OperationalError = OperationalError  # type: ignore[attr-defined]
_PG.errors = types.SimpleNamespace(InsufficientPrivilege=InsufficientPrivilege)  # type: ignore[attr-defined]
_PG.connect = MagicMock()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _stub_psycopg2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg2", _PG)


_HOST = "ep-" + secrets.token_hex(6) + ".example.invalid"
_PW = "w" + secrets.token_urlsafe(24)
_OWNER_PW = "o" + secrets.token_urlsafe(24)
_WRITER = cl.LOGINS[la.TELEMETRY_LOGIN]


def _scope_row(login: cl.Login = _WRITER) -> tuple:
    return tuple(login.expected_scope[key] for key in la._SCOPE_KEYS)


_SCOPE_ROW = _scope_row()
_EXPECTED_SCOPE = dict(_WRITER.expected_scope)
_PRECONDITION = (True, True)


def _dsn(**over: str) -> dict[str, str]:
    base = {"host": _HOST, "dbname": "ducklake_ops", "username": "ducklake_ops", "password": _OWNER_PW, "sslmode": "require"}
    return {**base, **over}


def _scoped(**over: str) -> dict[str, str]:
    return _dsn(**{"username": la.TELEMETRY_LOGIN, "password": _PW, **over})


class _Cursor:
    def __init__(self, fetches: list, log: list) -> None:
        self._fetches, self.log = fetches, log

    def execute(self, sql: str, params: object = None) -> None:
        self.log.append((sql, params))
        if self._fetches and isinstance(self._fetches[0], Exception):
            raise self._fetches.pop(0)

    def fetchone(self) -> tuple:
        return self._fetches.pop(0)

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _conn(fetches: list) -> tuple[MagicMock, list]:
    log: list = []
    conn = MagicMock()
    conn.cursor.side_effect = lambda: _Cursor(fetches, log)
    return conn, log


def _run(owner_dsn=None, scoped_dsn=None, member=(True,), scoped_fetches=None, event=None, precondition=_PRECONDITION):
    owner, owner_log = _conn([member, precondition])
    scoped, scoped_log = _conn(scoped_fetches if scoped_fetches is not None else [_SCOPE_ROW, (7,)])
    dsns = {None: owner_dsn or _dsn(), la.TELEMETRY_DSN_SECRET_ID: scoped_dsn or _scoped()}
    with (
        patch.object(la.rt, "fetch_dsn", side_effect=lambda secret_id=None, **_: dsns[secret_id]) as fetch,
        patch("psycopg2.connect", side_effect=[owner, scoped]) as connect,
    ):
        out = la.action_provision_telemetry_login(event or {"confirm": la.TELEMETRY_LOGIN}, None)
    return out, owner, owner_log, scoped_log, fetch, connect


@pytest.mark.parametrize(
    "event", [{}, {"confirm": "ducklake_ops"}, {"confirm": None}, {"confirm": "DUCKLAKE_TELEMETRY_WRITER"}, {"confirm": 7}]
)
def test_missing_or_wrong_confirm_refuses_before_any_connection(event: dict) -> None:
    with patch.object(la.rt, "fetch_dsn") as fetch, patch("psycopg2.connect") as connect:
        with pytest.raises(DuckLakeRuntimeError, match="confirm"):
            la.action_provision_telemetry_login(event, None)
    fetch.assert_not_called()
    connect.assert_not_called()


def test_owner_runs_the_sql_file_unparameterised_and_commits_and_hashes_it() -> None:
    out, owner, owner_log, _, _, connect = _run()
    sql_text = cl.render_login_sql(la.TELEMETRY_LOGIN)
    assert (sql_text, None) in owner_log
    assert out["migration_sha256"] == hashlib.sha256((Path(cl.__file__).parent / _WRITER.template).read_bytes()).hexdigest()
    assert out["migration_applied"] is True
    assert owner.commit.call_count >= 2
    conninfos = [call.args[0] for call in connect.call_args_list]
    assert "user=ducklake_ops " in conninfos[0] and f"user={la.TELEMETRY_LOGIN} " in conninfos[1]


def test_password_is_set_only_as_a_bound_parameter_from_the_scoped_secret() -> None:
    _, _, owner_log, _, _, _ = _run()
    alters = [(sql, params) for sql, params in owner_log if sql.lstrip().upper().startswith("ALTER ROLE")]
    assert alters == [(f"ALTER ROLE {la.TELEMETRY_LOGIN} PASSWORD %s", (_PW,))]
    assert not any(_PW in sql for sql, _ in owner_log)


@pytest.mark.parametrize(
    "override",
    [
        {"username": "ducklake_ops"},
        {"host": "other." + _HOST},
        {"dbname": "elsewhere"},
        {"sslmode": "prefer"},
        {"sslmode": ""},
    ],
)
def test_a_scoped_secret_that_differs_is_refused_before_alter_role(override: dict) -> None:
    with pytest.raises(DuckLakeRuntimeError) as err:
        _run(scoped_dsn=_scoped(**override))
    assert _HOST not in str(err.value)
    assert _PW not in str(err.value)


def test_refused_secret_never_reaches_alter_role_or_the_scoped_connection() -> None:
    owner, owner_log = _conn([(True,), _PRECONDITION])
    with (
        patch.object(
            la.rt,
            "fetch_dsn",
            side_effect=lambda secret_id=None, **_: _dsn() if secret_id is None else _scoped(sslmode="prefer"),
        ),
        patch("psycopg2.connect", side_effect=[owner]) as connect,
        pytest.raises(DuckLakeRuntimeError),
    ):
        la.action_provision_telemetry_login({"confirm": la.TELEMETRY_LOGIN}, None)
    assert connect.call_count == 1
    assert not any(sql.lstrip().upper().startswith("ALTER ROLE") for sql, _ in owner_log)


def test_response_reports_scope_and_readability_without_host_or_password(
    capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
) -> None:
    out, *_ = _run()
    assert out["ok"] is True and out["password_set"] is True and out["metadata_readable"] is True
    assert out["scope"] == _EXPECTED_SCOPE
    assert out["owner_is_neon_superuser_member"] is True
    rendered = json.dumps(out) + capsys.readouterr().out + caplog.text
    assert _HOST not in rendered and _PW not in rendered and _OWNER_PW not in rendered


def test_unreadable_metadata_is_reported_false_not_raised() -> None:
    out, *_ = _run(scoped_fetches=[_SCOPE_ROW, _PG.errors.InsufficientPrivilege("denied")])
    assert out["metadata_readable"] is False
    assert out["scope"] == _EXPECTED_SCOPE


def test_absent_neon_superuser_role_reports_none() -> None:
    out, *_ = _run(member=(None,))
    assert out["owner_is_neon_superuser_member"] is None


def test_database_errors_surface_only_the_class_and_sqlstate() -> None:
    boom = OperationalError(f"connection to server at {_HOST} failed: {_PW}", "08001")
    with patch.object(la.rt, "fetch_dsn", return_value=_dsn()), patch("psycopg2.connect", side_effect=boom):
        with pytest.raises(DuckLakeRuntimeError) as err:
            la.action_provision_telemetry_login({"confirm": la.TELEMETRY_LOGIN}, None)
    assert "OperationalError" in str(err.value) and "sqlstate=08001" in str(err.value)
    assert _HOST not in str(err.value) and _PW not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__ is True


def test_a_missing_scoped_secret_key_is_a_runtime_error_without_the_message() -> None:
    def fetch(secret_id=None, **_):
        if secret_id is None:
            return _dsn()
        raise RuntimeError(f"DSN secret {secret_id!r} is missing required keys: ['password'] near {_HOST}")

    owner, _ = _conn([(True,), _PRECONDITION])
    with patch.object(la.rt, "fetch_dsn", side_effect=fetch), patch("psycopg2.connect", side_effect=[owner]):
        with pytest.raises(DuckLakeRuntimeError) as err:
            la.action_provision_telemetry_login({"confirm": la.TELEMETRY_LOGIN}, None)
    assert _HOST not in str(err.value)


def _run_login(login: cl.Login, scope_row: tuple | None = None, extra: list | None = None):
    owner, owner_log = _conn([(True,), _PRECONDITION])
    scoped, scoped_log = _conn([scope_row or _scope_row(login), (7,)])
    dsns = {None: _dsn(), login.secret_id: _scoped(username=login.name)}
    fetched: list = []

    def fetch(secret_id=None, **_):
        fetched.append(secret_id)
        return dsns[secret_id]

    with (
        patch.object(la.rt, "fetch_dsn", side_effect=fetch),
        patch("psycopg2.connect", side_effect=[owner, scoped]) as connect,
        patch.object(la.rt, "open_connection", **(extra and {"side_effect": extra} or {"return_value": MagicMock()})),
    ):
        out = la.action_provision_telemetry_login({"confirm": login.name}, None)
    return out, owner_log, scoped_log, fetched, connect


@pytest.mark.parametrize("name", sorted(cl.LOGINS))
def test_each_closed_login_uses_its_own_secret_and_template(name: str) -> None:
    login = cl.LOGINS[name]
    out, owner_log, _scoped_log, fetched, connect = _run_login(login)
    assert fetched == [None, login.secret_id]
    assert (cl.render_login_sql(name), None) in owner_log
    assert [(sql, p) for sql, p in owner_log if sql.startswith("ALTER ROLE")] == [(f"ALTER ROLE {name} PASSWORD %s", (_PW,))]
    assert out["login"] == name and out["migration_sha256"] == cl.template_sha256(name)
    assert f"user={name} " in connect.call_args_list[1].args[0]


def test_confirm_must_name_a_closed_login() -> None:
    for event in ({"confirm": "ducklake_telemetry_writer_prod2"}, {"confirm": "ducklake_smoke_owner"}, {"confirm": "owner"}):
        with patch.object(la.rt, "fetch_dsn") as fetch, patch("psycopg2.connect") as connect:
            with pytest.raises(DuckLakeRuntimeError, match="confirm to name one of"):
                la.action_provision_telemetry_login(event, None)
        fetch.assert_not_called()
        connect.assert_not_called()


@pytest.mark.parametrize("name", sorted(cl.LOGINS))
def test_scope_probe_matches_the_closed_table_expectation(name: str) -> None:
    login = cl.LOGINS[name]
    out, *_ = _run_login(login)
    assert out["scope"] == dict(login.expected_scope)
    assert out["scope"]["connection_limit"] == login.connection_limit


def test_provisioning_refuses_until_the_owner_role_and_schema_exist() -> None:
    for precondition, needle in (((False, True), "owner role ducklake_smoke_owner"), ((True, False), "schema ducklake_smoke")):
        owner, owner_log = _conn([(True,), precondition])
        with (
            patch.object(la.rt, "fetch_dsn", return_value=_dsn()) as fetch,
            patch("psycopg2.connect", side_effect=[owner]) as connect,
            pytest.raises(DuckLakeRuntimeError, match="provision_catalog_owner first") as err,
        ):
            la.action_provision_telemetry_login({"confirm": la.TELEMETRY_LOGIN}, None)
        assert needle in str(err.value)
        assert connect.call_count == 1 and fetch.call_count == 1
        assert not any(sql.lstrip().upper().startswith(("ALTER ROLE", "SET LOCAL")) for sql, _ in owner_log)
        assert cl.render_login_sql(la.TELEMETRY_LOGIN) not in [sql for sql, _ in owner_log]


def test_scope_probe_guards_every_role_and_schema_name() -> None:
    import re

    for login in cl.LOGINS.values():
        sql = la._scope_query(login.schema)
        for call in re.findall(
            r"(?:has_schema_privilege|pg_has_role|has_table_privilege)\([^()]*(?:\([^()]*\))?[^()]*\)", sql
        ):
            assert (
                "to_regnamespace(" in call or "to_regrole(" in call or "to_regclass(" in call or "current_database" in call
            ), call
        for role in cl.OWNER_ROLES.values():
            assert f"to_regrole('{role}')" in sql and f"pg_has_role(session_user, '{role}'" not in sql
        for schema in ("ducklake_ops", "ducklake_smoke", "ducklake_telemetry", "public"):
            assert f"to_regnamespace('{schema}')" in sql
        assert sql.count("COALESCE(") >= 10 and f"to_regclass('{login.schema}.ducklake_snapshot')" in sql


def test_scope_probe_reports_current_user_and_session_user() -> None:
    maintainer = cl.LOGINS["ducklake_smoke_maintainer"]
    sql = la._scope_query(maintainer.schema)
    assert sql.startswith("SELECT current_user::text, session_user::text,")
    assert "FROM pg_roles WHERE rolname = session_user" in sql and "pg_has_role(session_user," in sql
    out, *_ = _run_login(maintainer)
    assert out["scope"]["current_user"] == "ducklake_smoke_owner"
    assert out["scope"]["session_user"] == "ducklake_smoke_maintainer"
    assert out["scope"]["smoke_owner_member"] is True


def test_reader_login_reports_its_read_only_attach_outcome_without_raising() -> None:
    reader = cl.LOGINS["ducklake_telemetry_reader"]
    out, *_ = _run_login(reader)
    assert out["read_only_attach"] == "accepted" and "read_only_attach_error" not in out
    refused, *_ = _run_login(reader, extra=[RuntimeError("attach to " + _HOST + " refused")])
    assert refused["ok"] is True and refused["read_only_attach"] == "refused"
    assert refused["read_only_attach_error"] == "RuntimeError" and _HOST not in json.dumps(refused)
    opened = MagicMock()
    with (
        patch.object(
            la.rt,
            "fetch_dsn",
            side_effect=lambda secret_id=None, **_: _dsn() if secret_id is None else _scoped(username=reader.name),
        ),
        patch("psycopg2.connect", side_effect=[_conn([(True,), _PRECONDITION])[0], _conn([_scope_row(reader), (7,)])[0]]),
        patch.object(la.rt, "open_connection", return_value=opened) as attach,
    ):
        la.action_provision_telemetry_login({"confirm": reader.name}, None)
    kwargs = attach.call_args.kwargs
    assert kwargs["read_only"] is True and kwargs["meta_schema"] == "ducklake_telemetry"
    assert kwargs["data_path"] == "s3://agent-platform-data-lake/ducklake-telemetry/"
    opened.close.assert_called_once()
    writer, *_ = _run_login(cl.LOGINS["ducklake_telemetry_writer_prod"])
    assert "read_only_attach" not in writer


def test_handler_dispatches_the_new_action() -> None:
    assert h._ACTIONS["provision_telemetry_login"] is la.action_provision_telemetry_login
    body = json.loads(h.handler({"action": "provision_telemetry_login"})["body"])
    assert body["ok"] is False and body["error_type"] == "runtime" and "confirm" in body["error"]
