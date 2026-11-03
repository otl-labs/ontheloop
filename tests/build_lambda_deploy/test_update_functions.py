"""Tests for scripts/build_lambda_deploy.py -- update-path concern (VERBATIM split from
tests/test_build_lambda_deploy.py, rec-2709 Wave 12).
"""

import json
import types
from unittest.mock import MagicMock, patch

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
    _LAMBDA_FUNCTION_NAMES,
)

pytestmark = pytest.mark.unit


class TestUpdateLambdaFunctions:
    """Tests for the update_lambda_functions deploy path (prod class: dispatcher,
    findings-processor).

    T2.43: the prod (only_ducklake=False) path now ALSO writes a deploy-records/prod/<fn>.json
    record per function, mirroring the T2.38 ducklake path -- update-function-code THEN (on
    success) the deploy-record S3 write: 2 subprocess.run calls x 2 functions = 4 total. See
    TestUpdateLambdaFunctionsDucklakeOnly below for the ducklake-channel equivalent.
    """

    def _update_response(self, code_sha256: str = "deadbeef") -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"CodeSha256": code_sha256}), stderr="")

    def _write_response(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def _side_effects(self, n: int = len(_LAMBDA_FUNCTION_NAMES)) -> list:
        effects: list = []
        for _ in range(n):
            effects.append(self._update_response())
            effects.append(self._write_response())
        return effects

    def test_update_calls_all_functions(self):
        """Every prod Lambda function receives an update-function-code call (+ a deploy-record write each)."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = self._side_effects()

            bd.update_lambda_functions("my-bucket", "my-profile", "eu-west-2")

            assert mock_run.call_count == len(_LAMBDA_FUNCTION_NAMES) * 2

    def test_update_function_names(self):
        """Each update-function-code call (even index) targets the correct Lambda function name."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = self._side_effects()

            bd.update_lambda_functions("my-bucket", "my-profile", "eu-west-2")

            for idx, fn_name in enumerate(_LAMBDA_FUNCTION_NAMES):
                cmd = mock_run.call_args_list[idx * 2][0][0]
                fn_idx = cmd.index("--function-name")
                assert cmd[fn_idx + 1] == fn_name

    def test_update_pipeline_functions_use_pipeline_zip(self):
        """Dispatcher and findings-processor use data-pipeline.zip."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = self._side_effects()

            bd.update_lambda_functions("deploy-bucket", "prof", "eu-west-2")

            for idx in range(len(_LAMBDA_FUNCTION_NAMES)):
                cmd = mock_run.call_args_list[idx * 2][0][0]
                bucket_idx = cmd.index("--s3-bucket")
                assert cmd[bucket_idx + 1] == "deploy-bucket"
                key_idx = cmd.index("--s3-key")
                assert cmd[key_idx + 1] == "lambda-packages/data-pipeline.zip"

    def test_update_region_and_profile(self):
        """Region and profile are forwarded to the AWS CLI on every call (update + deploy-record write)."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = self._side_effects()

            bd.update_lambda_functions("b", "test-profile", "eu-west-2")

            for c in mock_run.call_args_list:
                cmd = c[0][0]
                region_idx = cmd.index("--region")
                assert cmd[region_idx + 1] == "eu-west-2"
                profile_idx = cmd.index("--profile")
                assert cmd[profile_idx + 1] == "test-profile"

    def test_update_subprocess_kwargs(self):
        """Windows-safe subprocess kwargs are set on every call."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = self._side_effects()

            bd.update_lambda_functions("b", "p", "eu-west-2")

            for c in mock_run.call_args_list:
                kwargs = c[1]
                assert kwargs["text"] is True
                assert kwargs["encoding"] == "utf-8"
                assert kwargs["errors"] == "replace"
                assert kwargs["capture_output"] is True

    def test_update_exits_on_failure(self):
        """sys.exit is called when a Lambda update fails."""
        fail_mock = MagicMock()
        fail_mock.returncode = 1
        fail_mock.stderr = "AccessDenied"

        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = [fail_mock]

            with pytest.raises(SystemExit) as exc_info:
                bd.update_lambda_functions("b", "p", "eu-west-2")

            assert exc_info.value.code == 1

    def test_lambda_function_names_constant(self):
        """All expected Lambda function names are present."""
        assert "agent-platform-scheduled-agent-dispatcher" in _LAMBDA_FUNCTION_NAMES
        assert "agent-platform-findings-processor" in _LAMBDA_FUNCTION_NAMES

    def test_prod_path_writes_deploy_records_to_prod_prefix(self):
        """T2.43: the prod path (only_ducklake=False, default) now writes deploy-records/prod/<fn>.json
        for each prod function -- the OLD invariant (no records) is intentionally inverted
        (rec-2157/rec-2164 governed code-deploy channel)."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = self._side_effects()
            bd.update_lambda_functions("b", "p", "eu-west-2")

        assert mock_run.call_count == len(_LAMBDA_FUNCTION_NAMES) * 2
        write_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][:3] == ["aws", "s3", "cp"]]
        assert len(write_calls) == len(_LAMBDA_FUNCTION_NAMES)
        written_keys = {cmd[4] for cmd in write_calls}
        assert written_keys == {f"s3://b/deploy-records/prod/{fn}.json" for fn in _LAMBDA_FUNCTION_NAMES}


