"""AWS and Neon calls for the restore rehearsal orchestrator (rec-5023), all in memory.

The target DSN and the presigned dump URL never touch a file, argv or stdout: the DSN is passed only in the restore
function's Environment at create time, the URL only in one invoke payload. Calls go over 443 (boto3); all SQL runs inside
the ephemeral functions. Function names never start with agent-platform- (CI roles hold lambda:Get* on that prefix).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from scripts.ducklake_smoke import canary
from scripts.ducklake_smoke import restore_rehearsal_evidence as ev
from src.common import ducklake_runtime as rt
from src.common import neon_api

BUCKET = rt.SMOKE_DATA_PATH.split("/")[2]
SMOKE_PREFIX = "ducklake-neon-smoke/"
REGION = "eu-west-2"
RESTORE_FN = "maintenance-restore-rehearsal-ephemeral"
CHECKSUM_FN = "reader-restore-rehearsal-ephemeral"
PROD_MAINTENANCE_FN = "agent-platform-ducklake-maintenance"
SMOKE_MAINTENANCE_FN = "agent-platform-ducklake-maintenance-smoke"
READER_FN = "agent-platform-ducklake-reader"
CATALOG_DR_FN = "agent-platform-ducklake-catalog-dr"
RECORD_KEY = "deploy-records/ducklake/agent-platform-ducklake-maintenance.json"
HANDLER = "src.lambdas.ducklake_maintenance.handler.handler"
COPY_SOURCES = ("ducklake/", "ducklake-telemetry/", "telemetry-blobs/")
DUMP_TIMEOUT_S = 330
INVOKE_READ_TIMEOUT_S = 960
PRESIGN_SECONDS = 900


class RehearsalError(RuntimeError):
    """Orchestrator failure; its text is always redacted before it is raised."""


class LimitHit(RehearsalError):
    """A Lambda limit stop (timeout, or ENOSPC on /tmp): a result, recorded as host_limit."""

    def __init__(self, step: str, limit: str, elapsed_s: float) -> None:
        super().__init__(f"{step} hit {limit}")
        self.step, self.limit, self.elapsed_s = step, limit, round(elapsed_s)


def scratch_prefix(run_id: str) -> str:
    return f"{SMOKE_PREFIX}restore-rehearsal/{run_id}/"


def scratch_key(run_id: str, relative: str) -> str:
    """The one scratch key builder: refuses anything that does not stay inside the run's scratch prefix."""
    prefix = scratch_prefix(run_id)
    key = f"{prefix}{relative.lstrip('/')}"
    if ".." in relative or not key.startswith(prefix):
        raise RehearsalError("key outside the rehearsal scratch prefix")
    return key


def session(profile: str | None, region: str = REGION) -> Any:
    import boto3  # noqa: PLC0415

    return boto3.Session(profile_name=profile, region_name=region)


def _code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__))


def read_deploy_record(dev: Any) -> dict[str, Any]:
    body = dev.client("s3").get_object(Bucket=BUCKET, Key=RECORD_KEY)["Body"].read()
    record = json.loads(body)
    if not record.get("source_git_sha") or not record.get("code_sha256"):
        raise RehearsalError("deploy record lacks source_git_sha or code_sha256")
    return record


def function_config(admin: Any, name: str) -> dict[str, Any]:
    cfg = admin.client("lambda").get_function_configuration(FunctionName=name)
    return {
        "CodeSha256": cfg["CodeSha256"],
        "Role": cfg["Role"],
        "MemorySize": cfg["MemorySize"],
        "Layers": [layer["Arn"] for layer in cfg.get("Layers", [])],
    }


def function_exists(admin: Any, name: str) -> bool:
    try:
        admin.client("lambda").get_function(FunctionName=name)
    except Exception as exc:  # noqa: BLE001
        if _code(exc) == "ResourceNotFoundException":
            return False
        raise
    return True


def function_url(admin: Any, name: str) -> str:
    return admin.client("lambda").get_function_url_config(FunctionName=name)["FunctionUrl"].rstrip("/")


def leftover_branches(api_key: str, project_id: str) -> list[str]:
    return [b["name"] for b in neon_api.list_branches(api_key, project_id) if b["name"].startswith("restore_rehearsal_")]


def scratch_objects(dev: Any, run_id: str | None = None) -> list[str]:
    """Keys under restore-rehearsal/ (or one run's prefix), listed as agent_platform."""
    prefix = scratch_prefix(run_id) if run_id else f"{SMOKE_PREFIX}restore-rehearsal/"
    keys: list[str] = []
    for page in dev.client("s3").get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", [])]
    return keys


def prefix_size(dev: Any, prefix: str) -> dict[str, int]:
    count = total = 0
    for page in dev.client("s3").get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            count, total = count + 1, total + obj["Size"]
    return {"objects": count, "bytes": total}


def copy_prefix(dev: Any, run_id: str, source: str = "ducklake/") -> dict[str, int]:
    """In-account server-side copy of the ops prefix into the run's scratch prefix; counts only."""
    s3 = dev.client("s3")
    started = time.monotonic()
    objects = [
        o
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=source)
        for o in page.get("Contents", [])
    ]

    def _copy(obj: dict[str, Any]) -> int:
        dest = scratch_key(run_id, obj["Key"])
        s3.copy({"Bucket": BUCKET, "Key": obj["Key"]}, BUCKET, dest)
        return int(obj["Size"])

    with ThreadPoolExecutor(max_workers=16) as pool:
        sizes = list(pool.map(_copy, objects))
    return {"objects": len(objects), "bytes": sum(sizes), "ms": int((time.monotonic() - started) * 1000)}


