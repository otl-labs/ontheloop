"""Tests for the secret-material-handling gate (Decision 175 / T2.56 migration step 7)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks.iam_tf.validate_secret_material_handling import validate_secret_material_handling

_CONTRACT_REL = "docs/contracts/secret-material-handling.yaml"


def _write_contract(root: Path, value_exceptions: list[dict[str, str]]) -> None:
    contract_path = root / _CONTRACT_REL
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        yaml.safe_dump({"contract": {"id": "secret-material-handling"}, "value_exceptions": value_exceptions}),
        encoding="utf-8",
    )


def _write_terraform(root: Path, tf_by_relpath: dict[str, str]) -> None:
    for relpath, text in tf_by_relpath.items():
        p = root / "terraform" / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_real_tree_passes() -> None:
    """The real repository's two live exceptions are both declared -- no undeclared _version resource."""
    failed: list[str] = []
    validate_secret_material_handling(failed)
    assert failed == [], failed


def test_undeclared_version_resource_fails(tmp_path: Path) -> None:
    _write_contract(tmp_path, value_exceptions=[])
    _write_terraform(
        tmp_path,
        {
            "personal/rogue.tf": (
                'resource "aws_secretsmanager_secret_version" "rogue_value" {\n'
                '  secret_id     = "rogue"\n'
                '  secret_string = "in-state-material"\n'  # pragma: allowlist secret
                "}\n"
            )
        },
    )
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 1
    assert "aws_secretsmanager_secret_version.rogue_value" in failed[0]


def test_declared_exception_passes(tmp_path: Path) -> None:
    _write_contract(
        tmp_path,
        value_exceptions=[{"resource": "aws_secretsmanager_secret_version.rogue_value", "file": "x", "reason": "y"}],
    )
    _write_terraform(
        tmp_path,
        {
            "personal/rogue.tf": (
                'resource "aws_secretsmanager_secret_version" "rogue_value" {\n'
                '  secret_id     = "rogue"\n'
                '  secret_string = "in-state-material"\n'  # pragma: allowlist secret
                "}\n"
            )
        },
    )
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert failed == [], failed


def test_data_source_secret_version_fails(tmp_path: Path) -> None:
    """Decision 237 cl.3: a data source read of a secret puts the value in state, so any such block fails."""
    _write_contract(tmp_path, value_exceptions=[])
    _write_terraform(
        tmp_path,
        {"personal/read_only.tf": ('data "aws_secretsmanager_secret_version" "some_api_key" {\n  secret_id = "x"\n}\n')},
    )
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 1 and "Decision 237" in failed[0]


def _ssm(kind: str, name: str, body: str) -> str:
    return f'{kind} "aws_ssm_parameter" "{name}" {{\n{body}\n}}\n'


def test_undeclared_securestring_fails_and_declared_passes(tmp_path: Path) -> None:
    tf = _ssm("resource", "dsn", '  name = "/secret/dsn"\n  type = "SecureString"\n  value = var.v')
    _write_terraform(tmp_path, {"personal/p.tf": tf})
    _write_contract(tmp_path, value_exceptions=[])
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 1 and "aws_ssm_parameter.dsn" in failed[0]

    _write_contract(tmp_path, value_exceptions=[{"resource": "aws_ssm_parameter.dsn", "file": "x", "reason": "y"}])
    failed = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert failed == [], failed


def test_non_literal_type_fails_closed_and_plain_string_passes(tmp_path: Path) -> None:
    _write_contract(tmp_path, value_exceptions=[])
    _write_terraform(
        tmp_path,
        {
            "personal/a.tf": _ssm("resource", "dyn", '  name = "/x"\n  type = var.kind\n  value = "v"'),
            "personal/b.tf": _ssm("resource", "plain", '  name = "/agent-platform/x"\n  type = "String"\n  value = "v"'),
        },
    )
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 1 and "aws_ssm_parameter.dyn" in failed[0]


def test_data_ssm_parameter_rules(tmp_path: Path) -> None:
    _write_contract(tmp_path, value_exceptions=[])
    _write_terraform(
        tmp_path,
        {
            "personal/secret.tf": _ssm("data", "s", '  name = "/secret/neon-api-key"'),
            "personal/dynamic.tf": _ssm("data", "d", "  name = local.secret_parameter_names.neon"),
            "personal/ok.tf": _ssm("data", "ok", '  name = "/agent-platform/url"'),
        },
    )
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 2
    assert not any("data.aws_ssm_parameter.ok" in f for f in failed)


