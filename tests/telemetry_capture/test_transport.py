"""Mirror test for scripts/telemetry_capture/transport.py (TR-1..TR-4) on an injected HTTP seam; no socket."""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import botocore.exceptions as botocore_exceptions
import pytest

from scripts.telemetry_capture import transport
from scripts.telemetry_capture.state import CaptureState
from scripts.telemetry_capture.transport import Budget, CaptureUnavailable, Limits, WriterClient

URL = "https://abc.lambda-url.eu-west-2.on.aws"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def client(answers: list[Any], clock: Clock, seconds: float = 600.0) -> tuple[WriterClient, list[Any], list[float]]:
    calls: list[Any] = []
    sleeps: list[float] = []

    def http(method: str, url: str, data: bytes, headers: Any, timeout: float) -> tuple[int, bytes]:
        calls.append((method, url, data, dict(headers), timeout))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def sleep(s: float) -> None:
        sleeps.append(s)
        clock.t += s

    signed = {"Authorization": "sig"}
    return (
        WriterClient(URL, lambda body: signed, Budget(seconds, clock), http=http, sleep=sleep, rng=lambda: 1.0),
        calls,
        sleeps,
    )


def test_invoke_signs_encodes_and_clamps_the_timeout() -> None:
    clock = Clock()
    writer, calls, _ = client([(200, b'{"ok": true}')], clock)
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert writer.invoke({"action": "x", "at": when}) == (200, {"ok": True})
    method, url, data, headers, timeout = calls[0]
    assert (method, url, timeout) == ("POST", URL, 130.0)
    assert json.loads(data) == {"action": "x", "at": "2026-01-01T00:00:00.000Z"}
    assert headers == {"Content-Type": "application/json", "Authorization": "sig"}
    clock.t = 590.0
    writer, calls, _ = client([(200, b"not json")], clock)
    assert writer.invoke_once({"action": "x"}) == (200, None) and calls[0][4] == pytest.approx(10.0)


def test_retry_table_backs_off_with_jitter_while_the_deadline_allows() -> None:
    clock = Clock()
    writer, calls, sleeps = client([(429, b"{}"), (503, b"{}"), OSError("x"), (200, b'{"ok": true}')], clock)
    assert writer.invoke({"action": "x"}) == (200, {"ok": True})
    assert sleeps == [0.5, 1.0, 2.0] and len(calls) == 4
    for status in (400, 409, 413, 422, 500, 502):
        writer, calls, _ = client([(status, b"{}")], Clock())
        assert writer.invoke({"action": "x"})[0] == status and len(calls) == 1


def test_retry_stops_at_the_deadline_and_an_expired_budget_never_sends() -> None:
    clock = Clock()
    writer, calls, sleeps = client([(503, b"{}")] * 50, clock, seconds=3.0)
    assert writer.invoke({"action": "x"}) == (503, {})
    assert clock.t <= 3.0 and sum(sleeps) <= 3.0
    clock.t = 10.0
    writer, calls, _ = client([], clock, seconds=3.0)
    assert writer.invoke({"action": "x"}) == (0, None) and calls == []
    with pytest.raises(TimeoutError):
        writer.put("https://bucket.s3.invalid/k", {}, b"x")


def test_put_is_one_clamped_attempt_with_the_grant_headers() -> None:
    writer, calls, _ = client([(200, b"")], Clock())
    assert writer.put("https://bucket.s3.invalid/k", {"x-amz-checksum-sha256": "c"}, b"data") == (200, b"")
    method, url, data, headers, timeout = calls[0]
    assert method == "PUT" and data == b"data" and timeout == 130.0
    assert headers == {"Content-Type": "application/octet-stream", "x-amz-checksum-sha256": "c"}


def test_urllib_request_maps_http_errors_and_hides_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(io.BytesIO):
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen: dict[str, Any] = {}

    def ok(request, timeout):
        seen.update(timeout=timeout, headers=dict(request.header_items()), method=request.get_method())
        return Response(b"done")

    monkeypatch.setattr(urllib.request, "urlopen", ok)
    assert transport.urllib_request("POST", URL, b"{}", {"X-A": "1"}, 7.0) == (201, b"done")
    assert seen == {"timeout": 7.0, "headers": {"X-a": "1"}, "method": "POST"}

    def http_error(request, timeout):
        raise urllib.error.HTTPError(URL, 413, "too big", {}, io.BytesIO(b'{"error_type": "x"}'))

    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    assert transport.urllib_request("POST", URL, b"{}", {}, 1.0) == (413, b'{"error_type": "x"}')

    def down(request, timeout):
        raise urllib.error.URLError("unreachable " + URL)

    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(OSError) as raised:
        transport.urllib_request("POST", URL, b"{}", {}, 1.0)
    assert URL not in str(raised.value)


