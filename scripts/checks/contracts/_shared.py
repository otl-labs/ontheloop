"""Constants and helpers shared by the contracts checks within this package only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

from scripts.checks import _common
from scripts.contract_rules import parse_yaml

_CONTRACTS_TREE_SPEC = "docs/contracts"


def merge_base_path_set(root: Path, merge_base: str) -> set[str] | None:
    """One `git ls-tree -r --name-only <merge_base> -- docs/contracts` read.

    Returns the full set of repo-relative paths present under docs/contracts/ at `merge_base`
    (any depth, any extension) -- the SAME ref Pass 2 resolves, so this reads a consistent
    baseline with the rest of the gate. Returns None on a git-level failure (unreachable ref,
    detached checkout); an EMPTY set is a genuine "nothing there" answer, distinguishable from
    the None failure sentinel.

    No module-level memoization: a cache keyed on `root` alone would be populated by the first
    test fixture to call this and then silently ignore every later monkeypatched `_common.run`,
    making the suite order-dependent and vacuously green. Each call is one fresh git read.
    """
    result = _common.run(
        ["git", "ls-tree", "-r", "--name-only", merge_base, "--", _CONTRACTS_TREE_SPEC],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
    )
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def rename_map(root: Path, merge_base: str) -> dict[str, str] | None:
    """One `git diff -M --name-status <merge_base> HEAD -- docs/contracts` read.

    Returns {new_path: old_path} for every rename git detects (past its ~50% similarity
    default) under docs/contracts/ between `merge_base` and HEAD. Returns None on a git-level
    failure; an empty dict is a genuine "no renames" answer.
    """
    result = _common.run(
        ["git", "diff", "-M", "--name-status", merge_base, "HEAD", "--", _CONTRACTS_TREE_SPEC],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
    )
    if result.returncode != 0:
        return None
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            _status, old_path, new_path = parts
            out[new_path] = old_path
    return out


def _parse_batch_output(text: str, refs: list[str]) -> dict[str, str | None]:
    """Parse `git cat-file --batch` stdout for the given `refs`, in the order they were fed.

    Each object is either `<sha> <type> <size>\\n<content>\\n` (content is exactly `size`
    characters, followed by one trailing newline) or `<ref> missing\\n`. Cursor-based, not
    regex-over-the-whole-blob, because blob content can itself contain lines that look like a
    header -- only a position-tracked parse is safe.
    """
    out: dict[str, str | None] = {}
    pos = 0
    for ref in refs:
        newline_idx = text.index("\n", pos)
        header = text[pos:newline_idx]
        pos = newline_idx + 1
        parts = header.split(" ")
        if len(parts) == 2 and parts[1] == "missing":
            out[ref] = None
            continue
        size = int(parts[2])
        out[ref] = text[pos : pos + size]
        pos += size + 1  # skip content + its trailing newline
    return out


def baseline_class_d_envelopes(root: Path, merge_base: str, candidate_paths: set[str]) -> dict[str, str] | None:
    """contract.id -> raw YAML text, for every baseline docs/contracts/*.{yaml,yml} blob (from
    `candidate_paths`) that parses as a Class D envelope (`contract.class == "D"`).

    One `git cat-file --batch` read regardless of how many candidate paths there are -- the
    id-based leg of Pass 2's base-resolution union, so editing a Class D file's `contract.id`
    does not, by itself, shed the amendment obligation. Returns None on a git-level failure
    (batch invocation itself failed); a per-path parse failure or a non-D envelope is simply
    skipped, never a None-propagating failure. First-wins on a duplicate id.
    """
    if not candidate_paths:
        return {}
    refs = [f"{merge_base}:{p}" for p in sorted(candidate_paths)]
    result = _common.run(
        ["git", "cat-file", "--batch"],
        input="\n".join(refs) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
    )
    if result.returncode != 0:
        return None

    contents = _parse_batch_output(result.stdout, refs)
    out: dict[str, str] = {}
    for ref, text in contents.items():
        if text is None:
            continue
        try:
            data = parse_yaml(text)
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        contract = data.get("contract")
        if not isinstance(contract, dict) or contract.get("class") != "D":
            continue
        contract_id = contract.get("id")
        if isinstance(contract_id, str) and contract_id not in out:
            out[contract_id] = text
    return out


def _load_prompt_compliance():
    """Lazy-load prompt_compliance to avoid import-time subprocess calls."""
    compliance_path = _common.ROOT / "scripts" / "prompt_compliance.py"
    if not compliance_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("prompt_compliance", compliance_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]

    # Ensure repo root is in sys.path so intra-package imports resolve
    root_str = str(_common.ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)
    return mod
