"""Tests for update_lambda_functions(only_functions=...) and --ducklake-deploy-only (hotfix
follow-up to the 2026-07-24 ops_decisions incident): only_functions filters the deploy set, an
unknown name raises, only_functions with only_ducklake=False raises, omitting it preserves
all-five behaviour, and --ducklake-deploy-only performs no build/upload.
"""

import json
import types
from unittest.mock import patch

import pytest

import scripts.build_lambda_deploy as bd
from scripts.build_lambda_config import (
    _DUCKLAKE_CATALOG_DR_FUNCTION,
    _DUCKLAKE_MAINTENANCE_FUNCTION,
    _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,
    _DUCKLAKE_READER_FUNCTION,
    _DUCKLAKE_TELEMETRY_READER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_FUNCTION,
    _DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION,
    _DUCKLAKE_WRITER_FUNCTION,
)

pytestmark = pytest.mark.unit


def _update_response(code_sha256: str = "deadbeef") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=0, stdout=json.dumps({"CodeSha256": code_sha256}), stderr="")


def _write_response() -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _side_effects(n: int) -> list:
    effects: list = []
    for _ in range(n):
        effects.append(_update_response())
        effects.append(_write_response())
    return effects


class TestOnlyFunctionsFiltersSet:
    def test_subset_of_two_deploys_only_those_two(self):
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = _side_effects(2)
            bd.update_lambda_functions(
                "b",
                "p",
                "eu-west-2",
                only_ducklake=True,
                only_functions={_DUCKLAKE_MAINTENANCE_FUNCTION, _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION},
            )
        assert mock_run.call_count == 4  # 2 functions x (update + deploy-record write)
        targeted = []
        for idx in range(0, 4, 2):
            cmd = mock_run.call_args_list[idx][0][0]
            targeted.append(cmd[cmd.index("--function-name") + 1])
        assert set(targeted) == {_DUCKLAKE_MAINTENANCE_FUNCTION, _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION}

    def test_subset_of_three_deploys_only_those_three(self):
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = _side_effects(3)
            bd.update_lambda_functions(
                "b",
                "p",
                "eu-west-2",
                only_ducklake=True,
                only_functions={_DUCKLAKE_WRITER_FUNCTION, _DUCKLAKE_READER_FUNCTION, _DUCKLAKE_CATALOG_DR_FUNCTION},
            )
        assert mock_run.call_count == 6
        targeted = []
        for idx in range(0, 6, 2):
            cmd = mock_run.call_args_list[idx][0][0]
            targeted.append(cmd[cmd.index("--function-name") + 1])
        assert set(targeted) == {_DUCKLAKE_WRITER_FUNCTION, _DUCKLAKE_READER_FUNCTION, _DUCKLAKE_CATALOG_DR_FUNCTION}

    def test_omitting_only_functions_deploys_every_function_unchanged(self):
        """A subset deploy that silently intersects to empty and reports success is exactly the
        false-green this feature exists to prevent -- proven by the inverse: omitting the filter
        must still deploy every function, byte-identical to prior behaviour."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = _side_effects(8)
            bd.update_lambda_functions("b", "p", "eu-west-2", only_ducklake=True, only_functions=None)
        assert mock_run.call_count == 16
        targeted = []
        for idx in range(0, 16, 2):
            cmd = mock_run.call_args_list[idx][0][0]
            targeted.append(cmd[cmd.index("--function-name") + 1])
        assert set(targeted) == {
            _DUCKLAKE_WRITER_FUNCTION,
            _DUCKLAKE_READER_FUNCTION,
            _DUCKLAKE_MAINTENANCE_FUNCTION,
            _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,
            _DUCKLAKE_CATALOG_DR_FUNCTION,
            _DUCKLAKE_TELEMETRY_WRITER_FUNCTION,
            _DUCKLAKE_TELEMETRY_WRITER_SMOKE_FUNCTION,
            _DUCKLAKE_TELEMETRY_READER_FUNCTION,
        }


class TestOnlyFunctionsRaisesOnMisuse:
    def test_unrecognised_function_name_raises(self):
        """A subset deploy that silently intersects to empty and reports success would be a
        false-green -- an unrecognised name must raise, never silently deploy nothing."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="unrecognised"):
                bd.update_lambda_functions(
                    "b", "p", "eu-west-2", only_ducklake=True, only_functions={"agent-platform-not-a-real-function"}
                )
        mock_run.assert_not_called()

    def test_only_functions_with_only_ducklake_false_raises(self):
        """The prod branch builds a different map; subsetting it is out of scope."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="only_ducklake=True"):
                bd.update_lambda_functions(
                    "b", "p", "eu-west-2", only_ducklake=False, only_functions={_DUCKLAKE_WRITER_FUNCTION}
                )
        mock_run.assert_not_called()


class TestDucklakeDeployOnlyCli:
    """scripts.build_lambda._run_ducklake_build's --ducklake-deploy-only leg: no build/upload."""

    def _args(self, **kw):
        import argparse

        ns = argparse.Namespace(
            skip_upload=True,
            bucket="",
            profile="agent_platform",
            region="eu-west-2",
            deploy=True,
            ducklake_only=True,
            ducklake_functions=None,
            ducklake_deploy_only=True,
            list_bundle=None,
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_deploy_only_skips_build_and_upload(self):
        import scripts.build_lambda as bl

        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch("scripts.build_lambda.build_ducklake_function_package") as mock_build_fn,
            patch("scripts.build_lambda.build_ducklake_deps_layer") as mock_build_deps,
            patch("scripts.build_lambda.build_ducklake_extensions_layer") as mock_build_ext,
            patch("scripts.build_lambda.build_pgclient_layer") as mock_build_pg,
            patch("scripts.build_lambda.upload_to_s3") as mock_upload,
            patch("scripts.build_lambda.update_lambda_functions") as mock_update,
        ):
            bl._run_ducklake_build(self._args())

        mock_build_fn.assert_not_called()
        mock_build_deps.assert_not_called()
        mock_build_ext.assert_not_called()
        mock_build_pg.assert_not_called()
        mock_upload.assert_not_called()
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs.get("only_ducklake") is True

    def test_deploy_only_threads_ducklake_functions_to_only_functions(self):
        import scripts.build_lambda as bl

        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch("scripts.build_lambda.update_lambda_functions") as mock_update,
        ):
            bl._run_ducklake_build(
                self._args(
                    ducklake_functions=f"{_DUCKLAKE_WRITER_FUNCTION},{_DUCKLAKE_READER_FUNCTION}",
                )
            )

        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs.get("only_functions") == {
            _DUCKLAKE_WRITER_FUNCTION,
            _DUCKLAKE_READER_FUNCTION,
        }

    def test_deploy_only_requires_deploy_flag(self):
        import scripts.build_lambda as bl

        with pytest.raises(SystemExit):
            bl._run_ducklake_build(self._args(deploy=False))

    def test_ducklake_functions_without_deploy_only_still_builds_but_narrows_deploy_set(self):
        """--ducklake-functions alone (no --ducklake-deploy-only) still builds+uploads everything
        (deploy-maintenance's leg) but narrows WHICH functions get code-updated."""
        import scripts.build_lambda as bl

        class _FakePath:
            def __init__(self, name="x.zip", size=100):
                self.name = name
                self._size = size

            def stat(self):
                return types.SimpleNamespace(st_size=self._size)

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
            bl._run_ducklake_build(
                self._args(
                    skip_upload=False,
                    ducklake_deploy_only=False,
                    ducklake_functions=f"{_DUCKLAKE_MAINTENANCE_FUNCTION},{_DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION}",
                )
            )

        assert mock_upload.call_count == 10  # all 10 artifacts still built+uploaded
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs.get("only_functions") == {
            _DUCKLAKE_MAINTENANCE_FUNCTION,
            _DUCKLAKE_MAINTENANCE_SMOKE_FUNCTION,
        }
