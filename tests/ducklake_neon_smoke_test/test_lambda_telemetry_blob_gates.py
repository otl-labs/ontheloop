"""CONCERN: scripts/ducklake_smoke/lambda_telemetry_blob_gates.py (T2.36 slice 2a-2 plan 4).

The claim-check vectors against the real handler over an in-memory store with core._sigv4_invoke and the PUT patched, each with
a red case, and the Decision 101 guards: no URL, X-Amz-* name, ASIA key, 12-digit run, ARN, key, digest or project_ref in any
output, failure text included."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest

from scripts.ducklake_smoke import core, lambda_telemetry_blob_gates
from scripts.ducklake_smoke import telemetry_blob_vectors as vec
from src.telemetry_client import blob_delivery as bd
from tests.fixtures.ducklake_smoke_fakes import _Resp
from tests.fixtures.telemetry_append_fakes import LiveFake
from tests.fixtures.telemetry_blob_fakes import _s3_error

REF = "synthetic:deploy-smoke-" + "0123456789ab" * 2 + "cdef"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
MARKERS = (
    "TELEMETRY_BLOB_PROTOCOL_OK",
    "TELEMETRY_BLOB_DELIVERED_OK",
    "TELEMETRY_BLOB_RESEND_EXISTED_OK",
    "TELEMETRY_BLOB_UNVERIFIED_OK",
    "TELEMETRY_BLOB_SIZE_MISMATCH_OK",
    "TELEMETRY_BLOB_S3_REJECTS_OK",
    "TELEMETRY_BLOB_BOUNDS_OK",
    "TELEMETRY_BLOB_AUTOREGISTER_OK",
)
LEAKS = (
    r"https?://",
    r"X-Amz",
    r"ASIA[A-Z0-9]{8}",
    r"(?<![0-9])[0-9]{12}(?![0-9])",
    r"arn:",
    r"[0-9a-f]{64}",
    r"telemetry-blobs",
)


class Wired:
    def __init__(self, live: LiveFake) -> None:
        self.live = live
        self.override: Any = None
        self.sent: list[dict[str, Any]] = []

    def invoke(self, url: str, payload: dict[str, Any], **kw: Any) -> Any:
        self.sent.append(payload)
        if self.override is not None:
            replaced = self.override(payload)
            if replaced is not None:
                return replaced
        return self.live.invoke(url, payload, **kw)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Wired:
    live = LiveFake().activate(monkeypatch)
    live.register(REF, PROJECT)
    state = Wired(live)
    monkeypatch.setattr(core, "_sigv4_invoke", state.invoke)
    return state


def gate_output(text: str) -> str:
    """The gate's own lines: the in-process writer's C6 JSON lines are CloudWatch's in production, not the gate's output."""
    return "\n".join(line for line in text.splitlines() if not line.startswith("{"))


def run() -> None:
    lambda_telemetry_blob_gates.lambda_telemetry_blob("https://fake-url.invalid", REF)


def failure(wired: Wired) -> str:
    with pytest.raises(core.SmokeTestFailure) as caught:
        run()
    return str(caught.value)


def test_vectors_pass_on_a_consistent_writer(wired: Wired, capsys: pytest.CaptureFixture[str]) -> None:
    run()
    out = gate_output(capsys.readouterr().out)
    for marker in MARKERS:
        assert marker in out, marker
    for measure in ("blob_grant_ms", "blob_put_ms", "blob_append_ms"):
        assert re.search(rf"^{measure}=\d+$", out, re.MULTILINE), measure
    begins = [p for p in wired.sent if p["action"] == "begin_blobs"]
    assert begins and sum(1 for p in begins if p["project_ref"].startswith("repo:deploy-smoke-blob-")) == 3
    assert wired.live.store.blobs, "the delivered object is stored"


def test_blob_vectors_read_nothing_through_a_reader(
    wired: Wired, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resolved: list[str] = []
    real = core._function_url
    monkeypatch.setattr(core, "_function_url", lambda role: resolved.append(role) or real(role))
    run()
    out = gate_output(capsys.readouterr().out)
    assert "READBACK" not in out and "blob_read_ms" not in out
    assert wired.sent and not [p for p in wired.sent if p["action"] == "named_read"]
    assert not [role for role in resolved if "reader" in role]


def _misdeclared(payload: dict[str, Any]) -> bool:
    if payload["action"] == "begin_blobs":
        return any(i["content_bytes"] == vec.BLOB_BYTES + 1 for i in payload["items"])
    if payload["action"] == "append_telemetry":
        return any(r.get("content_bytes") == vec.BLOB_BYTES + 1 for b in payload["batches"] for r in b["rows"])
    return False


def test_size_mismatch_vector_refuses_on_both_verbs(wired: Wired, capsys: pytest.CaptureFixture[str]) -> None:
    run()
    assert "TELEMETRY_BLOB_SIZE_MISMATCH_OK" in capsys.readouterr().out
    sent = [p for p in wired.sent if _misdeclared(p)]
    assert [p["action"] for p in sent] == ["append_telemetry", "begin_blobs"]
    assert vec.THRESHOLD_BYTES < vec.BLOB_BYTES + 1 <= vec.CAP_BYTES
    row = vec.blob_transcript_row("t", 0, datetime(2026, 1, 1, tzinfo=timezone.utc), vec.fresh_content("t"))
    assert vec.misdeclared(row)["content_bytes"] == row["content_bytes"] + 1
    assert {k: v for k, v in vec.misdeclared(row).items() if k != "content_bytes"} == {
        k: v for k, v in row.items() if k != "content_bytes"
    }


def test_resend_that_uploads_fails(wired: Wired) -> None:
    calls = {"n": 0}

    def vanish(payload: dict[str, Any]) -> Any:
        if payload["action"] == "begin_blobs" and payload["project_ref"] == REF:
            calls["n"] += 1
            if calls["n"] == 2:
                wired.live.store.blobs.clear()

    wired.override = vanish
    assert "re-delivery uploaded again" in failure(wired)


def test_gate_output_never_carries_a_url_or_identity(wired: Wired, capsys: pytest.CaptureFixture[str]) -> None:
    run()
    out = gate_output(capsys.readouterr().out)
    for pattern in LEAKS:
        assert not re.search(pattern, out), pattern
    assert REF not in out and REF.split(":", 1)[1] not in out and PROJECT not in out
    wired.override = lambda payload: _Resp(500, {"ok": False, "error_type": "blob_integrity", "url": "https://leak.invalid/k"})
    message = failure(wired)
    for pattern in LEAKS:
        assert not re.search(pattern, message), pattern
    assert REF not in message


@pytest.mark.parametrize(
    ("answer", "kind", "status"),
    [
        (_Resp(403, None, text="<html>proxy blocked</html>"), "proxy", 403),
        (_Resp(400, {"ok": False, "error_type": "bad_request"}), "refused", 400),
        (_Resp(500, {"ok": False, "error_type": "blob_integrity"}), "defect", 500),
        (_Resp(503, {"ok": False, "error_type": "blob_store_unavailable"}), "transient", 503),
    ],
)
def test_delivery_failure_names_its_kind(
    wired: Wired, monkeypatch: pytest.MonkeyPatch, answer: Any, kind: str, status: int
) -> None:
    monkeypatch.setattr(bd, "RETRIES", 0)
    wired.override = lambda payload: answer if payload["action"] == "begin_blobs" else None
    message = failure(wired)
    assert f"kind={kind} status={status}" in message and "blob delivery failed" in message


def test_put_failure_names_the_s3_code(wired: Wired, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bd, "urllib_put", lambda url, headers, data: (400, _s3_error("BadDigest")))
    message = failure(wired)
    assert "kind=defect status=400 s3_code=BadDigest" in message
    monkeypatch.setattr(bd, "urllib_put", lambda url, headers, data: (403, b"<html>blocked</html>"))
    assert "kind=proxy status=403" in failure(wired)


@pytest.mark.parametrize(
    ("break_", "needle"),
    [
        ("describe_version", "blob_protocol version 1"),
        ("describe_threshold", "threshold or cap differ"),
        ("describe_schema", "schema view"),
        ("put_accepts_anything", "same-length wrong-bytes PUT"),
        ("store_forgets", "append of a delivered blob row status=422"),
        ("unverified_commits", "unverified append status=200"),
        ("size_mismatch_pages", "size-mismatch append status=500"),
        ("size_mismatch_commits", "size-mismatch append status=200"),
        ("size_mismatch_grants", "size-mismatch begin_blobs status=200"),
        ("bounds_pass", "too_many_items"),
        ("signed_headers", "does not sign exactly"),
    ],
)
def test_each_divergence_fails_loudly(wired: Wired, monkeypatch: pytest.MonkeyPatch, break_: str, needle: str) -> None:
    def tweak_describe(mutate: Any) -> None:
        def override(payload: dict[str, Any]) -> Any:
            if payload["action"] == "describe":
                body = wired.live.invoke("u", payload).json()
                mutate(body)
                return _Resp(200, body)
            return None

        wired.override = override

    if break_ == "describe_version":
        tweak_describe(lambda body: body["blob_protocol"].update(version=2))
    elif break_ == "describe_threshold":
        tweak_describe(lambda body: body["blob_protocol"].update(inline_threshold_bytes=1))
    elif break_ == "describe_schema":
        tweak_describe(lambda body: body["schema"].pop("telemetry_agents"))
    elif break_ == "put_accepts_anything":
        real = wired.live.store.put
        calls = {"n": 0}

        def lenient(url: str, headers: Any, data: bytes) -> tuple[int, bytes]:
            calls["n"] += 1
            return real(url, headers, data) if calls["n"] <= 1 else (200, b"")

        monkeypatch.setattr(bd, "urllib_put", lenient)
    elif break_ == "store_forgets":
        wired.override = lambda payload: wired.live.store.blobs.clear() if payload["action"] == "append_telemetry" else None
    elif break_ == "unverified_commits":
        seen = {"begins": 0}

        def trust_everything(payload: dict[str, Any]) -> Any:
            if payload["action"] == "begin_blobs":
                seen["begins"] += 1
            elif payload["action"] == "append_telemetry" and seen["begins"] == 3:  # the unverified scenario's append
                monkeypatch.setattr(wired.live.store, "verify", lambda *a, **k: "verified")
            return None

        wired.override = trust_everything
    elif break_ == "size_mismatch_pages":
        wired.override = lambda payload: (
            _Resp(500, {"ok": False, "error_type": "blob_integrity"}) if _misdeclared(payload) else None
        )
    elif break_ == "size_mismatch_commits":
        wired.override = lambda payload: (
            _Resp(200, {"ok": True}) if payload["action"] == "append_telemetry" and _misdeclared(payload) else None
        )
    elif break_ == "size_mismatch_grants":
        wired.override = lambda payload: (
            _Resp(200, {"ok": True, "items": []}) if payload["action"] == "begin_blobs" and _misdeclared(payload) else None
        )
    elif break_ == "bounds_pass":
        wired.override = lambda payload: _Resp(200, {"ok": True}) if len(payload.get("items", [])) > 200 else None
    else:
        real_grant = wired.live.store.grant_put

        def unsigned(*a: Any, **k: Any) -> Any:
            grant = real_grant(*a, **k)
            return type(grant)(
                grant.url.replace("content-length;host;x-amz-checksum-sha256", "host"), grant.headers, grant.expires_at
            )

        monkeypatch.setattr(wired.live.store, "grant_put", unsigned)
    assert needle in failure(wired)
