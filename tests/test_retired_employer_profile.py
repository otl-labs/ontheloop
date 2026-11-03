"""Profile defaults for scripts/run_scheduled_agent.py and scripts/build_lambda.py (rec-3924).

Both resolve the AWS profile through scripts.aws_profile.resolve_aws_profile: explicit, else AWS_PROFILE,
else agent_platform, and no profile under environment credentials.
"""

import json
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import scripts.build_lambda as bl
import scripts.build_lambda_deploy as bd
from scripts.run_scheduled_agent import _smoke_test, _trigger_lambda

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(name, raising=False)


def _profile_arg(cmd):
    return cmd[cmd.index("--profile") + 1] if "--profile" in cmd else None


def _file_ctx():
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value='{"StatusCode": 200}')))
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _run_trigger():
    result = MagicMock(returncode=0)
    with (
        patch("scripts.run_scheduled_agent.subprocess.run", return_value=result) as mock_run,
        patch("scripts.run_scheduled_agent.tempfile.mkstemp", return_value=(0, "/tmp/test-out.json")),
        patch("builtins.open", MagicMock(return_value=_file_ctx())),
        patch("scripts.run_scheduled_agent.os.close"),
        patch("scripts.run_scheduled_agent.os.unlink"),
    ):
        assert _trigger_lambda("rec-curator") == 0
    return mock_run.call_args[0][0]


def _run_smoke_verify_cmd():
    s3_response = json.dumps({"Key": "agents/x/a.jsonl", "LastModified": datetime.now(timezone.utc).isoformat()})
    results = [
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(returncode=0, stdout=s3_response, stderr=""),
    ]
    with (
        patch("scripts.run_scheduled_agent.subprocess.run", side_effect=results) as mock_run,
        patch("scripts.run_scheduled_agent.tempfile.mkstemp", return_value=(0, "/tmp/smoke.json")),
        patch("builtins.open", MagicMock(return_value=_file_ctx())),
        patch("scripts.run_scheduled_agent.os.close"),
        patch("scripts.run_scheduled_agent.os.unlink"),
    ):
        assert _smoke_test("doc-freshness") == 0
    return mock_run.call_args_list[2][0][0]


class TestRunScheduledAgentProfile:
    @pytest.mark.parametrize("runner", [_run_trigger, _run_smoke_verify_cmd])
    def test_default_is_agent_platform(self, runner):
        assert _profile_arg(runner()) == "agent_platform"

    @pytest.mark.parametrize("runner", [_run_trigger, _run_smoke_verify_cmd])
    def test_aws_profile_env_wins(self, monkeypatch, runner):
        monkeypatch.setenv("AWS_PROFILE", "agent_platform_admin")
        assert _profile_arg(runner()) == "agent_platform_admin"

    @pytest.mark.parametrize("runner", [_run_trigger, _run_smoke_verify_cmd])
    def test_environment_credentials_pass_no_profile(self, monkeypatch, runner):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        assert "--profile" not in runner()


def _resolved_main_profile(monkeypatch, argv):
    captured = {}
    monkeypatch.setattr(sys, "argv", ["build_lambda", *argv])
    with patch("scripts.build_lambda._run_prod_build", side_effect=lambda a: captured.update(profile=a.profile)):
        bl.main()
    return captured["profile"]


class TestBuildLambdaProfile:
    def test_parser_default_is_none(self):
        assert bl.build_parser().parse_args([]).profile is None

    def test_default_resolves_agent_platform(self, monkeypatch):
        assert _resolved_main_profile(monkeypatch, []) == "agent_platform"

    def test_aws_profile_env_wins(self, monkeypatch):
        monkeypatch.setenv("AWS_PROFILE", "agent_platform_admin")
        assert _resolved_main_profile(monkeypatch, []) == "agent_platform_admin"

    def test_environment_credentials_resolve_empty(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        assert _resolved_main_profile(monkeypatch, []) == ""

    def test_explicit_profile_wins(self, monkeypatch):
        assert _resolved_main_profile(monkeypatch, ["--profile", "x"]) == "x"

    def test_explicit_empty_profile_wins(self, monkeypatch):
        monkeypatch.setenv("AWS_PROFILE", "agent_platform_admin")
        assert _resolved_main_profile(monkeypatch, ["--profile", ""]) == ""


class _FakePath:
    def __init__(self, name="x.zip"):
        self.name = name

    def stat(self):
        return MagicMock(st_size=100)


class TestDucklakePassthrough:
    def test_remap_helper_is_gone(self):
        assert not hasattr(bd, "_resolve_ducklake_profile")
        assert not hasattr(bl, "_resolve_ducklake_profile")

    def test_builders_receive_explicit_profile_unchanged(self, monkeypatch):
        captured = {}
        args = bl.build_parser().parse_args(["--ducklake-only", "--skip-upload", "--profile", "agent_platform_admin"])
        zips = [_FakePath(name=f"{n}.zip") for n in range(7)]
        with (
            patch("scripts.build_lambda.resolve_bucket", return_value="bk"),
            patch("scripts.build_lambda.build_ducklake_function_package", side_effect=zips),
            patch("scripts.build_lambda.build_ducklake_deps_layer", return_value=_FakePath(name="deps.zip")),
            patch(
                "scripts.build_lambda.build_ducklake_extensions_layer",
                side_effect=lambda td, **kw: captured.update(kw) or _FakePath(name="ext.zip"),
            ),
            patch("scripts.build_lambda.build_pgclient_layer", return_value=_FakePath(name="pgclient.zip")),
            patch("scripts.build_lambda.assert_within_size_limit"),
        ):
            bl._run_ducklake_build(args)
        assert captured["profile"] == "agent_platform_admin"
