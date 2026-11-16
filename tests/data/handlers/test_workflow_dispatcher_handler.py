"""Mirror test for src/data/handlers/workflow_dispatcher_handler.py (PLAN-scheduled-workflow-dispatch PR 2)."""

from __future__ import annotations

import builtins
import logging
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from scripts import repo_identity
from src.data.handlers import github_app_token as gat
from src.data.handlers import workflow_dispatcher_handler as wdh

_REPO = Path(__file__).resolve().parents[3]
PEM = "FAKE-PEM-SECRET-VALUE"
JWT = "eyJFAKEJWTSECRET"
TOKEN = "ghs_FAKETOKENSECRET"


class FakeApi:
    def __init__(self, runs: dict[str, Any], dispatch: Any = None, dispatch_error: Exception | None = None) -> None:
        self.runs = runs
        self.dispatch = dispatch
        self.dispatch_error = dispatch_error
        self.calls: list[tuple[str, str, Any]] = []

    def call(self, method: str, path: str, token: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        assert token == TOKEN
        if method == "GET":
            return 200, self.runs
        if self.dispatch_error:
            raise self.dispatch_error
        return (200 if self.dispatch else 204), self.dispatch


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("WORKFLOW_DISPATCH_ENABLED", "true")
    state: dict[str, Any] = {"revoked": [], "minted": []}
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": PEM}}
    boto3 = MagicMock()
    boto3.client.return_value = ssm
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)
    state["ssm"] = ssm
    monkeypatch.setattr(gat, "app_jwt", lambda client_id, pem, now: JWT)

    def mint(api: Any, app_token: str, repository: str, permissions: dict[str, str]) -> str:
        state["minted"].append((app_token, repository, permissions))
        return TOKEN

    monkeypatch.setattr(gat, "installation_token", mint)
    monkeypatch.setattr(gat, "revoke", lambda api, token: state["revoked"].append(token))
    return state


def _no_secret_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        text = record.getMessage()
        assert "SECRET" not in text


