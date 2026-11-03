"""Telemetry writer URL-only proofs for the DuckLake Neon smoke suite (T2.36, Decision 213, Decision 236 cl.1).

Two post-deploy gates. --lambda-telemetry-smoke proves the smoke-pinned twin serves only signed Function URL calls on the
ducklake_smoke catalog and that its caller cannot direct-invoke it (the live check behind Decision 213), then runs the
heavy vectors: plan 3 part 1's (provision, register_tenant, register_project and telemetry_probe), part 2's
append_telemetry vectors (lambda_telemetry_append_gates) under part 1's registered synthetic project, then plan 4's
claim-check vectors (lambda_telemetry_blob_gates). --lambda-telemetry-production is the light gate on the production
writer (ducklake_telemetry, Decision 230): the same URL-only cases, the Decision 213 direct-invoke denial and the Decision
216 can_create check, with no append, blob or project-registration vector, so it writes no test rows. Gates call
core._function_url / core._sigv4_invoke through the module object (Decision 104).

Failure text carries only a status code, a handler error_type, an exception class name or a request id (each printed
only when it matches its shape) or a botocore error code; never a response body, an error message, an ARN, an account id
or a URL (Decision 101).
"""

from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from scripts.ducklake_smoke import core
from src.common import ducklake_runtime

_WRITER_FUNCTION = "agent-platform-ducklake-telemetry-writer"
_TWIN_FUNCTION = "agent-platform-ducklake-telemetry-writer-smoke"
_DECOY = {"meta_schema": "ducklake_ops", "data_path": "s3://example-invalid/ducklake/"}
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_FAN_OUT = 4
_COUNTED = ("ops_tenants", "ops_projects")
_BACKENDS = ("neon", "rds", "other")
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class _Binding(NamedTuple):
    label: str
    meta_schema: str
    smoke: bool
    login: str


_SMOKE = _Binding("smoke", core.META_SCHEMA, True, "ducklake_telemetry_writer")
_PRODUCTION = _Binding("production", ducklake_runtime.TELEMETRY_PROD_META_SCHEMA, False, "ducklake_telemetry_writer_prod")


def _fail(message: str) -> core.SmokeTestFailure:
    return core.SmokeTestFailure(f"LAMBDA_TELEMETRY FAIL: {message}")


def _body(resp: Any) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _error_type(resp: Any) -> str:
    value = _body(resp).get("error_type")
    return value if isinstance(value, str) else "none"


def _shaped(value: Any, shape: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and shape.match(value) else "none"


def _diagnostics(resp: Any) -> str:
    """ "error_class=X request_id=Y" from the body, else the runtime's x-amzn-RequestId header; "none" off-shape."""
    body = _body(resp)
    headers = getattr(resp, "headers", None) or {}
    header_id = next((v for k, v in headers.items() if str(k).lower() == "x-amzn-requestid"), None)
    request_id = _shaped(body.get("request_id"), _UUID)
    if request_id == "none":
        request_id = _shaped(header_id, _UUID)
    return f"error_class={_shaped(body.get('error_class'), _CLASS_NAME)} request_id={request_id}"


def _expect_ok(resp: Any, check: str) -> dict[str, Any]:
    body = _body(resp)
    if resp.status_code != 200 or body.get("ok") is not True:
        raise _fail(f"{check} status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)}")
    return body


def _check_unsigned(url: str, profile: str | None, region: str) -> None:
    resp = core._sigv4_invoke(url, {"action": "describe"}, sign=False, profile=profile, region=region)
    if resp.status_code == 403 and _error_type(resp) == "missing_caller_identity":
        raise _fail("telemetry writer URL is not AWS_IAM")
    if resp.status_code != 403:
        raise _fail(f"unsigned describe status={resp.status_code} (want 403)")


def _check_describe(url: str, profile: str | None, region: str, *, binding: _Binding = _SMOKE) -> None:
    body = _expect_ok(core._sigv4_invoke(url, {"action": "describe"}, profile=profile, region=region), "describe")
    actions = body.get("actions")
    catalog = body.get("catalog")
    if not isinstance(actions, list) or not {"describe", "attach_check"} <= set(actions):
        raise _fail("describe actions missing describe or attach_check")
    if not isinstance(catalog, dict) or catalog.get("meta_schema") != binding.meta_schema:
        raise _fail(f"describe meta_schema is not the {binding.label} schema")
    if catalog.get("data_path_is_smoke") is not binding.smoke:
        raise _fail(f"describe data_path_is_smoke is not {str(binding.smoke).lower()}")


def _check_attach(url: str, profile: str | None, region: str, *, binding: _Binding = _SMOKE) -> None:
    resp = core._sigv4_invoke(url, {"action": "attach_check", **_DECOY}, profile=profile, region=region)
    body = _expect_ok(resp, "attach_check")
    if body.get("meta_schema") != binding.meta_schema:
        raise _fail(f"attach_check meta_schema is not the {binding.label} schema (event keys honoured)")
    if body.get("data_path_is_smoke") is not binding.smoke:
        raise _fail(f"attach_check data_path_is_smoke is not {str(binding.smoke).lower()}")
    if body.get("catalog_login") != binding.login:
        raise _fail(f"attach_check catalog_login is not the {binding.label} scoped telemetry login")


def _check_unknown_action(url: str, profile: str | None, region: str) -> None:
    resp = core._sigv4_invoke(url, {"action": "no_such_action"}, profile=profile, region=region)
    if resp.status_code != 400 or _error_type(resp) != "unknown_action":
        raise _fail(
            f"unknown action status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} "
            "(want 400 unknown_action)"
        )


def _check_direct_invoke_denied(function_name: str, profile: str | None, region: str) -> None:
    import boto3  # noqa: PLC0415
    from botocore.exceptions import ClientError  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    client = boto3.Session(profile_name=resolve_aws_profile(profile)).client("lambda", region_name=region)
    try:
        client.invoke(FunctionName=function_name, InvocationType="DryRun", Payload=b"{}")
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "unknown"))
        if code == "AccessDeniedException":
            return
        raise _fail(f"direct-invoke probe inconclusive: {code}") from None
    raise _fail("URL-only guard missing: direct invoke allowed")


