"""Bootstrap grants for the /secret/ SSM parameters (Decision 237, secrets-to-ssm-grants): exact Sids, verbs and ARNs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BOOTSTRAP = Path(__file__).resolve().parents[1] / "terraform" / "bootstrap"
_PREFIX = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/"


def _strip_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _read(name: str) -> str:
    return _strip_comments((_BOOTSTRAP / name).read_text(encoding="utf-8"))


def _statement(text: str, sid: str) -> dict[str, list[str]]:
    """The statement object whose Sid is `sid`: its quoted Action and Resource values."""
    m = re.search(rf'Sid\s*=\s*"{sid}"', text)
    assert m is not None, f"Sid {sid} not found"
    start = text.rfind("{", 0, m.start())
    depth, end = 0, start
    for i in range(start, len(text)):
        depth += text[i] == "{"
        depth -= text[i] == "}"
        if depth == 0:
            end = i
            break
    body = text[start : end + 1]
    out: dict[str, list[str]] = {}
    for field in ("Action", "Resource"):
        fm = re.search(rf"\b{field}\s*=\s*(\[.*?\]|\"[^\"]*\")", body, re.DOTALL)
        assert fm is not None, (sid, field)
        out[field] = re.findall(r'"([^"]*)"', fm.group(1))
    out["body"] = [body]
    return out


def test_statement_helper_reads_both_value_shapes() -> None:
    hcl = '{\n Sid = "A"\n Action = ["x:y", "x:z"]\n Resource = "arn:one"\n}\n'
    hcl += '{\n Sid = "B"\n Action = ["q"]\n Resource = ["r1", "r2"]\n}'
    assert _statement(hcl, "A")["Resource"] == ["arn:one"] and _statement(hcl, "B")["Resource"] == ["r1", "r2"]
    with pytest.raises(AssertionError):
        _statement(hcl, "Missing")


def test_apply_reads_exactly_three_parameters() -> None:
    text = _read("github_ci_apply_reads.tf")
    s = _statement(text, "SecretParameterValueRead")
    assert sorted(s["Action"]) == ["ssm:GetParameter", "ssm:ListTagsForResource"]
    assert sorted(s["Resource"]) == sorted(
        _PREFIX + n for n in ("neon-api-key", "agent-platform-terraform-personal-tfvars", "ducklake-neon-catalog-dsn")
    )
    assert "aws_caller_identity" not in s["body"][0]


def test_apply_writes_exactly_the_dsn_parameter() -> None:
    s = _statement(_read("github_ci_apply_policy.tf"), "SecretParameterDsnWrite")
    assert sorted(s["Action"]) == [
        "ssm:AddTagsToResource",
        "ssm:DeleteParameter",
        "ssm:PutParameter",
        "ssm:RemoveTagsFromResource",
    ]
    assert s["Resource"] == [_PREFIX + "ducklake-neon-catalog-dsn"]
    assert "aws_caller_identity" not in s["body"][0]


def test_pat_arn_is_gone_from_both_enumerated_sids() -> None:
    reads, policy = _read("github_ci_apply_reads.tf"), _read("github_ci_apply_policy.tf")
    assert "agent-platform-github-pat" not in reads and "agent-platform-github-pat" not in policy
    assert len(_statement(reads, "SecretsManagerValueReadEnumerated")["Resource"]) == 4
    assert len(_statement(policy, "SecretsManagerUpdateEnumerated")["Resource"]) == 2


def test_platform_dev_reads_exactly_the_two_inference_parameters() -> None:
    s = _statement(_read("platform_dev_policies.tf"), "InferenceCredentialsParameterRead")
    assert s["Action"] == ["ssm:GetParameter"]
    assert sorted(s["Resource"]) == [_PREFIX + "agent-platform-anthropic-api-key", _PREFIX + "agent-platform-deepseek-api-key"]


def test_platform_admin_administers_the_secret_family() -> None:
    text = _read("platform_admin_policies.tf")
    s = _statement(text, "SecretParameterAdmin")
    assert s["Resource"] == [_PREFIX + "*"]
    assert s["Action"] == _statement(text, "SSMParameterProvisioning")["Action"]


def test_existing_sids_are_unchanged() -> None:
    reads = _read("github_ci_apply_reads.tf")
    assert _statement(reads, "SSMParameterRead")["Resource"] == [
        "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/*"
    ]
    assert _statement(reads, "SecretsManagerMetadataRead")["Resource"] == [
        "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-*"
    ]
    admin = _statement(_read("platform_admin_policies.tf"), "SecretsManagerAdmin")
    assert "secretsmanager:PutSecretValue" in admin["Action"] and admin["Resource"] == ["*"]