@pytest.mark.parametrize("flag", [None, "false"])
def test_disabled_reads_nothing(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> None:
    if flag is None:
        monkeypatch.delenv("WORKFLOW_DISPATCH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("WORKFLOW_DISPATCH_ENABLED", flag)
    boto3 = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)
    api = FakeApi({"total_count": 0})
    assert wdh.handler({"schedule": "terraform-drift"}, None, api=api) == {"status": "disabled"}
    assert not boto3.client.called and api.calls == []


@pytest.mark.parametrize("flag", [None, "true"])
def test_smoke_lists_schedules_and_reads_no_secret(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> None:
    if flag is None:
        monkeypatch.delenv("WORKFLOW_DISPATCH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("WORKFLOW_DISPATCH_ENABLED", flag)
    boto3 = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)
    result = wdh.handler({}, None)
    assert result == {"status": "smoke", "schedules": ["convergence-health", "main-canary", "terraform-drift"]}
    assert not boto3.client.called


def test_smoke_raises_when_jwt_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "jwt":
            raise ImportError("no jwt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError):
        wdh.handler({}, None)


def test_unknown_schedule_raises_before_ssm(wired: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="unknown schedule"):
        wdh.handler({"schedule": "nope"}, None, api=FakeApi({}))
    assert not wired["ssm"].get_parameter.called


def test_happy_path_dispatches_and_revokes(wired: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=wdh.__name__)
    api = FakeApi({"total_count": 0}, dispatch={"workflow_run_id": 99})
    result = wdh.handler({"schedule": "terraform-drift"}, None, api=api)
    assert result == {
        "status": "dispatched",
        "schedule": "terraform-drift",
        "workflow": "terraform-drift.yml",
        "workflow_run_id": 99,
    }
    wired["ssm"].get_parameter.assert_called_once_with(
        Name="/secret/agent-platform-workflow-dispatcher-app-key", WithDecryption=True
    )
    assert wired["minted"] == [(JWT, "otl-labs/ontheloop", {"actions": "write", "metadata": "read"})]
    method, path, body = api.calls[1]
    assert (method, path) == ("POST", "/repos/otl-labs/ontheloop/actions/workflows/terraform-drift.yml/dispatches")
    assert body == {"ref": "main", "return_run_details": True}
    assert re.search(r"/runs\?created=>=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ&per_page=1$", api.calls[0][1])
    assert wired["revoked"] == [TOKEN]
    assert "status=dispatched schedule=terraform-drift workflow=terraform-drift.yml run_id=99" in caplog.text
    _no_secret_in_logs(caplog)


def test_204_without_run_id(wired: dict[str, Any]) -> None:
    result = wdh.handler({"schedule": "main-canary"}, None, api=FakeApi({"total_count": 0}, dispatch=None))
    assert result["workflow_run_id"] is None and result["status"] == "dispatched"


def test_200_without_run_id(wired: dict[str, Any]) -> None:
    result = wdh.handler({"schedule": "main-canary"}, None, api=FakeApi({"total_count": 0}, dispatch={"other": 1}))
    assert result["workflow_run_id"] is None


@pytest.mark.parametrize("extra", [{}, {"force_dispatch": True}])
def test_recent_run_skips_and_revokes(wired: dict[str, Any], caplog: pytest.LogCaptureFixture, extra: dict) -> None:
    caplog.set_level(logging.INFO, logger=wdh.__name__)
    api = FakeApi({"total_count": 1, "workflow_runs": [{"id": 7}]})
    result = wdh.handler({"schedule": "convergence-health", **extra}, None, api=api)
    assert result == {
        "status": "skipped",
        "schedule": "convergence-health",
        "workflow": "convergence-health.yml",
        "recent_run_id": 7,
    }
    assert [c[0] for c in api.calls] == ["GET"]
    assert wired["revoked"] == [TOKEN]
    _no_secret_in_logs(caplog)


def test_recent_count_without_runs_list(wired: dict[str, Any]) -> None:
    result = wdh.handler({"schedule": "convergence-health"}, None, api=FakeApi({"total_count": 2}))
    assert result["status"] == "skipped" and result["recent_run_id"] is None


def test_dispatch_422_raises_and_revokes(wired: dict[str, Any]) -> None:
    err = gat.GitHubApiError(422, "POST", "/x", "Workflow is disabled")
    with pytest.raises(gat.GitHubApiError):
        wdh.handler({"schedule": "terraform-drift"}, None, api=FakeApi({"total_count": 0}, dispatch_error=err))
    assert wired["revoked"] == [TOKEN]


def test_revoke_failure_raises(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(api: Any, token: str) -> None:
        raise RuntimeError("revoke failed")

    monkeypatch.setattr(gat, "revoke", boom)
    with pytest.raises(RuntimeError, match="revoke failed"):
        wdh.handler({"schedule": "terraform-drift"}, None, api=FakeApi({"total_count": 0}, dispatch={"workflow_run_id": 1}))


def test_default_api_is_constructed(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[FakeApi] = []

    def factory() -> FakeApi:
        built.append(FakeApi({"total_count": 1, "workflow_runs": [{"id": 3}]}))
        return built[0]

    monkeypatch.setattr(gat, "GitHubApi", factory)
    assert wdh.handler({"schedule": "terraform-drift"}, None)["status"] == "skipped"
    assert len(built) == 1


def test_read_key_refuses_outside_secret() -> None:
    with pytest.raises(ValueError, match="/secret/"):
        wdh._read_key("/other/key")


def test_real_register_has_the_three_rows() -> None:
    register = wdh.load_register()
    assert sorted(register["schedules"]) == ["convergence-health", "main-canary", "terraform-drift"]
    assert register["repository"] == repo_identity.full_name()


def _write(tmp_path: Path, mutate: Any) -> Path:
    register = yaml.safe_load(wdh.REGISTER_PATH.read_text(encoding="utf-8"))
    mutate(register)
    path = tmp_path / "schedules.yaml"
    path.write_text(yaml.safe_dump(register), encoding="utf-8")
    return path


def _row(field: str, value: Any) -> Any:
    def mutate(reg: dict) -> None:
        reg["schedules"]["terraform-drift"][field] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda r: r.update(schedules={}), "no schedules"),
        (_row("workflow", "Bad.yaml"), "workflow"),
        (lambda r: r.update(ref="dev"), "ref"),
        (lambda r: r.update(key_parameter="/plain/key"), "key_parameter"),
        (lambda r: r["app"].update(bot_login="someone"), "bot_login"),
        (_row("skip_window_seconds", 30), "skip_window_seconds"),
        (_row("skip_window_seconds", 4000), "skip_window_seconds"),
        (_row("aws_cron", "rate(1 hour)"), "aws_cron"),
    ],
)
def test_load_register_rejects_malformed(tmp_path: Path, mutate: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        wdh.load_register(_write(tmp_path, mutate))


def test_load_register_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text("- a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        wdh.load_register(path)


def _pins(lines: list[str], names: set[str]) -> dict[str, str]:
    found = {}
    for line in lines:
        match = re.match(r"^\s*-?\s*\"?([a-z0-9-]+)==([^\"\s]+)", line)
        if match and match.group(1) in names:
            found[match.group(1)] = match.group(2)
    return found


def test_manifest_pins_equal_requirements() -> None:
    names = {"pyjwt", "cryptography", "cffi", "pycparser"}
    manifest = yaml.safe_load((_REPO / "src/lambdas/data-pipeline/manifest.yaml").read_text(encoding="utf-8"))
    pinned = _pins([f"- {p}" for p in manifest["pip_packages"]], names)
    required = _pins((_REPO / "requirements.txt").read_text(encoding="utf-8").splitlines(), names)
    assert pinned == required and set(pinned) == names


def test_dispatch_error_survives_a_failing_revoke(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(api: Any, token: str) -> None:
        raise RuntimeError("revoke failed")

    caplog.set_level(logging.INFO, logger=wdh.__name__)
    monkeypatch.setattr(gat, "revoke", boom)
    err = gat.GitHubApiError(422, "POST", "/x", "Workflow is disabled")
    with pytest.raises(gat.GitHubApiError):
        wdh.handler({"schedule": "terraform-drift"}, None, api=FakeApi({"total_count": 0}, dispatch_error=err))
    assert "status=revoke_failed schedule=terraform-drift" in caplog.text
