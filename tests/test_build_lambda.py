"""Tests for the scripts.build_lambda thin CLI facade (Decision 104 pattern).

Keeps only the tests that exercise the retained CLI/orchestrators (_run_prod_build,
_run_ducklake_build, main) -- their patch("scripts.build_lambda.<callee>") sites intercept via
the facade re-export block regardless of which extracted module now defines the callee (the
orchestrators resolve callees by bare name in THIS module's namespace). All other test classes
moved to tests/test_build_lambda_config.py, tests/test_build_lambda_packaging.py, and
tests/test_build_lambda_deploy.py (each mapped 1:1 to the module it now defines).
"""

import types
from unittest.mock import patch

import pytest

import scripts.build_lambda as bl

pytestmark = pytest.mark.unit


class _FakePath:
    """Minimal Path double exposing .name + .stat().st_size for size-assert tests."""

    def __init__(self, size=100, name="x.zip"):
        self._size = size
        self.name = name

    def stat(self):
        return types.SimpleNamespace(st_size=self._size)


def _args(**kw):
    import argparse

    ns = argparse.Namespace(
        skip_upload=True,
        bucket="",
        profile="agent_platform",
        region="eu-west-2",
        deploy=False,
        ducklake_only=False,
        ducklake_functions=None,
        ducklake_deploy_only=False,
        list_bundle=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestRunBuilds:
    def test_run_ducklake_build_skip_upload(self):
        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch(
                "scripts.build_lambda.build_ducklake_function_package",
                side_effect=[
                    _FakePath(name="w.zip"),
                    _FakePath(name="r.zip"),
                    _FakePath(name="m.zip"),
                    _FakePath(name="ms.zip"),
                    _FakePath(name="dr.zip"),
                    _FakePath(name="tw.zip"),
                    _FakePath(name="trd.zip"),
                ],
            ),
            patch("scripts.build_lambda.build_ducklake_deps_layer", return_value=_FakePath(name="deps.zip")),
            patch("scripts.build_lambda.build_ducklake_extensions_layer", return_value=_FakePath(name="ext.zip")),
            patch("scripts.build_lambda.build_pgclient_layer", return_value=_FakePath(name="pgclient.zip")),
            patch("scripts.build_lambda.assert_within_size_limit") as mock_assert,
            patch("scripts.build_lambda.upload_to_s3") as mock_upload,
        ):
            bl._run_ducklake_build(_args(skip_upload=True))
        assert mock_assert.call_count == 10  # 7 function zips + 3 layers
        assert mock_upload.call_count == 0

    def test_run_ducklake_build_upload_and_deploy(self):
        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch(
                "scripts.build_lambda.build_ducklake_function_package",
                side_effect=[
                    _FakePath(name="w.zip"),
                    _FakePath(name="r.zip"),
                    _FakePath(name="m.zip"),
                    _FakePath(name="ms.zip"),
                    _FakePath(name="dr.zip"),
                    _FakePath(name="tw.zip"),
                    _FakePath(name="trd.zip"),
                ],
            ),
            patch("scripts.build_lambda.build_ducklake_deps_layer", return_value=_FakePath(name="deps.zip")),
            patch("scripts.build_lambda.build_ducklake_extensions_layer", return_value=_FakePath(name="ext.zip")),
            patch("scripts.build_lambda.build_pgclient_layer", return_value=_FakePath(name="pgclient.zip")),
            patch("scripts.build_lambda.assert_within_size_limit"),
            patch("scripts.build_lambda.validate_bucket_exists", return_value=True),
            patch("scripts.build_lambda.upload_to_s3") as mock_upload,
            patch("scripts.build_lambda.update_lambda_functions") as mock_update,
        ):
            bl._run_ducklake_build(_args(skip_upload=False, deploy=True))
        assert mock_upload.call_count == 10  # 7 function zips + 3 layers
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs.get("only_ducklake") is True

    def test_run_ducklake_build_bucket_missing_exits(self):
        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch(
                "scripts.build_lambda.build_ducklake_function_package",
                side_effect=[_FakePath(), _FakePath(), _FakePath(), _FakePath(), _FakePath(), _FakePath(), _FakePath()],
            ),
            patch("scripts.build_lambda.build_ducklake_deps_layer", return_value=_FakePath()),
            patch("scripts.build_lambda.build_ducklake_extensions_layer", return_value=_FakePath()),
            patch("scripts.build_lambda.build_pgclient_layer", return_value=_FakePath()),
            patch("scripts.build_lambda.assert_within_size_limit"),
            patch("scripts.build_lambda.validate_bucket_exists", return_value=False),
        ):
            with pytest.raises(SystemExit):
                bl._run_ducklake_build(_args(skip_upload=False))

    def test_run_ducklake_build_deploy_only_skips_build_and_upload(self):
        """--ducklake-deploy-only (hotfix follow-up to the 2026-07-24 ops_decisions incident):
        no build/upload phase, only update_lambda_functions against existing S3 artifacts."""
        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch("scripts.build_lambda.build_ducklake_function_package") as mock_build_fn,
            patch("scripts.build_lambda.upload_to_s3") as mock_upload,
            patch("scripts.build_lambda.update_lambda_functions") as mock_update,
        ):
            bl._run_ducklake_build(_args(deploy=True, ducklake_deploy_only=True))
        mock_build_fn.assert_not_called()
        mock_upload.assert_not_called()
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs.get("only_ducklake") is True

    def test_run_ducklake_build_deploy_only_requires_deploy_flag(self):
        with patch("scripts.build_lambda.resolve_bucket", return_value="bk"):
            with pytest.raises(SystemExit):
                bl._run_ducklake_build(_args(deploy=False, ducklake_deploy_only=True))

    def test_run_prod_build_skip_upload_does_not_upload(self):
        with (
            patch("scripts.build_lambda.build_app_package", return_value=_FakePath(name="dp.zip")),
            patch("scripts.build_lambda.assert_within_size_limit") as mock_assert,
            patch("scripts.build_lambda.upload_to_s3") as mock_upload,
        ):
            bl._run_prod_build(_args(skip_upload=True))
        assert mock_assert.call_count == 1
        assert mock_upload.call_count == 0

    def test_run_prod_build_bucket_missing_exits(self):
        with (
            patch("scripts.build_lambda.build_app_package", return_value=_FakePath(name="dp.zip")),
            patch("scripts.build_lambda.assert_within_size_limit"),
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch("scripts.build_lambda.validate_bucket_exists", return_value=False),
        ):
            with pytest.raises(SystemExit):
                bl._run_prod_build(_args(skip_upload=False))

    def test_run_prod_build_upload_and_deploy(self):
        with (
            patch("scripts.build_lambda.build_app_package", return_value=_FakePath(name="dp.zip")),
            patch("scripts.build_lambda.assert_within_size_limit"),
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch("scripts.build_lambda.validate_bucket_exists", return_value=True),
            patch("scripts.build_lambda.upload_to_s3") as mock_upload,
            patch("scripts.build_lambda.update_lambda_functions") as mock_update,
        ):
            bl._run_prod_build(_args(skip_upload=False, deploy=True))
        assert mock_upload.call_count == 1
        mock_update.assert_called_once()