def head_exists(dev: Any, key: str) -> bool:
    try:
        dev.client("s3").head_object(Bucket=BUCKET, Key=key)
    except Exception as exc:  # noqa: BLE001
        if _code(exc) in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def delete_scratch(dev: Any, run_id: str) -> int:
    """Teardown fallback as agent_platform, through the scratch key builder only."""
    s3, deleted = dev.client("s3"), 0
    for key in scratch_objects(dev, run_id):
        s3.delete_object(Bucket=BUCKET, Key=scratch_key(run_id, key[len(scratch_prefix(run_id)) :]))
        deleted += 1
    return deleted


def dump_catalog(admin: Any, url: str, region: str = REGION) -> dict[str, Any]:
    """One SigV4-signed POST to the catalog-dr Function URL (timeout 330 s; the function's own is 300 s)."""
    import requests  # noqa: PLC0415
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415

    body = "{}"
    req = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
    SigV4Auth(admin.get_credentials().get_frozen_credentials(), "lambda", region).add_auth(req)
    resp = requests.post(url, data=body, headers=dict(req.headers), timeout=DUMP_TIMEOUT_S)
    if resp.status_code != 200:
        raise RehearsalError(f"dump failed with status {resp.status_code}")
    out = resp.json()
    if not out.get("ok") or not out.get("s3_key") or not out.get("bucket"):
        raise RehearsalError("dump response lacked ok, s3_key or bucket")
    ev.register_bucket(out["bucket"])
    return out


def presign_dump(admin: Any, bucket: str, key: str) -> str:
    url = admin.client("s3").generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=PRESIGN_SECONDS
    )
    ev.register_secret(url)
    return url


def create_target(api_key: str, project_id: str, run_id: str, on_branch: Any = None) -> dict[str, str]:
    """Child branch, role and empty database, all named restore_rehearsal_<run_id>; returns branch_id and the DSN URI."""
    from src.common import catalog_dr  # noqa: PLC0415

    name = f"restore_rehearsal_{run_id}"
    branch = neon_api.create_branch(api_key, project_id, name=name)
    out = {"branch_id": branch["branch_id"]}
    if on_branch is not None:
        on_branch(branch["branch_id"])
    password = neon_api.create_role(api_key, project_id, branch["branch_id"], name)
    ev.register_secret(password)
    neon_api.create_database(api_key, project_id, branch["branch_id"], name, name)
    uri = catalog_dr.dsn_uri({"host": branch["host"], "dbname": name, "username": name, "password": password})
    ev.register_secret(uri)
    ev.register_secret(branch["host"])
    out["dsn_uri"] = uri
    return out


def create_function(
    admin: Any,
    name: str,
    *,
    zip_key: str,
    role: str,
    layers: list[str],
    memory: int,
    env: dict[str, str],
    profile: str | None,
    region: str = REGION,
) -> str:
    """Create one ephemeral function; returns CodeSha256 (the only create-response field read)."""
    client = admin.client("lambda")
    created = client.create_function(
        FunctionName=name,
        Runtime="python3.12",
        Role=role,
        Handler=HANDLER,
        Code={"S3Bucket": BUCKET, "S3Key": zip_key},
        Timeout=900,
        MemorySize=memory,
        EphemeralStorage={"Size": 10240},
        Layers=layers,
        Architectures=["x86_64"],
        Environment={"Variables": env},
    )
    sha = created["CodeSha256"]
    canary._wait_function_active(name, profile=profile, region=region)
    client.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=1)
    return sha


def invoker(admin: Any) -> Any:
    from botocore.config import Config  # noqa: PLC0415

    cfg = Config(read_timeout=INVOKE_READ_TIMEOUT_S, retries={"total_max_attempts": 1})
    return admin.client("lambda", config=cfg)


def invoke_step(client: Any, fn_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke one step; a Lambda timeout is a LimitHit, anything unexpected is a redacted RehearsalError."""
    step = str(payload.get("step"))
    started = time.monotonic()
    try:
        resp = client.invoke(FunctionName=fn_name, Payload=json.dumps(payload).encode())
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "ReadTimeoutError":
            raise LimitHit(step, "lambda_timeout_900s", time.monotonic() - started) from None
        raise RehearsalError(f"invoke {step} failed: {ev.redact(type(exc).__name__)}") from None
    raw = resp["Payload"].read()
    if resp.get("FunctionError"):
        if b"Task timed out" in raw:
            raise LimitHit(step, "lambda_timeout_900s", time.monotonic() - started)
        raise RehearsalError(f"invoke {step} raised a function error")
    body = json.loads(raw)
    if isinstance(body, dict) and "statusCode" in body and "body" in body:
        if body["statusCode"] != 200:
            raise RehearsalError(f"step {step} returned status {body['statusCode']}")
        body = json.loads(body["body"]) if isinstance(body["body"], str) else body["body"]
    return body


def delete_function_confirmed(admin: Any, name: str, *, profile: str | None, region: str = REGION) -> bool:
    """Delete, then confirm with get_function: a spurious False on an already-gone function counts as torn down."""
    deleted = canary._canary_delete_function(name, profile=profile, region=region)
    return deleted or not function_exists(admin, name)
