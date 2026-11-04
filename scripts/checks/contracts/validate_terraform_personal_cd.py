"""terraform-personal-cd contract guard (PLAN-terraform-personal-cd-split).

Three legs against the CD-pipeline contract split out of terraform-cc-web-operations.yaml: (1)
terraform/CLAUDE.md still carries a resolvable pointer to docs/contracts/terraform-personal-cd.yaml,
(2) every repo-relative path the contract's own `referenced_repo_paths` list declares exists, and (3)
no top-level section other than the shared scaffolding keys appears in both this contract and its
sibling terraform-cc-web-operations.yaml (the split boundary; Decision 86 one subject per file).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import _common, registry

_CONTRACT_REL_PATH = "docs/contracts/terraform-personal-cd.yaml"
_SIBLING_REL_PATH = "docs/contracts/terraform-cc-web-operations.yaml"
_TERRAFORM_CLAUDE_MD_REL_PATH = "terraform/CLAUDE.md"
_SHARED_SCAFFOLDING_KEYS = frozenset({"contract", "amendment_log", "referenced_repo_paths"})


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@registry.register("validate_terraform_personal_cd", owner="platform")
def validate_terraform_personal_cd(failed: list[str], *, repo_root: Path | None = None) -> None:
    """Fail if terraform/CLAUDE.md drops its pointer to terraform-personal-cd.yaml, if the contract
    names a repo path that does not exist, or if a section lives in both this contract and the
    terraform-cc-web-operations sibling."""
    print("\n=== terraform-personal-cd contract guard ===")
    root = repo_root if repo_root is not None else _common.ROOT

    contract_path = root / _CONTRACT_REL_PATH
    terraform_claude_md_path = root / _TERRAFORM_CLAUDE_MD_REL_PATH
    sibling_path = root / _SIBLING_REL_PATH

    if not contract_path.is_file():
        failed.append(f"terraform-personal-cd contract: {_CONTRACT_REL_PATH} not found")
        registry.skipped(f"{_CONTRACT_REL_PATH} not found")
        return

    if not terraform_claude_md_path.is_file():
        failed.append(f"terraform-personal-cd contract: {_TERRAFORM_CLAUDE_MD_REL_PATH} not found")
        registry.skipped(f"{_TERRAFORM_CLAUDE_MD_REL_PATH} not found")
        return

    try:
        contract_data = _load_yaml(contract_path)
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"terraform-personal-cd contract: could not parse {_CONTRACT_REL_PATH}: {exc}")
        registry.skipped(f"{_CONTRACT_REL_PATH} failed to parse")
        return

    pointer_ok = _CONTRACT_REL_PATH in terraform_claude_md_path.read_text(encoding="utf-8")
    if not pointer_ok:
        failed.append(
            f"terraform-personal-cd contract: {_TERRAFORM_CLAUDE_MD_REL_PATH} no longer points at {_CONTRACT_REL_PATH}"
        )

    named_paths = contract_data.get("referenced_repo_paths") if isinstance(contract_data, dict) else None
    if not isinstance(named_paths, list) or not named_paths:
        failed.append(f"terraform-personal-cd contract: {_CONTRACT_REL_PATH} missing or empty 'referenced_repo_paths'")
        named_paths = []

    missing = sorted(str(p) for p in named_paths if not (root / str(p)).exists())
    if missing:
        failed.append(f"terraform-personal-cd contract: {_CONTRACT_REL_PATH} names path(s) that do not exist: {missing}")

    registry.examined(len(named_paths), unit="named paths")

    if pointer_ok and not missing and named_paths:
        print(
            f"  PASS: {_TERRAFORM_CLAUDE_MD_REL_PATH} points at {_CONTRACT_REL_PATH}; "
            f"all {len(named_paths)} named path(s) exist."
        )

    _validate_disjoint_sections(failed, contract_data, sibling_path)


def _validate_disjoint_sections(failed: list[str], contract_data: object, sibling_path: Path) -> None:
    if not sibling_path.is_file():
        print(f"  SKIP: {_SIBLING_REL_PATH} not found; split-boundary leg not checked.")
        return
    try:
        sibling_data = _load_yaml(sibling_path)
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"terraform-personal-cd contract: could not parse {_SIBLING_REL_PATH}: {exc}")
        return
    if not isinstance(contract_data, dict) or not isinstance(sibling_data, dict):
        return
    shared = sorted((set(contract_data) & set(sibling_data)) - _SHARED_SCAFFOLDING_KEYS)
    if shared:
        failed.append(
            f"terraform-personal-cd contract: section(s) {shared} appear in both {_CONTRACT_REL_PATH} and {_SIBLING_REL_PATH}"
        )
    else:
        print(f"  PASS: no section is in both {_CONTRACT_REL_PATH} and {_SIBLING_REL_PATH}.")