class TestMainDispatch:
    def test_main_ducklake_only_routes(self):
        with (
            patch("scripts.build_lambda._run_ducklake_build") as mock_dl,
            patch("scripts.build_lambda._run_prod_build") as mock_prod,
            patch("sys.argv", ["build_lambda", "--ducklake-only", "--skip-upload"]),
        ):
            bl.main()
        mock_dl.assert_called_once()
        mock_prod.assert_not_called()

    def test_main_default_routes_prod(self):
        with (
            patch("scripts.build_lambda._run_ducklake_build") as mock_dl,
            patch("scripts.build_lambda._run_prod_build") as mock_prod,
            patch("sys.argv", ["build_lambda", "--skip-upload"]),
        ):
            bl.main()
        mock_prod.assert_called_once()
        mock_dl.assert_not_called()

    def test_main_list_bundle_short_circuits(self):
        with (
            patch("scripts.build_lambda.list_bundle") as mock_lb,
            patch("scripts.build_lambda._run_prod_build") as mock_prod,
            patch("sys.argv", ["build_lambda", "--list-bundle", "data-pipeline"]),
        ):
            bl.main()
        mock_lb.assert_called_once_with("data-pipeline")
        mock_prod.assert_not_called()

    def test_main_ducklake_publish_canary_layers_routes(self):
        """--ducklake-publish-canary-layers short-circuits to publish_canary_layers() then returns."""
        with (
            patch("scripts.build_lambda.publish_canary_layers") as mock_pub,
            patch("scripts.build_lambda.resolve_bucket", return_value="test-bucket"),
            patch("scripts.build_lambda._run_ducklake_build") as mock_dl,
            patch(
                "sys.argv",
                ["build_lambda", "--ducklake-publish-canary-layers", "--profile", "agent_platform"],
            ),
        ):
            bl.main()
        mock_pub.assert_called_once()
        mock_dl.assert_not_called()


