"""Standing guard for PLAN-secrets-to-ssm-expand (Decision 237): the /secret/ parameter names, the DSN SecureString,
the per-role parameter grants mirroring the Secrets Manager grants, the planner's drift-conditioned reads, the trusted
plan role's read and the new env vars. Nothing reads SSM yet; every consumer still reads Secrets Manager."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir
from scripts.checks.iam_tf._read_coverage import ROLE_PLANNER, _check_resource, _resolve_role_statements, _scan_resources
from scripts.checks.iam_tf.validate_planner_secret_value_reads import validate_planner_secret_value_reads

REPO = Path(__file__).resolve().parents[1]
PERSONAL = REPO / "terraform" / "personal"
_PREFIX = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/"
_OUT_OF_BAND = {  # pragma: allowlist secret
    "neon_api_key": "neon-api-key",  # pragma: allowlist secret
    "terraform_personal_tfvars": "agent-platform-terraform-personal-tfvars",  # pragma: allowlist secret
}  # pragma: allowlist secret -- resource names, no credential
_SECRET_KEYS = {
    "ducklake_neon_catalog_dsn": "ducklake_neon_catalog_dsn",
    "deepseek_api_key": "deepseek_api_key",  # pragma: allowlist secret
    "anthropic_api_key": "anthropic_api_key",  # pragma: allowlist secret
    "telemetry_writer_dsn": "ducklake_telemetry_writer_dsn",
    "telemetry_writer_prod_dsn": "ducklake_telemetry_writer_prod_dsn",
    "telemetry_reader_dsn": "ducklake_telemetry_reader_dsn",
    "smoke_maintainer_dsn": "ducklake_smoke_maintainer_dsn",
}

# file, Secrets Manager Sid, parameter Sid, env pairs (var, secret resource, parameter key)
_ROLES = [
    ("ducklake_lambdas.tf", "NeonDsnRead", "NeonDsnParameterRead", 2, []),
    ("ducklake_catalog_dr.tf", "NeonDsnRead", "NeonDsnParameterRead", 1, []),
    ("ducklake_maintenance.tf", "NeonDsnRead", "NeonDsnParameterRead", 1, []),
    (
        "ducklake_maintenance_smoke.tf",
        "NeonDsnRead",
        "NeonDsnParameterRead",
        1,
        [("DUCKLAKE_DSN_PARAMETER", "DUCKLAKE_DSN_SECRET_ID", "smoke_maintainer_dsn")],
    ),
    (
        "ducklake_telemetry_writer.tf",
        "TelemetryDsnRead",
        "TelemetryDsnParameterRead",
        1,
        [("TELEMETRY_DSN_PARAMETER", "TELEMETRY_DSN_SECRET_ID", "telemetry_writer_prod_dsn")],
    ),
    (
        "ducklake_telemetry_writer_smoke.tf",
        "TelemetryDsnRead",
        "TelemetryDsnParameterRead",
        1,
        [("TELEMETRY_DSN_PARAMETER", "TELEMETRY_DSN_SECRET_ID", "telemetry_writer_dsn")],
    ),
    (
        "ducklake_telemetry_reader.tf",
        "TelemetryReaderDsnRead",
        "TelemetryReaderDsnParameterRead",
        1,
        [("TELEMETRY_DSN_PARAMETER", "TELEMETRY_DSN_SECRET_ID", "telemetry_reader_dsn")],
    ),
]


def _text(name: str) -> str:
    return (PERSONAL / name).read_text(encoding="utf-8")


def _secret_names() -> dict[str, str]:
    """Secrets Manager resource name -> secret name, across the personal root."""
    out: dict[str, str] = {}
    for path in PERSONAL.glob("*.tf"):
        for m in re.finditer(
            r'resource "aws_secretsmanager_secret" "(\w+)" \{\s*name\s*=\s*"([^"]+)"', path.read_text("utf-8")
        ):
            out[m.group(1)] = m.group(2)
    return out


def _sid_blocks(text: str, sid: str) -> list[str]:
    return re.findall(rf'Sid\s*=\s*"{sid}"(.*?)\n      \}}', text, re.S)


def _resource_names(block: str) -> list[str]:
    return re.findall(r"aws_secretsmanager_secret\.(\w+)\.arn", block)


def _parameter_arns(block: str) -> list[str]:
    return re.findall(r'"(arn:aws:ssm:[^"]*)"', block)


def _env_value(text: str, var: str) -> str:
    m = re.search(rf"^\s*{var}\s*=\s*(.+)$", text, re.M)
    assert m, var
    return m.group(1).strip()


class TestParameterNames:
    def test_nine_names_match_the_secret_names(self) -> None:
        text = _text("secret_parameters.tf")
        names = dict(re.findall(r'^\s*(\w+)\s*=\s*"(/secret/[^"]+)"', text, re.M))
        assert set(names) == set(_OUT_OF_BAND) | set(_SECRET_KEYS)
        secrets = _secret_names()
        for key, name in _OUT_OF_BAND.items():
            assert names[key] == f"/secret/{name}"
        for key, resource in _SECRET_KEYS.items():
            assert names[key] == f"/secret/{secrets[resource]}"

    def test_no_arn_local_is_declared(self) -> None:
        assert "arn:" not in re.sub(r"#[^\n]*", "", _text("secret_parameters.tf"))


class TestDsnParameter:
    def test_securestring_shares_the_secret_versions_document(self) -> None:
        text = _text("neon_ducklake_catalog.tf")
        param = re.search(r'resource "aws_ssm_parameter" "ducklake_neon_catalog_dsn" \{(.*?)\n\}', text, re.S)
        assert param
        body = param.group(1)
        assert re.search(r'name\s*=\s*"/secret/ducklake-neon-catalog-dsn"', body)
        assert re.search(r'type\s*=\s*"SecureString"', body)
        assert re.search(r'tier\s*=\s*"Standard"', body)
        assert "key_id" not in body
        assert re.search(r"value\s*=\s*local\.ducklake_neon_dsn_document\b", body)
        version = re.search(
            r'resource "aws_secretsmanager_secret_version" "ducklake_neon_catalog_dsn" \{(.*?)\n\}', text, re.S
        )
        assert version and re.search(r"secret_string\s*=\s*local\.ducklake_neon_dsn_document\b", version.group(1))
        assert text.count("jsonencode(") == 1

    def test_output_names_the_parameter(self) -> None:
        text = _text("neon_ducklake_catalog.tf")
        assert re.search(r'output "ducklake_neon_catalog_dsn_parameter_name"', text)
        assert "aws_ssm_parameter.ducklake_neon_catalog_dsn.name" in text


class TestRoleGrants:
    @pytest.mark.parametrize(("file", "sm_sid", "param_sid", "count", "env"), _ROLES)
    def test_parameter_grant_mirrors_the_secret_grant(
        self, file: str, sm_sid: str, param_sid: str, count: int, env: list
    ) -> None:
        text = _text(file)
        sm_blocks, param_blocks = _sid_blocks(text, sm_sid), _sid_blocks(text, param_sid)
        assert len(sm_blocks) == len(param_blocks) == count
        secrets = _secret_names()
        for sm_block, param_block in zip(sm_blocks, param_blocks, strict=True):
            want = [f"{_PREFIX}{secrets[r]}" for r in _resource_names(sm_block)]
            assert _parameter_arns(param_block) == want
            assert re.findall(r"Action\s*=\s*\[([^\]]*)\]", param_block) == ['"ssm:GetParameter"']
            assert re.search(r'Effect\s*=\s*"Allow"', param_block)
            assert "Condition" not in param_block

    @pytest.mark.parametrize(("file", "sm_sid", "param_sid", "count", "env"), _ROLES)
    def test_env_vars_sit_beside_the_old_ones(self, file: str, sm_sid: str, param_sid: str, count: int, env: list) -> None:
        text = _text(file)
        for var, old_var, key in env:
            assert _env_value(text, var) == f"local.secret_parameter_names.{key}"
            assert _env_value(text, old_var).startswith("aws_secretsmanager_secret.")

    def test_maintenance_reads_the_dsn_and_four_logins(self) -> None:
        block = _sid_blocks(_text("ducklake_maintenance.tf"), "NeonDsnParameterRead")[0]
        assert len(_parameter_arns(block)) == 5

    def test_every_parameter_grant_is_the_literal_template(self) -> None:
        for path in PERSONAL.glob("*.tf"):
            for arn in re.findall(r'"(arn:aws:ssm:[^"]*parameter/secret/[^"]*)"', path.read_text("utf-8")):
                assert re.fullmatch(
                    r"arn:aws:ssm:\$\{var\.aws_region\}:\$\{var\.account_id\}:parameter/secret/[a-z0-9-]+", arn
                ), (path.name, arn)


class TestPlannerReads:
    def test_tfvars_and_dsn_metadata_in_the_shared_refresh_surface(self) -> None:
        text = _text("oidc.tf")
        tfvars = _sid_in_hcl(text, "SecretParameterTfvarsRead")
        assert 'actions   = ["ssm:GetParameter"]' in tfvars
        assert _parameter_arns(tfvars) == [f"{_PREFIX}agent-platform-terraform-personal-tfvars"]
        meta = _sid_in_hcl(text, "SecretParameterMetadataRead")
        assert 'actions   = ["ssm:ListTagsForResource"]' in meta
        assert _parameter_arns(meta) == [f"{_PREFIX}ducklake-neon-catalog-dsn"]
        assert "condition" not in tfvars + meta

    def test_neon_key_and_dsn_reads_are_drift_session_only(self) -> None:
        stmt = _sid_in_hcl(_text("oidc_pipeline_roles.tf"), "DriftSessionSecretParameterRead")
        assert 'actions = ["ssm:GetParameter"]' in stmt
        assert _parameter_arns(stmt) == [f"{_PREFIX}neon-api-key", f"{_PREFIX}ducklake-neon-catalog-dsn"]
        assert '"*:${local.convergence_writer_session_name}"' in stmt
        assert 'variable = "aws:userid"' in stmt and 'test     = "StringLike"' in stmt

    def test_reserved_session_name_counts_six_sites(self) -> None:
        assert "EXACTLY 6 coupled sites" in _text("oidc.tf")
        pipeline = _text("oidc_pipeline_roles.tf")
        assert "site 6/6" in pipeline and "/5" not in "".join(re.findall(r"site \d/\d", pipeline))
        assert "coupled site 4/6" in (REPO / ".github/workflows/terraform-drift.yml").read_text("utf-8")


class TestTrustedPlanRole:
    def test_reads_exactly_the_neon_key_and_dsn_parameters_unconditioned(self) -> None:
        stmt = _sid_in_hcl(_text("oidc_trusted_plan_role.tf"), "PlanSecretParameterRead")
        assert 'actions = ["ssm:GetParameter"]' in stmt
        assert re.findall(r'"(arn:aws:ssm:[^"]*)"', stmt) == [f"{_PREFIX}neon-api-key", f"{_PREFIX}ducklake-neon-catalog-dsn"]
        assert "condition" not in stmt


def _sid_in_hcl(text: str, sid: str) -> str:
    m = re.search(rf'sid\s*=\s*"{sid}"(.*?)\n  \}}', text, re.S)
    assert m, sid
    return m.group(1)


class TestCoverageChecks:
    def _inputs(self, role: str):
        text = _vir._read_root_text(PERSONAL)
        resources, locals_map, attr_index = _scan_resources(PERSONAL)
        docs = _vir._parse_policy_documents(text)
        statements = {
            "trusted": _vir._resolve_statements("github_ci_trusted_plan", docs),
            ROLE_PLANNER: _resolve_role_statements(text)[ROLE_PLANNER],
        }[role]
        return resources, locals_map, attr_index, statements

    @staticmethod
    def _check(inputs, statements: list[dict], role: str):
        _, locals_map, attr_index, _ = inputs
        return _check_resource(
            "aws_ssm_parameter",
            "ducklake_neon_catalog_dsn",
            "neon_ducklake_catalog.tf",
            locals_map,
            attr_index,
            {role: statements},
            "k:",
        )

    @pytest.mark.parametrize(
        ("role", "sid"), [("trusted", "PlanSecretParameterRead"), (ROLE_PLANNER, "DriftSessionSecretParameterRead")]
    )
    def test_dsn_parameter_is_covered_and_losing_the_statement_is_a_finding(self, role: str, sid: str) -> None:
        inputs = self._inputs(role)
        statements = inputs[3]
        findings, checked = self._check(inputs, statements, role)
        assert checked and findings == []
        without = [s for s in statements if s.get("sid") != sid and s.get("Sid") != sid]
        assert len(without) == len(statements) - 1
        findings, checked = self._check(inputs, without, role)
        assert checked and len(findings) == 1 and f"the {role} role policy" in findings[0]


_FILES = [
    "terraform/personal/oidc.tf",
    "terraform/personal/oidc_pipeline_roles.tf",
    "terraform/personal/neon_ducklake_catalog.tf",
    ".github/workflows/terraform-apply-sandbox.yml",
    ".github/workflows/reconcile.yml",
    ".github/workflows/terraform-drift.yml",
    ".github/workflows/terraform-plan-trusted.yml",
    ".github/actions/terraform-init-retry/action.yml",
    ".github/actions/terraform-init-retry/neon_api_key_check.sh",
]


def _planner_check(root: Path) -> list[str]:
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root):
        validate_planner_secret_value_reads(failed)
    return failed


def test_planner_value_read_check_requires_the_drift_condition(tmp_path: Path) -> None:
    for rel in _FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest)
    assert _planner_check(tmp_path) == []
    path = tmp_path / "terraform/personal/oidc_pipeline_roles.tf"
    text = path.read_text("utf-8")
    start = text.index('sid     = "DriftSessionSecretParameterRead"')
    cond = text.index("    condition {", start)
    end = text.index("    }\n", cond) + len("    }\n")
    path.write_text(text[:cond] + text[end:], encoding="utf-8")
    failed = _planner_check(tmp_path)
    assert failed and any("DriftSessionSecretParameterRead" in f for f in failed)
