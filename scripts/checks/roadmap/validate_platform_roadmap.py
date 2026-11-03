# complexity-waiver: decision-43
"""Platform roadmap schema validation (Decision 104)."""

from __future__ import annotations

import re
import subprocess
import sys

from scripts.checks import _common, registry
from scripts.checks._marker_guard import default_base_reader
from scripts.checks.roadmap import _roadmap_spans

_40HEX_RE = re.compile(r"^[0-9a-f]{40}$")
_ROADMAP_MAX_LINES = 10_000  # Decision 114 (raised from KG.11's 2500)
_ROADMAP_REL_PATH = "docs/ROADMAP-PLATFORM.yaml"


def _roadmap_size_issues(text: str, ceiling: int = _ROADMAP_MAX_LINES, rel_path: str = _ROADMAP_REL_PATH) -> list[str]:
    """Return a one-item FAIL list if text exceeds ceiling lines, else [] (each file on its own, Decision 227)."""
    line_count = len(text.splitlines())
    if line_count > ceiling:
        return [f"  FAIL: {rel_path} is {line_count} lines, exceeding the {ceiling}-line ceiling (Decision 114)"]
    return []


def _print_span_attribution(diff_text: str, post_text: str, legacy_ids: set[str]) -> None:
    """REPORT-ONLY: print the span attribution beside criterion (ii)'s legacy detector.

    Reads the origin/main image through the EXISTING scripts.checks._marker_guard
    .default_base_reader seam -- including its missing-base contract (None on an unreachable
    ref), which this advisory needs and which a second `git show` seam of this module's own
    would duplicate (Decision 104 sole-home discipline).

    Nothing here appends to `failed` and nothing here raises: the base-image read is guarded by
    name (OSError, UnicodeDecodeError -- the reader shells `git show` with strict UTF-8) and falls
    through to the SKIP line, so the presubmit exit code is identical with and without these two
    lines on every input.
    """
    try:
        base_text = default_base_reader(_ROADMAP_REL_PATH)
    except (OSError, UnicodeDecodeError):
        base_text = None
    if base_text is None:
        print(f"  SKIP: origin/main image of {_ROADMAP_REL_PATH} unreachable -- span attribution not reported")
        return
    pre_spans = _roadmap_spans.item_spans(base_text)
    post_spans = _roadmap_spans.item_spans(post_text)
    span_ids = _roadmap_spans.touched_item_ids(pre_spans, post_spans, _roadmap_spans.changed_lines(diff_text))
    consumed = legacy_ids & ({span.item_id for span in pre_spans} | {span.item_id for span in post_spans})
    print(
        f"  SPAN-ATTRIBUTION span_named={len(span_ids)} legacy_named={len(consumed)} "
        f"missed_by_legacy={sorted(span_ids - consumed)}"
    )
    print(f"  SPAN-ATTRIBUTION legacy_named_outside_spans={sorted(consumed - span_ids)} (report-only, nothing failed)")


