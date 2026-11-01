"""terraform-cc-web-operations contract pointer-resolution guard
(PLAN-ambient-prose-contract-relocation).

Mirrors validate_git_ops_contract.py's two-leg design against the terraform-side pair: (1)
terraform/CLAUDE.md still carries a resolvable pointer to
docs/contracts/terraform-cc-web-operations.yaml, and (2) every repo-relative path the contract's
own `referenced_repo_paths` list declares exists in the working tree.

A third, bidirectional leg (PLAN-tfvars-recovery-contract-closure) closes the tfvars-recovery
table against the live module: (3a) every no-default, non-ephemeral variable declared in
terraform/personal/*.tf appears in the contract's `where_values_live` per-variable mapping, and
(3b) every key listed there is still declared in the module -- catching a stale entry for a
retired variable, not merely an undocumented new one. Ephemeral no-default variables
(`ephemeral = true`, plan neon-key-out-of-pr-plans) are never in the tfvars secret: they are
checked the same two ways against `ephemeral_runtime_vars` and must not appear in
`where_values_live`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.checks import _common, registry

_CONTRACT_REL_PATH = "docs/contracts/terraform-cc-web-operations.yaml"
_TERRAFORM_CLAUDE_MD_REL_PATH = "terraform/CLAUDE.md"
_NO_DEFAULT_MODULE_REL_DIR = "terraform/personal"

_VARIABLE_BLOCK_RE = re.compile(r'variable\s+"([A-Za-z0-9_-]+)"\s*\{')
_DEFAULT_ASSIGNMENT_RE = re.compile(r"^\s*default\s*=")
_EPHEMERAL_ASSIGNMENT_RE = re.compile(r"^\s*ephemeral\s*=\s*true\b")


def _extract_brace_block(text: str, open_index: int) -> tuple[str, int]:
    """Return (body, index_after_close) for the `{...}` block starting at text[open_index].

    Brace-depth-aware: tracks nesting so an inner block's braces don't prematurely close the
    outer one.
    """
    depth = 0
    i = open_index
    body_start = open_index + 1
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:i], i + 1
        i += 1
    raise ValueError(f"unbalanced braces starting at index {open_index}")


def _has_top_level_attr(body: str, pattern: re.Pattern[str]) -> bool:
    """True if `body` (a variable block's interior) matches `pattern` at depth 0.

    A key nested inside a sub-block (e.g. `validation { ... }`) is never mistaken for the
    variable's own attribute -- only a depth-0 line counts.
    """
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "//")):
            continue
        if depth == 0 and pattern.match(line):
            return True
        depth += line.count("{") - line.count("}")
    return False


def _has_top_level_default(body: str) -> bool:
    return _has_top_level_attr(body, _DEFAULT_ASSIGNMENT_RE)


def _declared_no_default_variables(module_dir: Path, *, ephemeral: bool | None = None) -> set[str]:
    """Return the names of variables declared in `module_dir`/*.tf with no top-level default.

    `ephemeral` None returns every no-default variable; True only those declaring `ephemeral = true`;
    False only the rest.
    """
    no_default: set[str] = set()
    for tf_file in sorted(module_dir.glob("*.tf")):
        text = tf_file.read_text(encoding="utf-8")
        for match in _VARIABLE_BLOCK_RE.finditer(text):
            name = match.group(1)
            body, _ = _extract_brace_block(text, match.end() - 1)
            if _has_top_level_default(body):
                continue
            if ephemeral is not None and _has_top_level_attr(body, _EPHEMERAL_ASSIGNMENT_RE) != ephemeral:
                continue
            no_default.add(name)
    return no_default


@registry.register("validate_terraform_cc_web_operations", owner="platform")
def validate_terraform_cc_web_operations(failed: list[str], *, repo_root: Path | None = None) -> None:
    """Fail if terraform/CLAUDE.md drops its pointer to terraform-cc-web-operations.yaml, if
    the contract names a repo path that does not exist, or if the tfvars-recovery table and the
    live terraform/personal module's no-default variables have drifted apart in either direction."""
    print("\n=== terraform-cc-web-operations contract pointer resolution ===")
    root = repo_root if repo_root is not None else _common.ROOT

    contract_path = root / _CONTRACT_REL_PATH
    terraform_claude_md_path = root / _TERRAFORM_CLAUDE_MD_REL_PATH

    if not contract_path.is_file():
        failed.append(f"terraform-cc-web-operations contract: {_CONTRACT_REL_PATH} not found")
        registry.skipped(f"{_CONTRACT_REL_PATH} not found")
        return

    if not terraform_claude_md_path.is_file():
        failed.append(f"terraform-cc-web-operations contract: {_TERRAFORM_CLAUDE_MD_REL_PATH} not found")
        registry.skipped(f"{_TERRAFORM_CLAUDE_MD_REL_PATH} not found")
        return

    try:
        contract_data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"terraform-cc-web-operations contract: could not parse {_CONTRACT_REL_PATH}: {exc}")
        registry.skipped(f"{_CONTRACT_REL_PATH} failed to parse")
        return

    terraform_claude_md_text = terraform_claude_md_path.read_text(encoding="utf-8")
    if _CONTRACT_REL_PATH not in terraform_claude_md_text:
        failed.append(
            f"terraform-cc-web-operations contract: {_TERRAFORM_CLAUDE_MD_REL_PATH} no longer points at {_CONTRACT_REL_PATH}"
        )

    named_paths = contract_data.get("referenced_repo_paths") if isinstance(contract_data, dict) else None
    if not isinstance(named_paths, list) or not named_paths:
        failed.append(f"terraform-cc-web-operations contract: {_CONTRACT_REL_PATH} missing or empty 'referenced_repo_paths'")
        named_paths = []

    missing = sorted(str(p) for p in named_paths if not (root / str(p)).exists())
    if missing:
        failed.append(f"terraform-cc-web-operations contract: {_CONTRACT_REL_PATH} names path(s) that do not exist: {missing}")

    registry.examined(len(named_paths), unit="named paths")

    if _CONTRACT_REL_PATH in terraform_claude_md_text and not missing and named_paths:
        print(
            f"  PASS: {_TERRAFORM_CLAUDE_MD_REL_PATH} points at {_CONTRACT_REL_PATH}; "
            f"all {len(named_paths)} named path(s) exist."
        )

    _validate_recovery_table_coverage(failed, contract_data, root)


def _validate_recovery_table_coverage(failed: list[str], contract_data: object, root: Path) -> None:
    """Third leg (Decision 170: reports its examined population via stdout, since
    `registry.examined()` is a single per-dispatch slot already spent on `referenced_repo_paths`
    above)."""
    module_dir = root / _NO_DEFAULT_MODULE_REL_DIR
    if not module_dir.is_dir():
        print(f"  SKIP: {_NO_DEFAULT_MODULE_REL_DIR}/ not found; recovery-table coverage not checked.")
        return

    declared = _declared_no_default_variables(module_dir, ephemeral=False)
    declared_ephemeral = _declared_no_default_variables(module_dir, ephemeral=True)
    print(
        f"  Examined {len(declared) + len(declared_ephemeral)} no-default variable(s) declared in "
        f"{_NO_DEFAULT_MODULE_REL_DIR}/*.tf."
    )

    tfvars_recovery = contract_data.get("tfvars_and_remote_state_recovery") if isinstance(contract_data, dict) else None
    where_values_live = tfvars_recovery.get("where_values_live") if isinstance(tfvars_recovery, dict) else None

    if not isinstance(where_values_live, dict):
        failed.append(
            "terraform-cc-web-operations contract: tfvars_and_remote_state_recovery.where_values_live "
            "is not a per-variable mapping"
        )
        return

    ephemeral_runtime_vars = (tfvars_recovery or {}).get("ephemeral_runtime_vars", {})
    if not isinstance(ephemeral_runtime_vars, dict):
        failed.append(
            "terraform-cc-web-operations contract: tfvars_and_remote_state_recovery.ephemeral_runtime_vars "
            "is not a per-variable mapping"
        )
        return

    listed = {k for k in where_values_live if k != "description"}
    ephemeral_listed = {k for k in ephemeral_runtime_vars if k != "description"}
    ephemeral_in_tfvars_table = sorted(listed & declared_ephemeral)
    missing_from_table = sorted(declared - listed)
    stale_in_table = sorted(listed - declared - set(ephemeral_in_tfvars_table))
    missing_ephemeral = sorted(declared_ephemeral - ephemeral_listed)
    stale_ephemeral = sorted(ephemeral_listed - declared_ephemeral)

    if ephemeral_in_tfvars_table:
        failed.append(
            "terraform-cc-web-operations contract: where_values_live lists ephemeral variable(s) "
            f"{ephemeral_in_tfvars_table} -- ephemeral values are never in the tfvars secret; list them under "
            "ephemeral_runtime_vars"
        )
    if missing_from_table:
        failed.append(
            "terraform-cc-web-operations contract: where_values_live is missing declared "
            f"no-default variable(s): {missing_from_table}"
        )
    if stale_in_table:
        failed.append(
            "terraform-cc-web-operations contract: where_values_live lists variable(s) no "
            f"longer declared in {_NO_DEFAULT_MODULE_REL_DIR}: {stale_in_table}"
        )
    if missing_ephemeral:
        failed.append(
            "terraform-cc-web-operations contract: ephemeral_runtime_vars is missing declared "
            f"ephemeral no-default variable(s): {missing_ephemeral}"
        )
    if stale_ephemeral:
        failed.append(
            "terraform-cc-web-operations contract: ephemeral_runtime_vars lists variable(s) the module no "
            f"longer declares as ephemeral in {_NO_DEFAULT_MODULE_REL_DIR}: {stale_ephemeral}"
        )
    if not (ephemeral_in_tfvars_table or missing_from_table or stale_in_table or missing_ephemeral or stale_ephemeral):
        print(
            f"  PASS: where_values_live covers exactly the {len(declared)} non-ephemeral no-default variable(s); "
            f"ephemeral_runtime_vars covers the {len(declared_ephemeral)} ephemeral one(s)."
        )
