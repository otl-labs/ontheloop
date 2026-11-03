from __future__ import annotations

from scripts.checks import _common, registry


# File patterns that mark executor boundary files (Decision 44).
# Canonical source: config/agent/executor/capabilities.yaml -- do not edit this list directly.
def _load_boundary_patterns() -> tuple[str, ...]:
    import yaml  # noqa: PLC0415

    capabilities_path = _common.ROOT / "config" / "agent" / "executor" / "capabilities.yaml"
    data = yaml.safe_load(capabilities_path.read_text(encoding="utf-8"))
    return tuple(data["boundary_patterns"])


_EXECUTOR_BOUNDARY_PATTERNS = _load_boundary_patterns()


@registry.register("validate_executor_boundary", owner="platform")
def validate_executor_boundary(failed: list[str]) -> None:
    """Validate that no open rec with automatable:true targets an executor boundary file.

    Decision 44: executor machinery files (prompts, scripts, tests) must only be
    modified via /plan -> /implement, never by the autonomous executor.
    Uses _EXECUTOR_BOUNDARY_PATTERNS to classify boundary files.

    Matches only the rec's `file` field -- the executor's edit target. Acceptance-command
    text is intentionally not matched: a verification command that merely references a
    boundary filename (e.g. `grep 'DECISIONS.md' ...`) does not modify it, so matching it
    produced false positives.
    """
    print("\n=== Executor boundary validation ===")
    import json

    recs_jsonl = _common.ROOT / "logs" / ".recommendations-log.jsonl"

    if not recs_jsonl.exists():
        print("logs/.recommendations-log.jsonl not found — skipping.")
        registry.skipped(
            "recs cache retired (Decision 215) and this check still scans it; rec-4236 owns moving it onto a reader verb"
        )
        return

    violations: list[tuple[str, str, str]] = []
    try:
        lines = recs_jsonl.read_text(encoding="utf-8").splitlines()
        by_id: dict = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            rec_id = entry.get("id")
            if rec_id:
                by_id[rec_id] = entry
        for entry in by_id.values():
            if entry.get("status") != "open" or entry.get("automatable") is not True:
                continue
            file_field = entry.get("file", "")
            for pat in _EXECUTOR_BOUNDARY_PATTERNS:
                if pat in file_field:
                    violations.append((entry.get("id", "?"), file_field, pat))
                    break
    except OSError as e:
        print(f"ERROR: Could not read JSONL file: {e}")
        failed.append("Executor boundary validation")
        return

    if violations:
        print("Executor boundary violations (open rec with automatable:true targets boundary file):")
        for rec_id, file_field, matched_pat in violations:
            print(f"  - {rec_id}: file='{file_field}' matches pattern '{matched_pat}'")
        failed.append("Executor boundary validation")
    else:
        print("Executor boundary validation passed.")
