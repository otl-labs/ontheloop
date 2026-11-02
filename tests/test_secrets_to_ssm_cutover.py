"""PLAN-secrets-to-ssm-cutover: every consumer of the nine secrets reads SSM, none reads Secrets Manager."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import catalog_logins as cl

ROOT = Path(__file__).parent.parent
SM_VERBS = re.compile(r"secretsmanager|get_secret_value|get-secret-value|put_secret_value|put-secret-value")
NINE_NAMES = (
    "ducklake-neon-catalog-dsn",
    "agent-platform-ducklake-telemetry-writer-dsn",
    "agent-platform-ducklake-telemetry-writer-prod-dsn",
    "agent-platform-ducklake-telemetry-reader-dsn",
    "agent-platform-ducklake-smoke-maintainer-dsn",
    "neon-api-key",
    "agent-platform-terraform-personal-tfvars",
    "agent-platform-anthropic-api-key",
    "agent-platform-deepseek-api-key",
)
CONSUMER_FILES = (
    "src/common/ducklake_runtime.py",
    "src/common/ducklake_event_reads.py",
    "src/common/neon_api.py",
    "src/lambdas/ducklake_maintenance/catalog_logins.py",
    "src/lambdas/ducklake_maintenance/login_actions.py",
    "src/lambdas/ducklake_telemetry_writer/handler.py",
    "src/lambdas/ducklake_telemetry_reader/handler.py",
    "src/lambdas/ducklake_telemetry_content_reader/handler.py",
    "src/lambdas/ducklake_maintenance_smoke/handler.py",
    "scripts/ducklake_smoke/core.py",
    "scripts/ducklake_neon_smoke_test.py",
    "scripts/smoke_test_inference_credentials.py",
    "scripts/ci/neon_api_key.sh",
    ".github/actions/materialise-tfvars/materialise_tfvars.sh",
)
OLD_ENV = ("TELEMETRY_DSN_SECRET_ID", "DUCKLAKE_DSN_SECRET_ID", "DSN_SECRET_ID")


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def uses_secrets_manager(source: str) -> bool:
    return SM_VERBS.search(source) is not None


def names_old_env(source: str) -> bool:
    return any(name in source for name in OLD_ENV)


def workflow_names_secret_after_sm_verb(source: str) -> bool:
    names = "|".join(re.escape(n) for n in NINE_NAMES)
    return re.search(rf"(secretsmanager|get-secret-value)[^\n]*(?:{names})", source) is not None


class _Ssm:
    def __init__(self, value: str, calls: list[dict[str, Any]]) -> None:
        self._value, self._calls = value, calls

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self._calls.append(kwargs)
        return {"Parameter": {"Value": self._value}}


def _install_boto(monkeypatch: pytest.MonkeyPatch, value: str, clients: list[str], calls: list[dict[str, Any]]) -> None:
    class _Session:
        def __init__(self, **_: Any) -> None:
            pass

        def client(self, name: str, **_: Any) -> Any:
            clients.append(name)
            if name != "ssm":
                raise AssertionError(f"unexpected client {name}")
            return _Ssm(value, calls)

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=_Session))


class TestSecretsToSsmCutover:
    def test_fetch_dsn_reads_only_ssm_with_decryption(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clients: list[str] = []
        calls: list[dict[str, Any]] = []
        _install_boto(monkeypatch, '{"host":"h","dbname":"d","username":"u","password":"p"}', clients, calls)
        out = rt.fetch_dsn(profile="agent_platform")
        assert out["host"] == "h"
        assert clients == ["ssm"]
        assert calls == [{"Name": "/secret/ducklake-neon-catalog-dsn", "WithDecryption": True}]

    def test_fetch_dsn_rejects_a_stale_secrets_manager_id(self) -> None:
        with pytest.raises(ValueError):
            rt.fetch_dsn("ducklake-neon-catalog-dsn")

    def test_default_names_are_secret_parameters(self) -> None:
        assert rt.DSN_PARAMETER_NAME.startswith("/secret/")
        assert all(login.parameter_name.startswith("/secret/") for login in cl.LOGINS.values())

    @pytest.mark.parametrize("rel", CONSUMER_FILES)
    def test_no_consumer_uses_secrets_manager_or_the_old_env_vars(self, rel: str) -> None:
        source = _text(rel)
        assert not uses_secrets_manager(source), rel
        assert not names_old_env(source), rel

    @pytest.mark.parametrize(
        "rel,var",
        [
            ("src/lambdas/ducklake_telemetry_writer/handler.py", "TELEMETRY_DSN_PARAMETER"),
            ("src/lambdas/ducklake_telemetry_reader/handler.py", "TELEMETRY_DSN_PARAMETER"),
            ("src/lambdas/ducklake_telemetry_content_reader/handler.py", "TELEMETRY_DSN_PARAMETER"),
            ("src/lambdas/ducklake_maintenance_smoke/handler.py", "DUCKLAKE_DSN_PARAMETER"),
        ],
    )
    def test_handlers_read_the_parameter_env_vars(self, rel: str, var: str) -> None:
        assert var in _text(rel)

    def test_neon_api_and_inference_smoke_use_ssm(self) -> None:
        for rel in ("src/common/neon_api.py", "scripts/smoke_test_inference_credentials.py"):
            source = _text(rel)
            assert "get_parameter" in source and "WithDecryption=True" in source, rel
        assert "/secret/neon-api-key" in _text("src/common/neon_api.py")
        assert "/secret/agent-platform-anthropic-api-key" in _text("scripts/smoke_test_inference_credentials.py")

    def test_shell_consumers_fetch_with_ssm_get_parameter(self) -> None:
        neon = _text("scripts/ci/neon_api_key.sh")
        assert "aws ssm get-parameter" in neon and "/secret/neon-api-key" in neon and "--with-decryption" in neon
        tfvars = _text(".github/actions/materialise-tfvars/materialise_tfvars.sh")
        assert tfvars.count("aws ssm get-parameter --name /secret/agent-platform-terraform-personal-tfvars") == 2

    def test_no_workflow_names_a_secret_after_a_secretsmanager_verb(self) -> None:
        for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
            assert not workflow_names_secret_after_sm_verb(path.read_text(encoding="utf-8")), path.name

    def test_domains_lines_name_the_secret_parameters(self) -> None:
        doc = yaml.safe_load(_text("docs/contracts/secret-material-handling.yaml"))
        domains = doc["patterns"]["domains"]
        neon = next(d for d in domains if d.startswith("Neon provider API key"))
        tfvars = next(d for d in domains if d.startswith("terraform-personal tfvars"))
        assert "/secret/neon-api-key" in neon
        assert "/secret/agent-platform-terraform-personal-tfvars" in tfvars

    def test_deploy_paths_login_procedure_writes_the_parameter_then_the_old_secret(self) -> None:
        doc = yaml.safe_load(_text("docs/contracts/deploy-paths.yaml"))
        step = doc["admin_out_of_band"]["scoped_login_provisioning"]["procedure"]["mutation_1_write_secret"]
        assert "put_parameter" in step and "SecureString" in step
        assert step.index("put_parameter") < step.index("put_secret_value") < step.index("SECRET_WRITTEN")
        assert "removeprefix('/secret/')" in step


class TestRedCases:
    def test_secretsmanager_source_is_flagged(self) -> None:
        assert uses_secrets_manager('client = session.client("secretsmanager")')
        assert uses_secrets_manager("aws secretsmanager get-secret-value --secret-id x")
        assert not uses_secrets_manager("aws ssm get-parameter --name /secret/x --with-decryption")

    def test_old_env_var_is_flagged(self) -> None:
        assert names_old_env('os.environ["TELEMETRY_DSN_SECRET_ID"]')
        assert not names_old_env('os.environ["TELEMETRY_DSN_PARAMETER"]')

    def test_workflow_secret_after_verb_is_flagged(self) -> None:
        assert workflow_names_secret_after_sm_verb("run: aws secretsmanager get-secret-value --secret-id neon-api-key")
        assert not workflow_names_secret_after_sm_verb("run: aws ssm get-parameter --name /secret/neon-api-key")
