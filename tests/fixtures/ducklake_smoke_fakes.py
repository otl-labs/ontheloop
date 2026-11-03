"""Shared test doubles for the tests/ducklake_neon_smoke_test/ concern-split suite (rec-2709 Wave 7).

Cross-concern shared helpers hoisted out of the former tests/test_ducklake_neon_smoke_test.py
monolith: the smoke FakeCon (records executed SQL; used by core + direct_gates), _Resp (the HTTP
response double; used by core's url-invoke primitives, every lambda-gate concern, and canary), and
_DSN (used by core + direct_gates). These are DISTINCT from tests/fixtures/ducklake_fakes.py's
runtime FakeCon/_DSN (different signatures/shapes) -- kept as a separate fixture module so bodies
stay verbatim. An importable tests/fixtures/ module -- exempt from the no-cross-test-import guard
because its name does not start with test_ (tests/CLAUDE.md).
"""

from __future__ import annotations

_DSN = {
    "host": "ep-test-123.eu-west-2.aws.neon.tech",
    "dbname": "ducklake_ops",
    "username": "ducklake_ops",
    "password": "secret-pw",  # pragma: allowlist secret -- fake fixture value, not a real credential
    "sslmode": "require",
    "meta_schema": "ducklake_ops",
}


class FakeCon:
    """Minimal DuckDB-connection double: records executed SQL, optional per-substring raises."""

    def __init__(self, fetch_results=None, raise_on=None):
        self.executed: list[str] = []
        self._fetch_results = fetch_results if fetch_results is not None else []
        self._raise_on = raise_on or {}
        self.closed = False

    def execute(self, sql):
        for sub, exc in self._raise_on.items():
            if sub in sql:
                raise exc
        self.executed.append(sql)
        return self

    def fetchall(self):
        return self._fetch_results

    def close(self):
        self.closed = True


class _Resp:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload
