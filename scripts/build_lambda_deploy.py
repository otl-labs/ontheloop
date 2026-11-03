#!/usr/bin/env python3
"""AWS deploy/publish + bucket resolution for the Lambda build/deploy tool (Decision 104 pattern).

Holds upload_to_s3, update_lambda_functions, resolve_bucket, validate_bucket_exists,
and publish_canary_layers. Imports only the accessors and constants it
needs from scripts.build_lambda_config (no import of build_lambda_packaging -- no cycle). See
scripts/build_lambda.py for the CLI facade that re-exports this module's public and
test-patched symbols.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts.build_lambda_config import (
    ROOT,
    _aws_profile_args,
    _build_ducklake_function_zip_keys,
    _build_ducklake_layer_names,
    _build_prod_function_names,
)

_DEPLOY_RECORD_BUCKET = "agent-platform-data-lake"


def _resolve_artifact_sha() -> str:
    """Resolve the per-artifact-sha namespace for content-addressed S3 keys (T2.42 c3, DEP-03).

    Precedence: ``LAMBDA_ARTIFACT_SHA`` (explicit override -- the terraform CD workflows export
    this, set to the resolved PR-head-sha the same reviewed plan.bin is keyed by) -> ``GITHUB_SHA``
    (the ambient value every GitHub Actions run sets, so the governed deploy-ducklake-lambdas.yml
    channel resolves a namespace with no workflow edit) -> ``"local"`` (stable fallback for a
    break-glass / non-CI build, so upload_to_s3 always has a deterministic per-sha key to write to).
    """
    return os.environ.get("LAMBDA_ARTIFACT_SHA") or os.environ.get("GITHUB_SHA") or "local"


def _artifact_s3_key(name: str, sha: str) -> str:
    """Content-addressed per-artifact-sha S3 key: ``lambda-packages/<sha>/<name>`` (T2.42 c3).

    Pure key construction -- callers resolve ``sha`` via ``_resolve_artifact_sha()`` (or pass an
    explicit sha, e.g. reconcile.yml's red-commit-originating PR-head-sha). This namespace is used
    only at the build/upload + byte-assert + deploy-consume boundary; terraform's own ``s3_key``
    attribute values are NEVER made per-sha -- a churning s3_key would surface a layer/function
    replace on every PR and defeat T2.42 c1's ignore_changes decoupling.
    """
    return f"lambda-packages/{sha}/{name}"


def upload_to_s3(zip_path: Path, bucket: str, profile: str, region: str, *, artifact_sha: Optional[str] = None) -> None:
    """Upload package to S3, dual-writing the fixed key AND a per-artifact-sha key (T2.42 c3, DEP-03).

    The fixed key (``lambda-packages/<name>``) keeps terraform's static ``s3_key`` genuine-republish
    read fed -- refreshed on every build, so a genuine layer/function content bump (one that moves
    the resource's ``description`` / is not ignore_changes-suppressed) always finds current bytes
    there. The per-sha key (``lambda-packages/<sha>/<name>``) is overwrite-immune per build/PR-head
    sha: the CD byte-assert steps and the deploy-consume readers (``update_lambda_functions``,
    ``publish_canary_layers``) read it so a later, concurrent build cannot skew this build's
    reviewed artifact bytes. ``artifact_sha`` defaults to ``_resolve_artifact_sha()``
    (``LAMBDA_ARTIFACT_SHA`` -> ``GITHUB_SHA`` -> ``"local"``).
    """
    sha = artifact_sha if artifact_sha is not None else _resolve_artifact_sha()
    for s3_key in (f"lambda-packages/{zip_path.name}", _artifact_s3_key(zip_path.name, sha)):
        subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                str(zip_path),
                f"s3://{bucket}/{s3_key}",
                "--region",
                region,
                *_aws_profile_args(profile),
            ],
            check=True,
        )


def update_lambda_functions(
    bucket: str,
    profile: str,
    region: str,
    *,
    only_ducklake: bool = False,
    artifact_sha: Optional[str] = None,
    only_functions: Optional[set[str]] = None,
) -> None:
    """Update Lambda function code to point at the latest S3 ZIPs.

    Uses ``aws lambda update-function-code`` with --s3-bucket and --s3-key. Dispatcher and
    findings-processor both deploy from ``data-pipeline.zip``.

    ``only_ducklake`` scopes the deploy to the four DuckLake functions (T2.17/T2.18), leaving the
    prod functions untouched (Decision 79 affected-artifact hygiene). Both paths capture
    CodeSha256 from each successful update and write a per-function deployment record (see
    ``_write_ducklake_deploy_record`` / ``read_deploy_record``): ``deploy-records/ducklake/*``
    for the DuckLake path, ``deploy-records/prod/*`` for the prod path (T2.43).

    ``artifact_sha`` (T2.42 c3, DEP-03): when given, each function's code is read from the
    per-artifact-sha key (``lambda-packages/<sha>/<name>``, overwrite-immune) instead of the fixed
    key -- the deploy-consume boundary of the content-addressing scheme. Defaults to None (fixed-key
    read, unchanged prior behaviour) so a bare call is unaffected; ``scripts/build_lambda.py``'s
    orchestrators thread the build's resolved sha through explicitly.

    ``only_functions`` (hotfix follow-up to the 2026-07-24 ops_decisions incident -- splits the
    ducklake deploy into a maintenance leg and a serving leg so the reconcile gate can refuse only
    the latter): further filters ``only_ducklake``'s deploy map down to the named subset.
    Raises ``ValueError`` on an unrecognised function name (a subset deploy that silently
    intersects to empty and reports success is exactly the false-green this exists to prevent) and
    on ``only_functions`` passed with ``only_ducklake=False`` (the prod branch builds a different
    map; subsetting it is out of scope). ``None`` (the default) deploys every ducklake function,
    unchanged from prior behaviour.

    Ref: AWS CLI ``lambda update-function-code`` requires
    --function-name, --s3-bucket, --s3-key; optional --region and
    --profile.  Ref: ``docs/contracts/inference-provider.yaml`` for
    the inference-client packaging requirements.
    """
    if only_functions is not None and not only_ducklake:
        raise ValueError("only_functions requires only_ducklake=True -- the prod branch builds a different map.")

    if only_ducklake:
        # Scope the deploy to the DuckLake functions ONLY: data-pipeline is NOT redeployed by a
        # T2.17/T2.18 deploy (Decision 79 affected-artifact hygiene).
        function_zip_map = dict(_build_ducklake_function_zip_keys())
        if only_functions is not None:
            unrecognised = only_functions - set(function_zip_map)
            if unrecognised:
                raise ValueError(
                    f"only_functions names unrecognised function(s) {sorted(unrecognised)} -- "
                    f"expected a subset of {sorted(function_zip_map)}."
                )
            function_zip_map = {fn: key for fn, key in function_zip_map.items() if fn in only_functions}
        channel = "ducklake"
    else:
        function_zip_map = dict.fromkeys(_build_prod_function_names(), "lambda-packages/data-pipeline.zip")
        channel = "prod"

    for fn_name, fixed_s3_key in function_zip_map.items():
        s3_key = _artifact_s3_key(fixed_s3_key.rsplit("/", 1)[-1], artifact_sha) if artifact_sha is not None else fixed_s3_key
        print(f"  Updating {fn_name}...")
        result = subprocess.run(
            [
                "aws",
                "lambda",
                "update-function-code",
                "--function-name",
                fn_name,
                "--s3-bucket",
                bucket,
                "--s3-key",
                s3_key,
                "--region",
                region,
                "--output",
                "json",
                *_aws_profile_args(profile),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(f"  ERROR: Failed to update {fn_name} (exit {result.returncode})")
            if result.stderr:
                print(f"  {result.stderr.strip()}")
            sys.exit(1)
        print(f"  OK {fn_name} updated")

        _write_ducklake_deploy_record(fn_name, result.stdout, bucket, profile, region, channel=channel)


def _write_ducklake_deploy_record(
    function: str,
    update_function_code_stdout: str,
    bucket: str,
    profile: str,
    region: str,
    channel: str = "ducklake",
) -> None:
    """Capture CodeSha256 from the update-function-code JSON response and write a deploy record.

    Writes ``deploy-records/<channel>/<function>.json``: ``{function, code_sha256,
    source_git_sha, deployed_at}``. ``channel`` defaults to ``"ducklake"`` (the T2.38 original
    caller); the prod class (T2.43) passes ``channel="prod"`` -- same schema, different S3
    prefix, generalising this function rather than duplicating it. ``source_git_sha`` reads
    ``os.environ.get("GITHUB_SHA")`` -- None-safe: a local break-glass path (``bin/venv-python -m
    scripts.build_lambda [--ducklake-only] --deploy``) has no ``GITHUB_SHA``, so the record
    carries ``source_git_sha: null`` rather than raising. Read back via ``read_deploy_record``
    (consumed by ``scripts.convergence_health.detect_ducklake_code_drift`` /
    ``detect_prod_code_drift``).
    """
    try:
        code_sha256 = json.loads(update_function_code_stdout)["CodeSha256"]
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        print(f"  ERROR: could not parse CodeSha256 from update-function-code response for {function}: {exc}")
        sys.exit(1)

    record = {
        "function": function,
        "code_sha256": code_sha256,
        "source_git_sha": os.environ.get("GITHUB_SHA"),
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    key = f"deploy-records/{channel}/{function}.json"
    result = subprocess.run(
        [
            "aws",
            "s3",
            "cp",
            "-",
            f"s3://{bucket}/{key}",
            "--region",
            region,
            "--content-type",
            "application/json",
            *_aws_profile_args(profile),
        ],
        input=json.dumps(record),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"  ERROR: Failed to write deploy record for {function} (exit {result.returncode})")
        if result.stderr:
            print(f"  {result.stderr.strip()}")
        sys.exit(1)
    print(f"  OK deploy record written: {key}")


def read_deploy_record(
    function: str,
    s3_client: Any = None,
    bucket: str = _DEPLOY_RECORD_BUCKET,
    channel: str = "ducklake",
) -> Optional[dict[str, Any]]:
    """Read ``deploy-records/<channel>/<function>.json``. Returns None if the record is absent.

    ``channel`` defaults to ``"ducklake"`` (the T2.38 original caller); the prod class (T2.43)
    passes ``channel="prod"`` so ``scripts.convergence_health.detect_prod_code_drift`` can read
    its deploy records through the same generalised accessor. ``s3_client`` is injected for
    testability (a boto3-like client exposing ``get_object(Bucket, Key) -> {"Body": <stream>}``),
    mirroring ``scripts.convergence_health.read_convergence_record``. When None, a boto3 client is
    created lazily (never at import time, per the repo's import-safety rule).
    """
    if s3_client is None:
        import boto3  # noqa: PLC0415

        s3_client = boto3.client("s3")

    key = f"deploy-records/{channel}/{function}.json"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        exc_str = str(exc) + type(exc).__name__
        if any(marker in exc_str for marker in ("NoSuchKey", "404", "NoSuchBucket")):
            return None
        raise


def resolve_bucket(profile: str) -> str:
    """Resolve S3 bucket from Terraform output, falling back to default.

    Falls back to the well-known data-lake bucket when terraform is unavailable (e.g. a CC-web
    container without the terraform binary) or the output is empty.
    """
    try:
        result = subprocess.run(
            ["terraform", "-chdir=terraform", "output", "-raw", "s3_formulas_discovery_bucket"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
    except FileNotFoundError:
        return "agent-platform-data-lake"
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "agent-platform-data-lake"


def validate_bucket_exists(bucket: str, profile: str, region: str) -> bool:
    """Validate that S3 bucket exists.

    Args:
        bucket: S3 bucket name
        profile: AWS CLI profile
        region: AWS region

    Returns:
        True if bucket exists, False otherwise
    """
    result = subprocess.run(
        [
            "aws",
            "s3api",
            "head-bucket",
            "--bucket",
            bucket,
            "--region",
            region,
            *_aws_profile_args(profile),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0


def publish_canary_layers(
    *,
    bucket: str,
    profile: str = "agent_platform",
    region: str = "eu-west-2",
    artifact_sha: Optional[str] = None,
) -> dict[str, str]:
    """Publish the three DuckLake layer zips (already in S3) as new aws_lambda layer versions.

    Calls ``aws lambda publish-layer-version`` for each of the three DuckLake layers (deps, extensions,
    pgclient) using the S3 zips that ``--ducklake-only`` already uploaded. Prints JSON mapping layer
    name -> version ARN for the canary orchestrator to consume.

    Layer zips must already be in S3 (run ``--ducklake-only`` first). Fails closed if any publish
    fails. Returns the same ARN dict.

    ``artifact_sha`` (T2.42 c3, DEP-03): when given, each layer is published from the
    per-artifact-sha key (overwrite-immune) instead of the fixed key. Defaults to None (fixed-key
    read, unchanged prior behaviour); ``scripts/build_lambda.py``'s
    ``--ducklake-publish-canary-layers`` dispatch threads the resolved build sha through explicitly.
    """
    import json as _json  # noqa: PLC0415

    arns: dict[str, str] = {}
    for layer_name in _build_ducklake_layer_names():
        zip_name = f"{layer_name}.zip"
        s3_key = _artifact_s3_key(zip_name, artifact_sha) if artifact_sha is not None else f"lambda-packages/{zip_name}"
        result = subprocess.run(
            [
                "aws",
                "lambda",
                "publish-layer-version",
                "--layer-name",
                layer_name,
                "--content",
                f"S3Bucket={bucket},S3Key={s3_key}",
                "--region",
                region,
                "--profile",
                profile,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            print(
                f"ERROR: failed to publish layer {layer_name}: {result.stderr.strip()[:500]}",
                file=sys.stderr,
            )
            sys.exit(1)
        data = _json.loads(result.stdout)
        arn = data["LayerVersionArn"]
        arns[layer_name] = arn
        print(f"  OK {layer_name}: {arn}")
    print(_json.dumps(arns))
    return arns
