#!/usr/bin/env python3
"""T2.42 c4 (rec-2659) + DEP-08: tested DuckLake build byte-identity-assert + dual-write upload.

Extracted from the "Build DuckLake lambda packages" step duplicated 5x across
terraform-apply-sandbox.yml (apply-sandbox, ducklake-artifacts, gated-apply) and reconcile.yml
(apply-reconcile, gated-apply-reconcile). The .github/actions/build-ducklake-artifacts composite
action is the caller for its 'assert' and 'skip-assert' modes: it runs `python -m
scripts.build_lambda --ducklake-only --skip-upload` to produce the seven zips under
lambda-packages/, then calls assert_and_upload() below to byte-check (push path only) and
dual-write them to S3. The action's third mode, 'upload-only' (the PR ducklake-artifacts job's
first-upload path, which also builds three more artifacts -- ducklake-maintenance-smoke.zip,
ducklake-telemetry-writer.zip and ducklake-telemetry-reader.zip -- not in DUCKLAKE_ARTIFACT_NAMES below), does not
call this module at all: it runs `build_lambda
--ducklake-only` WITHOUT --skip-upload and lets build_lambda's own internal upload_to_s3 (already
a fixed+per-sha dual-write, scripts/build_lambda_deploy.py) handle the upload directly.

event_name convention (matches the original inline bash's `if [ "${{ github.event_name }}" =
"push" ]` gate): pass the literal string "push" to run the byte-identity assert before uploading
-- the apply-time path re-verifying the freshly rebuilt zips are byte-identical to what the
originating PR's ducklake-artifacts job uploaded (Decision 77 no-TOCTOU: the applied layer content
must not skew from the reviewed plan). Pass any other value (e.g. "workflow_dispatch",
"pull_request") to skip the assert and upload directly -- the PR ducklake-artifacts job populating
S3 for the first time, or a fresh workflow_dispatch plan with no prior PR artifact to compare
against. The calling composite action's own `mode: assert|skip-assert` input decides which string
to pass here -- independent of the workflow's REAL triggering github.event_name (Reconcile is
workflow_dispatch-triggered but always wants assert semantics, matching the push-path parity its
rebuild-from-the-red-commit intentionally mirrors).

Decision 154 / rec-2862: the composite action's fourth mode, 'fetch', calls fetch_reviewed()
below instead of rebuilding at all. Every human-gated apply path (gated-apply, apply-reconcile,
gated-apply-reconcile) sits behind an unbounded approval wait; rebuilding after that wait can
resolve a dependency to a version published during the wait, producing bytes that were never
reviewed (the exact rec-2862 TOCTOU). fetch_reviewed() instead downloads the already-reviewed
per-sha artifacts and server-side copies them onto the fixed key -- the push-path apply-sandbox
job keeps mode='assert' as the reproducibility canary, since it runs immediately after the
rebuild with no approval-wait exposure window.

rec-4372 (Decision 154 Update, 2026-10-17): the push-path assert's PR-time reference can go stale
-- its window runs from the PR's last speculative plan to its own merge, with no upper bound, and
main can edit a packaged file in between. The 'upload-only' mode (the PR ducklake-artifacts job's
first-upload path above) now also records scripts/ci/ducklake_inputs.record_inputs() as
lambda-packages/<artifact_sha>/inputs.json alongside the ten built zips. On a push-path mismatch,
assert_and_upload reads that record, computes the current tree's own record, and calls
scripts.ci.ducklake_inputs.attribute() to check whether every differing artifact's difference
traces to a tracked input that moved on main between the PR's last plan and its merge. When it
does, the push run applies the already-reviewed saved plan.bin unchanged: it uploads the rebuilt
(merge-tree) zips to the fixed key and to lambda-packages/<merge sha>/, and leaves a
superseded-by.json pointer beside the untouched PR-time zips so fetch_reviewed() (below) can
follow it one hop. Any difference attribute() cannot explain -- including a missing or
non-describing inputs.json, a schema mismatch, or an unset merge sha -- still raises
DucklakeArtifactError exactly as before, with no upload (fail closed).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from scripts.ci.ducklake_inputs import SCHEMA as _INPUTS_SCHEMA
from scripts.ci.ducklake_inputs import attribute, record_inputs

DUCKLAKE_ARTIFACT_NAMES: tuple[str, ...] = (
    "ducklake-writer.zip",
    "ducklake-reader.zip",
    "ducklake-maintenance.zip",
    "ducklake-catalog-dr.zip",
    "ducklake-deps-layer.zip",
    "ducklake-extensions-layer.zip",
    "ducklake-pgclient-layer.zip",
)


class DucklakeArtifactError(RuntimeError):
    """Fail-closed error: byte-mismatch or a missing per-sha reference object (Decision 77 no-TOCTOU)."""


def _md5_hex(data: bytes) -> str:
    """Content-identity digest (parity check, not a security digest) -- matches the original md5sum."""
    return hashlib.md5(data).hexdigest()


_ENTRY_DIFF_LIMIT = 10


def _zip_entry_diff(local: bytes, remote: bytes) -> str:
    """Name the zip entries differing by (CRC, uncompressed size): a mismatch is diagnosable from
    the log alone (rec-4189). rec-4198: any Exception here is caught for the diagnostic only --
    this helper must never itself raise and so mask the DUCKLAKE_ZIP_MISMATCH it is composing."""
    try:
        local_entries, remote_entries = (
            {info.filename: (info.CRC, info.file_size) for info in zipfile.ZipFile(io.BytesIO(data)).infolist()}
            for data in (local, remote)
        )
    except Exception as exc:  # noqa: BLE001 -- rec-4198: the diagnostic must never mask the mismatch it explains
        return f"entry diff unavailable: {exc}"
    shared = local_entries.keys() & remote_entries.keys()
    differing = sorted(
        [(name, "changed") for name in shared if local_entries[name] != remote_entries[name]]
        + [(name, "only-local") for name in local_entries.keys() - remote_entries.keys()]
        + [(name, "only-remote") for name in remote_entries.keys() - local_entries.keys()]
    )
    if not differing:
        return "entry contents identical (CRC and size); only the container bytes differ (compression or zip metadata)"
    lines = [f"{len(differing)} differing zip entr{'y' if len(differing) == 1 else 'ies'}:"]
    lines += [f"{kind}: {name}" for name, kind in differing[:_ENTRY_DIFF_LIMIT]]
    if len(differing) > _ENTRY_DIFF_LIMIT:
        lines.append(f"... and {len(differing) - _ENTRY_DIFF_LIMIT} more")
    return "\n".join(lines)


def _read_pointer(s3_client: Any, bucket: str, artifact_sha: str) -> dict[str, Any] | None:
    """Read lambda-packages/<artifact_sha>/superseded-by.json. None when absent (a botocore-shaped
    ClientError with Error.Code NoSuchKey or 404, read duck-typed from exc.response so no botocore
    import is added); any other read failure (403, a malformed body) fails closed."""
    key = f"lambda-packages/{artifact_sha}/superseded-by.json"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 -- only a confirmed-absent key is not fail-closed
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise DucklakeArtifactError(f"DUCKLAKE_FETCH_BAD_POINTER: could not read {key}: {exc}") from exc
    try:
        return json.loads(response["Body"].read())
    except Exception as exc:  # noqa: BLE001 -- an unreadable pointer body fails closed
        raise DucklakeArtifactError(f"DUCKLAKE_FETCH_BAD_POINTER: {key} is unreadable: {exc}") from exc


def _mismatch_error(name: str, local_bytes: bytes, remote_bytes: bytes, reason: str) -> DucklakeArtifactError:
    return DucklakeArtifactError(
        f"DUCKLAKE_ZIP_MISMATCH {name}: local={_md5_hex(local_bytes)} remote={_md5_hex(remote_bytes)}; applied layer "
        "content would skew from the reviewed plan.bin. Failing closed (Decision 77 no-TOCTOU).\n"
        + _zip_entry_diff(local_bytes, remote_bytes)
        + f"\nnot attributed: {reason}"
    )


def _read_inputs_record(s3_client: Any, bucket: str, artifact_sha: str) -> dict[str, Any]:
    response = s3_client.get_object(Bucket=bucket, Key=f"lambda-packages/{artifact_sha}/inputs.json")
    return json.loads(response["Body"].read())


def _reference_record_describes_zips(record: dict[str, Any], names: list[str], reference_bytes: dict[str, bytes]) -> bool:
    """True when *record* is actually a description of the per-sha zips assert_and_upload just
    read as the reference -- same md5 and same zip entries for every name."""
    artifacts = record.get("artifacts", {})
    for name in names:
        artifact = artifacts.get(name)
        if artifact is None:
            return False
        remote_bytes = reference_bytes[name]
        if artifact.get("md5") != _md5_hex(remote_bytes):
            return False
        with zipfile.ZipFile(io.BytesIO(remote_bytes)) as zf:
            remote_entries = {info.filename: hashlib.sha256(zf.read(info.filename)).hexdigest() for info in zf.infolist()}
        if artifact.get("entries") != remote_entries:
            return False
    return True


def _push_attribute_and_apply(
    names: list[str],
    artifact_sha: str,
    merge_sha: str | None,
    bucket: str,
    packages_root: Path,
    s3_client: Any,
    mismatched: list[str],
    local_bytes: dict[str, bytes],
    reference_bytes: dict[str, bytes],
) -> None:
    """Every mismatched name traced to a moved input -> apply the saved plan.bin unchanged on
    merge-tree zips (fixed key + lambda-packages/<merge_sha>/, plus the superseded-by.json
    pointer). Any failure to establish that -> DUCKLAKE_ZIP_MISMATCH, no upload (rec-4372)."""
    first = mismatched[0]
    reason = "unknown"
    try:
        if not merge_sha or merge_sha == artifact_sha:
            reason = "no merge sha available, or it equals artifact_sha (not a real merge rebuild)"
            raise ValueError(reason)  # noqa: TRY301
        reference_record = _read_inputs_record(s3_client, bucket, artifact_sha)
        if reference_record.get("schema") != _INPUTS_SCHEMA or not _reference_record_describes_zips(
            reference_record, names, reference_bytes
        ):
            reason = "the PR's inputs.json does not describe the reference zips just read"
            raise ValueError(reason)  # noqa: TRY301
        current_record = record_inputs(packages_root, names, Path.cwd())
        verdicts = attribute(reference_record, current_record)
        not_moved = [n for n in mismatched if verdicts.get(n, {}).get("verdict") != "moved"]
        if not_moved:
            reason = "; ".join(f"{n}: {verdicts.get(n, {}).get('reason', 'no verdict computed')}" for n in not_moved)
            raise ValueError(reason)  # noqa: TRY301
    except Exception as exc:  # noqa: BLE001 -- any attribution failure collapses to the fail-closed sentinel
        raise _mismatch_error(first, local_bytes[first], reference_bytes[first], reason) from exc

    for name in names:
        s3_client.upload_file(str(packages_root / name), bucket, f"lambda-packages/{name}")
        s3_client.upload_file(str(packages_root / name), bucket, f"lambda-packages/{merge_sha}/{name}")
    s3_client.put_object(
        Bucket=bucket, Key=f"lambda-packages/{merge_sha}/inputs.json", Body=json.dumps(current_record).encode()
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=f"lambda-packages/{artifact_sha}/superseded-by.json",
        Body=json.dumps(
            {
                "schema": 1,
                "artifact_sha": merge_sha,
                "reason": "DUCKLAKE_ZIP_INPUTS_MOVED",
                "moved": {n: verdicts[n]["inputs"] for n in mismatched},
            }
        ).encode(),
    )
    print(
        "DUCKLAKE_ZIP_INPUTS_MOVED: "
        + "; ".join(f"{n} <- {verdicts[n]['inputs']}" for n in mismatched)
        + f"; applied the reviewed plan.bin unchanged on merge-tree zips at lambda-packages/{merge_sha}/."
    )


def build_ducklake_only(*, cwd: str | None = None) -> None:
    """Thin wrapper: `python -m scripts.build_lambda --ducklake-only --skip-upload ...`.

    The caller (the build-ducklake-artifacts composite action) is responsible for installing
    build_lambda's import-time deps first (this runner has no .venv -- CC-web-only convention).
    """
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.build_lambda",
            "--ducklake-only",
            "--skip-upload",
            "--profile",
            "",
            "--bucket",
            "agent-platform-data-lake",
        ],
        cwd=cwd,
        check=True,
    )


def upload_only(
    artifact_sha: str,
    bucket: str,
    region: str = "eu-west-2",
    *,
    packages_dir: str | Path = "lambda-packages",
    root: str | Path = ".",
    s3_client: Any = None,
) -> None:
    """PR ducklake-artifacts first-upload path: run build_lambda's own build+dual-write unchanged
    (all ten zips, including the three outside DUCKLAKE_ARTIFACT_NAMES), then record and upload
    scripts.ci.ducklake_inputs.record_inputs() as lambda-packages/<artifact_sha>/inputs.json.
    Never calls assert_and_upload. Raises DucklakeArtifactError (fail loud, before merge) if the
    record cannot be computed or uploaded -- rec-4372's push-path attribution has nothing to
    compare against without it."""
    subprocess.run(
        [sys.executable, "-m", "scripts.build_lambda", "--ducklake-only", "--profile", "", "--bucket", bucket],
        check=True,
    )

    if s3_client is None:
        import boto3  # noqa: PLC0415

        s3_client = boto3.client("s3", region_name=region)

    try:
        record = record_inputs(packages_dir, list(DUCKLAKE_ARTIFACT_NAMES), root)
        s3_client.put_object(
            Bucket=bucket, Key=f"lambda-packages/{artifact_sha}/inputs.json", Body=json.dumps(record).encode()
        )
    except Exception as exc:  # noqa: BLE001 -- recording/uploading the record must fail loud before merge
        raise DucklakeArtifactError(
            f"DUCKLAKE_INPUTS_RECORD_FAILED: could not record or upload inputs.json for {artifact_sha}: {exc}"
        ) from exc


def assert_and_upload(
    names: list[str],
    artifact_sha: str,
    event_name: str,
    bucket: str,
    region: str = "eu-west-2",
    *,
    packages_dir: str | Path = "lambda-packages",
    s3_client: Any = None,
    merge_sha: str | None = None,
) -> None:
    """Byte-assert (push path only) then dual-write upload the built DuckLake zips.

    Raises DucklakeArtifactError (fail-closed) on a byte-mismatch or a missing per-sha reference
    object when event_name == "push" (see module docstring for the event_name convention); a
    mismatch message's later lines name the differing zip entries (_zip_entry_diff) then, last,
    why the mismatch was not attributed to a moved input (rec-4372).
    Upload always dual-writes BOTH the fixed key (lambda-packages/<name>, keeps terraform's
    static s3_key genuine-republish read fed) AND the per-sha key -- artifact_sha's when every
    zip matched, merge_sha's (resolved from GITHUB_SHA when not passed explicitly) when every
    mismatch traced to a moved input, leaving a superseded-by.json pointer beside the untouched
    PR-time zips either way the original per-sha prefix is never overwritten.

    s3_client is injected for testability (boto3.client("s3", region_name=region) when None) --
    mirrors the injection pattern in scripts/ci/reconcile_target.py.
    """
    if s3_client is None:
        import boto3  # noqa: PLC0415

        s3_client = boto3.client("s3", region_name=region)

    packages_root = Path(packages_dir)

    if event_name == "push":
        if merge_sha is None:
            merge_sha = os.environ.get("GITHUB_SHA")

        local_bytes: dict[str, bytes] = {}
        reference_bytes: dict[str, bytes] = {}
        mismatched: list[str] = []
        for name in names:
            data = (packages_root / name).read_bytes()
            local_bytes[name] = data
            key = f"lambda-packages/{artifact_sha}/{name}"
            try:
                response = s3_client.get_object(Bucket=bucket, Key=key)
                remote_bytes = response["Body"].read()
            except Exception as exc:  # noqa: BLE001 -- any fetch failure fails closed (mirrors `! aws s3 cp`)
                raise DucklakeArtifactError(
                    f"DUCKLAKE_ZIP_MISMATCH {name}: no existing per-sha S3 object found at {key} "
                    f"(PR job did not upload it); failing closed (Decision 77 no-TOCTOU). Underlying: {exc}"
                ) from exc
            reference_bytes[name] = remote_bytes
            if _md5_hex(data) != _md5_hex(remote_bytes):
                mismatched.append(name)

        if not mismatched:
            if _read_pointer(s3_client, bucket, artifact_sha) is not None:
                raise DucklakeArtifactError(
                    f"DUCKLAKE_ZIP_MISMATCH: lambda-packages/{artifact_sha}/superseded-by.json already exists but "
                    "every rebuilt zip is byte-identical to the per-sha reference; refusing to upload over a "
                    "pointer a prior moved-input push wrote (only the moved branch may create one)."
                )
            for name in names:
                s3_client.upload_file(str(packages_root / name), bucket, f"lambda-packages/{name}")
                s3_client.upload_file(str(packages_root / name), bucket, f"lambda-packages/{artifact_sha}/{name}")
            print(
                "DUCKLAKE_ZIP_IDEMPOTENT_OK: all seven rebuilt zips are byte-identical to the PR-uploaded per-sha artifacts."
            )
            return

        _push_attribute_and_apply(
            names, artifact_sha, merge_sha, bucket, packages_root, s3_client, mismatched, local_bytes, reference_bytes
        )
        return

    for name in names:
        local_path = packages_root / name
        s3_client.upload_file(str(local_path), bucket, f"lambda-packages/{name}")
        s3_client.upload_file(str(local_path), bucket, f"lambda-packages/{artifact_sha}/{name}")
    print(f"Uploaded {len(names)} DuckLake artifact(s) to s3://{bucket}/lambda-packages/ (fixed + per-sha dual-write).")


def fetch_reviewed(
    names: list[str],
    artifact_sha: str,
    bucket: str,
    region: str = "eu-west-2",
    *,
    packages_dir: str | Path = "lambda-packages",
    s3_client: Any = None,
) -> None:
    """Download the reviewed per-sha artifacts and server-side copy each onto its fixed key.

    For a human-gated apply consuming an already-reviewed plan (gated-apply, apply-reconcile,
    gated-apply-reconcile), the artifact that must land is the one a reviewer looked at, not a
    fresh rebuild -- rebuilding invites the exact TOCTOU rec-2862 hit (a dependency resolving to a
    newer version between review and approval). First reads
    lambda-packages/<artifact_sha>/superseded-by.json (Decision 154 Update / rec-4372): absent,
    the usual route, downloads lambda-packages/<artifact_sha>/<name>; present and valid (schema 1,
    naming a distinct 40-hex sha), follows it ONE hop and downloads from that sha's prefix instead
    -- the push run's own merge-tree rebuild, left there because the PR-time zips predated an
    input that moved on main. A malformed, unreadable or self-referencing pointer raises
    DucklakeArtifactError before any copy, same as a missing artifact (Decision 77 no-TOCTOU).
    Once every object is confirmed present, each per-sha object is server-side copy_object'd onto
    its fixed key (lambda-packages/<name>) -- the key terraform's static s3_key reads.
    Deliberately does NOT re-upload to the per-sha key: that key IS the immutable reviewed
    reference, and rewriting it would be a self-copy moving ~183 MB for no gain. Prints
    DUCKLAKE_FETCH_OK on success so a green convergence record can be attributed to this route.

    s3_client is injected for testability (boto3.client("s3", region_name=region) when None).
    """
    if s3_client is None:
        import boto3  # noqa: PLC0415

        s3_client = boto3.client("s3", region_name=region)

    packages_root = Path(packages_dir)
    packages_root.mkdir(parents=True, exist_ok=True)

    target_sha = artifact_sha
    pointer = _read_pointer(s3_client, bucket, artifact_sha)
    if pointer is not None:
        candidate = pointer.get("artifact_sha")
        if not isinstance(candidate, str) or not (
            pointer.get("schema") == 1
            and len(candidate) == 40
            and all(c in "0123456789abcdef" for c in candidate)
            and candidate != artifact_sha
        ):
            raise DucklakeArtifactError(
                f"DUCKLAKE_FETCH_BAD_POINTER: lambda-packages/{artifact_sha}/superseded-by.json is malformed "
                f"or self-referencing: {pointer!r}"
            )
        target_sha = candidate

    for name in names:
        key = f"lambda-packages/{target_sha}/{name}"
        local_path = packages_root / name
        try:
            s3_client.download_file(bucket, key, str(local_path))
        except Exception as exc:  # noqa: BLE001 -- any fetch failure fails closed (Decision 77 no-TOCTOU)
            raise DucklakeArtifactError(
                f"DUCKLAKE_FETCH_MISSING {name}: no reviewed per-sha S3 object found at {key}; "
                f"failing closed (Decision 77 no-TOCTOU). Underlying: {exc}"
            ) from exc

    for name in names:
        key = f"lambda-packages/{target_sha}/{name}"
        s3_client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=f"lambda-packages/{name}")

    if target_sha != artifact_sha:
        print(
            f"DUCKLAKE_FETCH_OK: followed superseded-by.json from {artifact_sha} to {target_sha}; fetched "
            f"{len(names)} reviewed DuckLake artifact(s) and server-side-copied each onto its fixed key."
        )
    else:
        print(
            f"DUCKLAKE_FETCH_OK: fetched {len(names)} reviewed DuckLake artifact(s) from "
            f"lambda-packages/{target_sha}/ and server-side-copied each onto its fixed key."
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the build-ducklake-artifacts composite action.

    --mode build (default): build (unless --skip-build) then assert_and_upload() -- the existing
    rebuild path (the composite action's 'assert' / 'skip-assert' modes).
    --mode upload-only: upload_only() -- the composite action's 'upload-only' mode (the PR
    ducklake-artifacts job's first upload): build_lambda's own build+dual-write, unchanged, then
    record and upload inputs.json. Never calls assert_and_upload.
    --mode fetch: fetch_reviewed() -- downloads the reviewed per-sha artifacts (following a
    superseded-by.json pointer one hop when one exists, rec-4372) and server-side copies them
    onto the fixed key, with no rebuild.

    Usage: ducklake_artifacts.py <event_name> <artifact_sha> <bucket> [--region REGION] [--skip-build]
           [--mode {build,upload-only,fetch}]
    """
    parser = argparse.ArgumentParser(prog="ducklake_artifacts.py")
    parser.add_argument(
        "event_name", help="'push' runs the byte-identity assert; anything else skips it. Unused in --mode fetch/upload-only."
    )
    parser.add_argument("artifact_sha", help="Content-addressing sha for the per-sha S3 key.")
    parser.add_argument("bucket", help="S3 bucket for the DuckLake artifacts.")
    parser.add_argument("--region", default="eu-west-2")
    parser.add_argument(
        "--skip-build", action="store_true", help="Skip the build step (artifacts already built). Ignored in --mode fetch."
    )
    parser.add_argument(
        "--mode",
        choices=("build", "upload-only", "fetch"),
        default="build",
        help=(
            "'build' rebuilds and asserts/uploads (default); 'upload-only' builds+dual-writes via build_lambda "
            "and records inputs.json; 'fetch' downloads the reviewed per-sha artifacts instead."
        ),
    )
    args = parser.parse_args(argv)

    if args.mode == "upload-only":
        try:
            upload_only(args.artifact_sha, args.bucket, args.region)
        except DucklakeArtifactError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        return 0

    if args.mode == "fetch":
        try:
            fetch_reviewed(list(DUCKLAKE_ARTIFACT_NAMES), args.artifact_sha, args.bucket, args.region)
        except DucklakeArtifactError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 1
        return 0

    if not args.skip_build:
        build_ducklake_only()

    try:
        assert_and_upload(list(DUCKLAKE_ARTIFACT_NAMES), args.artifact_sha, args.event_name, args.bucket, args.region)
    except DucklakeArtifactError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
