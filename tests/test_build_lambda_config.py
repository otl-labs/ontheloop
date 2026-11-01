"""Tests for scripts/build_lambda_config.py (Decision 104 split of tests/test_build_lambda.py)."""

import sys
from unittest.mock import patch

import pytest

import scripts.build_lambda as bl
import scripts.build_lambda_config as bl_config
from scripts.build_lambda_config import (
    _DUCKLAKE_CATALOG_DR_FUNCTION,
    _DUCKLAKE_MAINTENANCE_FUNCTION,
    _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,
    _DUCKLAKE_READER_FUNCTION,
    _DUCKLAKE_TELEMETRY_READER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION,
    _DUCKLAKE_WRITER_FUNCTION,
    _LAMBDA_SCRIPTS,
    PINNED_DUCKDB_VERSION,
)
from src.common.ducklake_version import pinned_duckdb_version

pytestmark = pytest.mark.unit


def test_ducklake_deps_all_exact_pinned():
    """Decision 154 / rec-2862: every DUCKLAKE_DEPS entry is exact-pinned ('=='), no floor pins
    ('>=') or compatible-release pins ('~=') survive. A surviving floor pin leaves the build
    time-variant -- a PyPI release published inside a human-approval window can resolve to bytes
    nobody reviewed, reproducing the rec-2862 failure class."""
    for entry in bl_config.DUCKLAKE_DEPS:
        assert "==" in entry, f"{entry!r} is not exact-pinned"
        assert ">=" not in entry, f"{entry!r} is still a floor pin"
        assert "~=" not in entry, f"{entry!r} is still a compatible-release pin"


class TestPinnedConstants:
    def test_pinned_duckdb_version(self):
        assert PINNED_DUCKDB_VERSION == pinned_duckdb_version()

    def test_ducklake_deps_pin_exact(self):
        assert f"duckdb=={PINNED_DUCKDB_VERSION}" in bl_config.DUCKLAKE_DEPS

    def test_postgres_published_as_scanner(self):
        stems = dict(bl_config.DUCKLAKE_EXTENSIONS)
        assert stems["postgres"] == "postgres_scanner"

    def test_ducklake_function_zip_keys(self):
        assert bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_WRITER_FUNCTION] == "lambda-packages/ducklake-writer.zip"
        assert bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_READER_FUNCTION] == "lambda-packages/ducklake-reader.zip"
        assert (
            bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_MAINTENANCE_FUNCTION] == "lambda-packages/ducklake-maintenance.zip"
        )
        assert (
            bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION]
            == "lambda-packages/ducklake-maintenance-smoke.zip"
        )
        assert (
            bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_CATALOG_DR_FUNCTION] == "lambda-packages/ducklake-catalog-dr.zip"
        )
        assert (
            bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_TELEMETRY_WRITER_FUNCTION]
            == "lambda-packages/ducklake-telemetry-writer.zip"
        )
        assert (
            bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION]
            == "lambda-packages/ducklake-telemetry-writer.zip"
        )
        assert (
            bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS[_DUCKLAKE_TELEMETRY_READER_FUNCTION]
            == "lambda-packages/ducklake-telemetry-reader.zip"
        )


class TestLambdaScriptsAndSdkConfig:
    """Tests for _LAMBDA_SCRIPTS constants."""

    def test_llm_client_in_lambda_scripts(self):
        """llm/client.py is listed in _LAMBDA_SCRIPTS."""
        assert "llm/client.py" in _LAMBDA_SCRIPTS

    def test_llm_utils_in_lambda_scripts(self):
        """llm/utils.py is listed in _LAMBDA_SCRIPTS."""
        assert "llm/utils.py" in _LAMBDA_SCRIPTS

    def test_tool_runtime_in_lambda_scripts(self):
        """tool_runtime.py is listed in _LAMBDA_SCRIPTS."""
        assert "tool_runtime.py" in _LAMBDA_SCRIPTS

    def test_bedrock_client_absent_from_lambda_scripts(self):
        """bedrock_client.py left the bundle with the CD.28 retirement."""
        assert "bedrock_client.py" not in _LAMBDA_SCRIPTS


