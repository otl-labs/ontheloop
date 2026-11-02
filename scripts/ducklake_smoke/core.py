"""Shared harness plumbing for the DuckLake Neon smoke-test gate package (T2.16b / T2.18 / CD.34).

Owns SmokeTestFailure (the loud-fail signal, Decision 55), the smoke catalog constants
(DSN_PARAMETER_NAME/SMOKE_DATA_PATH/CATALOG_ALIAS/META_SCHEMA), the Function-URL env names, and the
SigV4/JSON Function-URL invocation helpers (_function_url/_sigv4_invoke/_ok_json/_p95). Every
other scripts.ducklake_smoke module calls these through this module object (core.<name>) rather
than importing a copy, so each symbol keeps exactly one canonical patch target (Decision 104).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from src.common import ducklake_runtime

DSN_PARAMETER_NAME = "/secret/ducklake-neon-catalog-dsn"
# Single source of truth: the runtime owns the canonical smoke DATA_PATH. A divergent literal here
# re-introduces drift and can bind the shared catalog to the wrong path on direct pre-checks.
SMOKE_DATA_PATH = ducklake_runtime.SMOKE_DATA_PATH
CATALOG_ALIAS = "ops_catalog"
# Smoke runs in its OWN meta-schema (ducklake_smoke), isolated from the production ducklake_ops catalog
# so it can never pin a DATA_PATH on production again (rec-2099 root-cause fix).
META_SCHEMA = ducklake_runtime.SMOKE_META_SCHEMA

# Function-URL endpoints for the in-Lambda invoke gates (post-deploy). Resolved from env first, then
# terraform output. The URLs are AWS_IAM-protected (SigV4 required; unsigned -> 403).
WRITER_URL_ENV = "DUCKLAKE_WRITER_URL"
READER_URL_ENV = "DUCKLAKE_READER_URL"
MAINTENANCE_URL_ENV = "DUCKLAKE_MAINTENANCE_URL"
# T2.18 c9 split: the CI-invokable smoke sibling (github_ci_branch invoke grant scoped to this
# function ARN only -- see MaintenanceSmokeInvokeCI in terraform/personal/oidc.tf). The four
# maintenance smoke gates below resolve THIS url, never MAINTENANCE_URL_ENV (the admin function).
MAINTENANCE_SMOKE_URL_ENV = "DUCKLAKE_MAINTENANCE_SMOKE_URL"
CATALOG_DR_URL_ENV = "DUCKLAKE_CATALOG_DR_URL"


class SmokeTestFailure(RuntimeError):
    """Raised when a hard gate fails. Loud-fail (Decision 55) -- the caller must stop and RCA."""


# DSN fetch + conninfo now live in ducklake_runtime (single implementation, no drift). Re-exported
# here so existing callers/tests keep the smoke-module entrypoints.
fetch_dsn = ducklake_runtime.fetch_dsn
_libpq_conninfo = ducklake_runtime.libpq_conninfo


def _p95(values: list[float]) -> float:
    """Return the p95 of *values* (nearest-rank). Empty -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def _function_url(role: str) -> str:
    """Resolve the writer/reader/maintenance/maintenance_smoke/catalog_dr Function URL from env,
    then terraform output. Loud-fail if absent."""
    _env_map = {
        "writer": WRITER_URL_ENV,
        "reader": READER_URL_ENV,
        "maintenance": MAINTENANCE_URL_ENV,
        "maintenance_smoke": MAINTENANCE_SMOKE_URL_ENV,
        "catalog_dr": CATALOG_DR_URL_ENV,
    }
    env_name = _env_map.get(role, f"DUCKLAKE_{role.upper()}_URL")
    url = os.environ.get(env_name)
    if url:
        return url.rstrip("/")
    output_name = f"ducklake_{role}_function_url"
    try:
        result = subprocess.run(
            ["terraform", "-chdir=terraform/personal", "output", "-raw", output_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().rstrip("/")
    except FileNotFoundError:
        pass
    raise SmokeTestFailure(
        f"{env_name} not set and terraform output {output_name!r} unavailable -- cannot reach the "
        f"{role} Function URL. Set {env_name} or run from a checkout with terraform state."
    )


def _sigv4_invoke(
    url: str, payload: dict[str, Any], *, profile: str | None = None, region: str = "eu-west-2", sign: bool = True
) -> Any:
    """POST *payload* (JSON) to a Lambda Function URL, optionally SigV4-signed (service 'lambda')."""
    import boto3  # noqa: PLC0415
    import requests  # noqa: PLC0415
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    if sign:
        session = boto3.Session(profile_name=resolve_aws_profile(profile))
        creds = session.get_credentials().get_frozen_credentials()
        aws_req = AWSRequest(method="POST", url=url, data=body, headers=dict(headers))
        SigV4Auth(creds, "lambda", region).add_auth(aws_req)
        headers = dict(aws_req.headers)
    return requests.post(url, data=body, headers=headers, timeout=180)


def _ok_json(resp: Any, *, expect: int = 200) -> dict[str, Any]:
    """Assert the Function-URL response status and return the parsed JSON body. Loud-fail otherwise."""
    if resp.status_code != expect:
        raise SmokeTestFailure(f"unexpected status {resp.status_code} (expected {expect}): {resp.text[:300]}")
    return resp.json()
