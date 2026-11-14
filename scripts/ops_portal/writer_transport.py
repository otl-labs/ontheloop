"""DuckLake writer SigV4 transport (T2.19 / Decision 81; sole backend per Decision 84 I-1).

Two transports reach the writer: its AWS_IAM Function URL, or, where OTL_OPS_VERBS_TRANSPORT=execute_api selects it
(agent environments, whose proxy cannot sign the Function URL hosts), the ops/writer route of the IAM-auth REST API.

Owner-concern: getting a validated record onto the wire and back. The Single-Portal
caller surface (file_rec/update_rec/file_decision/update_decision/sync) is unchanged;
this module is the transport underneath the closed writer/reader Function-URL boundary.

Public-repo boundary (Decision 101): URL/ARN/account resolution stays 100% RUNTIME
(env DUCKLAKE_WRITER_URL / SSM / terraform output / lambda API) -- no Function-URL,
account-id, or ARN literal is inlined here.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Optional

from scripts.aws_profile import resolve_aws_profile
from scripts.ops_portal._common import _AWS_REGION, _SSO_PROFILE

logger = logging.getLogger(__name__)

_DUCKLAKE_WRITER_URL_ENV = "DUCKLAKE_WRITER_URL"
_DUCKLAKE_WRITER_FUNCTION_NAME = "agent-platform-ducklake-writer"
# SSM path declared in src/lambdas/ducklake_writer/manifest.yaml runtime_config[] (Decision 79 SSOT).
_DUCKLAKE_WRITER_SSM_PATH = "/agent-platform/ducklake/writer_url"

# Portal table -> DuckLake ops_* table (the writer/reader select schema by this name).
_PORTAL_TABLE_NAMES = ("ops_recommendations", "ops_decisions")

# Writer 5xx statuses retried once the request is idempotent (Neon scale-to-zero cold resume --
# same rationale as the reader's transient retry, src/common/ducklake_reader_client.py).
_WRITER_TRANSIENT_STATUS = (502, 503, 504)
_WRITER_MAX_ATTEMPTS = 3
_WRITER_RETRY_BACKOFF_S = (2.0, 5.0)


def _resolve_writer_url(profile: Optional[str] = None) -> str:
    """Resolve the ducklake_writer Function URL.

    Under OTL_OPS_VERBS_TRANSPORT=execute_api the order is DUCKLAKE_WRITER_URL, then the ops/writer API route
    from SSM, with no Function URL fallback. Otherwise (Decision 79 SSOT):
      1. env DUCKLAKE_WRITER_URL -- CI / explicit override
      2. SSM /agent-platform/ducklake/writer_url -- CC-web (no terraform binary)
      3. terraform output ducklake_writer_function_url -- local dev with initialized checkout
      4. lambda:GetFunctionUrlConfig -- last resort (CI runner, github_ci OIDC role)

    Loud-fail if all four are unavailable.
    """
    from src.common.ducklake_reader_client import (  # noqa: PLC0415
        _resolve_function_url_via_api as _api_resolver,
    )
    from src.common.ducklake_reader_client import (
        _resolve_function_url_via_ssm as _ssm_resolver,
    )
    from src.common.ducklake_reader_client import api_route_url, api_transport  # noqa: PLC0415

    url = os.environ.get(_DUCKLAKE_WRITER_URL_ENV)
    if url:
        return url.rstrip("/")
    if api_transport():
        return api_route_url("ops/writer", profile, _AWS_REGION).rstrip("/")
    ssm_url = _ssm_resolver(_DUCKLAKE_WRITER_SSM_PATH, profile=profile, region=_AWS_REGION)
    if ssm_url:
        return ssm_url
    try:
        proc = subprocess.run(
            ["terraform", "-chdir=terraform/personal", "output", "-raw", "ducklake_writer_function_url"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().rstrip("/")
    except FileNotFoundError:
        pass
    api_url = _api_resolver(_DUCKLAKE_WRITER_FUNCTION_NAME, profile=profile, region=_AWS_REGION)
    if api_url:
        return api_url.rstrip("/")
    raise RuntimeError(
        f"{_DUCKLAKE_WRITER_URL_ENV} not set, SSM {_DUCKLAKE_WRITER_SSM_PATH!r} unavailable, "
        "terraform output 'ducklake_writer_function_url' unavailable, and "
        "lambda:GetFunctionUrlConfig fallback failed -- cannot reach the DuckLake writer "
        "(Decision 84: DuckLake is the sole ops backend)."
    )


def _project_ops_record(table: str, record: dict) -> dict:
    """Project a validated record onto the table's INPUT columns for the writer schema gate.

    Drops derived fields (ulid/created_timestamp/last_updated_timestamp -- the runtime mints them)
    and any non-schema keys (e.g. the Decision-56-deprecated `date`). Keeps the merge key + business
    inputs. Mirrors the writer's schema gate so the request is accepted on the first try.
    """
    from src.common.ducklake_runtime import resolve_table_spec  # noqa: PLC0415

    spec = resolve_table_spec(table)
    inputs = {name for name, fspec in spec.fields.items() if fspec.get("role") == "input"}
    return {k: v for k, v in record.items() if k in inputs}


def _ducklake_write(
    table: str,
    record: dict,
    *,
    action: str,
    profile: Optional[str] = None,
    idempotency_ulid: Optional[str] = None,
) -> dict:
    """Invoke the ducklake_writer Function URL (SigV4) for a production ops write. Loud-fail on error.

    action is 'file_ops' (create; the writer allocates the entity id and returns it as `key`),
    'write_ops' (caller-keyed upsert: ETL backfill + test- probes), or 'update_ops' (update; the
    writer enforces the in-tx referential existence check). `idempotency_ulid` makes file_ops
    replay-safe, which is what licenses the transient-5xx retry below (Neon cold-resume): a retried
    request returns the originally allocated id instead of double-filing. Maps the writer's
    loud-fail status codes back to portal exceptions.
    """
    import time as _time  # noqa: PLC0415

    import boto3  # noqa: PLC0415
    import requests  # noqa: PLC0415
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415

    from src.common.ducklake_reader_client import signing_scope  # noqa: PLC0415

    url = _resolve_writer_url(profile=profile)
    service, region = signing_scope(url, _AWS_REGION)
    payload = {"action": action, "table": table, "record": _project_ops_record(table, record)}
    if idempotency_ulid is not None:
        payload["idempotency_ulid"] = idempotency_ulid
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    session = boto3.Session(profile_name=resolve_aws_profile(profile, default=_SSO_PROFILE))
    creds = session.get_credentials().get_frozen_credentials()

    retryable = idempotency_ulid is not None or action == "update_ops"
    last_status: Optional[int] = None
    last_text = ""
    for attempt in range(_WRITER_MAX_ATTEMPTS):
        # Re-sign per attempt: SigV4 carries a timestamp.
        aws_req = AWSRequest(method="POST", url=url, data=body, headers=dict(headers))
        SigV4Auth(creds, service, region).add_auth(aws_req)
        try:
            resp = requests.post(url, data=body, headers=dict(aws_req.headers), timeout=180)
        except requests.RequestException as exc:
            # The response-lost case the idempotency key exists FOR: the write may have committed.
            # Retrying with the SAME body/ULID makes the writer replay-check return the original
            # allocation instead of double-filing.
            last_status, last_text = None, f"{type(exc).__name__}: {exc}"
            if retryable and attempt < _WRITER_MAX_ATTEMPTS - 1:
                logger.warning(
                    "ducklake_writer %s connection failure (attempt %d/%d): %s -- retrying same ULID",
                    action,
                    attempt + 1,
                    _WRITER_MAX_ATTEMPTS,
                    exc,
                )
                _time.sleep(_WRITER_RETRY_BACKOFF_S[attempt])
                continue
            raise RuntimeError(f"ducklake_writer {action} {table} failed ({last_text})") from exc
        if resp.status_code == 200:
            return resp.json()
        last_status, last_text = resp.status_code, resp.text[:400]
        if resp.status_code == 409:
            raise RuntimeError(f"ducklake_writer referential failure ({action} {table}): {last_text}")
        if resp.status_code == 422:
            raise ValueError(f"ducklake_writer schema-gate rejection ({action} {table}): {last_text}")
        if resp.status_code == 503 and '"occ_exhausted"' in last_text:
            # OCC budget exhaustion is stop-and-RCA (Decision 55), never blindly re-driven.
            raise RuntimeError(f"ducklake_writer OCC budget exhausted ({action} {table}): {last_text}")
        if retryable and resp.status_code in _WRITER_TRANSIENT_STATUS and attempt < _WRITER_MAX_ATTEMPTS - 1:
            logger.warning(
                "ducklake_writer %s HTTP %d (attempt %d/%d) -- retrying after cold-resume backoff",
                action,
                resp.status_code,
                attempt + 1,
                _WRITER_MAX_ATTEMPTS,
            )
            _time.sleep(_WRITER_RETRY_BACKOFF_S[attempt])
            continue
        break
    raise RuntimeError(f"ducklake_writer {action} {table} failed (HTTP {last_status}): {last_text}")