def test_url_resolution_order_and_cache(tmp_path: Path) -> None:
    state = CaptureState(tmp_path / "s")
    assert transport.resolve_url({transport.URL_ENV: URL + "/"}, state, lambda: None) == URL
    assert transport.resolve_url({}, state, lambda: URL + "/") == URL and state.writer_url() == URL
    assert transport.resolve_url({}, state, lambda: None) == URL
    for ssm in (lambda: None, lambda: (_ for _ in ()).throw(RuntimeError("denied"))):
        with pytest.raises(CaptureUnavailable) as raised:
            transport.resolve_url({}, None, ssm)
        assert raised.value.reason == "no_writer_url"
    assert transport.url_region(URL) == "eu-west-2"
    with pytest.raises(CaptureUnavailable):
        transport.url_region("https://example.invalid")


def _fake_botocore(monkeypatch: pytest.MonkeyPatch, *, credentials: Any, ssm_value: str | None) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class Core:
        def set_default_client_config(self, config: Any) -> None:
            seen["config"] = config

    class Session:
        def __init__(self, botocore_session: Any, profile_name: Any) -> None:
            seen["core"], seen["profile"] = botocore_session, profile_name

        def get_credentials(self) -> Any:
            return credentials

        def client(self, name: str, region_name: str) -> Any:
            seen["client"] = (name, region_name)
            return types.SimpleNamespace(get_parameter=lambda **kw: {"Parameter": {"Value": ssm_value}})

    class Config:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    class AWSRequest:
        def __init__(self, method: str, url: str, data: bytes, headers: dict[str, str]) -> None:
            self.headers = dict(headers)

    class SigV4Auth:
        def __init__(self, creds: Any, service: str, region: str) -> None:
            seen["sigv4"] = (service, region)

        def add_auth(self, request: AWSRequest) -> None:
            request.headers["Authorization"] = "AWS4-HMAC-SHA256 signed"

    modules = {
        "boto3": types.SimpleNamespace(Session=Session),
        "botocore.session": types.SimpleNamespace(get_session=Core),
        "botocore.auth": types.SimpleNamespace(SigV4Auth=SigV4Auth),
        "botocore.awsrequest": types.SimpleNamespace(AWSRequest=AWSRequest),
        "botocore.config": types.SimpleNamespace(Config=Config),
    }
    modules["botocore.exceptions"] = botocore_exceptions
    modules["botocore"] = types.SimpleNamespace(session=modules["botocore.session"])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return seen


def test_make_client_installs_one_clamped_single_attempt_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    creds = types.SimpleNamespace(get_frozen_credentials=lambda: object())
    seen = _fake_botocore(monkeypatch, credentials=creds, ssm_value=URL)
    clock = Clock()
    clock.t = 500.0
    made = transport.make_client({}, CaptureState(tmp_path), Budget(530.0, clock))
    assert seen["config"].kw == {"retries": {"total_max_attempts": 1}, "connect_timeout": 30.0, "read_timeout": 30.0}
    assert seen["client"][0] == "ssm" and made.url == URL
    assert made._sign(b"{}") == {"Content-Type": "application/json", "Authorization": "AWS4-HMAC-SHA256 signed"}
    assert seen["sigv4"] == ("lambda", "eu-west-2")


@pytest.mark.parametrize(("credentials", "token"), [(None, "no_credentials"), ("raise", "credential_error")])
def test_make_client_without_credentials_is_unavailable(monkeypatch: pytest.MonkeyPatch, credentials: Any, token: str) -> None:
    def boom() -> Any:
        raise RuntimeError("sts down")

    creds = types.SimpleNamespace(get_frozen_credentials=boom) if credentials == "raise" else None
    _fake_botocore(monkeypatch, credentials=creds, ssm_value=URL)
    with pytest.raises(CaptureUnavailable) as raised:
        transport.make_client({}, None, Budget.start(10.0))
    assert raised.value.reason == token
    with pytest.raises(CaptureUnavailable) as raised:
        transport.make_client({}, None, Budget(0.0, lambda: 5.0))
    assert raised.value.reason == "deadline"


