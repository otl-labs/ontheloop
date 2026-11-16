"""Unit tests for scripts/smoke_test_inference_credentials.py.

The contracts supply each role's parameter name and key variable; boto3 and the transport's cold start are patched,
so no network call is made and no key is real.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from scripts import smoke_test_inference_credentials as smoke
from scripts.llm import tier_selection
from scripts.llm.litellm_cold_start import ColdStartResult
from scripts.llm.litellm_wire import CallRecord

ROLES = ("primary", "warm_fetched_escape_hatch")
VALUE = "smoke-test-sentinel-9876"
ROOT = Path(__file__).resolve().parents[1]


def _row(role: str) -> dict:
    _, config = tier_selection.load()
    return next(r for r in config["settings"] if r["id"] == f"ports.run_persona.litellm.api_key.{role}")


def _record(role: str) -> CallRecord:
    return CallRecord("p", role, "prov", "req/model", "served-x", "shape", "enabled", "high", 1, 1, 0, 0, None, 1, 5)


def _result(role: str, outcome: str, with_record: bool = True) -> ColdStartResult:
    return ColdStartResult(role, "assertion", outcome, _record(role) if with_record else None, "some detail")


def _client(value: str = VALUE) -> MagicMock:
    client = MagicMock()
    client.get_parameter.return_value = {"Parameter": {"Value": value}}
    return client


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "m"}}, "GetParameter")


def _run(role: str, client: MagicMock, cold: MagicMock | None = None, region: str | None = None) -> int:
    with (
        patch.object(smoke.boto3, "Session") as session,
        patch.object(smoke, "resolve_aws_profile", return_value=None),
        patch.object(smoke.litellm_transport, "cold_start_check", cold or MagicMock(return_value=_result(role, "asserted"))),
    ):
        session.return_value.client.return_value = client
        return smoke.run(role, region=region)


class TestParseArgs:
    def test_role_required_and_parsed(self) -> None:
        with pytest.raises(SystemExit):
            smoke._parse_args([])
        args = smoke._parse_args(["--role", "primary"])
        assert args.role == "primary" and args.region is None

    def test_removed_flags_are_gone(self) -> None:
        for flag in ("--provider", "--model", "--parameter-name"):
            with pytest.raises(SystemExit):
                smoke._parse_args(["--role", "primary", flag, "x"])


class TestRun:
    @pytest.mark.parametrize("role", ROLES)
    def test_pass_uses_the_contract_row(self, role: str, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
        row = _row(role)
        monkeypatch.delenv(row["variable"], raising=False)
        client, cold = _client(), MagicMock(return_value=_result(role, "asserted"))
        assert _run(role, client, cold) == 0
        client.get_parameter.assert_called_once_with(Name=row["this_repository"]["current_home"], WithDecryption=True)
        cold.assert_called_once_with(role)
        assert os.environ[row["variable"]] == VALUE
        out = capsys.readouterr()
        assert f"PASS [{role}] requested=req/model served=served-x assertion=asserted" in out.out
        assert VALUE not in out.out + out.err
        monkeypatch.delenv(row["variable"])

    def test_region_defaults_to_the_file_and_can_be_given(self) -> None:
        client = _client()
        with (
            patch.object(smoke.boto3, "Session") as session,
            patch.object(smoke, "resolve_aws_profile", return_value=None),
            patch.object(smoke.litellm_transport, "cold_start_check", return_value=_result("primary", "asserted")),
        ):
            session.return_value.client.return_value = client
            smoke.run("primary")
            smoke.run("primary", region="elsewhere-1")
        regions = [c.kwargs["region_name"] for c in session.return_value.client.call_args_list]
        assert regions == [(ROOT / "config" / "aws-region").read_text().strip(), "elsewhere-1"]
        assert smoke._default_region() == regions[0]

    @pytest.mark.parametrize("outcome", ["spent", "not_asserted", "not_applicable"])
    def test_not_asserted_outcomes_fail(self, outcome: str, capsys: pytest.CaptureFixture[str]) -> None:
        cold = MagicMock(return_value=_result("primary", outcome, with_record=False))
        assert _run("primary", _client(), cold) == 1
        assert f"FAIL [primary] assertion={outcome}" in capsys.readouterr().err

    @pytest.mark.parametrize(("code", "hint"), [("AccessDeniedException", "IAM grant"), ("ParameterNotFound", "not found")])
    def test_ssm_errors(self, code: str, hint: str, capsys: pytest.CaptureFixture[str]) -> None:
        client = MagicMock()
        client.get_parameter.side_effect = _client_error(code)
        assert _run("primary", client) == 1
        assert hint in capsys.readouterr().err

    def test_other_ssm_error_has_no_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = MagicMock()
        client.get_parameter.side_effect = _client_error("Throttling")
        assert _run("primary", client) == 1
        assert "SSM error (Throttling)" in capsys.readouterr().err

    def test_empty_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run("primary", _client("")) == 1
        assert "empty value" in capsys.readouterr().err

    def test_transport_exception(self, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
        cold = MagicMock(side_effect=RuntimeError("boom"))
        assert _run("primary", _client(), cold) == 1
        assert "boom" in capsys.readouterr().err
        monkeypatch.delenv(_row("primary")["variable"], raising=False)

    def test_missing_row(self) -> None:
        with patch.object(smoke.tier_selection, "load", return_value=({}, {"settings": []})):
            with pytest.raises(tier_selection.TierSelectionError):
                smoke.run("primary")


class TestMain:
    def test_main_exits_with_run_code(self) -> None:
        with patch.object(smoke, "run", return_value=3) as fake, pytest.raises(SystemExit) as exc:
            smoke.main(["--role", "warm_fetched_escape_hatch", "--region", "r-1"])
        assert exc.value.code == 3
        fake.assert_called_once_with("warm_fetched_escape_hatch", region="r-1")
