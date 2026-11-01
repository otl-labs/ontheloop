#!/usr/bin/env python3
"""Build Lambda deployment packages for the platform Lambdas (no Docker required).

Thin CLI facade over three cohesion-based modules (Decision 104 pattern, realizing rec-2414 for
this file): scripts/build_lambda_config.py (build configuration data + resolution),
scripts/build_lambda_packaging.py (bundle/zip assembly), and scripts/build_lambda_deploy.py (AWS
deploy/publish + bucket resolution). This module keeps only the argparse surface (build_parser),
the main() dispatcher, and the two build orchestrators (_run_prod_build, _run_ducklake_build) --
the functions tests patch AROUND -- plus a facade re-export block so `from scripts.build_lambda
import X`, `scripts.build_lambda.X`, and `patch("scripts.build_lambda.<name>")` keep resolving
for every public and test-patched symbol now defined in the three extracted modules.

Creates these zip artifacts (built by the extracted modules; see them for detail):
  1. data-pipeline.zip                      -- scheduled-agent dispatcher + findings-processor
                                               application code (manifest-driven)
  2. ducklake-{writer,reader,maintenance,maintenance-smoke,catalog-dr,telemetry-writer,
                                               telemetry-reader}.zip -- T2.17/T2.18 DuckLake
                                               runtime functions (--ducklake-only)
  3. ducklake-{deps,extensions}-layer.zip   -- duckdb (pinned via config/lambda/ducklake/version.yaml)
                                               + baked extensions (--ducklake-only)
  4. ducklake-pgclient-layer.zip            -- pg_dump + pg_restore 16 + libpq.so (--ducklake-only, T2.18 FP-B)

Every app zip is built from src/lambdas/<name>/manifest.yaml rather than
whole-src/whole-config copytrees (Decision 79 / CD.24).
"""

import argparse
import subprocess  # noqa: F401 -- deliberately-retained patch anchor
import sys
import tempfile
import urllib.request  # noqa: F401 -- deliberately-retained patch anchor
from pathlib import Path

from scripts.aws_profile import resolve_aws_profile
from scripts.build_lambda_config import (
    _BUILD_CONTRACT_PATH,  # noqa: F401
    _BUILD_CONTRACT_REGISTRY,  # noqa: F401
    _DUCKLAKE_CATALOG_DR_FUNCTION,  # noqa: F401
    _DUCKLAKE_FUNCTION_ZIP_KEYS,  # noqa: F401
    _DUCKLAKE_MAINTENANCE_FUNCTION,  # noqa: F401
    _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,  # noqa: F401
    _DUCKLAKE_READER_FUNCTION,  # noqa: F401
    _DUCKLAKE_WRITER_FUNCTION,  # noqa: F401
    _LAMBDA_FUNCTION_NAMES,  # noqa: F401
    _LAMBDA_SCRIPTS,  # noqa: F401
    DUCKLAKE_LAYER_NAMES,  # noqa: F401
    LAMBDA_FILE_PATTERNS,  # noqa: F401
    LAMBDA_SIZE_LIMIT_BYTES,  # noqa: F401
    LAMBDA_SIZE_WARN_BYTES,  # noqa: F401
    PINNED_DUCKDB_VERSION,  # noqa: F401
    PINNED_PG_MAJOR,  # noqa: F401
    _aws_profile_args,  # noqa: F401
    _build_ducklake_function_zip_keys,  # noqa: F401
    _build_ducklake_layer_names,  # noqa: F401
    _build_prod_function_names,  # noqa: F401
    _build_size_limit_bytes,  # noqa: F401
)
from scripts.build_lambda_deploy import (
    _artifact_s3_key,  # noqa: F401
    _resolve_artifact_sha,  # noqa: F401
    _write_ducklake_deploy_record,  # noqa: F401
    publish_canary_layers,  # noqa: F401
    read_deploy_record,  # noqa: F401
    resolve_bucket,  # noqa: F401
    update_lambda_functions,  # noqa: F401
    upload_to_s3,  # noqa: F401
    validate_bucket_exists,  # noqa: F401
)
from scripts.build_lambda_packaging import (
    OUTPUT_DIR,  # noqa: F401
    _deterministic_zipinfo,  # noqa: F401
    _fetch_extension_bytes,  # noqa: F401
    _try_s3_extension,  # noqa: F401
    _try_s3_pgclient,  # noqa: F401
    _zip_staged_dir,  # noqa: F401
    assert_within_size_limit,  # noqa: F401
    build_app_package,  # noqa: F401
    build_ducklake_deps_layer,  # noqa: F401
    build_ducklake_extensions_layer,  # noqa: F401
    build_ducklake_function_package,  # noqa: F401
    build_pgclient_layer,  # noqa: F401
    list_bundle,  # noqa: F401
)


