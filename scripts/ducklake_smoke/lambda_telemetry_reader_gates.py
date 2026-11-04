"""Telemetry reader deploy gate for the DuckLake Neon smoke suite (slice 2c, Decision 236 cl.2-3).

One post-deploy gate, --lambda-telemetry-reader, proves the telemetry reader is URL-only, bound to the production
telemetry catalog on a SELECT-only login attached READ_ONLY (observed live, never echoed from a flag), serves exactly the
event verbs minus transcript_content, answers no ops verb and no content, reads the four event tables, and answers the
session and verification-verb checks the shared reader answered before slice 2c's cleanup. It reads production and writes
nothing. Gates call core._function_url / core._sigv4_invoke through the module object (Decision 104).

Failure text carries only a status code, a handler error_type, an exception class name or a botocore error code; never a
response body, an error message, an ARN, an account id or a URL (Decision 101).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast

from scripts.ducklake_smoke import core, lambda_ec_gates, lambda_telemetry_gates
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_reader_client import VERB_FIELDS

_FUNCTION_NAME = "agent-platform-ducklake-telemetry-reader"
_PROD_META_SCHEMA = "ducklake_telemetry"
_LOGIN = "ducklake_telemetry_reader"
_SERVED = frozenset(EVENT_READS) - {"transcript_content"}
_TABLES = frozenset({"telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"})
_OPS_VERB = "open_recs_tally"
_USAGE_VERBS = ("model_usage_rollup", "tool_use_rollup", "session_startup_baseline")
_SUCCESS = (
    "LAMBDA_TELEMETRY_READER OK unsigned=403 describe=200 attach_check=ok query_ops=400 content=400 direct_invoke=denied"
)


def _fail(message: str) -> core.SmokeTestFailure:
    return core.SmokeTestFailure(f"LAMBDA_TELEMETRY_READER FAIL: {message}")


def _invoke(url: str, payload: dict[str, Any], profile: str | None, region: str) -> Any:
    return core._sigv4_invoke(url, payload, profile=profile, region=region)


def _expect_ok(resp: Any, check: str) -> dict[str, Any]:
    body = lambda_telemetry_gates._body(resp)
    if resp.status_code != 200 or body.get("ok") is not True:
        error_type = lambda_telemetry_gates._error_type(resp)
        raise _fail(f"{check} status={resp.status_code} error_type={error_type} {lambda_telemetry_gates._diagnostics(resp)}")
    return body


def _expect_400(resp: Any, check: str, error_type: str) -> None:
    got = lambda_telemetry_gates._error_type(resp)
    if resp.status_code != 400 or got != error_type:
        raise _fail(f"{check} status={resp.status_code} error_type={got} (want 400 {error_type})")


def _check_unsigned(url: str, profile: str | None, region: str) -> None:
    resp = core._sigv4_invoke(url, {"action": "describe"}, sign=False, profile=profile, region=region)
    if resp.status_code != 403:
        raise _fail(f"unsigned describe status={resp.status_code} (want 403)")


def _check_describe(url: str, profile: str | None, region: str) -> None:
    body = _expect_ok(_invoke(url, {"action": "describe"}, profile, region), "describe")
    catalog = body.get("catalog")
    if not isinstance(catalog, dict) or catalog.get("meta_schema") != _PROD_META_SCHEMA:
        raise _fail("describe meta_schema is not the production telemetry schema")
    if catalog.get("data_path_is_smoke") is not False:
        raise _fail("describe data_path_is_smoke is not false (smoke binding)")
    verbs = body.get("verbs")
    served = set(verbs) if isinstance(verbs, dict) else set()
    if served != _SERVED:
        raise _fail(f"describe lists {len(served - _SERVED)} unexpected and {len(_SERVED - served)} missing served verbs")


def _check_attach(url: str, profile: str | None, region: str) -> None:
    body = _expect_ok(_invoke(url, {"action": "attach_check"}, profile, region), "attach_check")
    if body.get("meta_schema") != _PROD_META_SCHEMA or body.get("data_path_is_smoke") is not False:
        raise _fail("attach_check is not bound to the production telemetry catalog (smoke binding)")
    if body.get("catalog_login") != _LOGIN:
        raise _fail("attach_check catalog_login is not the SELECT-only telemetry reader login")
    if body.get("read_only") is not True:
        raise _fail("attach_check read_only is not true: the catalog is not attached READ_ONLY")


def _check_refusals(url: str, profile: str | None, region: str) -> None:
    resp = _invoke(url, {"action": "query_ops", "sql": "SELECT 1"}, profile, region)
    if resp.status_code != 400:
        raise _fail(f"query_ops status={resp.status_code} (want 400: the reader has no caller SQL)")
    for verb, label in (("transcript_content", "content"), (_OPS_VERB, "ops verb")):
        resp = _invoke(url, {"action": "named_read", "verb": verb, "params": {}}, profile, region)
        if resp.status_code == 200:
            raise _fail(f"named_read of {label} answered 200")
        _expect_400(resp, f"named_read of {label}", "unknown_verb")


def _check_direct_invoke_denied(profile: str | None, region: str) -> None:
    import boto3  # noqa: PLC0415
    from botocore.exceptions import ClientError  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    client = boto3.Session(profile_name=resolve_aws_profile(profile)).client("lambda", region_name=region)
    try:
        client.invoke(FunctionName=_FUNCTION_NAME, InvocationType="DryRun", Payload=b"{}")
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "unknown"))
        if code == "AccessDeniedException":
            return
        raise _fail(f"direct-invoke probe inconclusive: {code}") from None
    raise _fail("URL-only guard missing: direct invoke allowed")


def _check_reads(url: str, profile: str | None, region: str) -> None:
    health = _expect_ok(
        _invoke(url, {"action": "named_read", "verb": "telemetry_health_count", "params": {}}, profile, region),
        "telemetry_health_count",
    )
    rows = health.get("rows")
    tables = {row.get("table_name") for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()
    if tables != _TABLES:
        raise _fail(f"telemetry_health_count named {len(tables)} tables (want the four event tables)")
    today = datetime.now(timezone.utc).date()
    window = {"day_from": (today - timedelta(days=1)).isoformat(), "day_to": today.isoformat()}
    params = {**window, "as_of": datetime.now(timezone.utc).isoformat()}
    _expect_ok(
        _invoke(url, {"action": "named_read", "verb": "sessions_window", "params": params}, profile, region),
        "sessions_window",
    )
    for verb in _USAGE_VERBS:
        body = _expect_ok(_invoke(url, {"action": "named_read", "verb": verb, "params": window}, profile, region), verb)
        usage_rows = body.get("rows")
        if not isinstance(usage_rows, list) or any(
            set(row) != set(cast(tuple[str, ...], VERB_FIELDS[verb])) for row in usage_rows
        ):
            raise _fail(f"{verb} rows do not carry exactly the declared columns")


def _check_sessions(url: str, profile: str | None, region: str) -> None:
    """An absent session answers no rows, a malformed one 400 named_read_input without an echo, then the verification verbs."""

    def call(payload: dict[str, Any]) -> Any:
        return _invoke(url, payload, profile, region)

    def fail(reason: str, detail: object) -> None:
        raise _fail(f"{reason}: {detail}")

    as_of = datetime.now(timezone.utc).isoformat()

    def session(session_id: str) -> Any:
        params = {"session_id": session_id, "as_of": as_of}
        return call({"action": "named_read", "verb": "session_state_and_duration", "params": params})

    if _expect_ok(session(lambda_ec_gates._ABSENT_SESSION), "absent session").get("row_count") != 0:
        raise _fail("a well-formed absent session_id returned rows")
    bad = session(lambda_ec_gates._MALFORMED_MARKER)
    if lambda_ec_gates._MALFORMED_MARKER in bad.text:
        raise _fail(f"a malformed session_id was echoed status={bad.status_code}")
    _expect_400(bad, "malformed session", "named_read_input")
    lambda_ec_gates._verification_verb_checks(call, fail)


def lambda_telemetry_reader(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """Prove the telemetry reader is URL-only, production-pinned, READ_ONLY, content-free, reads the event tables and
    answers the session and verification-verb checks."""
    url = core._function_url("telemetry_reader")
    _check_unsigned(url, profile, region)
    _check_describe(url, profile, region)
    _check_attach(url, profile, region)
    _check_refusals(url, profile, region)
    _check_direct_invoke_denied(profile, region)
    _check_reads(url, profile, region)
    _check_sessions(url, profile, region)
    print(_SUCCESS)
