"""github_ci_deploy's DeployRecordWrite names exactly the three deploy-record prefixes (Decision 272 cl.2).

The telemetry content channel's record job runs under agent-platform-github-ci-deploy after the operator-approved
deploy, so the locked deploy role keeps Decision 270 cl.2's grant. The statement grants s3:PutObject only, on
deploy-records/ducklake/*, deploy-records/prod/* and deploy-records/telemetry-content/*, and nothing else.
"""

from __future__ import annotations

import re

import pytest

from tests.fixtures.terraform_hcl_blocks import _TERRAFORM_PERSONAL_DIR

_TF = _TERRAFORM_PERSONAL_DIR / "oidc_pipeline_roles.tf"
_EXPECTED = {
    "${aws_s3_bucket.data_lake.arn}/deploy-records/ducklake/*",
    "${aws_s3_bucket.data_lake.arn}/deploy-records/prod/*",
    "${aws_s3_bucket.data_lake.arn}/deploy-records/telemetry-content/*",
}


def deploy_record_statement(hcl: str) -> tuple[list[str], set[str]]:
    """(actions, resources) of the DeployRecordWrite statement; ValueError when it is absent or ambiguous."""
    found = list(re.finditer(r'sid\s*=\s*"DeployRecordWrite"', hcl))
    if len(found) != 1:
        raise ValueError(f"expected one DeployRecordWrite statement, found {len(found)}")
    body = hcl[found[0].end() : hcl.index("\n  }", found[0].end())]
    actions = re.search(r"actions\s*=\s*\[([^\]]*)\]", body)
    resources = re.search(r"resources\s*=\s*\[([^\]]*)\]", body, re.DOTALL)
    if actions is None or resources is None:
        raise ValueError("DeployRecordWrite lacks an actions or resources list")
    return re.findall(r'"([^"]+)"', actions.group(1)), set(re.findall(r'"([^"]+)"', resources.group(1)))


def test_deploy_record_write_names_the_three_prefixes() -> None:
    actions, resources = deploy_record_statement(_TF.read_text(encoding="utf-8"))
    assert actions == ["s3:PutObject"]
    assert resources == _EXPECTED


def test_a_missing_content_prefix_is_red() -> None:
    hcl = (
        '  statement {\n    sid     = "DeployRecordWrite"\n    actions = ["s3:PutObject"]\n    resources = [\n'
        '      "${aws_s3_bucket.data_lake.arn}/deploy-records/ducklake/*",\n'
        '      "${aws_s3_bucket.data_lake.arn}/deploy-records/prod/*",\n    ]\n  }\n'
    )
    _actions, resources = deploy_record_statement(hcl)
    assert resources != _EXPECTED


def test_a_wider_grant_is_red() -> None:
    hcl = (
        '  statement {\n    sid     = "DeployRecordWrite"\n    actions = ["s3:PutObject", "s3:DeleteObject"]\n'
        '    resources = ["${aws_s3_bucket.data_lake.arn}/deploy-records/*"]\n  }\n'
    )
    actions, resources = deploy_record_statement(hcl)
    assert actions != ["s3:PutObject"] and resources != _EXPECTED


def test_an_absent_statement_raises() -> None:
    with pytest.raises(ValueError):
        deploy_record_statement('  statement {\n    sid = "Other"\n  }\n')
