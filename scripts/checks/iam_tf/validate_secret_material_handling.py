"""Secret-value-out-of-band gate (Decision 175 / docs/contracts/secret-material-handling.yaml).

Asserts that every `resource "aws_secretsmanager_secret_version"` and every SecureString
`resource "aws_ssm_parameter"` in the terraform tree is a declared exception in
secret-material-handling.yaml's `value_exceptions`. An undeclared one means a secret's VALUE
material entered Terraform state without the contract's sign-off (the SECRET-VALUE-OUT-OF-BAND
pattern that contract names). Also fails any `data "aws_secretsmanager_secret_version"` block and
any `data "aws_ssm_parameter"` block that reads a /secret/ parameter or whose name is not a string
literal (Decision 237 cl.3: no data source reads a secret; a data read puts the value in state).

Credential-free (pure text parsing over this repo's own terraform tree and its own contract) --
eligible for --pre and full tiers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir
from scripts.checks.iam_tf._secure_string_coverage import top_level_attr

# Path-shaped literal (evaluator_kinds.check, contract-population.yaml): a full joined literal is
# admitted as STRONGER evidence of reading than a bare basename match.
_CONTRACT_REL_PATH = "docs/contracts/secret-material-handling.yaml"

_VERSION_RESOURCE_RE = re.compile(r'resource\s+"aws_secretsmanager_secret_version"\s+"([a-zA-Z0-9_]+)"\s*\{')
_SSM_RESOURCE_RE = re.compile(r'resource\s+"aws_ssm_parameter"\s+"([a-zA-Z0-9_]+)"\s*\{')
_DATA_VERSION_RE = re.compile(r'data\s+"aws_secretsmanager_secret_version"\s+"([a-zA-Z0-9_]+)"\s*\{')
_DATA_SSM_RE = re.compile(r'data\s+"aws_ssm_parameter"\s+"([a-zA-Z0-9_]+)"\s*\{')
_DATA_BYPATH_RE = re.compile(r'data\s+"aws_ssm_parameters_by_path"\s+"([a-zA-Z0-9_]+)"\s*\{')


def _load_exceptions(contract_path: Path) -> set[str] | None:
    """Return the declared `resource` addresses from value_exceptions, or None if unreadable."""
    if not contract_path.exists():
        return None
    data: dict[str, Any] = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    exceptions = data.get("value_exceptions") or []
    return {str(e["resource"]) for e in exceptions if isinstance(e, dict) and "resource" in e}


def _plain_literal(raw: str) -> bool:
    """A quoted string with no interpolation: the only name form the scan can judge."""
    return raw.startswith('"') and raw.endswith('"') and "${" not in raw


def _scan(terraform_dir: Path) -> tuple[list[tuple[str, str, str]], int]:
    """Return ([(kind, address, relative_file)] for each undeclared-candidate block, blocks examined).

    kind is "value" (a resource whose value enters state, needs a value_exceptions entry) or "data_read" (always a finding).
    """
    found: list[tuple[str, str, str]] = []
    examined = 0
    for tf_path in sorted(terraform_dir.rglob("*.tf")):
        text = tf_path.read_text(encoding="utf-8")
        rel = str(tf_path.relative_to(terraform_dir.parent))
        for m in _VERSION_RESOURCE_RE.finditer(text):
            examined += 1
            found.append(("value", f"aws_secretsmanager_secret_version.{m.group(1)}", rel))
        for m in _SSM_RESOURCE_RE.finditer(text):
            examined += 1
            if top_level_attr(_vir._extract_block(text, m.end() - 1), "type") not in ('"String"', '"StringList"'):
                found.append(("value", f"aws_ssm_parameter.{m.group(1)}", rel))
        for m in _DATA_VERSION_RE.finditer(text):
            examined += 1
            found.append(("data_read", f"data.aws_secretsmanager_secret_version.{m.group(1)}", rel))
        for m in _DATA_SSM_RE.finditer(text):
            examined += 1
            raw = top_level_attr(_vir._extract_block(text, m.end() - 1), "name") or ""
            if not _plain_literal(raw) or raw.startswith('"/secret/'):
                found.append(("data_read", f"data.aws_ssm_parameter.{m.group(1)}", rel))
        for m in _DATA_BYPATH_RE.finditer(text):
            examined += 1
            raw = top_level_attr(_vir._extract_block(text, m.end() - 1), "path") or ""
            path = raw.strip('"')
            if not _plain_literal(raw) or "/secret/".startswith(path.rstrip("/") + "/") or path.startswith("/secret"):
                found.append(("data_read", f"data.aws_ssm_parameters_by_path.{m.group(1)}", rel))
    return found, examined


@registry.register("validate_secret_material_handling", owner="platform")
def validate_secret_material_handling(failed: list[str], root: Path | None = None) -> None:
    """Every secret-value RESOURCE is a declared exception in secret-material-handling.yaml's
    value_exceptions (read from the contract, never hardcoded) and no data source reads a secret.

    Two reachable exit paths, each with exactly one Decision 170 declaration: the contract file
    missing (or the terraform tree missing) `skipped()`s -- could not examine; the fall-through
    `examined(N, ...)`s the N secret blocks actually checked.
    """
    print("\n=== Secret-material-handling gate (Decision 175, 237) ===")
    key = "secret-material-handling:"
    root = root if root is not None else _common.ROOT

    contract_path = root / _CONTRACT_REL_PATH
    exceptions = _load_exceptions(contract_path)
    terraform_dir = root / "terraform"

    if exceptions is None or not terraform_dir.is_dir():
        reason = f"{contract_path} not found" if exceptions is None else f"{terraform_dir} not found"
        print(f"  SKIP: {reason}")
        registry.skipped(reason)
        return

    blocks, examined = _scan(terraform_dir)
    before = len(failed)
    for kind, addr, rel in blocks:
        if kind == "value" and addr in exceptions:
            continue
        if kind == "value":
            failed.append(
                f"{key} {addr} in {rel} is an undeclared secret-value resource (aws_secretsmanager_secret_version or "
                f"a SecureString or non-literal-type aws_ssm_parameter) -- either it is a genuine SECRET-VALUE-OUT-OF-BAND "
                f"violation, or it needs a value_exceptions entry in {_CONTRACT_REL_PATH} naming why the value is "
                "legitimately in-state."
            )
        else:
            failed.append(
                f"{key} {addr} in {rel} reads a secret through a data source (or a data aws_ssm_parameter whose name "
                "is not a string literal) -- Decision 237 cl.3: no Terraform data source reads a secret; the value "
                "would enter state."
            )
    for f in failed[before:]:
        print(f"  FAIL: {f}")
    if len(failed) == before:
        print(f"  PASS: {examined} secret block(s) examined; every value resource is a declared exception, no data read.")

    registry.examined(examined, unit="secret_material_blocks")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_secret_material_handling(_failed)
    for _f in _failed:
        print(f"  - {_f}")
    raise SystemExit(1 if _failed else 0)
