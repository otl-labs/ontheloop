"""The narrow URL-parameter form of validate_invoke_implies_resolve (PLAN-persona-verdict-credential).

A Decision 213 URL-only caller that invokes only the telemetry writer may read just that writer's URL parameter
instead of every /agent-platform/ parameter. Each case runs the check on a synthetic terraform/personal root.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.iam_tf.validate_invoke_implies_resolve import validate_invoke_implies_resolve

_URL_ARN = "arn:aws:ssm:eu-west-2:1234567890:parameter/agent-platform/ducklake/telemetry_writer_url"
_TELEMETRY = "aws_lambda_function.ducklake_telemetry_writer.arn"
_WRITER = "aws_lambda_function.ducklake_writer.arn"


def _write_oidc_tf(tmp_path: Path, body: str) -> None:
    oidc_path = tmp_path / "terraform" / "personal" / "oidc.tf"
    oidc_path.parent.mkdir(parents=True, exist_ok=True)
    oidc_path.write_text(body, encoding="utf-8")


def _role(invoke: str, reads: list[tuple[str, str]]) -> str:
    read_blocks = "".join(
        f"""
          statement {{
            sid       = "Read{i}"
            effect    = "Allow"
            actions   = ["{action}"]
            resources = ["{resource}"]
          }}"""
        for i, (action, resource) in enumerate(reads)
    )
    return f"""
        data "aws_iam_policy_document" "github_ci_narrow" {{
          statement {{
            sid       = "Invoke"
            effect    = "Allow"
            actions   = ["lambda:InvokeFunction"]
            resources = [{invoke}]
          }}{read_blocks}
        }}

        resource "aws_iam_role_policy" "github_ci_narrow" {{
          name   = "agent-platform-github-ci-narrow"
          role   = aws_iam_role.github_ci_narrow.id
          policy = data.aws_iam_policy_document.github_ci_narrow.json
        }}
        """


def _run(tmp_path: Path, body: str) -> list[str]:
    _write_oidc_tf(tmp_path, body)
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path):
        validate_invoke_implies_resolve(failed)
    return failed


def test_telemetry_only_invoker_with_literal_url_parameter_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, _role(_TELEMETRY, [("ssm:GetParameter", _URL_ARN)])) == []
    assert "PASS: github_ci_narrow invokes the telemetry writer and reads its URL parameter." in capsys.readouterr().out


def test_other_agent_platform_parameter_fails(tmp_path: Path) -> None:
    other = "arn:aws:ssm:eu-west-2:1234567890:parameter/agent-platform/ducklake/reader_url"
    assert _run(tmp_path, _role(_TELEMETRY, [("ssm:GetParameter", other)]))


def test_url_parameter_as_a_pattern_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, _role(_TELEMETRY, [("ssm:GetParameter", _URL_ARN + "*")]))


def test_ducklake_writer_invoker_with_only_the_url_parameter_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, _role(_WRITER, [("ssm:GetParameter", _URL_ARN)]))


def test_ducklake_writer_invoker_with_no_ssm_read_fails_with_the_broad_read_text(tmp_path: Path) -> None:
    failed = _run(tmp_path, _role(_WRITER, []))
    assert len(failed) == 1 and "lacks ssm:Get*" in failed[0]


def test_real_tree_passes_and_reports_the_persona_verdict_role_narrowly(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    validate_invoke_implies_resolve(failed)
    assert failed == []
    assert (
        "PASS: github_ci_persona_verdict invokes the telemetry writer and reads its URL parameter." in capsys.readouterr().out
    )
