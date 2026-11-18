"""git-ops contract pointer-resolution guard (PLAN-ambient-prose-contract-relocation).

Three legs: (1) AGENTS.md still carries a resolvable pointer to docs/contracts/git-ops.yaml (the
Class D contract this module itself names, satisfying evaluator_kinds.check's basename-match
rule) that names a `#<key>` anchor, (2) every repo-relative path the contract's own
`referenced_repo_paths` list declares exists in the working tree -- so a later relocation that drops
or renames a target is caught instead of silently breaking the pointer's promise, and (3) every
`git-ops.yaml#<key>` anchor in AGENTS.md, .claude/commands/*.md and .claude/skills/*/SKILL.md
resolves by walking mapping keys of the parsed contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.checks import _common, registry

_CONTRACT_REL_PATH = "docs/contracts/git-ops.yaml"
_AGENTS_MD_REL_PATH = "AGENTS.md"
_ANCHOR_RE = re.compile(r"git-ops\.yaml#([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
_ANCHOR_SURFACE_GLOBS = (".claude/commands/*.md", ".claude/skills/*/SKILL.md")


def _key_resolves(data: object, dotted: str) -> bool:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _surfaces(root: Path) -> list[Path]:
    paths = [root / _AGENTS_MD_REL_PATH]
    for pattern in _ANCHOR_SURFACE_GLOBS:
        paths.extend(sorted(root.glob(pattern)))
    return paths


@registry.register("validate_git_ops_contract", owner="platform")
def validate_git_ops_contract(failed: list[str], *, repo_root: Path | None = None) -> None:
    """Fail if AGENTS.md drops its pointer to git-ops.yaml, or if the contract names a repo path
    that does not exist."""
    print("\n=== git-ops contract pointer resolution ===")
    root = repo_root if repo_root is not None else _common.ROOT

    contract_path = root / _CONTRACT_REL_PATH
    agents_md_path = root / _AGENTS_MD_REL_PATH

    if not contract_path.is_file():
        failed.append(f"git-ops contract: {_CONTRACT_REL_PATH} not found")
        registry.skipped(f"{_CONTRACT_REL_PATH} not found")
        return

    if not agents_md_path.is_file():
        failed.append(f"git-ops contract: {_AGENTS_MD_REL_PATH} not found")
        registry.skipped(f"{_AGENTS_MD_REL_PATH} not found")
        return

    try:
        contract_data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"git-ops contract: could not parse {_CONTRACT_REL_PATH}: {exc}")
        registry.skipped(f"{_CONTRACT_REL_PATH} failed to parse")
        return

    agents_text = agents_md_path.read_text(encoding="utf-8")
    agents_anchors = _ANCHOR_RE.findall(agents_text)
    if _CONTRACT_REL_PATH not in agents_text:
        failed.append(f"git-ops contract: {_AGENTS_MD_REL_PATH} no longer points at {_CONTRACT_REL_PATH}")
    elif not agents_anchors:
        failed.append(f"git-ops contract: {_AGENTS_MD_REL_PATH} points at {_CONTRACT_REL_PATH} without a #<key> anchor")

    anchors_checked = 0
    unresolved = 0
    for surface in _surfaces(root):
        for key in _ANCHOR_RE.findall(surface.read_text(encoding="utf-8")):
            anchors_checked += 1
            if not _key_resolves(contract_data, key):
                unresolved += 1
                failed.append(
                    f"git-ops contract: {surface.relative_to(root).as_posix()} anchor git-ops.yaml#{key} does not resolve"
                )

    named_paths = contract_data.get("referenced_repo_paths") if isinstance(contract_data, dict) else None
    if not isinstance(named_paths, list) or not named_paths:
        failed.append(f"git-ops contract: {_CONTRACT_REL_PATH} missing or empty 'referenced_repo_paths'")
        named_paths = []

    missing = sorted(str(p) for p in named_paths if not (root / str(p)).exists())
    if missing:
        failed.append(f"git-ops contract: {_CONTRACT_REL_PATH} names path(s) that do not exist: {missing}")

    registry.examined(len(named_paths) + anchors_checked, unit="named paths and anchors")

    if agents_anchors and not missing and not unresolved and named_paths:
        print(
            f"  PASS: {_AGENTS_MD_REL_PATH} points at {_CONTRACT_REL_PATH}#{agents_anchors[0]}; "
            f"{anchors_checked} anchor(s) resolve; all {len(named_paths)} named path(s) exist."
        )
