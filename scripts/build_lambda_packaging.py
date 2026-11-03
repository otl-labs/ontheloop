#!/usr/bin/env python3
# complexity-waiver: decision-43 -- build_pgclient_layer's fail-closed pg_dump/pg_restore version
# assertions (T2.17/T2.18 FP-B) legitimately exceed CC 20 (radon; the repo branch counter also
# counts With/BoolOp). See docs/DECISIONS.md Decision 43/102.
"""Bundle/zip assembly for the Lambda build/deploy tool (Decision 104 pattern): prod + DuckLake
package and layer builders, plus their S3/CDN input-fetch helpers. Carries the CC waiver for
build_pgclient_layer (complexity-waiver: decision-43 above); no other function here needs it. See
scripts/build_lambda.py for the CLI facade that re-exports this module's public and test-patched
symbols.
"""

import csv
import gzip
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from scripts.build_lambda_config import (
    _EXT_FETCH_HEADERS,
    DUCKLAKE_DEPS,
    DUCKLAKE_EXT_PLATFORM,
    DUCKLAKE_EXT_S3_PREFIX,
    DUCKLAKE_EXT_URL_BASE,
    DUCKLAKE_EXTENSIONS,
    DUCKLAKE_PGCLIENT_S3_PREFIX,
    PINNED_DUCKDB_VERSION,
    PINNED_PG_MAJOR,
    ROOT,
    _aws_profile_args,
    _build_size_limit_bytes,
)

OUTPUT_DIR = ROOT / "lambda-packages"

# Fixed ZipInfo timestamp (DOS epoch floor) so byte-reproducible zips don't leak build-time
# wall-clock mtimes into the archive (Decision 77 no-TOCTOU: the applied zip must be byte-identical
# to the reviewed PR-job artifact).
_FIXED_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)


def _deterministic_zipinfo(arcname: str, *, executable: bool = False) -> zipfile.ZipInfo:
    """Build a ZipInfo with a pinned timestamp + explicit permission bits (no filesystem mtime leak)."""
    info = zipfile.ZipInfo(arcname, date_time=_FIXED_ZIP_DATETIME)
    info.external_attr = (0o755 if executable else 0o644) << 16
    return info