def test_missing_contract_skips(tmp_path: Path) -> None:
    (tmp_path / "terraform").mkdir()
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert failed == []


def test_contract_mutation_flips_verdict(tmp_path: Path) -> None:
    """Load-bearing case (Decision 168 clause 2): the verdict follows the CONTRACT, not hardcoded
    names. Mirrors the real tree's two live exceptions; dropping one from the fixture contract
    must make the check flag exactly that resource, proving it reads value_exceptions at runtime."""
    tf_by_relpath = {
        "personal/neon_ducklake_catalog.tf": (
            'resource "aws_secretsmanager_secret_version" "ducklake_neon_catalog_dsn" {\n'
            '  secret_id     = "ducklake-neon-catalog-dsn"\n'
            '  secret_string = "generated-by-neon-provider"\n'  # pragma: allowlist secret
            "}\n"
        ),
        "scheduled_agents.tf": (
            'resource "aws_secretsmanager_secret_version" "github_pat_placeholder" {\n'
            '  secret_id     = "agent-platform-github-pat"\n'
            '  secret_string = "PLACEHOLDER_SET_MANUALLY"\n'  # pragma: allowlist secret
            "}\n"
        ),
    }
    _write_terraform(tmp_path, tf_by_relpath)
    both_exceptions = [
        {"resource": "aws_secretsmanager_secret_version.ducklake_neon_catalog_dsn", "file": "x", "reason": "y"},
        {"resource": "aws_secretsmanager_secret_version.github_pat_placeholder", "file": "x", "reason": "y"},
    ]

    _write_contract(tmp_path, value_exceptions=both_exceptions)
    failed_before: list[str] = []
    validate_secret_material_handling(failed_before, root=tmp_path)
    assert failed_before == [], failed_before

    # Drop ducklake_neon_catalog_dsn from the fixture contract -- the verdict must flip.
    narrowed = [e for e in both_exceptions if "ducklake_neon_catalog_dsn" not in e["resource"]]
    _write_contract(tmp_path, value_exceptions=narrowed)
    failed_after: list[str] = []
    validate_secret_material_handling(failed_after, root=tmp_path)
    assert len(failed_after) == 1
    assert "ducklake_neon_catalog_dsn" in failed_after[0]
    assert "github_pat_placeholder" not in failed_after[0]


def test_real_contract_names_ssm_pattern_and_both_stores() -> None:
    real = yaml.safe_load((Path(__file__).resolve().parents[3] / _CONTRACT_REL).read_text(encoding="utf-8"))
    assert "/secret/" in real["patterns"]["secret_parameter_out_of_band"]
    assert "SSM Parameter" in real["patterns"]["runtime_fetch"] and "Secrets Manager" in real["patterns"]["runtime_fetch"]
    assert any("SSM secret parameters" in d for d in real["patterns"]["domains"])


def test_interpolated_data_name_and_by_path_data_fail(tmp_path: Path) -> None:
    _write_contract(tmp_path, value_exceptions=[])
    _write_terraform(
        tmp_path,
        {
            "personal/interp.tf": _ssm("data", "i", '  name = "${var.root}/neon-api-key"'),
            "personal/bypath.tf": _ssm("data", "b", '  path = "/secret/"').replace(
                "aws_ssm_parameter", "aws_ssm_parameters_by_path"
            ),
            "personal/ok.tf": _ssm("data", "ok", '  path = "/agent-platform/"').replace(
                "aws_ssm_parameter", "aws_ssm_parameters_by_path"
            ),
        },
    )
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 2 and not any("data.aws_ssm_parameters_by_path.ok" in f for f in failed)


def test_nested_tags_type_does_not_hide_a_securestring(tmp_path: Path) -> None:
    _write_contract(tmp_path, value_exceptions=[])
    body = '  name = "/secret/a"\n  tags = {\n    type = "String"\n  }\n  type = "SecureString"'
    _write_terraform(tmp_path, {"personal/p.tf": _ssm("resource", "sneaky", body)})
    failed: list[str] = []
    validate_secret_material_handling(failed, root=tmp_path)
    assert len(failed) == 1 and "aws_ssm_parameter.sneaky" in failed[0]