def _run_prod_build(args: argparse.Namespace) -> None:
    """Build (+optionally upload/deploy) the data-pipeline prod artifact."""
    with tempfile.TemporaryDirectory(prefix="lambda-build-") as tmp:
        temp_dir = Path(tmp)

        print("[1/3] Building application code package (data-pipeline, manifest-driven)...")
        app_zip = build_app_package(temp_dir)
        print(f"  OK data-pipeline.zip ({round(app_zip.stat().st_size / 1024 / 1024, 2)} MB)")

        assert_within_size_limit(app_zip)

        if not args.skip_upload:
            bucket = args.bucket or resolve_bucket(args.profile)
            if not validate_bucket_exists(bucket, args.profile, args.region):
                print(f"ERROR: S3 bucket does not exist: s3://{bucket}")
                sys.exit(1)
            # T2.42 c3 (DEP-03): resolve the artifact-sha namespace ONCE per build invocation and
            # thread it to every upload/deploy call site, so the upload and the immediately-following
            # deploy-consume read agree on the same per-sha key within this process.
            artifact_sha = _resolve_artifact_sha()
            print(f"[2/3] Uploading to s3://{bucket}/lambda-packages/...")
            upload_to_s3(app_zip, bucket, args.profile, args.region, artifact_sha=artifact_sha)
            print("  OK Uploaded to S3")
            if args.deploy:
                print("[2b/3] Updating Lambda function code...")
                update_lambda_functions(bucket, args.profile, args.region, artifact_sha=artifact_sha)
                print("  OK Lambda functions updated")
        else:
            print("[2/3] Skipping S3 upload (--skip-upload)")
        print("[3/3] Build complete.")