class TestBuildLambdaContract:
    """Tests for the lazy YAML loader and accessors in build_lambda_config (T-1.16).

    test_canary_layers_sourced_from_accessor is NOT here -- it exercises publish_canary_layers
    and lives in tests/test_build_lambda_deploy.py (critique finding B).
    """

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Save and restore the cached registry and contract path around each test."""
        original_registry = bl_config._BUILD_CONTRACT_REGISTRY
        original_path = bl_config._BUILD_CONTRACT_PATH
        bl_config._BUILD_CONTRACT_REGISTRY = None
        yield
        bl_config._BUILD_CONTRACT_REGISTRY = original_registry
        bl_config._BUILD_CONTRACT_PATH = original_path

    def test_reads_yaml(self):
        """Loader reads the YAML contract and returns YAML-sourced values from each accessor."""
        import yaml

        d = yaml.safe_load(bl_config._BUILD_CONTRACT_PATH.read_text(encoding="utf-8"))
        assert bl_config._build_size_limit_bytes() == d["size_limit_bytes"]
        assert set(bl_config._build_prod_function_names()) == set(d["deploy_targets"]["prod_functions"])
        assert bl_config._build_ducklake_function_zip_keys() == d["deploy_targets"]["ducklake_function_zip_keys"]
        assert list(bl_config._build_ducklake_layer_names()) == list(d["deploy_targets"]["ducklake_layer_names"])

    def test_anti_drift(self):
        """YAML registry equals in-code FALLBACK_* constants (no silent divergence)."""
        import yaml

        d = yaml.safe_load(bl_config._BUILD_CONTRACT_PATH.read_text(encoding="utf-8"))
        assert bl_config.LAMBDA_SIZE_LIMIT_BYTES == d["size_limit_bytes"]
        assert set(bl_config._LAMBDA_FUNCTION_NAMES) == set(d["deploy_targets"]["prod_functions"])
        assert bl_config._DUCKLAKE_FUNCTION_ZIP_KEYS == d["deploy_targets"]["ducklake_function_zip_keys"]
        assert list(bl_config.DUCKLAKE_LAYER_NAMES) == list(d["deploy_targets"]["ducklake_layer_names"])

    def test_cli_flag_equivalence(self):
        """YAML cli_flags match build_parser() option strings (excluding --help).

        build_parser stays in scripts.build_lambda (the thin CLI facade), so this one method
        keeps referencing bl -- it is the sole exception to the blanket bl_config repoint.
        """
        import yaml

        d = yaml.safe_load(bl_config._BUILD_CONTRACT_PATH.read_text(encoding="utf-8"))
        p = bl.build_parser()
        flags = {s for s in p._option_string_actions if s.startswith("--")} - {"--help"}
        assert flags == set(d["cli_flags"])

    def test_absent_yaml_fallback(self, tmp_path):
        """Returns fallback constants when the contract file does not exist."""
        bl_config._BUILD_CONTRACT_PATH = tmp_path / "does-not-exist.yaml"
        assert bl_config._build_size_limit_bytes() == bl_config.LAMBDA_SIZE_LIMIT_BYTES
        assert set(bl_config._build_prod_function_names()) == set(bl_config._LAMBDA_FUNCTION_NAMES)

    def test_unparseable_yaml_fallback(self, tmp_path):
        """Returns fallback constants when the contract file contains invalid YAML."""
        contract = tmp_path / "build-lambda.yaml"
        contract.write_text("}{invalid yaml{", encoding="utf-8")
        bl_config._BUILD_CONTRACT_PATH = contract
        assert bl_config._build_size_limit_bytes() == bl_config.LAMBDA_SIZE_LIMIT_BYTES

    def test_yaml_import_unavailable_fallback(self, tmp_path):
        """Returns fallback constants when yaml is unavailable (simulates missing pyyaml)."""
        contract = tmp_path / "build-lambda.yaml"
        contract.write_text("size_limit_bytes: 999", encoding="utf-8")
        bl_config._BUILD_CONTRACT_PATH = contract
        with patch.dict(sys.modules, {"yaml": None}):
            assert bl_config._build_size_limit_bytes() == bl_config.LAMBDA_SIZE_LIMIT_BYTES

    def test_lazy_import_safety(self):
        """Registry is None immediately after import (no I/O at module import time)."""
        assert bl_config._BUILD_CONTRACT_REGISTRY is None, "registry must be lazy, not loaded at import"


class TestGetLambdaFilePatterns:
    """_get_lambda_file_patterns had no direct test before the split (only exercised indirectly
    via the LAMBDA_FILE_PATTERNS module-level constant at import time); added to meet the
    per-file coverage floor for the new module."""

    def test_returns_list_matching_module_constant(self):
        """Happy path: derives a list from the union of active manifests."""
        result = bl_config._get_lambda_file_patterns()
        assert isinstance(result, list)
        assert result == bl_config.LAMBDA_FILE_PATTERNS

    def test_import_failure_degrades_to_empty_list(self):
        """Any failure resolving scripts.lambda_manifest degrades to [] (import-safety, never raises)."""
        with patch.dict(sys.modules, {"scripts.lambda_manifest": None}):
            assert bl_config._get_lambda_file_patterns() == []

    def test_sys_path_injection_when_root_absent(self):
        """When ROOT is not already on sys.path, the function injects it for the duration of the
        call and removes it again afterward (the injected/finally branch)."""
        root_str = str(bl_config.ROOT)
        original_count = sys.path.count(root_str)
        while root_str in sys.path:
            sys.path.remove(root_str)
        try:
            assert root_str not in sys.path
            result = bl_config._get_lambda_file_patterns()
            assert isinstance(result, list)
            assert root_str not in sys.path  # removed again by the finally block
        finally:
            for _ in range(original_count):
                sys.path.insert(0, root_str)


class TestProfilelessArgv:
    """aws CLI argv omits `--profile` when the resolved profile is empty (GitHub-hosted OIDC
    runners resolve creds from the environment and have no named profile) and includes it when
    non-empty (local/agent_platform dev). Unblocks `--ducklake-only` under CI (rec-2512).

    Only the two pure _aws_profile_args tests live here; the profileless argv tests for functions
    owned by build_lambda_packaging / build_lambda_deploy live alongside those modules' test files.
    """

    def test_aws_profile_args_empty(self):
        assert bl_config._aws_profile_args("") == []

    def test_aws_profile_args_non_empty(self):
        assert bl_config._aws_profile_args("agent_platform") == ["--profile", "agent_platform"]
