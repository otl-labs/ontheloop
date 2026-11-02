"""Unit tests for scripts/smoke_test_inference_credentials.py.

Covers both providers (deepseek, anthropic), the success path, AccessDeniedException,
ResourceNotFoundException, and the empty-response failure path to satisfy 100% coverage.
All boto3 and litellm calls are mocked; no live network calls are made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts.smoke_test_inference_credentials import _parse_args, main, run


def _make_sm_response(secret_string: str) -> dict:
    return {"Parameter": {"Value": secret_string}}


def _make_litellm_result(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class TestParseArgs:
    def test_provider_deepseek(self) -> None:
        with patch("sys.argv", ["smoke_test_inference_credentials", "--provider", "deepseek"]):
            args = _parse_args()
        assert args.provider == "deepseek"
        assert args.model is None
        assert args.envelope_id is None
        assert args.region == "eu-west-2"

    def test_provider_anthropic_with_overrides(self) -> None:
        argv = [
            "smoke_test_inference_credentials",
            "--provider",
            "anthropic",
            "--model",
            "anthropic/claude-opus-4",
            "--parameter-name",
            "my-secret",
            "--region",
            "us-east-1",
        ]
        with patch("sys.argv", argv):
            args = _parse_args()
        assert args.provider == "anthropic"
        assert args.model == "anthropic/claude-opus-4"
        assert args.envelope_id == "my-secret"
        assert args.region == "us-east-1"


class TestMain:
    def test_main_calls_run_and_exits(self) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-test")
        with (
            patch("sys.argv", ["smoke_test_inference_credentials", "--provider", "deepseek"]),
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("OK")
            main()
        assert exc_info.value.code == 0


class TestRunDeepseekSuccess:
    def test_success_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-deepseek-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value="agent_platform"),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("OK")

            result = run("deepseek")

        assert result == 0
        captured = capsys.readouterr()
        assert "PASS [deepseek]" in captured.out
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args
        assert call_kwargs.kwargs["model"] == "deepseek/deepseek-chat"
        assert call_kwargs.kwargs["api_key"] == "sk-deepseek-test"  # pragma: allowlist secret

    def test_model_override(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-deepseek-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("OK")

            result = run("deepseek", model="deepseek/deepseek-reasoner")

        assert result == 0
        assert mock_completion.call_args.kwargs["model"] == "deepseek/deepseek-reasoner"

    def test_parameter_name_override(self) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-deepseek-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("OK")

            result = run("deepseek", envelope_id="my-custom-secret")

        assert result == 0
        sm_client.get_parameter.assert_called_once_with(
            Name="my-custom-secret", WithDecryption=True
        )  # pragma: allowlist secret


class TestRunAnthropicSuccess:
    def test_success_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-ant-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value="agent_platform"),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("OK")

            result = run("anthropic")

        assert result == 0
        captured = capsys.readouterr()
        assert "PASS [anthropic]" in captured.out
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["model"] == "anthropic/claude-haiku-4-5"
        assert call_kwargs["api_key"] == "sk-ant-test"  # pragma: allowlist secret


class TestRunAccessDenied:
    def test_access_denied_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        from botocore.exceptions import ClientError

        sm_client = MagicMock()
        sm_client.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "GetParameter",
        )
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value="agent_platform"),
        ):
            mock_session.return_value.client.return_value = sm_client
            result = run("deepseek")

        assert result == 1
        captured = capsys.readouterr()
        assert "FAIL [deepseek]" in captured.err
        assert "InferenceCredentialsParameterRead" in captured.err

    def test_resource_not_found_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        from botocore.exceptions import ClientError

        sm_client = MagicMock()
        sm_client.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": "Not found"}},
            "GetParameter",
        )
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            result = run("anthropic")

        assert result == 1
        captured = capsys.readouterr()
        assert "ParameterNotFound" in captured.err or "not found" in captured.err.lower()


class TestRunEmptySecretValue:
    def test_empty_secret_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            result = run("deepseek")

        assert result == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower()

    def test_missing_secret_string_key_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = {}
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            result = run("anthropic")

        assert result == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower()


class TestRunLiteLLMFailures:
    def test_litellm_auth_error_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("bad-key")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.side_effect = Exception("AuthenticationError: invalid api_key")

            result = run("deepseek")

        assert result == 1
        captured = capsys.readouterr()
        assert "FAIL [deepseek]" in captured.err
        assert "Authentication" in captured.err

    def test_litellm_generic_error_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("some-key")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.side_effect = Exception("Connection timeout")

            result = run("anthropic")

        assert result == 1
        captured = capsys.readouterr()
        assert "FAIL [anthropic]" in captured.err

    def test_empty_completion_content_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("")

            result = run("deepseek")

        assert result == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower()

    def test_whitespace_only_content_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = _make_litellm_result("   ")

            result = run("anthropic")

        assert result == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower()

    def test_none_choices_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        sm_client = MagicMock()
        sm_client.get_parameter.return_value = _make_sm_response("sk-test")
        with (
            patch("scripts.smoke_test_inference_credentials.boto3.Session") as mock_session,
            patch("scripts.smoke_test_inference_credentials.litellm.completion") as mock_completion,
            patch("scripts.smoke_test_inference_credentials.resolve_aws_profile", return_value=None),
        ):
            mock_session.return_value.client.return_value = sm_client
            mock_completion.return_value = SimpleNamespace(choices=[])

            result = run("deepseek")

        assert result == 1
        captured = capsys.readouterr()
        assert "empty" in captured.err.lower()