def _run_ducklake_build(args: argparse.Namespace) -> None:
    """Build (+optionally upload/deploy) ONLY the T2.17/T2.18 DuckLake artifacts (Decision 79 hygiene).

    ``--ducklake-functions`` and ``--ducklake-deploy-only`` (hotfix follow-up to the 2026-07-24
    ops_decisions incident) split the deploy leg from the build/upload leg so a governed pipeline
    can deploy the DuckLake functions in two gated stages (maintenance first, serving last)
    without building and uploading the same artifacts twice. Both default to today's behaviour
    when omitted: every existing caller and the Decision 125 break-glass path are byte-identical.
    """
    bucket = args.bucket or resolve_bucket(args.profile)
    only_functions = set(args.ducklake_functions.split(",")) if getattr(args, "ducklake_functions", None) else None

    if getattr(args, "ducklake_deploy_only", False):
        if not args.deploy:
            print("ERROR: --ducklake-deploy-only requires --deploy.")
            sys.exit(1)
        artifact_sha = _resolve_artifact_sha()
        print("[1/1] Deploying DuckLake Lambda function code from existing S3 artifacts (--ducklake-deploy-only)...")
        update_lambda_functions(
            bucket,
            args.profile,
            args.region,
            only_ducklake=True,
            artifact_sha=artifact_sha,
            only_functions=only_functions,
        )
        print("  OK DuckLake Lambda functions updated (deploy-only, no build/upload).")
        return

    with tempfile.TemporaryDirectory(prefix="ducklake-build-") as tmp:
        temp_dir = Path(tmp)

        print(
            "[1/4] Building ducklake function zips (writer + reader + maintenance + maintenance-smoke + "
            "catalog-dr + telemetry-writer + telemetry-reader, manifest-driven)..."
        )
        writer_zip = build_ducklake_function_package(temp_dir, "ducklake_writer", "ducklake-writer.zip")
        reader_zip = build_ducklake_function_package(temp_dir, "ducklake_reader", "ducklake-reader.zip")
        maintenance_zip = build_ducklake_function_package(temp_dir, "ducklake_maintenance", "ducklake-maintenance.zip")
        maintenance_smoke_zip = build_ducklake_function_package(
            temp_dir, "ducklake_maintenance_smoke", "ducklake-maintenance-smoke.zip"
        )
        catalog_dr_zip = build_ducklake_function_package(temp_dir, "ducklake_catalog_dr", "ducklake-catalog-dr.zip")
        telemetry_writer_zip = build_ducklake_function_package(
            temp_dir, "ducklake_telemetry_writer", "ducklake-telemetry-writer.zip"
        )
        telemetry_reader_zip = build_ducklake_function_package(
            temp_dir, "ducklake_telemetry_reader", "ducklake-telemetry-reader.zip"
        )
        print(f"  OK ducklake-writer.zip ({round(writer_zip.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-reader.zip ({round(reader_zip.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-maintenance.zip ({round(maintenance_zip.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-maintenance-smoke.zip ({round(maintenance_smoke_zip.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-catalog-dr.zip ({round(catalog_dr_zip.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-telemetry-writer.zip ({round(telemetry_writer_zip.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-telemetry-reader.zip ({round(telemetry_reader_zip.stat().st_size / 1024 / 1024, 2)} MB)")

        print("[2/4] Building ducklake-deps + ducklake-extensions + ducklake-pgclient layers...")
        deps_layer = build_ducklake_deps_layer(temp_dir)
        ext_layer = build_ducklake_extensions_layer(temp_dir, bucket=bucket, profile=args.profile, region=args.region)
        pgclient_layer = build_pgclient_layer(temp_dir, bucket=bucket, profile=args.profile, region=args.region)
        print(f"  OK ducklake-deps-layer.zip ({round(deps_layer.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-extensions-layer.zip ({round(ext_layer.stat().st_size / 1024 / 1024, 2)} MB)")
        print(f"  OK ducklake-pgclient-layer.zip ({round(pgclient_layer.stat().st_size / 1024 / 1024, 2)} MB)")

        artifacts = (
            writer_zip,
            reader_zip,
            maintenance_zip,
            maintenance_smoke_zip,
            catalog_dr_zip,
            telemetry_writer_zip,
            telemetry_reader_zip,
            deps_layer,
            ext_layer,
            pgclient_layer,
        )
        for artifact in artifacts:
            assert_within_size_limit(artifact)

        if not args.skip_upload:
            if not validate_bucket_exists(bucket, args.profile, args.region):
                print(f"ERROR: S3 bucket does not exist: s3://{bucket}")
                sys.exit(1)
            # T2.42 c3 (DEP-03): resolve the artifact-sha namespace ONCE per build invocation and
            # thread it to every upload/deploy call site, so the upload and the immediately-following
            # deploy-consume read agree on the same per-sha key within this process.
            artifact_sha = _resolve_artifact_sha()
            print(f"[3/4] Uploading to s3://{bucket}/lambda-packages/...")
            for artifact in artifacts:
                upload_to_s3(artifact, bucket, args.profile, args.region, artifact_sha=artifact_sha)
            print("  OK Uploaded to S3")
            if args.deploy:
                print(
                    "[3b/4] Updating DuckLake Lambda function code "
                    "(writer + reader + maintenance + maintenance-smoke + catalog-dr + telemetry-writer)..."
                )
                update_lambda_functions(
                    bucket,
                    args.profile,
                    args.region,
                    only_ducklake=True,
                    artifact_sha=artifact_sha,
                    only_functions=only_functions,
                )
                print("  OK DuckLake Lambda functions updated")
        else:
            print("[3/4] Skipping S3 upload (--skip-upload)")
        print("[4/4] DuckLake build complete.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Lambda deployment packages")
    parser.add_argument("--skip-upload", action="store_true", help="Build zips only, skip S3 upload")
    parser.add_argument("--bucket", default="", help="S3 bucket name (default: from terraform output)")
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS CLI profile (default: $AWS_PROFILE, else agent_platform; none under environment credentials)",
    )
    parser.add_argument("--region", default="eu-west-2", help="AWS region")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="After S3 upload, update Lambda function code",
    )
    parser.add_argument(
        "--ducklake-only",
        action="store_true",
        help="Build/upload/deploy ONLY the DuckLake artifacts (7 zips + 3 layers; the "
        "deployed functions are writer, reader, maintenance, maintenance-smoke, "
        "catalog-dr, telemetry-writer); leave data-pipeline untouched (Decision 79).",
    )
    parser.add_argument(
        "--list-bundle",
        metavar="ARTIFACT_SLUG",
        default=None,
        help="Stage a manifest-driven bundle and emit its static file list (skip_pip=True). "
        "ARTIFACT_SLUG is the src/lambdas/<name>/ directory name (e.g. data-pipeline).",
    )
    parser.add_argument(
        "--ducklake-functions",
        default=None,
        metavar="FN1,FN2,...",
        help="Comma-separated subset of the full AWS DuckLake function names (e.g. "
        "agent-platform-ducklake-writer) to deploy -- not bare slugs. Requires --ducklake-only "
        "--deploy. Omit to deploy every DuckLake function, unchanged from today's behaviour.",
    )
    parser.add_argument(
        "--ducklake-deploy-only",
        action="store_true",
        help="Skip the build/upload phase entirely and call update_lambda_functions() against "
        "DuckLake artifacts already uploaded to S3 by a prior --ducklake-only build in the same "
        "run (the deploy-serving leg of the split deploy pipeline, hotfix follow-up to the "
        "2026-07-24 ops_decisions incident). Requires --ducklake-only --deploy.",
    )
    parser.add_argument(
        "--ducklake-publish-canary-layers",
        action="store_true",
        dest="ducklake_publish_canary_layers",
        help="Publish the three DuckLake layer zips (already uploaded to S3 by --ducklake-only) as new "
        "aws_lambda layer versions. Prints JSON mapping layer name -> version ARN for the canary "
        "orchestrator (OQ.12 clone-rehearsal). Run after --ducklake-only to get candidate layer ARNs.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.profile is None:
        args.profile = resolve_aws_profile() or ""

    if args.list_bundle:
        list_bundle(args.list_bundle)
        return

    if args.ducklake_publish_canary_layers:
        bucket = args.bucket or resolve_bucket(args.profile)
        publish_canary_layers(bucket=bucket, profile=args.profile, region=args.region, artifact_sha=_resolve_artifact_sha())
        return

    print("Lambda Package Builder (Manifest-Driven, CD.24)")
    print()
    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.ducklake_only:
        _run_ducklake_build(args)
    else:
        _run_prod_build(args)

    print()
    print("Build Complete")
    print("  Artifacts in: lambda-packages/")
    print("  Next: terraform apply")


if __name__ == "__main__":  # pragma: no cover
    main()
