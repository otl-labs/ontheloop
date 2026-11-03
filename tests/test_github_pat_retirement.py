"""rec-4539 guard: the GitHub Models PAT secret, its grants and its env var are gone from the prod Lambda root.

The secret agent-platform-github-pat was deleted rather than moved to SSM because the GitHub Models API was retired.
Pure text/YAML predicates (red cases on synthetic input) plus handler behaviour; no network, terraform or AWS.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tests.fixtures.terraform_hcl_blocks import find_resource_block, tf_dir_file_texts

_ROOT = Path(__file__).resolve().parents[1]
_TF_DIR = _ROOT / "terraform" / "personal"
_FORBIDDEN = (
    'resource "aws_secretsmanager_secret" "github_pat"',
    "aws_secretsmanager_secret.github_pat",
    "GITHUB_PAT_SECRET_ARN",
    "agent-platform-github-pat",
)
_ROLE_POLICIES = ("scheduled_agent_dispatcher", "findings_processor")


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _forbidden_hits(file_texts: dict[str, str]) -> list[str]:
    return [
        f"{name}: {needle}" for name, text in file_texts.items() for needle in _FORBIDDEN if needle in _strip_comments(text)
    ]


def _policy_sids(text: str, role: str) -> list[str]:
    return re.findall(r'Sid\s*=\s*"([^"]+)"', find_resource_block(text, "aws_iam_role_policy", role))


def _policy_names_secretsmanager(text: str, role: str) -> bool:
    return "secretsmanager:" in find_resource_block(text, "aws_iam_role_policy", role)


def _domains_name_pat(contract: dict) -> list[str]:
    return [d for d in contract["patterns"]["domains"] if "GitHub PAT" in d]


def _secrets_manager_names(contract: dict) -> list[str]:
    return [e.get("name", "") for e in contract["iam_requirements"]["secrets_manager"] if isinstance(e, dict)]


class TestPatSurfaceRetired:
    def test_real_tree_has_no_pat_secret_grant_or_env_var(self) -> None:
        assert _forbidden_hits(tf_dir_file_texts(_TF_DIR)) == []

    def test_red_case_secret_resource(self) -> None:
        hits = _forbidden_hits({"x.tf": 'resource "aws_secretsmanager_secret" "github_pat" {\n}\n'})
        assert hits

    def test_red_case_env_var(self) -> None:
        assert _forbidden_hits({"x.tf": "      GITHUB_PAT_SECRET_ARN = foo\n"})

    def test_comments_are_ignored(self) -> None:
        assert _forbidden_hits({"x.tf": "# GITHUB_PAT_SECRET_ARN was removed\n"}) == []

    @pytest.mark.parametrize("role", _ROLE_POLICIES)
    def test_real_policies_have_no_secretsmanager_action(self, role: str) -> None:
        text = (_TF_DIR / "prod_lambdas.tf").read_text(encoding="utf-8")
        assert not _policy_names_secretsmanager(text, role)

    def test_red_case_policy_with_secretsmanager(self) -> None:
        synthetic = (
            'resource "aws_iam_role_policy" "findings_processor" {\n'
            '  policy = jsonencode({ Statement = [{ Sid = "GithubPatRead", Action = ["secretsmanager:GetSecretValue"] }] })\n'
            "}\n"
        )
        assert _policy_names_secretsmanager(synthetic, "findings_processor")

    @pytest.mark.parametrize("role", _ROLE_POLICIES)
    def test_control_s3_statements_remain(self, role: str) -> None:
        text = (_TF_DIR / "prod_lambdas.tf").read_text(encoding="utf-8")
        sids = _policy_sids(text, role)
        assert "GithubPatRead" not in sids
        assert any(sid.startswith("S3") for sid in sids), sids

    def test_red_case_control_without_s3(self) -> None:
        synthetic = 'resource "aws_iam_role_policy" "findings_processor" {\n  x = { Sid = "Logs" }\n}\n'
        assert not any(sid.startswith("S3") for sid in _policy_sids(synthetic, "findings_processor"))

    def test_contracts_drop_the_pat(self) -> None:
        smh = yaml.safe_load((_ROOT / "docs/contracts/secret-material-handling.yaml").read_text(encoding="utf-8"))
        ip = yaml.safe_load((_ROOT / "docs/contracts/inference-provider.yaml").read_text(encoding="utf-8"))
        assert _domains_name_pat(smh) == []
        assert "agent-platform-github-pat" not in _secrets_manager_names(ip)

    def test_red_case_contracts_still_naming_pat(self) -> None:
        assert _domains_name_pat({"patterns": {"domains": ["GitHub PAT -- x"]}})
        assert "agent-platform-github-pat" in _secrets_manager_names(
            {"iam_requirements": {"secrets_manager": [{"name": "agent-platform-github-pat"}]}}
        )


class TestHandlersIgnoreSecretArn:
    @pytest.mark.parametrize(
        "module", ["src.data.handlers.scheduled_agent_handler", "src.data.handlers.findings_processor_handler"]
    )
    def test_arn_alone_never_reaches_secrets_manager(self, module: str, monkeypatch: pytest.MonkeyPatch) -> None:
        mod = importlib.import_module(module)
        monkeypatch.delenv("GITHUB_PAT", raising=False)
        monkeypatch.setenv("GITHUB_PAT_SECRET_ARN", "placeholder-secret-ref")
        with patch("boto3.client", MagicMock()) as client:
            assert mod._get_github_pat() == ""
        client.assert_not_called()

    @pytest.mark.parametrize(
        "module", ["src.data.handlers.scheduled_agent_handler", "src.data.handlers.findings_processor_handler"]
    )
    def test_env_pat_still_returned(self, module: str, monkeypatch: pytest.MonkeyPatch) -> None:
        mod = importlib.import_module(module)
        monkeypatch.setenv("GITHUB_PAT", " local-token ")
        assert mod._get_github_pat() == "local-token"
