"""Fast-tier re-run of the graduated records that read plan content (rec-4114, Decision 244 Update).

validate_graduated_rerun reruns every live record in the full tier only. This pre-only check reruns the subset
whose check_spec command names docs/plans/ (the slash spelling, or the path-join spelling ROOT/'docs'/'plans'),
outside a `:!docs/plans/` pathspec exclusion, so a plans-only diff is still checked by the records that read plans.
Red records on known_red.yaml pass; bookkeeping faults of that list belong to validate_graduated_rerun and
validate_known_red_shrinks. A record reading plans only through an indirection is rec-5040's declared residual.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks import _common, registry
from scripts.checks.verification import validate_graduated_rerun as rerun

PLAN_READ_RE = re.compile(r"""(?<!:!)docs/plans|['"]docs['"]\s*/\s*['"]plans['"]""")


def reads_plans(row: dict) -> bool:
    command = (row.get("check_spec") or {}).get("command")
    if isinstance(command, list):
        command = " ".join(str(a) for a in command)
    return isinstance(command, str) and PLAN_READ_RE.search(command) is not None


@registry.register("validate_graduated_rerun_plan_readers", owner="platform")
def validate_graduated_rerun_plan_readers(failed: list[str], root: Path | None = None) -> None:
    tree = Path(root) if root is not None else _common.ROOT
    print("=== Graduated plan-reader re-run ===")
    outcomes = rerun.evaluate(tree, select=reads_plans)
    listed, problem = rerun._load_known_red(tree)
    if listed is None:
        failed.append(problem)
        listed = []
    red = [o for o in outcomes if o.red]
    sensitive = [o for o in outcomes if o.load_sensitive]
    for o in sensitive:
        print(f"load-sensitive (failed in the pool, passed alone): {o.check_id}")
    for o in red:
        if o.check_id not in listed:
            failed.append(f"graduated record {o.check_id} (guard_target {o.guard_target}) is red: {o.detail}")
    listed_red = sum(1 for o in red if o.check_id in listed)
    print(f"{len(outcomes)} plan-reading graduated_records examined, {len(red)} red ({listed_red} listed)")
    registry.examined(len(outcomes), unit="graduated_records")
