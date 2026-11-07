"""Tests for scripts/build_lambda_deploy.py -- deploy-record write/read concern (VERBATIM split
from tests/test_build_lambda_deploy.py, rec-2709 Wave 12).
"""

import io
import json
import types
from unittest.mock import MagicMock, patch

# boto3 is imported at MODULE scope even though it is referenced only via read_deploy_record's
# lazy fallback / patch("boto3.client") strings and local `from botocore.exceptions import
# ClientError` imports below. This makes the file's heavy-dep requirement visible to the fast
# tier's cheap `--collect-only` pass so pr-validate defers it PROACTIVELY to the full post-merge
# tier, instead of catching it REACTIVELY -- mirrors tests/test_convergence_health.py's identical
# marker (boto3 is deliberately excluded from requirements-fast.txt; the full tier runs this file).
import boto3  # noqa: F401
import pytest

import scripts.build_lambda_deploy as bd
from scripts.build_lambda_config import (
    _DUCKLAKE_CATALOG_DR_FUNCTION,
    _DUCKLAKE_MAINTENANCE_FUNCTION,
    _DUCKLAKE_READER_FUNCTION,
    _DUCKLAKE_WRITER_FUNCTION,
)

pytestmark = pytest.mark.unit


class TestWriteDucklakeDeployRecord:
    """Unit tests for _write_ducklake_deploy_record (T2.38 c3: CodeSha256 capture + record write)."""

    def _stdout(self, code_sha256: str = "sha-abc123") -> str:
        return json.dumps({"CodeSha256": code_sha256, "FunctionName": "whatever"})

    def test_writes_record_with_expected_schema(self, monkeypatch):
        monkeypatch.delenv("LAMBDA_ARTIFACT_SHA", raising=False)
        monkeypatch.setenv("GITHUB_SHA", "deadbeefcafe")
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(
                _DUCKLAKE_WRITER_FUNCTION, self._stdout("sha-writer"), "my-bucket", "p", "eu-west-2"
            )

        assert captured["cmd"][:3] == ["aws", "s3", "cp"]
        assert captured["cmd"][3] == "-"
        assert captured["cmd"][4] == f"s3://my-bucket/deploy-records/ducklake/{_DUCKLAKE_WRITER_FUNCTION}.json"
        record = json.loads(captured["input"])
        assert record["function"] == _DUCKLAKE_WRITER_FUNCTION
        assert record["code_sha256"] == "sha-writer"
        assert record["source_git_sha"] == "deadbeefcafe"
        assert "deployed_at" in record and record["deployed_at"]

    def test_record_names_lambda_artifact_sha_over_github_sha(self, monkeypatch):
        monkeypatch.setenv("LAMBDA_ARTIFACT_SHA", "gated-sha")
        monkeypatch.setenv("GITHUB_SHA", "main-head-sha")
        captured = {}

        def fake_run(cmd, **kw):
            captured["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(_DUCKLAKE_WRITER_FUNCTION, self._stdout(), "b", "p", "eu-west-2")
        assert json.loads(captured["input"])["source_git_sha"] == "gated-sha"

    def test_key_is_per_function(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["dest"] = cmd[4]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(_DUCKLAKE_READER_FUNCTION, self._stdout(), "bucket", "p", "eu-west-2")
        assert captured["dest"] == f"s3://bucket/deploy-records/ducklake/{_DUCKLAKE_READER_FUNCTION}.json"

    def test_source_git_sha_none_safe_when_github_sha_unset(self, monkeypatch):
        """Local break-glass path (bin/venv-python -m scripts.build_lambda --ducklake-only
        --deploy) has no GITHUB_SHA -- the record must carry source_git_sha=null, not KeyError."""
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        monkeypatch.delenv("LAMBDA_ARTIFACT_SHA", raising=False)
        captured = {}

        def fake_run(cmd, **kw):
            captured["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(_DUCKLAKE_MAINTENANCE_FUNCTION, self._stdout(), "bucket", "p", "eu-west-2")
        record = json.loads(captured["input"])
        assert record["source_git_sha"] is None

    def test_content_type_json(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(_DUCKLAKE_CATALOG_DR_FUNCTION, self._stdout(), "bucket", "p", "eu-west-2")
        cmd = captured["cmd"]
        assert "--content-type" in cmd
        assert cmd[cmd.index("--content-type") + 1] == "application/json"

    def test_exits_on_unparseable_json_response(self):
        with patch("scripts.build_lambda.subprocess.run") as mock_run:
            with pytest.raises(SystemExit) as exc_info:
                bd._write_ducklake_deploy_record(_DUCKLAKE_WRITER_FUNCTION, "not-json-at-all", "bucket", "p", "eu-west-2")
            assert exc_info.value.code == 1
        mock_run.assert_not_called()

    def test_exits_when_codesha256_key_missing(self):
        with pytest.raises(SystemExit) as exc_info:
            bd._write_ducklake_deploy_record(
                _DUCKLAKE_WRITER_FUNCTION, json.dumps({"FunctionName": "x"}), "bucket", "p", "eu-west-2"
            )
        assert exc_info.value.code == 1

    def test_exits_on_s3_write_failure(self):
        fail = types.SimpleNamespace(returncode=1, stdout="", stderr="AccessDenied")
        with patch("scripts.build_lambda.subprocess.run", return_value=fail):
            with pytest.raises(SystemExit) as exc_info:
                bd._write_ducklake_deploy_record(_DUCKLAKE_WRITER_FUNCTION, self._stdout(), "bucket", "p", "eu-west-2")
            assert exc_info.value.code == 1

    def test_profile_and_region_forwarded(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(
                _DUCKLAKE_WRITER_FUNCTION, self._stdout(), "bucket", "agent_platform", "us-west-2"
            )
        cmd = captured["cmd"]
        assert "--region" in cmd and cmd[cmd.index("--region") + 1] == "us-west-2"
        assert "--profile" in cmd and cmd[cmd.index("--profile") + 1] == "agent_platform"

    def test_profileless_omits_profile_flag(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(_DUCKLAKE_WRITER_FUNCTION, self._stdout(), "bucket", "", "eu-west-2")
        assert "--profile" not in captured["cmd"]

    def test_default_channel_is_ducklake(self):
        """Omitting channel writes deploy-records/ducklake/ (T2.38 original caller, unchanged)."""
        captured = {}

        def fake_run(cmd, **kw):
            captured["dest"] = cmd[4]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(_DUCKLAKE_WRITER_FUNCTION, self._stdout(), "bucket", "p", "eu-west-2")
        assert captured["dest"] == f"s3://bucket/deploy-records/ducklake/{_DUCKLAKE_WRITER_FUNCTION}.json"

    def test_prod_channel_writes_to_prod_prefix(self):
        """T2.43: channel='prod' writes deploy-records/prod/<function>.json instead of ducklake/."""
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(
                "agent-platform-scheduled-agent-dispatcher",
                self._stdout("sha-prod"),
                "my-bucket",
                "p",
                "eu-west-2",
                channel="prod",
            )
        assert captured["cmd"][4] == "s3://my-bucket/deploy-records/prod/agent-platform-scheduled-agent-dispatcher.json"
        record = json.loads(captured["input"])
        assert record["function"] == "agent-platform-scheduled-agent-dispatcher"
        assert record["code_sha256"] == "sha-prod"


class TestReadDeployRecord:
    """Unit tests for read_deploy_record (T2.38 c3 read-back helper; injected S3, no live AWS)."""

    def test_returns_parsed_json(self):
        payload = json.dumps(
            {"function": _DUCKLAKE_WRITER_FUNCTION, "code_sha256": "abc123", "source_git_sha": "sha1", "deployed_at": "x"}
        ).encode()
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(payload)}

        result = bd.read_deploy_record(_DUCKLAKE_WRITER_FUNCTION, s3_client=mock_s3)

        assert result == {
            "function": _DUCKLAKE_WRITER_FUNCTION,
            "code_sha256": "abc123",
            "source_git_sha": "sha1",
            "deployed_at": "x",
        }
        mock_s3.get_object.assert_called_once_with(
            Bucket="agent-platform-data-lake",
            Key=f"deploy-records/ducklake/{_DUCKLAKE_WRITER_FUNCTION}.json",
        )

    def test_custom_bucket_honoured(self):
        payload = json.dumps({"code_sha256": "x"}).encode()
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(payload)}
        bd.read_deploy_record(_DUCKLAKE_READER_FUNCTION, s3_client=mock_s3, bucket="other-bucket")
        mock_s3.get_object.assert_called_once_with(
            Bucket="other-bucket",
            Key=f"deploy-records/ducklake/{_DUCKLAKE_READER_FUNCTION}.json",
        )

    def test_prod_channel_reads_prod_prefix(self):
        """T2.43: channel='prod' reads deploy-records/prod/<function>.json instead of ducklake/."""
        payload = json.dumps({"function": "agent-platform-findings-processor", "code_sha256": "sha-p"}).encode()
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(payload)}
        result = bd.read_deploy_record(
            "agent-platform-findings-processor", s3_client=mock_s3, bucket="my-bucket", channel="prod"
        )
        assert result["code_sha256"] == "sha-p"
        mock_s3.get_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="deploy-records/prod/agent-platform-findings-processor.json",
        )

    def test_returns_none_on_no_such_key(self):
        from botocore.exceptions import ClientError

        error = ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = error
        assert bd.read_deploy_record(_DUCKLAKE_WRITER_FUNCTION, s3_client=mock_s3) is None

    def test_reraises_non_nosuchkey_error(self):
        from botocore.exceptions import ClientError

        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = err
        with pytest.raises(ClientError):
            bd.read_deploy_record(_DUCKLAKE_WRITER_FUNCTION, s3_client=mock_s3)

    def test_lazy_creates_boto3_client_when_none(self):
        with patch("boto3.client") as mock_client:
            mock_client.return_value.get_object.side_effect = RuntimeError("NoSuchKey")
            bd.read_deploy_record(_DUCKLAKE_WRITER_FUNCTION)
        mock_client.assert_called_once_with("s3")

    def test_round_trip_write_then_read(self, monkeypatch):
        """The record _write_ducklake_deploy_record writes is exactly what read_deploy_record
        parses back -- proves schema symmetry end-to-end (minus the live S3 transport)."""
        monkeypatch.delenv("LAMBDA_ARTIFACT_SHA", raising=False)
        monkeypatch.setenv("GITHUB_SHA", "roundtrip-sha")
        written = {}

        def fake_run(cmd, **kw):
            written["input"] = kw.get("input")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("scripts.build_lambda.subprocess.run", side_effect=fake_run):
            bd._write_ducklake_deploy_record(
                _DUCKLAKE_MAINTENANCE_FUNCTION,
                json.dumps({"CodeSha256": "sha-m"}),
                "roundtrip-bucket",
                "p",
                "eu-west-2",
            )

        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(written["input"].encode())}
        record = bd.read_deploy_record(_DUCKLAKE_MAINTENANCE_FUNCTION, s3_client=mock_s3, bucket="roundtrip-bucket")

        assert record["function"] == _DUCKLAKE_MAINTENANCE_FUNCTION
        assert record["code_sha256"] == "sha-m"
        assert record["source_git_sha"] == "roundtrip-sha"
        assert "deployed_at" in record