def test_credential_chain_error_is_its_own_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("secret-profile-name sts down")

    _fake_botocore(monkeypatch, credentials=types.SimpleNamespace(get_frozen_credentials=boom), ssm_value=URL)
    with pytest.raises(CaptureUnavailable) as raised:
        transport.make_client({}, None, Budget.start(10.0))
    assert raised.value.reason == "credential_error"
    assert "secret-profile-name" not in str(raised.value) and raised.value.__cause__ is None
    for error in (
        botocore_exceptions.ProxyConnectionError(proxy_url="http://127.0.0.1:1", error="refused"),
        botocore_exceptions.EndpointConnectionError(endpoint_url="https://sts.invalid"),
        botocore_exceptions.ConnectTimeoutError(endpoint_url="https://sts.invalid"),
        botocore_exceptions.ConnectionClosedError(endpoint_url="https://sts.invalid"),
    ):

        def cut(error=error) -> Any:
            raise error

        _fake_botocore(monkeypatch, credentials=types.SimpleNamespace(get_frozen_credentials=cut), ssm_value=URL)
        with pytest.raises(CaptureUnavailable) as lost:
            transport.make_client({}, None, Budget.start(10.0))
        assert lost.value.reason == "credential_transport" and lost.value.__cause__ is None
    _fake_botocore(monkeypatch, credentials=None, ssm_value=URL)
    with pytest.raises(CaptureUnavailable) as absent:
        transport.make_client({}, None, Budget.start(10.0))
    assert absent.value.reason == "no_credentials"


def _row(ref: str, size: int = 10, blob: bool = False) -> dict[str, Any]:
    if blob:
        return {"external_ref": ref, "content_sha256": "a" * 64, "content_bytes": 100_000}
    return {"external_ref": ref, "content": "x" * size}


def _items(spec: list[tuple[str, int]], **kw: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(table, _row(f"{table}#{i}", **kw)) for table, n in spec for i in range(n)]


LIMITS = Limits(blob_items=2, threshold=65536, cap=8388608)


def test_chunk_keeps_order_and_the_row_caps() -> None:
    items = _items([("telemetry_observations", 4500), ("telemetry_transcripts", 1000), ("telemetry_sessions", 3)])
    chunks = transport.chunk(items, "p", LIMITS)
    assert [i for c in chunks for i in c] == items
    for c in chunks:
        assert len(c) <= 5000
        tables = [t for t, _ in c]
        assert all(tables.count(t) <= 2000 for t in set(tables))
    assert chunks[-1][-1][0] == "telemetry_sessions"
    body = transport.append_body("p", chunks[0])
    assert [b["table"] for b in body["batches"]] == ["telemetry_observations"] and body["component"] == "turn_capture"


def test_chunk_caps_blob_rows_per_request() -> None:
    items = _items([("telemetry_transcripts", 5)], blob=True)
    chunks = transport.chunk(items, "p", LIMITS)
    assert [len(c) for c in chunks] == [2, 2, 1]


def test_chunk_body_measure_is_an_upper_bound_on_the_exact_invoke_size() -> None:
    limits = Limits(blob_items=200, threshold=65536, cap=8388608, body_bytes=20_000)
    items = _items([("telemetry_observations", 30), ("telemetry_transcripts", 30)], size=900)
    items += [("telemetry_transcripts", {"external_ref": "q", "content": 'quote " and \\ and é' * 40})]
    chunks = transport.chunk(items, "p", limits)
    assert len(chunks) > 1 and [i for c in chunks for i in c] == items
    for c in chunks:
        assert transport.invoke_size(transport.append_body("p", c)) <= limits.body_bytes
    lone = transport.chunk([("telemetry_transcripts", _row("big", size=50_000))], "p", limits)
    assert len(lone) == 1


def test_encode_renders_datetimes_and_refuses_other_objects() -> None:
    assert transport.encode({"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}) == '{"t": "2026-01-01T00:00:00.000Z"}'
    with pytest.raises(TypeError):
        transport.encode({"x": object()})