def test_facade_reexports_complete():
    """Every public symbol and every test-patched private symbol re-exported by the facade
    resolves via scripts.build_lambda and is the same object as its defining module -- except the
    two mutable-global names (_BUILD_CONTRACT_PATH, _BUILD_CONTRACT_REGISTRY), which are
    snapshotted at import time by a plain `from X import Y` and legitimately diverge from their
    defining module's live value the first time any accessor call or test reassigns it there (see
    TestBuildLambdaContract in tests/test_build_lambda_config.py) -- presence is still asserted.
    """
    import scripts.build_lambda_config as bl_config
    import scripts.build_lambda_deploy as bd
    import scripts.build_lambda_packaging as bm

    identity_checked = {
        "_DUCKLAKE_CATALOG_DR_FUNCTION": bl_config,
        "_DUCKLAKE_FUNCTION_ZIP_KEYS": bl_config,
        "_DUCKLAKE_MAINTENANCE_FUNCTION": bl_config,
        "_DUCKLAKE_READER_FUNCTION": bl_config,
        "_DUCKLAKE_WRITER_FUNCTION": bl_config,
        "_LAMBDA_FUNCTION_NAMES": bl_config,
        "_LAMBDA_SCRIPTS": bl_config,
        "_aws_profile_args": bl_config,
        "_build_ducklake_function_zip_keys": bl_config,
        "_build_ducklake_layer_names": bl_config,
        "_build_prod_function_names": bl_config,
        "_build_size_limit_bytes": bl_config,
        "DUCKLAKE_LAYER_NAMES": bl_config,
        "LAMBDA_FILE_PATTERNS": bl_config,
        "LAMBDA_SIZE_LIMIT_BYTES": bl_config,
        "LAMBDA_SIZE_WARN_BYTES": bl_config,
        "PINNED_DUCKDB_VERSION": bl_config,
        "PINNED_PG_MAJOR": bl_config,
        "OUTPUT_DIR": bm,
        "_deterministic_zipinfo": bm,
        "_fetch_extension_bytes": bm,
        "_try_s3_extension": bm,
        "_try_s3_pgclient": bm,
        "_zip_staged_dir": bm,
        "assert_within_size_limit": bm,
        "build_app_package": bm,
        "build_ducklake_deps_layer": bm,
        "build_ducklake_extensions_layer": bm,
        "build_ducklake_function_package": bm,
        "build_pgclient_layer": bm,
        "list_bundle": bm,
        "publish_canary_layers": bd,
        "resolve_bucket": bd,
        "update_lambda_functions": bd,
        "upload_to_s3": bd,
        "validate_bucket_exists": bd,
    }
    for name, owner in identity_checked.items():
        assert hasattr(bl, name), f"{name} missing from scripts.build_lambda facade"
        assert getattr(bl, name) is getattr(owner, name), f"{name} resolves to a different object than its defining module"

    # Mutable-global names: presence only (see docstring for why identity is not asserted here).
    for name in ("_BUILD_CONTRACT_PATH", "_BUILD_CONTRACT_REGISTRY"):
        assert hasattr(bl, name), f"{name} missing from scripts.build_lambda facade"

    # Locally-defined (not re-exports): confirm they still resolve on the facade too.
    for name in ("build_parser", "main", "_run_prod_build", "_run_ducklake_build"):
        assert hasattr(bl, name), f"{name} missing from scripts.build_lambda"