def _zip_staged_dir(stage_dir: Path, zip_path: Path) -> Path:
    """Write all files in stage_dir into zip_path (ZIP_DEFLATED), byte-reproducibly.

    Sorted iteration + pinned ZipInfo timestamp/permissions make the output byte-identical across
    builds of the same input tree (Decision 77). Shared by the four DuckLake function zips and the
    two DuckLake layer builders that don't need pgclient's per-entry executable-bit logic.
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(stage_dir.rglob("*")):
            if f.is_file():
                zf.writestr(_deterministic_zipinfo(str(f.relative_to(stage_dir))), f.read_bytes())
    return zip_path


_zip_layer_dir_deterministic = _zip_staged_dir


def build_app_package(temp_dir: Path) -> Path:
    """Create data-pipeline.zip from the data-pipeline manifest (manifest-driven, CD.24).

    Retired whole-src and whole-config tree bundling in favour of explicit manifest declarations.
    All bundled files are explicitly declared in src/lambdas/data-pipeline/manifest.yaml.
    """
    from scripts.lambda_manifest import load, stage_bundle  # noqa: PLC0415

    manifest = load(ROOT / "src" / "lambdas" / "data-pipeline" / "manifest.yaml")
    app_dir = temp_dir / "app"
    app_dir.mkdir(parents=True)

    print("  Installing pip dependencies into app package...")
    stage_bundle(manifest, app_dir, skip_pip=False)

    zip_path = OUTPUT_DIR / "data-pipeline.zip"
    return _zip_staged_dir(app_dir, zip_path)


def list_bundle(artifact_slug: str) -> None:
    """Stage a manifest-driven bundle and emit the static file list to stdout.

    Excludes __pycache__ entries and pip-installed packages (those are determined
    by pip install and not manifest-declared paths).  Used for file-list equivalence
    diffing against pre-change copytree baselines (VP Step 7 of CD.24 plans).
    """
    from scripts.lambda_manifest import load, stage_bundle  # noqa: PLC0415

    manifest_path = ROOT / "src" / "lambdas" / artifact_slug / "manifest.yaml"
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path.relative_to(ROOT)}", file=sys.stderr)
        sys.exit(1)

    manifest = load(manifest_path)
    with tempfile.TemporaryDirectory(prefix=f"list-bundle-{artifact_slug}-") as tmp:
        stage_dir = Path(tmp)
        stage_bundle(manifest, stage_dir, skip_pip=True)
        for f in sorted(stage_dir.rglob("*")):
            if f.is_file() and "__pycache__" not in str(f):
                print(str(f.relative_to(stage_dir)))


def assert_within_size_limit(zip_path: Path) -> None:
    """Hard-fail the build if *zip_path* exceeds the 262 MB Lambda ceiling (terraform CLAUDE.md).

    Replaces the prior non-fatal WARN: an oversize artifact is rejected at deploy time, so the build
    must stop here rather than ship a zip that cannot be deployed.
    """
    size = zip_path.stat().st_size
    limit = _build_size_limit_bytes()
    if size > limit:
        mb = round(size / 1024 / 1024, 2)
        print(
            f"ERROR: {zip_path.name} is {mb} MB ({size} bytes), over the "
            f"{limit} byte (262 MB) Lambda zip/layer limit. Build failed.",
            file=sys.stderr,
        )
        sys.exit(1)


def build_ducklake_function_package(temp_dir: Path, slug: str, zip_name: str) -> Path:
    """Build a DuckLake function zip from its manifest (deps live in the layer, not the zip)."""
    from scripts.lambda_manifest import load, stage_bundle  # noqa: PLC0415

    manifest = load(ROOT / "src" / "lambdas" / slug / "manifest.yaml")
    app_dir = temp_dir / slug
    app_dir.mkdir(parents=True)
    stage_bundle(manifest, app_dir, skip_pip=True)  # pip_packages empty; duckdb/ulid come from the layer
    return _zip_staged_dir(app_dir, OUTPUT_DIR / zip_name)


def _is_console_script_row(record_path: str) -> bool:
    """A RECORD path pip wrote for a wrapper in its --target scripts dir: one or more '..' parts, then bin/<name>."""
    parts = PurePosixPath(record_path).parts
    leading = 0
    while leading < len(parts) and parts[leading] == "..":
        leading += 1
    return leading > 0 and len(parts) - leading == 2 and parts[leading] == "bin"


def _prune_console_scripts(site_packages: Path) -> list[str]:
    """Remove pip's console-script wrappers and the RECORD rows naming them (rec-4189).

    pip writes each wrapper's shebang as the building interpreter's sys.executable, so runners with
    different Python patch versions built different layer bytes and the push-path byte-identity
    assert failed closed. In a layer the wrappers are dead: site-packages/bin is off PATH and the
    shebang names an interpreter that does not exist on Lambda. Retained RECORD lines keep their bytes.
    """
    pruned: list[str] = []
    for record in sorted(site_packages.glob("*.dist-info/RECORD")):
        lines = record.read_bytes().splitlines(keepends=True)
        kept = []
        for line in lines:
            row = next(csv.reader([line.decode("utf-8")]), [])
            if row and _is_console_script_row(row[0]):
                name = PurePosixPath(row[0]).name
                (site_packages / "bin" / name).unlink(missing_ok=True)
                pruned.append(name)
            else:
                kept.append(line)
        if len(kept) != len(lines):
            record.write_bytes(b"".join(kept))
    scripts_dir = site_packages / "bin"
    if scripts_dir.is_dir() and not any(scripts_dir.iterdir()):
        scripts_dir.rmdir()
    return sorted(pruned)


def build_ducklake_deps_layer(temp_dir: Path) -> Path:
    """Create ducklake-deps-layer.zip (duckdb pinned via config/lambda/ducklake/version.yaml
    + psycopg2-binary + python-ulid + pyyaml)."""
    site_packages = temp_dir / "ducklake-deps" / "python" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)

    req_file = temp_dir / "requirements-ducklake.txt"
    req_file.write_text("\n".join(DUCKLAKE_DEPS), encoding="utf-8")

    print("  Installing DuckLake deps to Lambda layer structure...")
    # duckdb publishes a manylinux_2_28 wheel (no manylinux2014/2_17 wheel above 1.2.2);
    # Lambda python3.12 runs on Amazon Linux 2023 (glibc 2.34), compatible with 2_28. Offer both
    # tags newest-first so each dep resolves to its best available wheel.
    pip_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--requirement",
            str(req_file),
            "--target",
            str(site_packages),
            "--platform",
            "manylinux_2_28_x86_64",
            "--platform",
            "manylinux2014_x86_64",
            "--implementation",
            "cp",
            "--python-version",
            "3.12",
            "--only-binary=:all:",
            "--quiet",
        ],
        check=False,
    )
    if pip_result.returncode != 0:
        print(f"ERROR: DuckLake deps installation failed (exit {pip_result.returncode})")
        sys.exit(1)

    pruned = _prune_console_scripts(site_packages)
    if pruned:
        print(f"  Pruned console-script wrapper(s) that embed the build interpreter path: {', '.join(pruned)}")

    # Do NOT strip *.dist-info: duckdb>=1.3 reads its own version via importlib.metadata at import
    # time (duckdb/_version.py -> importlib.metadata.version("duckdb")), which needs the dist-info
    # METADATA present. Removing it raises PackageNotFoundError on a clean Lambda runtime (no other
    # duckdb metadata on the path), surfacing as an ImportError. The size saved is trivial.
    for pattern in ("__pycache__", "*.pyc", "tests", "test"):
        for path in site_packages.rglob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    zip_path = OUTPUT_DIR / "ducklake-deps-layer.zip"
    layer_dir = temp_dir / "ducklake-deps"
    return _zip_layer_dir_deterministic(layer_dir, zip_path)


def _fetch_extension_bytes(stem: str, *, bucket: str | None, profile: str, region: str) -> bytes:
    """Return the raw .duckdb_extension bytes for *stem*.

    Prefers the vendored S3 fallback (raw, pre-gunzipped) when present; otherwise fetches the .gz
    from the DuckDB CDN with a browser User-Agent (the CDN 403s the default urllib UA) and gunzips.
    """
    if bucket is not None:
        raw = _try_s3_extension(bucket, stem, profile, region)
        if raw is not None:
            print(f"    {stem}: from S3 fallback s3://{bucket}/{DUCKLAKE_EXT_S3_PREFIX}/")
            return raw
    url = f"{DUCKLAKE_EXT_URL_BASE}/{stem}.duckdb_extension.gz"
    print(f"    {stem}: fetching {url}")
    req = urllib.request.Request(url, headers=_EXT_FETCH_HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 -- pinned https host
        return gzip.decompress(resp.read())


def _try_s3_extension(bucket: str, stem: str, profile: str, region: str) -> bytes | None:
    """Try to read the vendored raw extension from S3; return None if absent/unreadable."""
    import tempfile as _tf  # noqa: PLC0415

    key = f"{DUCKLAKE_EXT_S3_PREFIX}/{stem}.duckdb_extension"
    with _tf.TemporaryDirectory() as td:
        dest = Path(td) / f"{stem}.duckdb_extension"
        result = subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest), "--region", region, *_aws_profile_args(profile)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and dest.exists():
            return dest.read_bytes()
    return None


def build_ducklake_extensions_layer(
    temp_dir: Path, *, bucket: str | None = None, profile: str = "agent_platform", region: str = "eu-west-2"
) -> Path:
    """Create ducklake-extensions-layer.zip staging the 3 pinned extensions under /opt.

    Layout: duckdb_extensions/v{ver}/linux_amd64/{ducklake,httpfs,postgres_scanner}.duckdb_extension
    so the runtime sets extension_directory=/opt/duckdb_extensions and LOADs them with no network.
    """
    ext_root = temp_dir / "ducklake-ext" / "duckdb_extensions" / f"v{PINNED_DUCKDB_VERSION}" / DUCKLAKE_EXT_PLATFORM
    ext_root.mkdir(parents=True)

    print("  Staging DuckLake extensions (prefer S3 fallback, else CDN)...")
    for _load_name, stem in DUCKLAKE_EXTENSIONS:
        raw = _fetch_extension_bytes(stem, bucket=bucket, profile=profile, region=region)
        (ext_root / f"{stem}.duckdb_extension").write_bytes(raw)

    zip_path = OUTPUT_DIR / "ducklake-extensions-layer.zip"
    layer_dir = temp_dir / "ducklake-ext"
    return _zip_layer_dir_deterministic(layer_dir, zip_path)


def _try_s3_pgclient(bucket: str, filename: str, profile: str, region: str) -> bytes | None:
    """Try to fetch a vendored pgclient binary from S3; return None if absent/unreadable."""
    import tempfile as _tf  # noqa: PLC0415

    key = f"{DUCKLAKE_PGCLIENT_S3_PREFIX}/{filename}"
    with _tf.TemporaryDirectory() as td:
        dest = Path(td) / filename
        result = subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest), "--region", region, *_aws_profile_args(profile)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and dest.exists():
            return dest.read_bytes()
    return None


def build_pgclient_layer(
    temp_dir: Path, *, bucket: str | None = None, profile: str = "agent_platform", region: str = "eu-west-2"
) -> Path:
    """Create ducklake-pgclient-layer.zip staging pg_dump + pg_restore 16 + libpq.so under /opt/bin + /opt/lib.

    The Lambda runtime expects the binaries at /opt/bin/{pg_dump,pg_restore} with
    LD_LIBRARY_PATH=/opt/lib so libpq.so resolves at invocation.

    Binary source: AL2023/x86_64 pg_dump + pg_restore 16 + libpq.so vendored to S3
    (s3://<data-lake-bucket>/ducklake-pgclient/{pg_dump16,pg_restore16,libpq.so.5}) by the operator.
    Fetched here at build time; fails closed if the S3 objects are absent (a version mismatch
    at deploy time is better than one at runtime).

    After staging, asserts `./opt/bin/pg_dump --version` and `./opt/bin/pg_restore --version`
    output contains PG major version 16 to catch version drift before the layer is uploaded
    (fail-closed guard). pg_restore is re-added solely for the Decision 88/107 DR
    restore-drill path (action_restore_drill); the Decision 100 clone-rehearsal
    (action_clone_catalog) stays on Neon native branching and does not use it.
    """
    opt_bin = temp_dir / "pgclient" / "bin"
    opt_lib = temp_dir / "pgclient" / "lib"
    opt_bin.mkdir(parents=True)
    opt_lib.mkdir(parents=True)

    if bucket is None:
        print("  pgclient layer: no bucket specified -- skipping S3 fetch (local dev mode)")
    else:
        # Single vendored bundle: bin/pg_dump + lib/<libpq + full transitive .so closure>
        # (ldap/lber/sasl/krb5 family/com_err/keyutils/libevent/libselinux/pcre2). The common
        # libs (libcrypto/libssl/libz/libzstd/liblz4) are provided by the AL2023 Lambda base and
        # deliberately NOT bundled, to avoid overriding the runtime's crypto on the global lib path.
        bundle_name = "pgclient-bundle.tar.gz"
        print(f"  pgclient: fetching {bundle_name} from s3://{bucket}/{DUCKLAKE_PGCLIENT_S3_PREFIX}/...")
        raw = _try_s3_pgclient(bucket, bundle_name, profile, region)
        if raw is None:
            print(
                f"  ERROR: {bundle_name} not found in s3://{bucket}/{DUCKLAKE_PGCLIENT_S3_PREFIX}/. "
                "Seed it first: a gzip tarball with bin/{pg_dump,pg_restore} + lib/<libpq.so.5 + its "
                "transitive shared-library closure>, built from RHEL9/AL2023-ABI (glibc 2.34) RPMs. "
                "See the catalog-DR runbook (Section 4) for the closure-build procedure.",
                file=sys.stderr,
            )
            sys.exit(1)

        import io as _io  # noqa: PLC0415
        import tarfile as _tarfile  # noqa: PLC0415

        n_files = 0
        with _tarfile.open(fileobj=_io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or not (member.name.startswith("bin/") or member.name.startswith("lib/")):
                    continue
                target = temp_dir / "pgclient" / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                target.write_bytes(extracted.read())
                target.chmod(0o755)
                n_files += 1

        pg_dump_bin = opt_bin / "pg_dump"
        if not pg_dump_bin.exists():
            print(f"  ERROR: {bundle_name} did not contain bin/pg_dump", file=sys.stderr)
            sys.exit(1)

        # Fail-closed version assert: pg_dump --version must report PG major 16, with the bundled
        # libs on the loader path (proves the closure links before the layer ships).
        version_env = {**os.environ, "LD_LIBRARY_PATH": str(opt_lib)}
        version_result = subprocess.run(
            [str(pg_dump_bin), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=version_env,
        )
        if version_result.returncode != 0 or f"pg_dump (PostgreSQL) {PINNED_PG_MAJOR}" not in version_result.stdout:
            actual = version_result.stdout.strip() or version_result.stderr.strip()
            print(
                f"ERROR: pg_dump version assertion failed. Expected PG{PINNED_PG_MAJOR}, got: {actual!r}. "
                "Re-seed the S3 bundle with a PG16 build + complete lib closure.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  OK pg_dump --version reports PG{PINNED_PG_MAJOR} ({n_files} bundled files)")

        # Fail-closed pg_restore existence + version assert, symmetric to the pg_dump guard above.
        # Re-added solely for the Decision 88/107 DR restore-drill path (action_restore_drill);
        # the Decision 100 clone-rehearsal (action_clone_catalog) stays on Neon native branching.
        pg_restore_bin = opt_bin / "pg_restore"
        if not pg_restore_bin.exists():
            print(f"  ERROR: {bundle_name} did not contain bin/pg_restore", file=sys.stderr)
            sys.exit(1)

        restore_version_result = subprocess.run(
            [str(pg_restore_bin), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=version_env,
        )
        if (
            restore_version_result.returncode != 0
            or f"pg_restore (PostgreSQL) {PINNED_PG_MAJOR}" not in restore_version_result.stdout
        ):
            actual = restore_version_result.stdout.strip() or restore_version_result.stderr.strip()
            print(
                f"ERROR: pg_restore version assertion failed. Expected PG{PINNED_PG_MAJOR}, got: {actual!r}. "
                "Re-seed the S3 bundle with a PG16 build + complete lib closure.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  OK pg_restore --version reports PG{PINNED_PG_MAJOR}")

    layer_root = temp_dir / "pgclient"
    zip_path = OUTPUT_DIR / "ducklake-pgclient-layer.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(layer_root.rglob("*")):
            if f.is_file() or f.is_symlink():
                arcname = str(f.relative_to(layer_root))
                if f.is_symlink():
                    info = zipfile.ZipInfo(arcname, date_time=_FIXED_ZIP_DATETIME)
                    info.create_system = 3  # Unix
                    info.external_attr = 0o120755 << 16  # symlink type
                    zf.writestr(info, str(f.resolve().name))
                else:
                    zf.writestr(_deterministic_zipinfo(arcname, executable="bin/" in arcname), f.read_bytes())
    return zip_path