def _invoke(url: str, payload: dict[str, Any], profile: str | None, region: str) -> Any:
    return core._sigv4_invoke(url, payload, profile=profile, region=region)


def _check_provision(url: str, profile: str | None, region: str) -> None:
    first = _expect_ok(_invoke(url, {"action": "provision_telemetry"}, profile, region), "provision_telemetry")
    second = _expect_ok(_invoke(url, {"action": "provision_telemetry"}, profile, region), "provision_telemetry (repeat)")
    tables = first.get("tables")
    if not isinstance(tables, list) or len(tables) != 9 or tables != second.get("tables"):
        raise _fail("provision_telemetry did not return one stable set of nine tables")
    print("TELEMETRY_PROVISION_OK")


def _probe(url: str, profile: str | None, region: str) -> dict[str, Any]:
    return _expect_ok(_invoke(url, {"action": "telemetry_probe"}, profile, region), "telemetry_probe")


def _report_measurements(url: str, profile: str | None, region: str) -> None:
    body = _probe(url, profile, region)
    backend, sessions = body.get("catalog_backend"), body.get("catalog_sessions")
    print(f"catalog_backend={backend if backend in _BACKENDS else 'unrecognised'}")
    print(f"catalog_sessions={sessions if isinstance(sessions, int) else 'unavailable'}")


def _check_tenant(url: str, profile: str | None, region: str, *, print_id: bool = True) -> None:
    first = _expect_ok(_invoke(url, {"action": "register_tenant"}, profile, region), "register_tenant")
    second = _expect_ok(_invoke(url, {"action": "register_tenant"}, profile, region), "register_tenant (repeat)")
    tenant_id = first.get("tenant_id")
    if not isinstance(tenant_id, str) or not _ULID.match(tenant_id):
        raise _fail("register_tenant did not return a canonical uppercase ULID")
    if second.get("tenant_id") != tenant_id or second.get("created") is not False:
        raise _fail("register_tenant is not stable across calls (tenant id drift or created not false)")
    print(f"TELEMETRY_TENANT_STABLE tenant_id={tenant_id}" if print_id else "TELEMETRY_TENANT_STABLE")


def _check_project_fan_out(url: str, profile: str | None, region: str) -> str:
    """Register one synthetic project concurrently and return its ref (never printed: a uuid4 hex can hold a 12-digit run)."""
    project_ref = "synthetic:deploy-smoke-" + uuid.uuid4().hex
    payload = {"action": "register_project", "project_ref": project_ref}
    with ThreadPoolExecutor(max_workers=_FAN_OUT) as pool:
        responses = list(pool.map(lambda _i: _invoke(url, payload, profile, region), range(_FAN_OUT)))
    bodies = [_expect_ok(r, "register_project") for r in responses]
    ids = {b.get("project_id") for b in bodies}
    if len(ids) != 1:
        raise _fail(f"concurrent register_project returned {len(ids)} project ids (Decision 200 reversal condition)")
    project_id = next(iter(ids))
    if not isinstance(project_id, str) or not _ULID.match(project_id):
        raise _fail("register_project did not return a canonical uppercase ULID")
    created = sum(1 for b in bodies if b.get("created") is True)
    if created != 1:
        raise _fail(f"concurrent register_project created {created} projects (want exactly 1)")
    if any(b.get("purpose") != "synthetic" or b.get("registration") != "registered" for b in bodies):
        raise _fail("register_project purpose or registration is not synthetic/registered")
    print(f"TELEMETRY_PROJECT_CONVERGED project_id={project_id}")
    return project_ref


