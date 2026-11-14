"""Tests for the scripts/ops_portal/writer_transport.py DuckLake write transport: writer-URL
resolution, record projection, SigV4-signed POST, and the 5xx/idempotency retry policy.

Split out of the former tests/test_ops_data_portal.py monolith (rec-2709 Wave 3). _FakeResp and
_patch_writer_transport are single-consumer (TestDuckLakeTransportHelpers only) so they move
here verbatim rather than being hoisted to tests/fixtures/ (Wave 1 SINGLE-CONSUMER precedent).
"""

from __future__ import annotations

import json

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ops_portal import writer_transport as _writer_transport_mod  # noqa: E402


class _FakeResp:
    """Minimal requests.Response stand-in for _ducklake_write transport tests."""

    def __init__(self, status_code: int, body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self) -> dict:
        return self._body


def _patch_writer_transport(monkeypatch, responses: list[_FakeResp], sleeps: list | None = None) -> list[dict]:
    """Install fake boto3 session / SigV4 / requests.post returning *responses* in order.

    Returns the list of JSON-decoded request bodies captured per POST.
    """
    import time

    import boto3
    import requests
    from botocore.auth import SigV4Auth

    class _Creds:
        access_key = "AK"
        secret_key = "SK"  # noqa: S105 -- fake fixture  # pragma: allowlist secret
        token = None

        def get_frozen_credentials(self):
            return self

    class _Session:
        def __init__(self, profile_name=None):
            pass

        def get_credentials(self):
            return _Creds()

    monkeypatch.setattr(boto3, "Session", _Session)
    monkeypatch.setattr(SigV4Auth, "add_auth", lambda self, req: None)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s) if sleeps is not None else None)

    bodies: list[dict] = []
    seq = iter(responses)

    def _post(url, data=None, headers=None, timeout=None):
        bodies.append(json.loads(data))
        return next(seq)

    monkeypatch.setattr(requests, "post", _post)
    return bodies


