#!/usr/bin/env python3
"""Prompt compliance checker.

Parses ``## Behavioural Invariants`` YAML blocks from skill files and
validates them against session data (retro-lite log, execution state).
Reads docs/contracts/instruction-architecture.yaml for the canonical
behavioural_invariant_sources list and layer content_locations.

Usage:
    python scripts/prompt_compliance.py --prompt .claude/skills/implement/SKILL.md
    python scripts/prompt_compliance.py --all
    python scripts/prompt_compliance.py --all --session agent/infra-testing-enforcement
"""

import argparse
import json
import re
import sys
from pathlib import Path

from scripts.s3_log_store import get_backend, read_jsonl

try:
    import yaml
except ImportError:

    class yaml:  # type: ignore[no-redef]
        """Sentinel: pyyaml not installed."""

        @staticmethod
        def safe_load(stream: str) -> dict:
            raise ImportError("pyyaml is required: pip install pyyaml")


ROOT = Path(__file__).resolve().parent.parent

_INVARIANTS_PATTERN = re.compile(
    r"##\s+Behavioural Invariants\s*\n+```ya?ml\n(.*?)```",
    re.DOTALL,
)

_INSTRUCTION_ARCH_PATH = ROOT / "docs" / "contracts" / "instruction-architecture.yaml"
_INSTRUCTION_ARCH_REGISTRY: dict | None = None


def _load_instruction_architecture() -> dict:
    """Load and cache the instruction-architecture.yaml contract.

    Lazy import of yaml happens here; no I/O at module import time.
    On any failure (absent file, parse error, yaml unavailable) returns a
    fallback dict with behavioural_invariant_sources=['.claude/skills/*/SKILL.md']
    and empty layers.
    """
    global _INSTRUCTION_ARCH_REGISTRY
    if _INSTRUCTION_ARCH_REGISTRY is not None:
        return _INSTRUCTION_ARCH_REGISTRY

    _fallback: dict = {"behavioural_invariant_sources": [".claude/skills/*/SKILL.md"], "layers": []}

    try:
        import yaml as _yaml  # lazy -- never import at module level for side-effect safety

        raw = _INSTRUCTION_ARCH_PATH.read_text(encoding="utf-8")
        data = _yaml.safe_load(raw)
        if not isinstance(data, dict):
            _INSTRUCTION_ARCH_REGISTRY = _fallback
            return _fallback
        _INSTRUCTION_ARCH_REGISTRY = data
        return data
    except Exception:
        _INSTRUCTION_ARCH_REGISTRY = _fallback
        return _fallback


def get_behavioural_invariant_sources() -> list[str]:
    """Return the list of glob patterns for files that carry Behavioural Invariants blocks.

    Reads behavioural_invariant_sources from instruction-architecture.yaml.
    Falls back to ['.claude/skills/*/SKILL.md'] if the YAML is absent or unparseable.
    """
    contract = _load_instruction_architecture()
    sources = contract.get("behavioural_invariant_sources")
    if isinstance(sources, list) and sources:
        return [str(s) for s in sources]
    return [".claude/skills/*/SKILL.md"]


def check_layer_compliance(contract: dict) -> list[str]:
    r"""Check that every layer's content_locations resolve to at least one file.

    Excludes matches under .venv/ and .git/ so vendored CLAUDE.md files do
    not satisfy the Layer 1 '**\/CLAUDE.md' existence check.

    Returns a list of violation strings (empty = all layers resolve).
    """
    violations: list[str] = []
    layers = contract.get("layers", [])
    for layer_entry in layers:
        layer_num = layer_entry.get("layer", "?")
        layer_name = layer_entry.get("name", "")
        for glob_pattern in layer_entry.get("content_locations", []):
            candidates = list(ROOT.glob(glob_pattern))
            live = [p for p in candidates if ".venv" not in p.parts and ".git" not in p.parts]
            if not live:
                violations.append(f"layer {layer_num} ({layer_name}): no files match {glob_pattern!r}")
    return violations


def parse_invariants(prompt_path: Path) -> dict[str, bool]:
    """Parse ``## Behavioural Invariants`` YAML block from a prompt file.

    Returns a dict of invariant name -> bool value.
    Returns empty dict if no section found or YAML is invalid.
    """
    try:
        content = prompt_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    match = _INVARIANTS_PATTERN.search(content)
    if not match:
        return {}

    yaml_text = match.group(1)
    try:
        data = yaml.safe_load(yaml_text)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    return {k: bool(v) for k, v in data.items() if not str(k).startswith("#")}


