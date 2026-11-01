"""Credential-free tests for scripts/aws_identity.py (mocked boto3, no network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from scripts import aws_identity
from scripts.ci.redaction_check import ACCOUNT_ID_SHAPE

FROZEN = ("AK", "SK", "TOK")
ROLE_ARN = "arn:aws:sts::acct:assumed-role/PlatformDev/session"


def _session_with_creds() -> MagicMock:
    session = MagicMock()
    frozen = MagicMock(access_key=FROZEN[0], secret_key=FROZEN[1], token=FROZEN[2])
    session.get_credentials.return_value.get_frozen_credentials.return_value = frozen
    return session


def test_identity_call_uses_probe_region() -> None:
    session = _session_with_creds()
    client = MagicMock()
    client.get_caller_identity.return_value = {"Arn": ROLE_ARN}
    with (
        patch.object(aws_identity.boto3, "Session", return_value=session) as session_cls,
        patch.object(aws_identity.boto3, "client", return_value=client) as client_fn,
    ):
        assert aws_identity.caller_arn("agent_platform") == ROLE_ARN
    assert session_cls.call_args.kwargs == {"profile_name": "agent_platform"}
    kwargs = client_fn.call_args.kwargs
    assert client_fn.call_args.args == ("sts",)
    assert kwargs["region_name"] == aws_identity.IDENTITY_PROBE_REGION
    assert kwargs["config"] is aws_identity._IDENTITY_CONFIG
    assert (kwargs["aws_access_key_id"], kwargs["aws_secret_access_key"], kwargs["aws_session_token"]) == FROZEN


def test_profile_none_uses_default_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ci-key")
    session = _session_with_creds()
    client = MagicMock()
    client.get_caller_identity.return_value = {"Arn": ROLE_ARN}
    with (
        patch.object(aws_identity.boto3, "Session", return_value=session) as session_cls,
        patch.object(aws_identity.boto3, "client", return_value=client),
    ):
        aws_identity.caller_arn(None)
    assert session_cls.call_args.kwargs == {"profile_name": None}


def test_no_credentials_raises() -> None:
    session = MagicMock()
    session.get_credentials.return_value = None
    with patch.object(aws_identity.boto3, "Session", return_value=session):
        with pytest.raises(RuntimeError):
            aws_identity.caller_arn("agent_platform")


def test_cli_masks_account_id(capsys: pytest.CaptureFixture[str]) -> None:
    account = str(10**11 + 7)
    full = f"arn:aws:sts::{account}:assumed-role/PlatformDev/session"
    with patch.object(aws_identity, "caller_arn", return_value=full) as fn:
        assert aws_identity.main(["caller-arn", "--profile", "agent_platform"]) == 0
    fn.assert_called_once_with("agent_platform")
    out = capsys.readouterr()
    assert out.out.strip() == "arn:aws:sts::[ACCOUNT_ID]:assumed-role/PlatformDev/session"
    assert "assumed-role/PlatformDev/" in out.out
    assert not ACCOUNT_ID_SHAPE.search(out.out)
    assert out.err == ""


def test_cli_client_error_prints_code_only(capsys: pytest.CaptureFixture[str]) -> None:
    err = ClientError(
        {"Error": {"Code": "InvalidClientTokenId", "Message": "arn:aws:iam::acct:user/x SECRETVALUE"}},
        "GetCallerIdentity",
    )
    with patch.object(aws_identity, "caller_arn", side_effect=err):
        assert aws_identity.main(["caller-arn"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "caller-arn failed: ClientError InvalidClientTokenId"
    assert "SECRETVALUE" not in out.err
    assert "acct" not in out.err


def test_cli_generic_error_prints_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(aws_identity, "caller_arn", side_effect=ValueError("secret detail")):
        assert aws_identity.main(["caller-arn"]) == 1
    assert capsys.readouterr().err.strip() == "caller-arn failed: ValueError"


def test_identity_config_is_bounded() -> None:
    cfg = aws_identity._IDENTITY_CONFIG
    assert cfg.connect_timeout == 5
    assert cfg.read_timeout == 10
    assert cfg.retries == {"max_attempts": 2}


def test_probe_region_differs_from_project_region() -> None:
    project_region = Path("config/aws-region").read_text(encoding="utf-8").strip()
    assert aws_identity.IDENTITY_PROBE_REGION != project_region
