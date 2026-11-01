"""Standing guard for the telemetry catalog deploy output (PLAN-telemetry-catalog-aware-capture, PR deploy output).

The SSM parameter /agent-platform/ducklake/telemetry_catalog is the one place read clients learn which catalog the
deployed telemetry writer serves. It must carry the writer's own pin local (the token its TELEMETRY_META_SCHEMA env
reads), never a literal, so the production flip's one local move changes the writer and the deploy output in one apply.
"""

from __future__ import annotations

import re

from tests.fixtures.terraform_hcl_blocks import _TERRAFORM_PERSONAL_DIR, find_resource_block

_WRITER_FILE = "ducklake_telemetry_writer.tf"
_PARAMETER_NAME = "/agent-platform/ducklake/telemetry_catalog"


def _attr(block: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*$", block, re.M)
    return m.group(1) if m else None


def _writer_pin(text: str) -> str | None:
    function = find_resource_block(text, "aws_lambda_function", "ducklake_telemetry_writer")
    return _attr(function, "TELEMETRY_META_SCHEMA")


def deploy_output_problems(text: str) -> list[str]:
    parameter = find_resource_block(text, "aws_ssm_parameter", "ducklake_telemetry_catalog")
    problems: list[str] = []
    if _attr(parameter, "name") != f'"{_PARAMETER_NAME}"':
        problems.append("parameter name is not the catalog deploy output")
    if _attr(parameter, "type") != '"String"':
        problems.append("parameter type is not String")
    value = _attr(parameter, "value")
    pin = _writer_pin(text)
    if value is None or not re.fullmatch(r"local\.\w+", value):
        problems.append("parameter value is not a local reference")
    elif value != pin:
        problems.append("parameter value is not the writer's TELEMETRY_META_SCHEMA pin")
    return problems


def test_catalog_deploy_output_is_the_writer_pin() -> None:
    text = (_TERRAFORM_PERSONAL_DIR / _WRITER_FILE).read_text(encoding="utf-8")
    assert deploy_output_problems(text) == []
    parameter = find_resource_block(text, "aws_ssm_parameter", "ducklake_telemetry_catalog")
    assert _attr(parameter, "value") == "local.ducklake_telemetry_meta_schema"
    assert _writer_pin(text) == "local.ducklake_telemetry_meta_schema"


_SYNTHETIC = """
resource "aws_lambda_function" "ducklake_telemetry_writer" {
  environment {
    variables = {
      TELEMETRY_META_SCHEMA = local.ducklake_telemetry_meta_schema
    }
  }
}

resource "aws_ssm_parameter" "ducklake_telemetry_catalog" {
  name  = "/agent-platform/ducklake/telemetry_catalog"
  type  = "String"
  value = VALUE
}
"""


def test_deploy_output_guard_rejects_a_literal_value() -> None:
    assert deploy_output_problems(_SYNTHETIC.replace("VALUE", "local.ducklake_telemetry_meta_schema")) == []
    assert deploy_output_problems(_SYNTHETIC.replace("VALUE", '"ducklake_smoke"')) == [
        "parameter value is not a local reference"
    ]
    assert deploy_output_problems(_SYNTHETIC.replace("VALUE", "local.ducklake_smoke_meta_schema")) == [
        "parameter value is not the writer's TELEMETRY_META_SCHEMA pin"
    ]
    renamed = _SYNTHETIC.replace("VALUE", "local.ducklake_telemetry_meta_schema").replace("/telemetry_catalog", "/other")
    assert deploy_output_problems(renamed) == ["parameter name is not the catalog deploy output"]