def parse_retro_lite_log(log_path: Path, session_filter: str | None = None) -> list[dict]:
    """Parse ``.retro-lite-log.jsonl`` and return a list of entries.

    Skips malformed (non-JSON) lines with a warning.
    If ``session_filter`` is provided, only entries whose ``session`` field
    contains the filter string are returned.
    Uses S3 backend when S3_LOG_BUCKET is set; otherwise reads from log_path.
    """
    if get_backend() == "s3":
        entries = read_jsonl(".retro-lite-log.jsonl")
    else:
        # Local mode: use provided log_path for backward compatibility + test isolation
        if not log_path.exists():
            return []
        entries = []
        for i, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  Warning: skipping malformed JSON on line {i} of {log_path.name}")
                continue
    if session_filter:
        entries = [e for e in entries if session_filter in e.get("session", "")]
    return entries


def parse_execution_state(state_path: Path) -> dict | None:
    """Parse ``.execution-state.json`` and return the dict, or None if missing/invalid."""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def check_retro_lite_compliance(
    invariants: dict[str, bool],
    retro_entries: list[dict],
    execution_state: dict | None,
) -> list[str]:
    """Check retro-lite and checkpoint invariants against session data.

    Validates that per-step friction entries exist in the retro-lite log for
    completed sessions. Entries may be written by the parent agent directly
    (``run_retro_lite.py --append``) or via the @retro-lite subagent -- the
    check is mechanism-agnostic.

    Returns a list of violation strings (empty = compliant).
    """
    violations: list[str] = []

    if invariants.get("retro_lite_per_step") and execution_state is not None:
        total_steps = execution_state.get("total_steps")
        if total_steps is not None:
            count = len(retro_entries)
            if count < int(total_steps):
                violations.append(f"retro_lite_per_step: expected {total_steps} entries, found {count}")

    if invariants.get("checkpoint_per_step") and execution_state is not None:
        status = execution_state.get("status", "")
        # Skip mid-session: IN_PROGRESS means the session is still running
        if status != "IN_PROGRESS":
            current = execution_state.get("current_step")
            total = execution_state.get("total_steps")
            if current is not None and total is not None:
                if int(current) < int(total):
                    violations.append(f"checkpoint_per_step: execution state shows step {current}/{total}")

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Check prompt behavioural invariants against session data.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--prompt",
        type=Path,
        help="Path to a specific skill or prompt file to check.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Check all files listed in behavioural_invariant_sources (from instruction-architecture.yaml).",
    )
    parser.add_argument(
        "--session",
        help="Filter retro-lite log entries by session string.",
    )
    args = parser.parse_args()

    retro_log = ROOT / "logs" / ".retro-lite-log.jsonl"
    state_path = ROOT / "logs" / ".execution-state.json"

    if args.all:
        sources = get_behavioural_invariant_sources()
        prompt_files: list[Path] = []
        for glob_pattern in sources:
            prompt_files.extend(ROOT.glob(glob_pattern))
        # Also run layer-claims check
        contract = _load_instruction_architecture()
        layer_violations = check_layer_compliance(contract)
        if layer_violations:
            print("Layer claims violations:")
            for v in layer_violations:
                print(f"  - {v}")
    else:
        prompt_files = [args.prompt]
        layer_violations = []

    retro_entries = parse_retro_lite_log(retro_log, session_filter=args.session)
    execution_state = parse_execution_state(state_path)

    all_violations: list[str] = []
    for prompt_file in prompt_files:
        invariants = parse_invariants(prompt_file)
        if not invariants:
            continue

        for v in check_retro_lite_compliance(invariants, retro_entries, execution_state):
            all_violations.append(f"{prompt_file.name}: {v}")

    all_violations.extend(layer_violations)

    if all_violations:
        print("Prompt compliance violations:")
        for v in all_violations:
            print(f"  - {v}")
        sys.exit(1)
    else:
        print(f"Prompt compliance: {len(prompt_files)} file(s) checked, no violations.")
        sys.exit(0)


if __name__ == "__main__":
    main()
