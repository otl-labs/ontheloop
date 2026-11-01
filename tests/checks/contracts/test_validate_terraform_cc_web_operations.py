"""Tests for validate_terraform_cc_web_operations() -- PLAN-ambient-prose-contract-relocation.

Mirrors test_validate_git_ops_contract.py's coverage against the terraform-side pair: green
path (terraform/CLAUDE.md points at the contract, every referenced_repo_paths entry exists),
the pointer-removed red path, and the dangling-path red path -- the two failure modes
test_obligations names for this check -- plus missing-contract, missing-terraform/CLAUDE.md,
malformed-YAML, and missing/empty referenced_repo_paths edge cases.

PLAN-tfvars-recovery-contract-closure adds coverage for the third, bidirectional leg: the
brace-depth-aware no-default-variable HCL parser (TestNoDefaultVariableParser) and the three
red paths -- a declared variable absent from the table, a table entry for a retired variable,
and a scalar (pre-restructure) where_values_live -- plus a matching-table green path
(TestRecoveryTableCoverage).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.contracts.validate_terraform_cc_web_operations import (
    _declared_no_default_variables,
    _extract_brace_block,
    validate_terraform_cc_web_operations,
)

_CONTRACT_REL_PATH = "docs/contracts/terraform-cc-web-operations.yaml"
_TERRAFORM_CLAUDE_MD_REL_PATH = "terraform/CLAUDE.md"

_SIMPLE_NO_DEFAULT_VAR_TF = 'variable "widget_id" {\n  type = string\n}\n'


def _write_repo(
    tmp_path: Path,
    *,
    terraform_claude_md_text: str = f"See `{_CONTRACT_REL_PATH}` for the full CC-web operating procedure.\n",
    referenced_repo_paths: list[str] | None = None,
    create_referenced_files: bool = True,
    write_contract: bool = True,
    write_terraform_claude_md: bool = True,
    tfvars_recovery: dict | None = None,
    module_tf_files: dict[str, str] | None = None,
) -> Path:
    if referenced_repo_paths is None:
        referenced_repo_paths = ["some/real/file.py", "another/real-file.yaml"]

    if create_referenced_files:
        for rel in referenced_repo_paths:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("placeholder\n", encoding="utf-8")

    if write_contract:
        contract_dir = tmp_path / "docs" / "contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        contract_body = {
            "contract": {
                "id": "terraform-cc-web-operations",
                "class": "D",
                "contract_version": 1,
                "status": "ratified",
            },
            "referenced_repo_paths": referenced_repo_paths,
        }
        if tfvars_recovery is not None:
            contract_body["tfvars_and_remote_state_recovery"] = tfvars_recovery
        (contract_dir / "terraform-cc-web-operations.yaml").write_text(yaml.safe_dump(contract_body), encoding="utf-8")

    if write_terraform_claude_md:
        terraform_dir = tmp_path / "terraform"
        terraform_dir.mkdir(parents=True, exist_ok=True)
        (terraform_dir / "CLAUDE.md").write_text(terraform_claude_md_text, encoding="utf-8")

    if module_tf_files is not None:
        module_dir = tmp_path / "terraform" / "personal"
        module_dir.mkdir(parents=True, exist_ok=True)
        for name, content in module_tf_files.items():
            (module_dir / name).write_text(content, encoding="utf-8")

    return tmp_path


class TestGreenPath:
    def test_pointer_present_and_all_paths_exist_passes(self, tmp_path: Path) -> None:
        _write_repo(tmp_path)

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert failed == []


class TestPointerRemovedRedPath:
    def test_terraform_claude_md_missing_pointer_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, terraform_claude_md_text="Some unrelated content with no pointer.\n")

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("no longer points at" in f for f in failed)

    def test_terraform_claude_md_missing_file_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, write_terraform_claude_md=False)

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("terraform/CLAUDE.md not found" in f for f in failed)


class TestDanglingPathRedPath:
    def test_named_path_that_does_not_exist_fails(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            referenced_repo_paths=["real/file.py", "ghost/does-not-exist.py"],
            create_referenced_files=False,
        )
        (tmp_path / "real").mkdir(parents=True)
        (tmp_path / "real" / "file.py").write_text("x\n", encoding="utf-8")

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("ghost/does-not-exist.py" in f for f in failed)

    def test_all_named_paths_missing_fails_with_full_list(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            referenced_repo_paths=["missing/one.py", "missing/two.yaml"],
            create_referenced_files=False,
        )

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("missing/one.py" in f and "missing/two.yaml" in f for f in failed)


class TestContractShapeEdgeCases:
    def test_contract_missing_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, write_contract=False)

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any(_CONTRACT_REL_PATH in f and "not found" in f for f in failed)

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path)
        (tmp_path / "docs" / "contracts" / "terraform-cc-web-operations.yaml").write_text(
            "key: [unterminated\n", encoding="utf-8"
        )

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("could not parse" in f for f in failed)

    def test_missing_referenced_repo_paths_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path)
        contract_dir = tmp_path / "docs" / "contracts"
        contract_body = {
            "contract": {
                "id": "terraform-cc-web-operations",
                "class": "D",
                "contract_version": 1,
                "status": "ratified",
            }
        }
        (contract_dir / "terraform-cc-web-operations.yaml").write_text(yaml.safe_dump(contract_body), encoding="utf-8")

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("missing or empty" in f for f in failed)

    def test_empty_referenced_repo_paths_fails(self, tmp_path: Path) -> None:
        _write_repo(tmp_path, referenced_repo_paths=[], create_referenced_files=False)

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("missing or empty" in f for f in failed)


class TestNoDefaultVariableParser:
    def test_variable_without_default_is_no_default(self, tmp_path: Path) -> None:
        (tmp_path / "a.tf").write_text(_SIMPLE_NO_DEFAULT_VAR_TF, encoding="utf-8")

        assert _declared_no_default_variables(tmp_path) == {"widget_id"}

    def test_variable_with_top_level_default_is_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "a.tf").write_text('variable "widget_id" {\n  type    = string\n  default = "x"\n}\n', encoding="utf-8")

        assert _declared_no_default_variables(tmp_path) == set()

    def test_nested_validation_block_default_key_not_mistaken_for_top_level_default(self, tmp_path: Path) -> None:
        # A nested `validation { ... }` block with its own keys must not fool the depth-0 scan --
        # this is the exact shape terraform/personal/variables.tf's account_id variable uses.
        (tmp_path / "a.tf").write_text(
            'variable "widget_id" {\n'
            "  type = string\n"
            "\n"
            "  validation {\n"
            '    condition     = can(regex("^[0-9]{12}$", var.widget_id))\n'
            '    error_message = "bad"\n'
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        assert _declared_no_default_variables(tmp_path) == {"widget_id"}

    def test_multiple_files_and_variables_combine(self, tmp_path: Path) -> None:
        (tmp_path / "a.tf").write_text(_SIMPLE_NO_DEFAULT_VAR_TF, encoding="utf-8")
        (tmp_path / "b.tf").write_text(
            'variable "has_default" {\n  type    = number\n  default = 1\n}\n\n'
            'variable "also_no_default" {\n  type = string\n}\n',
            encoding="utf-8",
        )

        assert _declared_no_default_variables(tmp_path) == {"widget_id", "also_no_default"}

    def test_comment_line_inside_body_is_skipped_not_mistaken_for_default(self, tmp_path: Path) -> None:
        # A bare comment line (e.g. "# No default: ..." -- the exact shape
        # terraform/personal/sns_alerts.tf's alerts_email variable uses) must be skipped rather
        # than matched against the default-assignment regex or counted for brace depth.
        (tmp_path / "a.tf").write_text(
            'variable "widget_id" {\n  type = string\n  # No default: set via tfvars.\n}\n',
            encoding="utf-8",
        )

        assert _declared_no_default_variables(tmp_path) == {"widget_id"}

    def test_unbalanced_braces_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unbalanced braces"):
            _extract_brace_block('variable "widget_id" {\n  type = string\n', 21)


class TestRecoveryTableCoverage:
    def test_declared_variable_absent_from_table_fails(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            module_tf_files={"variables.tf": _SIMPLE_NO_DEFAULT_VAR_TF},
            tfvars_recovery={"where_values_live": {"description": "per-variable recovery source"}},
        )

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("missing declared no-default variable" in f and "widget_id" in f for f in failed)

    def test_table_lists_undeclared_variable_fails(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            module_tf_files={"variables.tf": _SIMPLE_NO_DEFAULT_VAR_TF},
            tfvars_recovery={
                "where_values_live": {
                    "description": "per-variable recovery source",
                    "widget_id": {"declared_in": "terraform/personal/variables.tf", "source": "state"},
                    "retired_var": {"declared_in": "terraform/personal/variables.tf", "source": "state"},
                }
            },
        )

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("no longer declared" in f and "retired_var" in f for f in failed)

    def test_scalar_where_values_live_fails(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            module_tf_files={"variables.tf": _SIMPLE_NO_DEFAULT_VAR_TF},
            tfvars_recovery={"where_values_live": "everything lives in the state, somewhere"},
        )

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert any("not a per-variable mapping" in f for f in failed)

    def test_matching_table_passes(self, tmp_path: Path) -> None:
        _write_repo(
            tmp_path,
            module_tf_files={"variables.tf": _SIMPLE_NO_DEFAULT_VAR_TF},
            tfvars_recovery={
                "where_values_live": {
                    "description": "per-variable recovery source",
                    "widget_id": {"declared_in": "terraform/personal/variables.tf", "source": "state"},
                }
            },
        )

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert failed == []

    def test_module_dir_absent_does_not_fail(self, tmp_path: Path) -> None:
        # No module_tf_files -> terraform/personal/ never created under tmp_path; the leg must
        # skip rather than fail so this test suite's other fixtures (which don't set up a module
        # dir at all) stay green.
        _write_repo(tmp_path)

        failed: list[str] = []
        validate_terraform_cc_web_operations(failed, repo_root=tmp_path)

        assert failed == []


_EPHEMERAL_VAR_TF = 'variable "neon_api_key" {\n  type      = string\n  ephemeral = true\n  sensitive = true\n}\n'
_EPHEMERAL_ENTRY = {"declared_in": "terraform/personal/variables.tf", "source": "fetched at run time"}
_WIDGET_ENTRY = {"declared_in": "terraform/personal/variables.tf", "source": "state"}


def _ephemeral_repo(
    tmp_path: Path, *, where_values_live: dict, ephemeral_runtime_vars: dict | None, tf: str = _EPHEMERAL_VAR_TF
) -> Path:
    recovery: dict = {"where_values_live": {"description": "per-variable recovery source", **where_values_live}}
    if ephemeral_runtime_vars is not None:
        recovery["ephemeral_runtime_vars"] = ephemeral_runtime_vars
    return _write_repo(
        tmp_path,
        module_tf_files={"variables.tf": _SIMPLE_NO_DEFAULT_VAR_TF + tf},
        tfvars_recovery=recovery,
    )


def test_ephemeral_var_listed_under_ephemeral_runtime_vars_passes(tmp_path: Path) -> None:
    _ephemeral_repo(
        tmp_path, where_values_live={"widget_id": _WIDGET_ENTRY}, ephemeral_runtime_vars={"neon_api_key": _EPHEMERAL_ENTRY}
    )
    failed: list[str] = []
    validate_terraform_cc_web_operations(failed, repo_root=tmp_path)
    assert failed == []


def test_ephemeral_var_listed_under_where_values_live_fails(tmp_path: Path) -> None:
    _ephemeral_repo(
        tmp_path,
        where_values_live={"widget_id": _WIDGET_ENTRY, "neon_api_key": _EPHEMERAL_ENTRY},
        ephemeral_runtime_vars={"neon_api_key": _EPHEMERAL_ENTRY},
    )
    failed: list[str] = []
    validate_terraform_cc_web_operations(failed, repo_root=tmp_path)
    assert any("where_values_live lists ephemeral variable" in f and "neon_api_key" in f for f in failed)


def test_ephemeral_var_missing_from_both_fails(tmp_path: Path) -> None:
    _ephemeral_repo(tmp_path, where_values_live={"widget_id": _WIDGET_ENTRY}, ephemeral_runtime_vars=None)
    failed: list[str] = []
    validate_terraform_cc_web_operations(failed, repo_root=tmp_path)
    assert any("ephemeral_runtime_vars is missing" in f and "neon_api_key" in f for f in failed)


def test_stale_ephemeral_runtime_vars_entry_fails(tmp_path: Path) -> None:
    _ephemeral_repo(
        tmp_path,
        where_values_live={"widget_id": _WIDGET_ENTRY},
        ephemeral_runtime_vars={"neon_api_key": _EPHEMERAL_ENTRY, "retired_key": _EPHEMERAL_ENTRY},
    )
    failed: list[str] = []
    validate_terraform_cc_web_operations(failed, repo_root=tmp_path)
    assert any("ephemeral_runtime_vars lists variable" in f and "retired_key" in f for f in failed)


def test_ephemeral_entry_for_non_ephemeral_variable_fails(tmp_path: Path) -> None:
    _ephemeral_repo(
        tmp_path,
        where_values_live={"widget_id": _WIDGET_ENTRY},
        ephemeral_runtime_vars={"neon_api_key": _EPHEMERAL_ENTRY},
        tf='variable "neon_api_key" {\n  type = string\n}\n',
    )
    failed: list[str] = []
    validate_terraform_cc_web_operations(failed, repo_root=tmp_path)
    assert failed


def test_scalar_ephemeral_runtime_vars_fails(tmp_path: Path) -> None:
    _ephemeral_repo(tmp_path, where_values_live={"widget_id": _WIDGET_ENTRY}, ephemeral_runtime_vars="not a mapping")  # type: ignore[arg-type]
    failed: list[str] = []
    validate_terraform_cc_web_operations(failed, repo_root=tmp_path)
    assert any("ephemeral_runtime_vars is not a per-variable mapping" in f for f in failed)
