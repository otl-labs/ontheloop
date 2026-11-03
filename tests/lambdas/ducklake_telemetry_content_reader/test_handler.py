"""Content reader handler tests with a scripted connection (no duckdb): served set, refusals, window, masking, access log."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import src.lambdas.ducklake_telemetry_content_reader.handler as h
from src.common import ducklake_event_content as content
from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt
from src.common.ducklake_content_read_verbs import CONTENT_READS
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_scd2_schema import NAMED_READS
from src.common.ducklake_telemetry_blobstore import BlobStoreUnavailableError
from src.telemetry.identity import _assemble_ulid

pytestmark = pytest.mark.unit


SECRET = "reader-secret"  # pragma: allowlist secret
ROW_COLUMNS = ["content", "content_uri", "content_sha256", "content_bytes", "content_omitted_reason", "compared_versions"]
SESSION_COLUMNS = [
    "session_id",
    "producer",
    "event_id",
    "parser_version",
    "event_timestamp",
    "source_ordinal",
    "purpose",
    "origin",
    "content",
    "content_uri",
    "content_sha256",
    "content_bytes",
    "content_omitted_reason",
    "tree_started_at",
    "total_rows",
    "conflicted_grain_keys",
]


def ulid_aged(days: float, entropy: int = 7) -> str:
    started = datetime.now(timezone.utc) - timedelta(days=days)
    return _assemble_ulid(int(started.timestamp() * 1000), entropy.to_bytes(10, "big"))


def caller_event(body: dict[str, Any] | str, role: str = "PlatformDev") -> dict[str, Any]:
    account = "".join(["1234", "5678", "9012"])
    arn = f"arn:aws:sts::{account}:assumed-role/{role}/someone"
    return {
        "body": body if isinstance(body, str) else json.dumps(body),
        "requestContext": {"authorizer": {"iam": {"accountId": account, "userArn": arn}}},
    }


class FakeCon:
    def __init__(self, columns: list[str], rows: list[dict[str, Any]], fail_first: bool = False) -> None:
        self.columns, self.rows, self.fail_first = columns, rows, fail_first
        self.executed: list[tuple[str, Any]] = []
        self.description: list[tuple[str]] = []
        self._result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> FakeCon:
        self.executed.append((sql, params))
        if sql.lstrip().startswith("WITH"):
            if self.fail_first:
                self.fail_first = False
                raise Exception("server closed the connection unexpectedly")
            self.description = [(c,) for c in self.columns]
            self._result = [tuple(r[c] for c in self.columns) for r in self.rows]
        else:
            self.description, self._result = [("x",)], [(1,)]
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result

    def queries(self) -> list[str]:
        return [sql for sql, _ in self.executed if sql.lstrip().startswith("WITH")]

    def close(self) -> None:
        pass


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    rt.reset_warm_connection()
    ev._INTERRUPTED_SLOTS.clear()
    state: dict[str, Any] = {
        "opened": 0,
        "kwargs": [],
        "secrets": [],
        "fail_first": False,
        "columns": ROW_COLUMNS,
        "rows": [],
        "cons": [],
    }
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", rt.TELEMETRY_PROD_META_SCHEMA)
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.TELEMETRY_PROD_DATA_PATH)
    monkeypatch.setenv("TELEMETRY_DSN_PARAMETER", SECRET)

    def open_connection(**kwargs: Any) -> FakeCon:
        state["opened"] += 1
        state["kwargs"].append(kwargs)
        con = FakeCon(state["columns"], state["rows"], fail_first=state["fail_first"])
        state["fail_first"] = False
        state["cons"].append(con)
        return con

    def fetch_dsn(parameter_name: str = "", **_: Any) -> dict[str, str]:
        state["secrets"].append(parameter_name)
        return {"username": "ducklake_telemetry_reader"}

    monkeypatch.setattr(rt, "open_connection", open_connection)
    monkeypatch.setattr(rt, "fetch_dsn", fetch_dsn)
    yield state
    rt.reset_warm_connection()
    ev._INTERRUPTED_SLOTS.clear()


def inline_row(text: str, versions: int = 1) -> dict[str, Any]:
    return {
        "content": text,
        "content_uri": None,
        "content_sha256": "0" * 64,
        "content_bytes": len(text.encode("utf-8")),
        "content_omitted_reason": None,
        "compared_versions": versions,
    }


def decode(envelope: dict[str, Any]) -> str:
    return base64.b64decode(envelope["data"]).decode("utf-8")


_HOSTILE = "x'; DROP TABLE telemetry_transcripts; --"
_EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_LOG_FIELDS = {"event", "role", "verb", "status", "session_id", "rows", "masked_bytes", "mask_counts", "request_id"}


class _Context:
    aws_request_id = "req-content"

    def __init__(self, remaining: Any) -> None:
        self._remaining = remaining

    def get_remaining_time_in_millis(self) -> float:
        return self._remaining


def _row_params(session: str, **over: Any) -> dict[str, Any]:
    return {
        "session_id": session,
        "producer": "claude_code",
        "event_id": _EVENT_ID,
        "parser_version": 3,
        "offset": 0,
        "max_bytes": 1000,
        **over,
    }


def _row_read(session: str, **over: Any) -> dict[str, Any]:
    return {"action": "named_read", "verb": "transcript_row_masked", "params": _row_params(session, **over)}


def _session_read(session: str, **params: Any) -> dict[str, Any]:
    return {
        "action": "named_read",
        "verb": "session_transcript_masked",
        "params": {"session_id": session, "offset": 0, "max_rows": 10, **params},
    }


def _call(payload: Any, context: Any = None) -> tuple[int, dict[str, Any]]:
    response = h.handler(caller_event(payload), context)
    return response["statusCode"], json.loads(response["body"])


def _lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in out.splitlines() if '"telemetry_content_read"' in ln]


def test_served_set_is_the_two_content_verbs_and_nothing_else() -> None:
    assert h.SERVED_VERBS == frozenset(CONTENT_READS) == {"transcript_row_masked", "session_transcript_masked"}
    assert h.SERVED_VERBS.isdisjoint(EVENT_READS) and h.SERVED_VERBS.isdisjoint(NAMED_READS)


def test_describe_lists_the_two_verbs_and_the_optional_flag(wiring: dict[str, Any]) -> None:
    status, body = _call({"action": "describe"})
    assert status == 200 and body["function"] == "telemetry_content_reader" and set(body["verbs"]) == h.SERVED_VERBS
    session = body["verbs"]["session_transcript_masked"]
    assert session["params_schema"]["properties"]["include_tool_output"] == {"type": "boolean"}
    assert all(len(v["read_version"]) == 16 for v in body["verbs"].values()) and wiring["opened"] == 0


@pytest.mark.parametrize("verb", ["transcript_content", "session_tree_rows", "count_by_status", _HOSTILE, 17, None])
def test_unserved_verbs_are_400_before_any_connect(wiring: dict[str, Any], verb: Any) -> None:
    status, body = _call({"action": "named_read", "verb": verb, "params": {}})
    assert status == 400 and body["error_type"] == "unknown_verb" and body["served"] == sorted(h.SERVED_VERBS)
    assert _HOSTILE not in json.dumps(body) and wiring["opened"] == 0 and wiring["secrets"] == []


def test_caller_deadline_and_window_are_refused_before_connect(wiring: dict[str, Any]) -> None:
    for key in ("deadline_ms", "window", "limit"):
        status, body = _call({**_row_read(ulid_aged(1)), key: 5})
        assert status == 400 and body["error_type"] == "bad_request"
    assert wiring["opened"] == 0


def test_bad_flag_and_refused_binds_are_400_before_connect(wiring: dict[str, Any]) -> None:
    status, body = _call(_session_read(ulid_aged(1), include_tool_output="yes"))
    assert status == 400 and body["error_type"] == "named_read_input" and wiring["opened"] == 0
    for bad in ("not-a-ulid", "".join(["1234", "5678", "9012"]), 12):
        status, body = _call(_row_read(bad))
        assert status == 400 and body["error_type"] == "named_read_input" and str(bad) not in json.dumps(body)
    status, body = _call(_row_read(ulid_aged(1), producer=""))
    assert status == 400 and wiring["opened"] == 0
    status, body = _call({**_row_read(ulid_aged(1)), "params": {**_row_params(ulid_aged(1)), "include_tool_output": True}})
    assert status == 400 and body["error_type"] == "named_read_input"


def test_sessions_older_than_the_window_are_404_before_connect(wiring: dict[str, Any]) -> None:
    status, body = _call(_row_read(ulid_aged(91)))
    assert status == 404 and body["error_type"] == "outside_read_window" and wiring["opened"] == 0
    assert _call(_session_read(ulid_aged(91)))[0] == 404
    wiring["rows"] = []
    assert _call(_row_read(ulid_aged(89)))[0] == 200


@pytest.mark.parametrize("meta_schema", ["ducklake_smoke", "ducklake_ops", ""])
def test_production_pin_refusal_happens_before_any_connect(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch, meta_schema: str
) -> None:
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", meta_schema)
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 500 and body["error_type"] == "runtime" and wiring["opened"] == 0


def test_acquire_uses_the_reader_secret_and_a_read_only_attach(wiring: dict[str, Any]) -> None:
    wiring["rows"] = [inline_row("hello")]
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 200 and body["row_count"] == 1 and len(body["read_version"]) == 16
    assert wiring["secrets"] == [SECRET]
    (kwargs,) = wiring["kwargs"]
    assert kwargs["read_only"] is True and kwargs["alias"] == rt.TELEMETRY_CATALOG_ALIAS
    assert kwargs["meta_schema"] == "ducklake_telemetry" and kwargs["data_path"] == rt.TELEMETRY_PROD_DATA_PATH
    assert decode(body["rows"][0]) == "hello"


def test_unset_parameter_fails_closed_before_any_connect(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEMETRY_DSN_PARAMETER")
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 500 and body["error_type"] == "runtime" and wiring["opened"] == 0


def test_typed_errors_dead_connection_and_413_guard(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    wiring["rows"] = [inline_row("hello")]
    wiring["fail_first"] = True
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 200 and wiring["opened"] == 2
    monkeypatch.setattr(h, "MAX_RESPONSE_BYTES", 10)
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 413 and body["error_type"] == "response_too_large"
    rt.reset_warm_connection()
    monkeypatch.setattr(rt, "open_connection", lambda **_: (_ for _ in ()).throw(ValueError("secret detail 123")))
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 500 and body["error_type"] == "unmapped" and "secret detail" not in json.dumps(body)


def test_unknown_action_and_bad_body_are_400(wiring: dict[str, Any]) -> None:
    for action in ("attach_check", "query_ops", None, 5, _HOSTILE):
        status, body = _call({"action": action})
        assert status == 400 and body["actions"] == ["describe", "named_read"]
    assert h.handler({"body": "[1, 2]"})["statusCode"] == 400 and h.handler({"body": "{nope"})["statusCode"] == 400


def test_every_read_writes_an_access_log_row(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    account = "".join(["1234", "5678", "9012"])
    session = ulid_aged(1)
    blob = {**inline_row(""), "content": None, "content_uri": "s3://bucket/key", "content_bytes": 5}

    def scenario(name: str, payload: Any, context: Any = None) -> tuple[int, dict[str, Any]]:
        capsys.readouterr()
        status, body = _call(payload, context)
        lines = _lines(capsys.readouterr().out)
        assert len(lines) == 1, (name, lines)
        (line,) = lines
        assert set(line) == _LOG_FIELDS and line["status"] == status and line["role"] == "PlatformDev", name
        assert account not in json.dumps(line) and "arn:" not in json.dumps(line) and "someone" not in json.dumps(line)
        return status, line

    wiring["rows"] = [inline_row("alpha")]
    status, line = scenario("row ok", _row_read(session))
    assert (status, line["verb"], line["rows"], line["session_id"]) == (200, "transcript_row_masked", 1, session)
    wiring["columns"], wiring["rows"] = SESSION_COLUMNS, []
    rt.reset_warm_connection()
    assert scenario("session empty", _session_read(session))[1]["rows"] == 0
    assert scenario("unknown verb", {"action": "named_read", "verb": _HOSTILE})[1]["verb"] == "unknown"
    status, line = scenario("bind refused", _row_read(account))
    assert status == 400 and line["session_id"] is None
    status, line = scenario("window", _row_read(ulid_aged(120)))
    assert status == 404 and line["session_id"] is not None
    monkeypatch.setattr(h, "MAX_RESPONSE_BYTES", 10)
    status, line = scenario("too large", _session_read(session))
    assert status == 413 and line["masked_bytes"] == 0 and line["rows"] == 0
    monkeypatch.setattr(h, "MAX_RESPONSE_BYTES", 6_000_000)
    wiring["columns"], wiring["rows"] = ROW_COLUMNS, [blob]
    rt.reset_warm_connection()

    def unavailable(*_a: Any, **_k: Any) -> Any:
        raise BlobStoreUnavailableError("down")

    monkeypatch.setattr(content.BLOB_STORE, "get_blob", unavailable, raising=False)
    status, line = scenario("blob unavailable", _row_read(session))
    assert status == 503 and line["rows"] == 0
    wiring["rows"] = [inline_row("a", versions=2)]
    rt.reset_warm_connection()
    status, _ = scenario("grain conflict", _row_read(session))
    assert status == 500
    status, _ = scenario("deadline", _row_read(session), _Context(5_000))
    assert status == 504
    rt.reset_warm_connection()
    monkeypatch.setattr(rt, "open_connection", lambda **_: (_ for _ in ()).throw(ValueError("boom")))
    status, _ = scenario("unmapped", _row_read(session))
    assert status == 500
    capsys.readouterr()
    _call({"action": "describe"})
    assert _lines(capsys.readouterr().out) == []


def test_every_served_byte_is_masked(wiring: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    token = "ghp_" + "a" * 40
    account = "".join(["1234", "5678", "9012"])
    dsn = "postgres://" + "user:pw" + "@db.internal/main"
    text = f"token {token} account {account} dsn {dsn} done"
    wiring["rows"] = [inline_row(text)]
    status, body = _call(_row_read(ulid_aged(1), max_bytes=3_000_000))
    assert status == 200
    (envelope,) = body["rows"]
    served = decode(envelope)
    for secret in (token, account, "user:pw"):
        assert secret not in served and secret not in json.dumps(body)
    assert hashlib.sha256(served.encode()).hexdigest() == envelope["masked_sha256"]
    assert envelope["content_sha256"] == "0" * 64 and envelope["data_class"] == "untrusted_transcript"
    assert envelope["mask_counts"] == {"GITHUB_TOKEN": 1, "URL_CREDENTIAL": 1, "ACCOUNT_ID": 1}
    row = {
        "session_id": ulid_aged(1),
        "producer": "claude_code",
        "event_id": _EVENT_ID,
        "parser_version": 3,
        "event_timestamp": "2026-10-01T00:00:00+00:00",
        "source_ordinal": 1,
        "purpose": "prompt",
        "origin": "user",
        "content": text,
        "content_uri": None,
        "content_sha256": "0" * 64,
        "content_bytes": len(text),
        "content_omitted_reason": None,
        "tree_started_at": datetime.now(timezone.utc) - timedelta(days=1),
        "total_rows": 1,
        "conflicted_grain_keys": [],
    }
    wiring["columns"], wiring["rows"] = SESSION_COLUMNS, [row]
    rt.reset_warm_connection()
    status, body = _call(_session_read(ulid_aged(1)))
    assert status == 200 and token not in json.dumps(body) and account not in json.dumps(body)
    (page,) = body["rows"]
    (item,) = page["rows"]
    assert (
        "[REDACTED:GITHUB_TOKEN]" in item["text"]
        and item["masked_sha256"] == hashlib.sha256(item["text"].encode()).hexdigest()
    )


def _tree_row(started: datetime) -> dict[str, Any]:
    return {
        "session_id": ulid_aged(1),
        "producer": "claude_code",
        "event_id": _EVENT_ID,
        "parser_version": 3,
        "event_timestamp": "2026-10-01T00:00:00+00:00",
        "source_ordinal": 1,
        "purpose": "prompt",
        "origin": "user",
        "content": "hello",
        "content_uri": None,
        "content_sha256": "0" * 64,
        "content_bytes": 5,
        "content_omitted_reason": None,
        "tree_started_at": started,
        "total_rows": 1,
        "conflicted_grain_keys": [],
    }


def test_a_recent_session_in_an_old_tree_is_404(wiring: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    wiring["columns"] = SESSION_COLUMNS
    wiring["rows"] = [_tree_row(now - timedelta(days=100))]
    status, body = _call(_session_read(ulid_aged(1)))
    assert status == 404 and body["error_type"] == "outside_read_window"
    wiring["rows"] = [_tree_row((now - timedelta(days=5)).replace(tzinfo=None))]
    rt.reset_warm_connection()
    assert _call(_session_read(ulid_aged(1)))[0] == 200


def test_runtime_and_version_faults_carry_no_exception_text(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: Any) -> Any:
        raise rt.DuckLakeRuntimeError("dsn secret-id arn:aws:x /private/path")

    monkeypatch.setattr(rt, "open_connection", boom)
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 500 and body == {"ok": False, "error_type": "runtime"}


def test_include_tool_output_true_and_dict_bodies_are_served(wiring: dict[str, Any]) -> None:
    wiring["columns"] = SESSION_COLUMNS
    wiring["rows"] = [_tree_row(datetime.now(timezone.utc) - timedelta(days=1))]
    assert _call(_session_read(ulid_aged(1), include_tool_output=True))[0] == 200
    event = caller_event(_session_read(ulid_aged(1)))
    event["body"] = _session_read(ulid_aged(1))
    assert h.handler(event)["statusCode"] == 200
    assert h.handler(_session_read(ulid_aged(91)))["statusCode"] == 404


def test_version_mismatch_is_a_value_free_500(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: Any) -> Any:
        raise rt.VersionMismatchError("duckdb 9.9 at /private/path")

    monkeypatch.setattr(rt, "open_connection", boom)
    status, body = _call(_row_read(ulid_aged(1)))
    assert status == 500 and body == {"ok": False, "error_type": "version_mismatch"}