@registry.register("validate_platform_roadmap", owner="platform")
def validate_platform_roadmap(failed: list[str]) -> None:
    """Validate docs/ROADMAP-PLATFORM.yaml against the RoadmapDocument Pydantic schema.

    Rejects structural drift: duplicate ids, dangling depends_on, dependency cycles,
    unknown gate-rule helpers, invalid filed_via, unsupported document version.
    Added by T-1.5. Runs in both the --pre and full presubmit tiers (closes-criteria-integrity-guard).

    Extended by T-1.23: criteria-status integrity assertions.
      (i)  met criterion met_by resolves to a real docs/plans/PLAN-<slug>.yaml or a 40-hex sha.
      (ii) any tier_item touched in the git diff (vs origin/main) must have no bare-string criteria.
      (iii) every PLAN-*.yaml closes_criteria ref resolves to a real item:criterion in the roadmap.
    """
    import yaml as _yaml  # noqa: PLC0415

    print("\n=== Platform roadmap schema validation ===")

    roadmap_path = _common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
    if not roadmap_path.exists():
        print(f"  FAIL: {roadmap_path.relative_to(_common.ROOT)} not found")
        failed.append("Platform roadmap schema validation")
        return

    root_str = str(_common.ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)
    try:
        from pydantic import ValidationError  # noqa: PLC0415

        from scripts.roadmap import roadmap_archive  # noqa: PLC0415
        from scripts.roadmap.platform_roadmap import ExitCriterion, load  # noqa: PLC0415

        doc = load(roadmap_path)
        issues: list[str] = []
        roadmap_text = roadmap_path.read_text(encoding="utf-8")
        issues += _roadmap_size_issues(roadmap_text)
        issues += [
            m
            for a in roadmap_archive.archive_texts(roadmap_path)
            for m in _roadmap_size_issues(a, rel_path=roadmap_archive.ARCHIVE_REL_PATH)
        ]

        # (i) met criterion met_by resolves to a real plan file OR a 40-hex sha
        plans_root = _common.ROOT / "docs" / "plans"
        for item in doc.tier_items:
            for crit in item.exit_criteria:
                if not isinstance(crit, ExitCriterion):
                    continue
                if crit.status == "met" and crit.met_by:
                    plan_file = plans_root / f"PLAN-{crit.met_by}.yaml"
                    if not plan_file.exists() and not _40HEX_RE.match(crit.met_by):
                        issues.append(
                            f"  FAIL: tier_item '{item.id}' criterion '{crit.id}': "
                            f"met_by='{crit.met_by}' does not resolve to a real plan or 40-hex commit"
                        )

        # (ii) git-diff-touched tier_items must have fully-structured criteria (no bare strings)
        diff_result = subprocess.run(
            ["git", "diff", "origin/main", "--", str(roadmap_path.relative_to(_common.ROOT))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(_common.ROOT),
        )
        if diff_result.returncode == 0 and diff_result.stdout.strip():
            touched_ids: set[str] = _roadmap_spans.legacy_regex_item_ids(diff_result.stdout)
            _print_span_attribution(diff_result.stdout, roadmap_text, touched_ids)
            if touched_ids:
                with roadmap_path.open(encoding="utf-8") as fh:
                    raw_doc = _yaml.safe_load(fh)
                for raw_item in raw_doc.get("tier_items", []):
                    if raw_item.get("id") in touched_ids:
                        for raw_crit in raw_item.get("exit_criteria", []):
                            if isinstance(raw_crit, str):
                                issues.append(
                                    f"  FAIL: tier_item '{raw_item['id']}' is touched in the git diff "
                                    f"but still has bare-string criteria -- convert to ExitCriterion objects"
                                )
                                break
        issues += roadmap_archive.criterion_ii_archive_issues(roadmap_path, default_base_reader)

        # (iii) every PLAN-*.yaml closes_criteria ref resolves to a real item:criterion
        item_criteria: dict[str, set[str]] = {}
        for item in doc.tier_items:
            item_criteria[item.id] = {c.id for c in item.exit_criteria if isinstance(c, ExitCriterion)}
        if plans_root.is_dir():
            for plan_file in sorted(plans_root.glob("PLAN-*.yaml")):
                try:
                    with plan_file.open(encoding="utf-8") as fh:
                        plan_data = _yaml.safe_load(fh)
                    if not isinstance(plan_data, dict):
                        continue
                    closes = plan_data.get("closes_criteria") or []
                    if not isinstance(closes, list):
                        continue
                    for ref in closes:
                        if not isinstance(ref, str) or ":" not in ref:
                            issues.append(
                                f"  FAIL: {plan_file.name}: closes_criteria entry {ref!r} "
                                f"is not in '<item-id>:<crit-id>' format"
                            )
                            continue
                        item_id, crit_id = ref.split(":", 1)
                        if item_id not in item_criteria:
                            issues.append(
                                f"  FAIL: {plan_file.name}: closes_criteria ref '{ref}' names unknown tier_item '{item_id}'"
                            )
                        elif crit_id not in item_criteria[item_id]:
                            issues.append(
                                f"  FAIL: {plan_file.name}: closes_criteria ref '{ref}' "
                                f"names unknown criterion '{crit_id}' on item '{item_id}'"
                            )
                except Exception as plan_exc:  # noqa: BLE001
                    issues.append(f"  FAIL: {plan_file.name}: could not parse for closes_criteria: {plan_exc}")

        registry.examined(len(doc.tier_items), unit="tier_items")
        if issues:
            for msg in issues:
                print(msg)
            failed.append("Platform roadmap criteria integrity")
        else:
            print("  PASS: platform roadmap schema validation passed.")
    except ImportError as exc:
        print(f"  ERROR: Could not import platform_roadmap: {exc}")
        failed.append("Platform roadmap schema validation")
    except ValidationError as exc:
        print(f"  FAIL: Pydantic validation error:\n{exc}")
        failed.append("Platform roadmap schema validation")
    except _yaml.YAMLError as exc:
        print(f"  FAIL: YAML parse error:\n{exc}")
        failed.append("Platform roadmap schema validation")
    except Exception as exc:
        print(f"  FAIL: Unexpected error: {exc}")
        failed.append("Platform roadmap schema validation")
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)
