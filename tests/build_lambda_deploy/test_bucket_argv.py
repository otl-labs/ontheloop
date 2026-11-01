"""Tests for scripts/build_lambda_deploy.py -- bucket-existence + resolve-bucket + profileless-argv
concern (VERBATIM split from tests/test_build_lambda_deploy.py, rec-2709 Wave 12).
"""

import json
import types
from unittest.mock import patch

import pytest

import scripts.build_lambda_deploy as bd

pytestmark = pytest.mark.unit


def test_validate_bucket_exists_success():
    """Test that validate_bucket_exists returns True when bucket exists."""
    with patch("scripts.build_lambda.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        result = bd.validate_bucket_exists("test-bucket", "test-profile", "eu-west-2")

        assert result is True
        mock_run.assert_called_once()


def test_validate_bucket_exists_failure():
    """Test that validate_bucket_exists returns False when bucket doesn't exist."""
    with patch("scripts.build_lambda.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 254

        result = bd.validate_bucket_exists("nonexistent-bucket", "test-profile", "eu-west-2")

        assert result is False
        mock_run.assert_called_once()


def test_validate_bucket_exists_call_args():
    """Test that validate_bucket_exists calls aws s3api head-bucket with correct arguments."""
    with patch("scripts.build_lambda.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        bd.validate_bucket_exists("my-bucket", "my-profile", "us-west-2")

        call_args = mock_run.call_args[0][0]
        assert "aws" in call_args
        assert "s3api" in call_args
        assert "head-bucket" in call_args
        assert "--bucket" in call_args
        assert "my-bucket" in call_args
        assert "--profile" in call_args
        assert "my-profile" in call_args
        assert "--region" in call_args
        assert "us-west-2" in call_args


class TestResolveBucket:
    def test_terraform_output_used(self):
        with patch(
            "scripts.build_lambda.subprocess.run", return_value=types.SimpleNamespace(returncode=0, stdout="tf-bucket\n")
        ):
            assert bd.resolve_bucket("p") == "tf-bucket"

    def test_empty_output_falls_back(self):
        with patch("scripts.build_lambda.subprocess.run", return_value=types.SimpleNamespace(returncode=0, stdout="")):
            assert bd.resolve_bucket("p") == "agent-platform-data-lake"

    def test_terraform_missing_falls_back(self):
        with patch("scripts.build_lambda.subprocess.run", side_effect=FileNotFoundError):
            assert bd.resolve_bucket("p") == "agent-platform-data-lake"


class TestProfilelessArgv:
    """aws CLI argv omits `--profile` when the resolved profile is empty (GitHub-hosted OIDC
    runners resolve creds from the environment and have no named profile) and includes it when
    non-empty (local/agent_platform dev). Unblocks `--ducklake-only` under CI (rec-2512).

    Only the deploy-owned functions' profileless tests live here; see the config/packaging test
    files for the rest of the original TestProfilelessArgv split.
    """

    def test_upload_to_s3_omits_profile_when_empty(self, tmp_path):
        zip_path = tmp_path / "x.zip"
        zip_path.write_bytes(b"z")
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            bd.upload_to_s3(zip_path, "bucket", "", "eu-west-2")
        argv = mock_run.call_args[0][0]
        assert "--profile" not in argv

    def test_upload_to_s3_includes_profile_when_set(self, tmp_path):
        zip_path = tmp_path / "x.zip"
        zip_path.write_bytes(b"z")
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            bd.upload_to_s3(zip_path, "bucket", "agent_platform", "eu-west-2")
        argv = mock_run.call_args[0][0]
        assert "--profile" in argv
        assert "agent_platform" in argv

    def test_validate_bucket_exists_omits_profile_when_empty(self):
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            bd.validate_bucket_exists("bucket", "", "eu-west-2")
        argv = mock_run.call_args[0][0]
        assert "--profile" not in argv

    def test_update_lambda_functions_omits_profile_when_empty(self):
        # Shared return_value covers BOTH the update-function-code call and (only_ducklake=True)
        # the deploy-record s3-cp write call, so it must carry a parseable CodeSha256 stdout.
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.return_value = types.SimpleNamespace(returncode=0, stdout=json.dumps({"CodeSha256": "sha"}), stderr="")
            bd.update_lambda_functions("bucket", "", "eu-west-2", only_ducklake=True)
        assert mock_run.call_args_list
        for call in mock_run.call_args_list:
            assert "--profile" not in call[0][0]

    def test_update_lambda_functions_includes_profile_when_set(self):
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            mock_run.return_value = types.SimpleNamespace(returncode=0, stdout=json.dumps({"CodeSha256": "sha"}), stderr="")
            bd.update_lambda_functions("bucket", "agent_platform", "eu-west-2", only_ducklake=True)
        assert mock_run.call_args_list
        for call in mock_run.call_args_list:
            assert "--profile" in call[0][0]
            assert "agent_platform" in call[0][0]
