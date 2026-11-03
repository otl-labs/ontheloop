"""Recommendations-JSONL schema validation (Decision 104)."""

from __future__ import annotations

from scripts.checks import _common, registry


@registry.register("validate_recommendations_schema", owner="platform")
def validate_recommendations_schema(failed: list[str]) -> None:
    """Validate that all entries in logs/.recommendations-log.jsonl conform to schema.

    Uses Pydantic v2 Recommendation model from scripts.executor.jsonl_store.
    Validates line-by-line, skips comments and blank lines, collects errors.
    """
    print("\n=== Recommendations schema validation ===")
    import json
    import sys

    recs_jsonl = _common.ROOT / "logs" / ".recommendations-log.jsonl"

    if not recs_jsonl.exists():
        # rec-3309: this used to return silently here -- a vacuous pass in both CI tiers, since
        # the gitignored cache is never synced by either job. Declaring skipped() (an unavailable
        # input, not an empty domain -- check-accounting.yaml's discrimination_rule) makes that
        # honest instead of indistinguishable from "examined 0 rows and they were all fine".
        print("logs/.recommendations-log.jsonl not found — skipping.")
        registry.skipped(
            "recs cache retired (Decision 215); rec-4237 (claim-check export) owns a future full-table schema scan"
        )
        return

    # Lazy import with sys.path injection
    root_str = str(_common.ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)

    try:
        from pydantic import ValidationError

        from scripts.executor.jsonl_store import Recommendation
    except ImportError as e:
        logger_error = f"Could not import Recommendation model: {e}"
        print(f"ERROR: {logger_error}")
        failed.append("Recommendations schema validation")
        return
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)

    errors: list[str] = []
    examined_count = 0
    try:
        lines = recs_jsonl.read_text(encoding="utf-8").splitlines()
        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            examined_count += 1
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as e:
                errors.append(f"Line {line_num}: JSON parse error: {e}")
                continue

            try:
                Recommendation.model_validate(entry)
            except ValidationError as e:
                field_errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
                errors.append(f"Line {line_num}: {field_errors}")
                continue

            # Catch banned acceptance patterns at commit time.
            # 'python -c' with nested quotes breaks shell escaping in executor pre-flight.
            # Exclude cases where 'python -c' appears as a grep search string (inside quotes).
            acceptance = entry.get("acceptance") or ""
            if "python -c" in acceptance and "'python -c'" not in acceptance:
                errors.append(
                    f"Line {line_num}: acceptance contains banned pattern 'python -c'"
                    f" (use a shell command or pytest invocation instead)"
                )
    except OSError as e:
        errors.append(f"Could not read JSONL file: {e}")

    if errors:
        print("Recommendations schema validation errors:")
        for error_msg in errors:
            print(f"  - {error_msg}")
        failed.append("Recommendations schema validation")
    else:
        print("Recommendations schema validation passed.")
        registry.examined(examined_count, unit="rec rows")