class TestUpdateLambdaFunctionsDucklakeOnly:
    # only_ducklake=True call sequence per function (T2.38 c3): update-function-code, THEN
    # (on success) the deploy-record S3 write -- 2 subprocess.run calls x 8 functions = 16 total
    # (T2.18 c9 split added the maintenance-smoke function). See docs/PROJECT_CONTEXT.md
    # "postflight.py function mock exhaustion" gotcha: a new subprocess.run call added inside
    # update_lambda_functions' only_ducklake branch means every side_effect list feeding it here
    # must grow to match, or the extra call silently StopIterations.

    def _update_response(self, code_sha256: str = "deadbeef"):
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"CodeSha256": code_sha256}), stderr="")

    def _write_response(self):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_only_ducklake_updates_every_ducklake_function(self):
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = [
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
            ]
            bd.update_lambda_functions("b", "p", "eu-west-2", only_ducklake=True)
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

    def test_only_ducklake_update_call_requests_json_output(self):
        """The update-function-code call must request --output json (CodeSha256 capture, c3)."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.side_effect = [
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
                self._update_response(),
                self._write_response(),
            ]
            bd.update_lambda_functions("b", "p", "eu-west-2", only_ducklake=True)
        for idx in range(0, 16, 2):
            cmd = mock_run.call_args_list[idx][0][0]
            assert "--output" in cmd
            assert cmd[cmd.index("--output") + 1] == "json"

    def test_non_ducklake_path_writes_deploy_records_to_prod_not_ducklake(self):
        """T2.43 REWRITE (was test_non_ducklake_path_does_not_write_deploy_records): the OLD
        invariant (prod path writes no records) is intentionally inverted now that the prod class
        has its own governed deploy channel. The prod path writes deploy-records/prod/ -- it must
        still never write deploy-records/ducklake/. See
        TestUpdateLambdaFunctions.test_prod_path_writes_deploy_records_to_prod_prefix for full
        coverage of the prod-path write behaviour; this test asserts the channel separation."""
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            effects: list = []
            for idx in range(len(_LAMBDA_FUNCTION_NAMES)):
                effects.append(types.SimpleNamespace(returncode=0, stdout=json.dumps({"CodeSha256": f"s{idx}"}), stderr=""))
                effects.append(types.SimpleNamespace(returncode=0, stdout="", stderr=""))
            mock_run.side_effect = effects
            bd.update_lambda_functions("b", "p", "eu-west-2")
        assert mock_run.call_count == len(_LAMBDA_FUNCTION_NAMES) * 2
        write_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][:3] == ["aws", "s3", "cp"]]
        assert len(write_calls) == len(_LAMBDA_FUNCTION_NAMES)
        for cmd in write_calls:
            assert cmd[4].startswith("s3://b/deploy-records/prod/")
            assert "ducklake" not in cmd[4]
