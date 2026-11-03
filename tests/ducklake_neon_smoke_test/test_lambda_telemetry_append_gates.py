"""CONCERN: scripts/ducklake_smoke/lambda_telemetry_append_gates.py (T2.36 slice 2a-2 plan 3 part 2).

The append vectors against the real handler over an in-memory store, each with a red case, and the Decision 101 guards:
no account id, URL, host or project_ref in any output, failure text included."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from scripts.ducklake_smoke import core, lambda_telemetry_append_gates
from tests.fixtures.ducklake_smoke_fakes import _Resp
from tests.fixtures.telemetry_append_fakes import LiveFake

REF = "synthetic:deploy-smoke-" + "0123456789ab" * 2 + "cdef"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
MARKERS = (
    "TELEMETRY_APPEND_OK",
    "TELEMETRY_RESEND_NOOP",
    "TELEMETRY_GRAIN_CONFLICT_OK",
    "TELEMETRY_REJECTS_OK",
    "TELEMETRY_NEAR_CAP_OK",
    "TELEMETRY_ROWCAP_OK",
    "TELEMETRY_AUTOREGISTER_OK",
    "near_cap_commit_ms=",
    "near_cap_inflation=",
    "rowcap_commit_ms=",
    "append_files_per_commit=",
    "append_commit_ms=",
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


def run() -> None:
    lambda_telemetry_append_gates.lambda_telemetry_append("https://fake-url.invalid", REF)


def test_vectors_pass_on_a_consistent_writer(wired: Wired, capsys: pytest.CaptureFixture[str]) -> None:
    run()
    out = capsys.readouterr().out
    for marker in MARKERS:
        assert marker in out, marker
    appended = [p for p in wired.sent if p["action"] == "append_telemetry"]
    assert appended and {p["project_ref"] for p in appended[:10]} >= {REF}
    assert sum(1 for p in appended if p["project_ref"].startswith("repo:deploy-smoke-auto-")) == 3


def respond(status: int, error_type: str | None = None, **body: Any) -> Any:
    payload = {"ok": error_type is None, **body} | ({"error_type": error_type} if error_type else {})
    return _Resp(status, payload)


def test_resend_that_inserts_fails(wired: Wired) -> None:
    seen = {"n": 0}

    def drift(payload: dict[str, Any]) -> Any:
        if payload["action"] == "append_telemetry" and payload["project_ref"] == REF and len(payload["batches"]) == 4:
            seen["n"] += 1
            if seen["n"] == 2:
                tables = {t["table"]: {"submitted": len(t["rows"]), "collapsed": 0, "inserted": 1} for t in payload["batches"]}
                return respond(200, tables=tables, project_registration="existing", commit_ms=5)
        return None

    wired.override = drift
    with pytest.raises(core.SmokeTestFailure, match="re-send inserted rows"):
        run()


def test_autoregister_off_smoke_binding_fails(wired: Wired) -> None:
    wired.live.data_path_is_smoke = False
    with pytest.raises(core.SmokeTestFailure, match="requires the smoke binding"):
        run()
    assert not any(p["project_ref"].startswith("repo:") for p in wired.sent if p["action"] == "append_telemetry")


@pytest.mark.parametrize(
    ("needle", "divergence"),
    [
        ("grain conflict status=200", lambda p: respond(200, tables={}) if _is_conflict(p) else None),
        (
            "did not name the conflicting table and row",
            lambda p: respond(409, "grain_conflict", table="x", row=0) if _is_conflict(p) else None,
        ),
        (
            "unregistered_drill_ref status=200",
            lambda p: respond(200, tables={}) if p.get("project_ref", "").startswith("drill:") else None,
        ),
        (
            "never_on_main_name did not name rule=pattern_when",
            lambda p: respond(422, "row_rule", rule="other") if _is_never_on_main(p) else None,
        ),
        ("not found as existing", lambda p: _golden_fake(p, "created")),
        ("created 0 projects", lambda p: _fresh_existing(p)),
    ],
)
def test_each_divergence_fails_loudly(wired: Wired, needle: str, divergence: Any) -> None:
    wired.override = divergence
    with pytest.raises(core.SmokeTestFailure, match=needle):
        run()


def _is_conflict(payload: dict[str, Any]) -> bool:
    rows = next((b["rows"] for b in payload.get("batches", []) if b["table"] == "telemetry_observations"), [])
    return len(rows) == 2 and rows[1].get("name") == "changed-after-first-write"


def _is_never_on_main(payload: dict[str, Any]) -> bool:
    rows = next((b["rows"] for b in payload.get("batches", []) if b["table"] == "telemetry_observations"), [])
    return bool(rows) and rows[0].get("name") == "never_on_main"


def _golden_fake(payload: dict[str, Any], registration: str) -> Any:
    if payload["action"] == "append_telemetry" and payload["project_ref"] == REF and len(payload["batches"]) == 4:
        tables = {
            t["table"]: {"submitted": len(t["rows"]), "collapsed": 0, "inserted": len(t["rows"])} for t in payload["batches"]
        }
        return respond(200, tables=tables, project_registration=registration, commit_ms=5)
    return None


def _fresh_existing(payload: dict[str, Any]) -> Any:
    if payload["action"] == "append_telemetry" and payload["project_ref"].startswith("repo:deploy-smoke-auto-"):
        tables = {
            t["table"]: {"submitted": len(t["rows"]), "collapsed": 0, "inserted": len(t["rows"])} for t in payload["batches"]
        }
        return respond(200, tables=tables, project_registration="existing", project_id=PROJECT, commit_ms=5)
    return None


def test_gate_output_never_contains_an_account_id(wired: Wired, capsys: pytest.CaptureFixture[str]) -> None:
    account = "1234" + "56789012"

    def float_ms(payload: dict[str, Any]) -> Any:
        resp = wired.live.invoke("u", payload)
        body = resp.json()
        if isinstance(body.get("commit_ms"), int):
            body["commit_ms"] = 1234567890.123456 + 0.000789
        return _Resp(resp.status_code, body)

    wired.override = float_ms
    run()
    out = capsys.readouterr().out
    assert account not in out and "arn:" not in out and "lambda-url" not in out and "fake-url" not in out
    assert REF not in out and REF.split(":", 1)[1] not in out
    assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])", out)
    wired.override = lambda p: respond(
        500, "unmapped", error_class="KeyError", error=f"{account} arn:aws:iam::{account}:role/r {REF}"
    )
    with pytest.raises(core.SmokeTestFailure) as info:
        run()
    text = str(info.value) + capsys.readouterr().out
    assert account not in text and "arn:" not in text and REF not in text


def test_measurements_print_as_bounded_precision(wired: Wired, capsys: pytest.CaptureFixture[str]) -> None:
    wired.override = lambda p: None
    run()
    out = capsys.readouterr().out
    assert re.search(r"^near_cap_inflation=\d+\.\d{3}$", out, re.M)
    for name in ("near_cap_commit_ms", "rowcap_commit_ms", "append_commit_ms", "append_files_per_commit"):
        assert re.search(rf"^{name}=\d+$", out, re.M), name
    ratio = float(re.search(r"near_cap_inflation=(\S+)", out).group(1))
    assert 1.0 < ratio < 4.0
    assert json.dumps(out).count("e+") == 0