def _check_probe(url: str, profile: str | None, region: str, *, binding: _Binding = _SMOKE) -> None:
    _probe(url, profile, region)
    body = _probe(url, profile, region)
    if body.get("connect_reused") is not True:
        raise _fail("telemetry_probe connect_reused is not true on the second back-to-back call")
    if body.get("meta_schema") != binding.meta_schema or body.get("data_path_is_smoke") is not binding.smoke:
        raise _fail(f"telemetry_probe is not bound to the {binding.label} catalog")
    if body.get("catalog_login") != binding.login:
        raise _fail(f"telemetry_probe catalog_login is not the {binding.label} scoped telemetry login")
    if body.get("can_create") is not False:
        raise _fail(
            "telemetry_probe can_create is not false (true: the scoped login can create, Decision 216 cl.1; null: unreadable)"
        )
    tables = body.get("tables")
    if not isinstance(tables, dict) or len(tables) != 9:
        raise _fail("telemetry_probe did not report the nine physical tables")
    missing = [t for t, s in tables.items() if not (isinstance(s, dict) and s.get("present") is True)]
    undeclared = [t for t, s in tables.items() if not (isinstance(s, dict) and s.get("partitioned_as_declared") is True)]
    if missing or undeclared:
        raise _fail(f"telemetry_probe: {len(missing)} tables absent, {len(undeclared)} not partitioned as declared")
    rows, counters, counts = body.get("counter_rows"), body.get("counters"), body.get("current_counts")
    if not (isinstance(rows, dict) and isinstance(counters, dict) and isinstance(counts, dict)):
        raise _fail("telemetry_probe omitted the counter reads")
    for name in _COUNTED:
        value, count = counters.get(name), counts.get(name)
        if rows.get(name) != 1:
            raise _fail(f"telemetry_probe: {name} counter does not have exactly one row")
        if not isinstance(value, int) or not isinstance(count, int) or value < count:
            raise _fail(f"telemetry_probe: {name} counter is below its table count (ulid floor)")
    print("TELEMETRY_PROBE_OK")


def _check_self_only(url: str, profile: str | None, region: str) -> None:
    resp = _invoke(url, {"action": "register_tenant", "principal_ref": "x"}, profile, region)
    if resp.status_code != 400 or _error_type(resp) != "bad_request":
        raise _fail(
            f"self-only register_tenant status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} "
            "(want 400 bad_request)"
        )
    print("TELEMETRY_SELF_ONLY_OK")


def _run(*, role: str, function_name: str, label: str, profile: str | None, region: str) -> None:
    url = core._function_url(role)
    _check_unsigned(url, profile, region)
    _check_describe(url, profile, region)
    _check_attach(url, profile, region)
    _check_unknown_action(url, profile, region)
    _check_direct_invoke_denied(function_name, profile, region)
    _check_provision(url, profile, region)
    _report_measurements(url, profile, region)
    _check_tenant(url, profile, region)
    project_ref = _check_project_fan_out(url, profile, region)
    _check_probe(url, profile, region)
    _check_self_only(url, profile, region)
    from scripts.ducklake_smoke import lambda_telemetry_append_gates  # noqa: PLC0415 -- it imports this module's helpers

    lambda_telemetry_append_gates.lambda_telemetry_append(url, project_ref, profile=profile, region=region)
    from scripts.ducklake_smoke import lambda_telemetry_blob_gates  # noqa: PLC0415 -- it imports this module's helpers

    lambda_telemetry_blob_gates.lambda_telemetry_blob(url, project_ref, profile=profile, region=region)
    suffix = " target=twin" if label == "twin" else ""
    print(f"LAMBDA_TELEMETRY OK unsigned=403 describe=200 attach_check=ok unknown_action=400 direct_invoke=denied{suffix}")


def lambda_telemetry_production(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """The light gate on the production writer: URL-only, production-bound, no CREATE; no append, blob or project row."""
    url = core._function_url("telemetry_writer")
    _check_unsigned(url, profile, region)
    _check_describe(url, profile, region, binding=_PRODUCTION)
    _check_attach(url, profile, region, binding=_PRODUCTION)
    _check_unknown_action(url, profile, region)
    _check_direct_invoke_denied(_WRITER_FUNCTION, profile, region)
    _check_provision(url, profile, region)
    _report_measurements(url, profile, region)
    _check_tenant(url, profile, region, print_id=False)
    _check_probe(url, profile, region, binding=_PRODUCTION)
    _check_self_only(url, profile, region)
    print("LAMBDA_TELEMETRY_PRODUCTION OK unsigned=403 describe=200 attach_check=ok unknown_action=400 direct_invoke=denied")


def lambda_telemetry_smoke(*, profile: str | None = None, region: str = "eu-west-2") -> None:
    """The same proof against the smoke-pinned twin, which carries the disposable deploy vectors (Decision 236 cl.1)."""
    _run(role="telemetry_writer_smoke", function_name=_TWIN_FUNCTION, label="twin", profile=profile, region=region)