class TestDuckLakeTransportHelpers:
    """Coverage for the DuckLake transport helpers: URL resolve, projection, SigV4 write, retry."""

    def test_resolve_writer_url_env(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        monkeypatch.setenv("DUCKLAKE_WRITER_URL", "https://writer.example/")
        assert p._resolve_writer_url() == "https://writer.example"

    def test_resolve_writer_url_loud_fail(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p
        import src.common.ducklake_reader_client as ir

        monkeypatch.delenv("DUCKLAKE_WRITER_URL", raising=False)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        # Patch at the source module so the lazy import inside _resolve_writer_url picks them up.
        monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", lambda *a, **k: None)
        monkeypatch.setattr(ir, "_resolve_function_url_via_api", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="DUCKLAKE_WRITER_URL not set"):
            p._resolve_writer_url()

    def test_resolve_writer_url_api_fallback(self, monkeypatch) -> None:
        """When env + terraform are unavailable, the writer URL resolves via GetFunctionUrlConfig (CI case)."""
        import scripts.ops_data_portal as p
        import src.common.ducklake_reader_client as ir

        monkeypatch.delenv("DUCKLAKE_WRITER_URL", raising=False)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        # Patch at the source module so the lazy import inside _resolve_writer_url picks them up.
        monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", lambda *a, **k: None)
        monkeypatch.setattr(ir, "_resolve_function_url_via_api", lambda name, **kw: "https://api.example/")
        assert p._resolve_writer_url() == "https://api.example"

    def test_resolve_writer_url_api_route(self, monkeypatch) -> None:
        """Under execute_api the only SSM name read is the ops/writer API route, never a Function URL."""
        import scripts.ops_data_portal as p
        import src.common.ducklake_reader_client as ir

        monkeypatch.delenv("DUCKLAKE_WRITER_URL", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        monkeypatch.setenv("OTL_OPS_VERBS_TRANSPORT", "execute_api")
        seen: list[tuple[str, str | None]] = []

        def _ssm(name, profile=None, region=None):
            seen.append((name, profile))
            return "https://api.example/ops/writer" if name == "/agent-platform/ducklake/api_url/ops/writer" else None

        monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", _ssm)
        monkeypatch.setattr(ir, "_resolve_function_url_via_api", lambda *a, **k: pytest.fail("GetFunctionUrlConfig used"))
        monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("terraform used"))
        assert p._resolve_writer_url() == "https://api.example/ops/writer"
        assert [n for n, _ in seen] == ["/agent-platform/ducklake/api_url/ops/writer"]
        assert seen[0][1] == "agent_platform"

        monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="/agent-platform/ducklake/api_url/ops/writer"):
            p._resolve_writer_url()

    def test_ducklake_write_signs_for_the_url_scope(self, monkeypatch) -> None:
        from botocore.auth import SigV4Auth

        import scripts.ops_data_portal as p

        scopes: list[tuple[str, str]] = []
        for url, expected in (
            ("https://abc123.execute-api.eu-west-2.amazonaws.com/v1/ops/writer", ("execute-api", "eu-west-2")),
            ("https://abc123.lambda-url.eu-west-2.on.aws/", ("lambda", "eu-west-2")),
        ):
            monkeypatch.setenv("DUCKLAKE_WRITER_URL", url)
            _patch_writer_transport(monkeypatch, [_FakeResp(200, {"ok": True})])
            real_init = SigV4Auth.__init__

            def _init(self, creds, service, region, _real=real_init):
                scopes.append((service, region))
                _real(self, creds, service, region)

            monkeypatch.setattr(SigV4Auth, "__init__", _init)
            p._ducklake_write("ops_recommendations", {"id": "rec-1", "status": "open"}, action="write_ops")
            assert scopes[-1] == expected

    def test_project_ops_record_drops_derived_and_unknown(self) -> None:
        import scripts.ops_data_portal as p

        rec = {
            "id": "rec-1",
            "status": "open",
            "title": "t",
            "ulid": "01X",  # derived -> dropped
            "created_timestamp": "2026-01-01",  # derived -> dropped
            "last_updated_timestamp": "2026-01-01",  # derived -> dropped
            "date": "2026-01-01",  # unknown -> dropped
        }
        projected = p._project_ops_record("ops_recommendations", rec)
        assert "ulid" not in projected and "created_timestamp" not in projected and "date" not in projected
        assert projected["id"] == "rec-1" and projected["status"] == "open" and projected["title"] == "t"

    def test_ducklake_write_sigv4_success(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        monkeypatch.setenv("DUCKLAKE_WRITER_URL", "https://writer.example/")
        bodies = _patch_writer_transport(monkeypatch, [_FakeResp(200, {"ok": True, "key": "rec-1"})])

        out = p._ducklake_write("ops_recommendations", {"id": "rec-1", "status": "open"}, action="write_ops")
        assert out["ok"] is True
        assert bodies[0]["action"] == "write_ops"
        assert bodies[0]["table"] == "ops_recommendations"
        assert "idempotency_ulid" not in bodies[0]

    def test_ducklake_write_carries_idempotency_ulid(self, monkeypatch) -> None:
        """When the caller passes idempotency_ulid it is included in the request payload."""
        import scripts.ops_data_portal as p

        monkeypatch.setenv("DUCKLAKE_WRITER_URL", "https://writer.example/")
        bodies = _patch_writer_transport(monkeypatch, [_FakeResp(200, {"key": "rec-9"})])

        out = p._ducklake_write(
            "ops_recommendations", {"status": "open", "title": "t"}, action="file_ops", idempotency_ulid="abc123"
        )
        assert out["key"] == "rec-9"
        assert bodies[0]["idempotency_ulid"] == "abc123"
        assert bodies[0]["action"] == "file_ops"

    def test_ducklake_write_referential_409_maps_to_runtimeerror(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_writer_transport_mod, "_resolve_writer_url", lambda *a, **k: "https://w.example")
        _patch_writer_transport(monkeypatch, [_FakeResp(409, text="absent")])
        with pytest.raises(RuntimeError, match="referential"):
            p._ducklake_write("ops_recommendations", {"id": "rec-x", "status": "closed"}, action="update_ops")

    def test_ducklake_write_schema_422_maps_to_valueerror(self, monkeypatch) -> None:
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_writer_transport_mod, "_resolve_writer_url", lambda *a, **k: "https://w.example")
        _patch_writer_transport(monkeypatch, [_FakeResp(422, text="bad field")])
        with pytest.raises(ValueError, match="schema-gate"):
            p._ducklake_write("ops_recommendations", {"id": "rec-1", "status": "open"}, action="write_ops")

    def test_ducklake_write_transient_503_retries_when_idempotent(self, monkeypatch) -> None:
        """A 503 (Neon cold-resume) retries when idempotency_ulid is set; the retry consumes the 200."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_writer_transport_mod, "_resolve_writer_url", lambda *a, **k: "https://w.example")
        sleeps: list = []
        bodies = _patch_writer_transport(
            monkeypatch,
            [_FakeResp(503, text="cold"), _FakeResp(200, {"key": "rec-77"})],
            sleeps=sleeps,
        )

        out = p._ducklake_write(
            "ops_recommendations", {"status": "open", "title": "t"}, action="file_ops", idempotency_ulid="u1"
        )
        assert out["key"] == "rec-77"
        assert len(bodies) == 2  # exactly one retry
        assert len(sleeps) == 1  # backed off once
        assert bodies[0] == bodies[1]  # the retried request is the identical idempotent payload

    def test_ducklake_write_transient_503_no_retry_without_idempotency(self, monkeypatch) -> None:
        """A non-idempotent write (write_ops without ULID is caller-keyed but file-path-unsafe) fails fast."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_writer_transport_mod, "_resolve_writer_url", lambda *a, **k: "https://w.example")
        bodies = _patch_writer_transport(monkeypatch, [_FakeResp(503, text="cold")])

        with pytest.raises(RuntimeError, match="HTTP 503"):
            p._ducklake_write("ops_recommendations", {"id": "rec-1", "status": "open"}, action="write_ops")
        assert len(bodies) == 1  # no retry attempted

    def test_ducklake_write_update_ops_retries_transient(self, monkeypatch) -> None:
        """update_ops is idempotent by id, so transient 5xx retries even without a ULID."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_writer_transport_mod, "_resolve_writer_url", lambda *a, **k: "https://w.example")
        sleeps: list = []
        bodies = _patch_writer_transport(
            monkeypatch, [_FakeResp(502, text="cold"), _FakeResp(200, {"ok": True})], sleeps=sleeps
        )

        out = p._ducklake_write("ops_recommendations", {"id": "rec-1", "status": "closed"}, action="update_ops")
        assert out == {"ok": True}
        assert len(bodies) == 2

    def test_ducklake_write_transient_exhausts_after_three_attempts(self, monkeypatch) -> None:
        """Three transient failures exhaust the retry budget and raise loudly."""
        import scripts.ops_data_portal as p

        monkeypatch.setattr(_writer_transport_mod, "_resolve_writer_url", lambda *a, **k: "https://w.example")
        bodies = _patch_writer_transport(
            monkeypatch,
            [_FakeResp(503, text="cold")] * 3,
            sleeps=[],
        )

        with pytest.raises(RuntimeError, match="HTTP 503"):
            p._ducklake_write(
                "ops_recommendations", {"status": "open", "title": "t"}, action="file_ops", idempotency_ulid="u2"
            )
        assert len(bodies) == 3
