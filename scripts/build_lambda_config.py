#!/usr/bin/env python3
"""Build configuration data + resolution for the Lambda build/deploy tool (Decision 104 pattern).

Holds constants (size limits, DuckLake dependency/extension/layer metadata, the DuckDB version
pin via the SSOT loader per Decision 99), the lazy build-contract loader + its four accessors,
Lambda file-pattern derivation, and the shared `_aws_profile_args` argv helper. Bottom of the
build_lambda_* import DAG -- imports nothing from build_lambda_packaging or build_lambda_deploy,
so those two may import from here with no cycle. See scripts/build_lambda.py for the CLI facade
that re-exports this module's public and test-patched symbols.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# AWS Lambda hard ceiling on a function zip / layer (terraform CLAUDE.md). Exceeding it must FAIL
# the build, not warn -- an oversize artifact is rejected at deploy time anyway.
LAMBDA_SIZE_LIMIT_BYTES = 262144000  # 262 MB
LAMBDA_SIZE_WARN_BYTES = 250 * 1024 * 1024  # early warning before the hard ceiling

# DuckLake lockstep pin (OQ.12): derived from the SSOT (config/lambda/ducklake/version.yaml).
from src.common.ducklake_version import (  # noqa: E402, I001
    extension_platform as _extension_platform,
    pinned_duckdb_version as _pinned_duckdb_version,
)

PINNED_DUCKDB_VERSION = _pinned_duckdb_version()

# DuckLake deps layer (ducklake-deps): duckdb pinned exactly, plus the runtime's import-time deps.
# pyyaml is required by ducklake_runtime.load_field_semantics; boto3 is provided by the Lambda base.
#
# Decision 154 / rec-2862: every entry below is EXACT-pinned (never a floor pin, `>=`/`~=`). A
# floor pin left this build time-variant -- a PyPI release published inside a human-approval
# window (pytz 2026.3.post1, observed between the PR's byte-identical assert and the reviewer's
# approval) resolved to different bytes on rebuild, which is precisely the rec-2862 TOCTOU. Exact
# pins make the resolved set a function of this file's content alone, not of wall-clock timing.
# Versions below equal those already baked into the reviewed deps layer (VP5) -- this pin
# introduces zero artifact-byte change. Bumping any of these pins is a deliberate, reviewed
# version-bump PR, not a background drift.
DUCKLAKE_DEPS = [
    f"duckdb=={PINNED_DUCKDB_VERSION}",
    "psycopg2-binary==2.9.12",
    "python-ulid==4.0.1",
    # python-ulid imports `from typing_extensions import Self` unconditionally, but its dependency
    # marker only requires typing_extensions on python<3.11. Building for 3.12 therefore skips it,
    # so the write path ImportErrors at runtime (ModuleNotFoundError: typing_extensions). Pin it.
    "typing_extensions==4.16.0",
    # duckdb lazily imports pytz when it converts tz-aware Python datetimes to/from its TIMESTAMP
    # types (the SCD2 path binds UTC-aware ULID timestamps). duckdb declares no hard pytz dep, so it
    # must be bundled explicitly or the write/read paths raise InvalidInputException at runtime.
    "pytz==2026.3",
    "pyyaml==6.0.3",
]

# DuckLake extensions baked into the ducklake-extensions layer: (LOAD name, published file stem).
# DuckDB publishes the Postgres extension binary as postgres_scanner.duckdb_extension even though it
# LOADs as `postgres` (verified against the pinned duckdb / config/lambda/ducklake/version.yaml).
DUCKLAKE_EXTENSIONS = (("ducklake", "ducklake"), ("httpfs", "httpfs"), ("postgres", "postgres_scanner"))
DUCKLAKE_EXT_PLATFORM = _extension_platform()  # derived from config/lambda/ducklake/version.yaml
DUCKLAKE_EXT_URL_BASE = f"https://extensions.duckdb.org/v{PINNED_DUCKDB_VERSION}/{DUCKLAKE_EXT_PLATFORM}"
# Vendored fallback prefix (raw .duckdb_extension files), seeded when egress to the CDN is blocked.
DUCKLAKE_EXT_S3_PREFIX = f"ducklake-extensions/v{PINNED_DUCKDB_VERSION}"
# The DuckDB CDN 403s the default urllib User-Agent; a browser UA returns 200.
_EXT_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0"}

_DUCKLAKE_WRITER_FUNCTION = "agent-platform-ducklake-writer"
_DUCKLAKE_READER_FUNCTION = "agent-platform-ducklake-reader"
_DUCKLAKE_MAINTENANCE_FUNCTION = "agent-platform-ducklake-maintenance"
_DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION = "agent-platform-ducklake-maintenance-smoke"
_DUCKLAKE_CATALOG_DR_FUNCTION = "agent-platform-ducklake-catalog-dr"
_DUCKLAKE_TELEMETRY_WRITER_FUNCTION = "agent-platform-ducklake-telemetry-writer"
_DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION = "agent-platform-ducklake-telemetry-writer-smoke"
_DUCKLAKE_TELEMETRY_READER_FUNCTION = "agent-platform-ducklake-telemetry-reader"
_DUCKLAKE_FUNCTION_ZIP_KEYS = {
    _DUCKLAKE_WRITER_FUNCTION: "lambda-packages/ducklake-writer.zip",
    _DUCKLAKE_READER_FUNCTION: "lambda-packages/ducklake-reader.zip",
    _DUCKLAKE_MAINTENANCE_FUNCTION: "lambda-packages/ducklake-maintenance.zip",
    _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION: "lambda-packages/ducklake-maintenance-smoke.zip",
    _DUCKLAKE_CATALOG_DR_FUNCTION: "lambda-packages/ducklake-catalog-dr.zip",
    _DUCKLAKE_TELEMETRY_WRITER_FUNCTION: "lambda-packages/ducklake-telemetry-writer.zip",
    _DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION: "lambda-packages/ducklake-telemetry-writer.zip",
    _DUCKLAKE_TELEMETRY_READER_FUNCTION: "lambda-packages/ducklake-telemetry-reader.zip",
}

# S3 key prefix for vendored AL2023/x86_64 pg_dump 16 binary + libpq.so.
# The binary is fetched at layer-build time from this S3 prefix (no pip wheel for pg_dump).
# Seeded by the operator via: aws s3 cp pg_dump s3://<bucket>/ducklake-pgclient/pg_dump16 --profile agent_platform
DUCKLAKE_PGCLIENT_S3_PREFIX = "ducklake-pgclient"
PINNED_PG_MAJOR = "16"

# The three DuckLake Lambda layers (deps + extensions + pgclient). Used by publish_canary_layers.
DUCKLAKE_LAYER_NAMES = (
    "ducklake-deps-layer",
    "ducklake-extensions-layer",
    "ducklake-pgclient-layer",
)

# Retained for backward compatibility with external callers and tests.
_LAMBDA_SCRIPTS = [
    "__init__.py",
    "aws_profile.py",
    "llm/github_models_client.py",
    "llm/client.py",
    "llm/utils.py",
    "run_scheduled_agent.py",
    "s3_log_store.py",
    "tool_runtime.py",
]

_LAMBDA_FUNCTION_NAMES = [
    "agent-platform-scheduled-agent-dispatcher",
    "agent-platform-findings-processor",
]

# ---------------------------------------------------------------------------
# Build contract loader (T-1.16): lazy, cached, import-safe.
# Falls through to in-code FALLBACK_* constants on ANY read/parse failure.
# ---------------------------------------------------------------------------

_BUILD_CONTRACT_PATH = ROOT / "docs" / "contracts" / "build-lambda.yaml"
_BUILD_CONTRACT_REGISTRY: dict | None = None  # None until first accessor call


def _fallback_build_registry() -> dict:
    return {
        "size_limit_bytes": LAMBDA_SIZE_LIMIT_BYTES,
        "deploy_targets": {
            "prod_functions": list(_LAMBDA_FUNCTION_NAMES),
            "ducklake_function_zip_keys": dict(_DUCKLAKE_FUNCTION_ZIP_KEYS),
            "ducklake_layer_names": list(DUCKLAKE_LAYER_NAMES),
        },
    }


def _load_build_contract() -> dict:
    global _BUILD_CONTRACT_REGISTRY
    if _BUILD_CONTRACT_REGISTRY is not None:
        return _BUILD_CONTRACT_REGISTRY
    try:
        import yaml  # noqa: PLC0415

        raw = yaml.safe_load(_BUILD_CONTRACT_PATH.read_text(encoding="utf-8"))
        registry = {
            "size_limit_bytes": raw["size_limit_bytes"],
            "deploy_targets": {
                "prod_functions": list(raw["deploy_targets"]["prod_functions"]),
                "ducklake_function_zip_keys": dict(raw["deploy_targets"]["ducklake_function_zip_keys"]),
                "ducklake_layer_names": list(raw["deploy_targets"]["ducklake_layer_names"]),
            },
        }
        _BUILD_CONTRACT_REGISTRY = registry
    except Exception:
        _BUILD_CONTRACT_REGISTRY = _fallback_build_registry()
    return _BUILD_CONTRACT_REGISTRY


def _build_size_limit_bytes() -> int:
    return _load_build_contract()["size_limit_bytes"]


def _build_prod_function_names() -> list[str]:
    return list(_load_build_contract()["deploy_targets"]["prod_functions"])


def _build_ducklake_function_zip_keys() -> dict[str, str]:
    return dict(_load_build_contract()["deploy_targets"]["ducklake_function_zip_keys"])


def _build_ducklake_layer_names() -> list[str]:
    return list(_load_build_contract()["deploy_targets"]["ducklake_layer_names"])


def _get_lambda_file_patterns() -> list[str]:
    """Derive LAMBDA_FILE_PATTERNS from the union of all active manifests (CD.24)."""
    root_str = str(ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)
    try:
        from scripts.lambda_manifest import derive_lambda_file_patterns  # noqa: PLC0415

        return derive_lambda_file_patterns()
    except Exception:
        return []
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)


# Derived at import time from the union of active manifests.
LAMBDA_FILE_PATTERNS: list[str] = _get_lambda_file_patterns()


def _aws_profile_args(profile: str) -> list[str]:
    """Return the `--profile <name>` argv tokens, or [] when profile is empty.

    GitHub-hosted OIDC runners resolve AWS credentials from the environment (no named profile);
    passing `--profile ""` to the aws CLI is an error, so an empty profile must omit the flag
    entirely rather than pass it empty.
    """
    return ["--profile", profile] if profile else []
