"""OQ.12 canary-rehearsal orchestration for the DuckLake Neon smoke suite (pre-deploy, CC-web/443).

Publishes candidate DuckLake Lambda layers, stands up ephemeral writer/reader/maintenance canaries
on a scratch meta-schema/data-path, proves ATTACH + read-your-write, exercises Neon's native
copy-on-write branching for a real-prod read-clone (Decision 100), and tears everything down in a
finally block. All AWS API calls happen over 443 from CC-web; TCP/5432 happens server-side in
Lambda.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any
from uuid import uuid4

from scripts.ducklake_smoke import core
from src.common import neon_api

_CANARY_SCRATCH_META = "ducklake_canary_rehearsal"
_CANARY_FUNCTION_NAMES = {
    "writer": "ducklake-writer-canary-ephemeral",
    "reader": "ducklake-reader-canary-ephemeral",
    "maintenance": "ducklake-maintenance-canary-ephemeral",
}
_CANARY_PROD_FUNCTION_NAMES = {
    "writer": "agent-platform-ducklake-writer",
    "reader": "agent-platform-ducklake-reader",
    "maintenance": "agent-platform-ducklake-maintenance",
}
_CANARY_ZIP_KEYS = {
    "writer": "lambda-packages/ducklake-writer.zip",
    "reader": "lambda-packages/ducklake-reader.zip",
    "maintenance": "lambda-packages/ducklake-maintenance.zip",
}
_CANARY_HANDLERS = {
    "writer": "src.lambdas.ducklake_writer.handler.handler",
    "reader": "src.lambdas.ducklake_reader.handler.handler",
    "maintenance": "src.lambdas.ducklake_maintenance.handler.handler",
}


def _aws_cmd(args_list: list[str], *, profile: str | None) -> list[str]:
    """Prepend [aws] and optional --profile to a command list."""
    cmd = ["aws"] + args_list
    if profile:
        cmd += ["--profile", profile]
    return cmd


def _publish_candidate_layers(*, bucket: str, profile: str | None, region: str) -> dict[str, str]:
    """Call build_lambda --ducklake-publish-canary-layers and parse the ARN JSON from stdout.

    Returns a dict mapping layer name -> version ARN for all three DuckLake layers.
    Loud-fail if the subprocess errors or no ARN JSON is found in stdout.
    """
    cmd = [
        sys.executable,
        "-m",
        "scripts.build_lambda",
        "--ducklake-publish-canary-layers",
        "--bucket",
        bucket,
        "--region",
        region,
    ]
    if profile:
        cmd += ["--profile", profile]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise core.SmokeTestFailure(f"publish-canary-layers failed rc={result.returncode}: {result.stderr.strip()[:300]}")
    for line in reversed(result.stdout.strip().splitlines()):
        try:
            data = json.loads(line)
            if isinstance(data, dict) and data:
                return data
        except json.JSONDecodeError:
            continue
    raise core.SmokeTestFailure(f"publish-canary-layers: no ARN JSON found in output: {result.stdout[:300]}")


def _get_function_role_arn(fn_name: str, *, profile: str | None, region: str) -> str:
    """Get the execution role ARN from an existing Lambda function's configuration."""
    cmd = _aws_cmd(
        [
            "lambda",
            "get-function-configuration",
            "--function-name",
            fn_name,
            "--query",
            "Role",
            "--output",
            "text",
            "--region",
            region,
        ],
        profile=profile,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise core.SmokeTestFailure(f"get-function-configuration {fn_name} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def _canary_create_function(
    fn_name: str,
    *,
    handler: str,
    bucket: str,
    zip_key: str,
    role_arn: str,
    layer_arns: list[str],
    env_vars: dict[str, str],
    profile: str | None,
    region: str,
) -> None:
    """Create an ephemeral Lambda function on the candidate layers.

    Loud-fail if create-function returns non-zero. Reuses an existing execution role ARN --
    no new IAM is created (Decision 77). The function is ephemeral: torn down in canary_rehearsal's
    finally block.
    """
    env_str = "Variables={" + ",".join(f"{k}={v}" for k, v in env_vars.items()) + "}"
    cmd = _aws_cmd(
        [
            "lambda",
            "create-function",
            "--function-name",
            fn_name,
            "--runtime",
            "python3.12",
            "--role",
            role_arn,
            "--handler",
            handler,
            "--code",
            f"S3Bucket={bucket},S3Key={zip_key}",
            "--layers",
        ]
        + layer_arns
        + [
            "--environment",
            env_str,
            "--timeout",
            "900",
            "--memory-size",
            "512",
            "--region",
            region,
        ],
        profile=profile,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise core.SmokeTestFailure(f"create-function {fn_name} failed: {result.stderr.strip()[:300]}")
    _wait_function_active(fn_name, profile=profile, region=region)


def _wait_function_active(fn_name: str, *, profile: str | None, region: str) -> None:
    """Block until a freshly-created Lambda leaves the Pending state (State=Active).

    A just-created function is briefly Pending; invoking it then raises ResourceConflictException.
    The function-active-v2 waiter polls until the function is Active (Decision 55: loud-fail on a
    waiter error rather than racing the first invoke).
    """
    cmd = _aws_cmd(
        ["lambda", "wait", "function-active-v2", "--function-name", fn_name, "--region", region],
        profile=profile,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise core.SmokeTestFailure(f"wait function-active-v2 {fn_name} failed: {result.stderr.strip()[:300]}")


def _canary_delete_function(fn_name: str, *, profile: str | None, region: str) -> bool:
    """Delete an ephemeral Lambda function. Returns True on success (including 404 = already gone)."""
    cmd = _aws_cmd(["lambda", "delete-function", "--function-name", fn_name, "--region", region], profile=profile)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    return result.returncode == 0


def _lambda_invoke_cli(fn_name: str, payload: dict[str, Any], *, profile: str | None, region: str) -> dict[str, Any]:
    """Invoke a Lambda function via `aws lambda invoke` (over 443 -- CC-web compatible).

    Returns the parsed response body dict. Loud-fail if the AWS CLI call errors.
    Does NOT check for ok=True -- callers inspect the body.
    """
    import tempfile as _tf  # noqa: PLC0415

    with _tf.TemporaryDirectory() as tmp:
        out_path = f"{tmp}/response.json"
        cmd = _aws_cmd(
            [
                "lambda",
                "invoke",
                "--function-name",
                fn_name,
                "--payload",
                json.dumps(payload),
                "--cli-binary-format",
                "raw-in-base64-out",
                "--region",
                region,
                out_path,
            ],
            profile=profile,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if result.returncode != 0:
            raise core.SmokeTestFailure(
                f"lambda invoke {fn_name} failed rc={result.returncode}: {result.stderr.strip()[:300]}"
            )
        with open(out_path) as f:
            raw = json.load(f)
    # `aws lambda invoke` returns the function's RAW return value. The DuckLake handlers wrap their
    # payload in a Function-URL envelope {"statusCode", "headers", "body"} where body is a JSON string;
    # unwrap it so callers read the action payload directly (parity with _ok_json over the Function URL).
    # A handler runtime error ({"errorMessage", "errorType"}) has no statusCode/body -- returned as-is.
    if isinstance(raw, dict) and "statusCode" in raw and "body" in raw:
        body = raw["body"]
        return json.loads(body) if isinstance(body, str) else body
    return raw


def canary_rehearsal(
    *,
    profile: str | None = None,
    region: str = "eu-west-2",
    json_output: bool = False,
    project_id: str | None = None,
) -> None:
    """OQ.12 pre-deploy clone-rehearsal from CC-web (no TCP/5432).

    Full orchestration (all AWS API calls over 443; TCP/5432 happens server-side inside Lambda):
      (1) Publish candidate DuckLake layers via build_lambda --ducklake-publish-canary-layers.
      (2) Create ephemeral writer/reader/maintenance canaries on candidate layers + scratch env
          (DUCKLAKE_META_SCHEMA=ducklake_canary_rehearsal, DUCKLAKE_DATA_PATH=.../ducklake/_canary_rehearsal/).
      (3) Prove ATTACH: writer canary action=create_tables at the scratch catalog.
      (4) Prove RYW: reader canary reads back the probe row.
      (5) Real-prod read-clone: maintenance canary action=clone_catalog.
      (6) Teardown in finally: delete canaries, catalog_reinit scratch meta-schema, delete scratch S3 prefix.

    OCC/latency churn is NOT measured here: the canary environment uses freshly-created Lambdas that pay
    full DuckDB init + extension load + Neon ATTACH (~15s) on every churn_single invocation because
    churn_single bypasses the warm-connection cache by design. The CD.33 2000ms budget was calibrated for
    warm steady-state production Lambda containers, not cold-start canaries. The canonical OCC gate is
    lambda_churn (EC8) on the deployed production Lambdas, which have warm cached connections.

    project_id (default: env NEON_PROJECT_ID) skips the org-level project listing, which a project-scoped Neon
    key cannot make; branch create and delete are already project-scoped.

    With json_output=True, emits a JSON dict for VP10 isolation check including torn_down + scratch identifiers.
    Loud-fail (SmokeTestFailure) on any gate miss (Decision 55).
    """
    _bucket = core.SMOKE_DATA_PATH.split("/")[2]
    scratch_data_path = f"s3://{_bucket}/ducklake/_canary_rehearsal/"
    scratch_meta = _CANARY_SCRATCH_META

    out: dict[str, Any] = {
        "attach_ok": False,
        "ryw_ok": False,
        "clone_ok": False,
        "torn_down": {
            "canary_functions": False,
            "scratch_meta": False,
            "branch": False,
            "scratch_s3_prefix": False,
        },
        "scratch": {
            "meta_schema": scratch_meta,
            "data_path": scratch_data_path,
            "branch_id": None,
        },
    }
    # When emitting JSON on stdout, intermediate progress lines go to stderr so the pipe is clean.
    _progress_file = sys.stderr if json_output else sys.stdout

    api_key = neon_api.fetch_api_key(profile=profile)
    project_id = project_id or os.environ.get("NEON_PROJECT_ID")
    if not project_id:
        try:
            project_id = neon_api.resolve_project_id(api_key)
        except neon_api.NeonApiError as exc:
            raise core.SmokeTestFailure(
                f"canary-rehearsal: cannot list Neon projects ({exc}); pass --project-id or set NEON_PROJECT_ID "
                "(a project-scoped key cannot list projects)"
            ) from exc

    layer_arns = _publish_candidate_layers(bucket=_bucket, profile=profile, region=region)
    layer_arn_list = list(layer_arns.values())

    role_arns = {
        role: _get_function_role_arn(prod_fn, profile=profile, region=region)
        for role, prod_fn in _CANARY_PROD_FUNCTION_NAMES.items()
    }

    scratch_env = {"DUCKLAKE_META_SCHEMA": scratch_meta, "DUCKLAKE_DATA_PATH": scratch_data_path}
    writer_fn = _CANARY_FUNCTION_NAMES["writer"]
    reader_fn = _CANARY_FUNCTION_NAMES["reader"]
    maint_fn = _CANARY_FUNCTION_NAMES["maintenance"]

    try:
        for role, fn_name in _CANARY_FUNCTION_NAMES.items():
            _canary_create_function(
                fn_name,
                handler=_CANARY_HANDLERS[role],
                bucket=_bucket,
                zip_key=_CANARY_ZIP_KEYS[role],
                role_arn=role_arns[role],
                layer_arns=layer_arn_list,
                env_vars=scratch_env,
                profile=profile,
                region=region,
            )

        # Initialize the scratch catalog before the writer ATTACHes: DuckLake v1.0 does not auto-create
        # the Postgres meta-schema on ATTACH (it errors "Schema not found"), so the maintenance canary's
        # catalog_reinit drops (no-op if absent) + recreates the empty scratch meta-schema and
        # ATTACH-initializes its metadata tables at the scratch DATA_PATH. Idempotent across re-runs; the
        # writer's create_tables below then ATTACHes into the initialized scratch catalog.
        reinit_body = _lambda_invoke_cli(
            maint_fn,
            {
                "action": "catalog_reinit",
                "meta_schema": scratch_meta,
                "data_path": scratch_data_path,
                "confirm": scratch_meta,
            },
            profile=profile,
            region=region,
        )
        if not reinit_body.get("ok"):
            raise core.SmokeTestFailure(f"CANARY_ATTACH FAIL (catalog_reinit init): {reinit_body}")

        create_body = _lambda_invoke_cli(
            writer_fn, {"action": "create_tables", "force_recreate_tables": True}, profile=profile, region=region
        )
        if not create_body.get("ok"):
            raise core.SmokeTestFailure(f"CANARY_ATTACH FAIL (create_tables): {create_body}")
        out["attach_ok"] = True
        print(f"CANARY_ATTACH OK (scratch catalog at {scratch_data_path})", file=_progress_file)

        probe_id = uuid4().hex
        write_body = _lambda_invoke_cli(
            writer_fn,
            {"action": "write", "record": {"rec_id": probe_id, "payload": "canary-probe"}},
            profile=profile,
            region=region,
        )
        if not write_body.get("ok"):
            raise core.SmokeTestFailure(f"CANARY_RYW probe write FAIL: {write_body}")

        read_body = _lambda_invoke_cli(
            reader_fn, {"action": "read_current", "rec_id": probe_id}, profile=profile, region=region
        )
        rows = read_body.get("rows") or []
        if not rows:
            raise core.SmokeTestFailure(f"CANARY_RYW FAIL: probe {probe_id!r} not found (body: {read_body})")
        out["ryw_ok"] = True
        print(f"CANARY_RYW OK probe {probe_id!r} verified via reader canary", file=_progress_file)

        branch_id: str | None = None
        try:
            branch_info = neon_api.create_branch(api_key, project_id)
            branch_id = branch_info["branch_id"]
            branch_host = branch_info["host"]
            out["scratch"]["branch_id"] = branch_id
            # The Neon branch is a COW of the production catalog, which records its own
            # data_path internally. DuckLake rejects an ATTACH whose data_path argument
            # does not match the stored value -- pass the production path, not the scratch path.
            prod_data_path = f"s3://{_bucket}/ducklake/"
            clone_body = _lambda_invoke_cli(
                maint_fn,
                {"action": "clone_catalog", "branch_host": branch_host, "data_path": prod_data_path},
                profile=profile,
                region=region,
            )
            if not clone_body.get("ok"):
                raise core.SmokeTestFailure(f"CANARY_CLONE_CATALOG FAIL: {clone_body}")
            out["clone_ok"] = True
            print(
                f"CANARY_CLONE_CATALOG OK branch_id={branch_id!r} meta_schema={clone_body.get('meta_schema')!r}",
                file=_progress_file,
            )
        finally:
            if branch_id is not None:
                neon_api.delete_branch(api_key, project_id, branch_id)
            out["torn_down"]["branch"] = branch_id is not None

    finally:
        all_fn_deleted = True
        for fn_name in _CANARY_FUNCTION_NAMES.values():
            if not _canary_delete_function(fn_name, profile=profile, region=region):
                all_fn_deleted = False
        out["torn_down"]["canary_functions"] = all_fn_deleted

        try:
            maint_url = core._function_url("maintenance")
            reinit_resp = core._sigv4_invoke(
                maint_url,
                {
                    "action": "catalog_reinit",
                    "meta_schema": scratch_meta,
                    "confirm": scratch_meta,
                    "data_path": scratch_data_path,
                },
                profile=profile,
                region=region,
            )
            out["torn_down"]["scratch_meta"] = reinit_resp.status_code == 200
        except Exception:  # noqa: BLE001
            out["torn_down"]["scratch_meta"] = False

        rm_cmd = _aws_cmd(["s3", "rm", "--recursive", scratch_data_path, "--region", region], profile=profile)
        rm_result = subprocess.run(rm_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        out["torn_down"]["scratch_s3_prefix"] = rm_result.returncode == 0

    if json_output:
        print(json.dumps(out))
        return

    print(
        f"CANARY_REHEARSAL OK attach={out['attach_ok']} "
        f"ryw={out['ryw_ok']} clone={out['clone_ok']} torn_down={out['torn_down']}"
    )
