"""Egress floor rules for a covered verdict job (PLAN-persona-verdict-egress-floor; T4.32 c7 shell leg).

Called by validate_persona_verdict_egress for every covered job. The filter's own sudo and container switch is off; the one
floor step, right after the filter and its checkouts, lays the floor and revokes sudo, and the last step runs the self-check
again whatever happened before it. Reads parsed YAML only; imports nothing from scripts/personas/.
"""

from __future__ import annotations

from typing import Any

PROBE = ".github/workflows/persona-verdict-egress-probe.yml"
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
PROBE_PREFIX = "python3 -I scripts/personas/run.py "
FLOOR_VERB = "egress-floor"
DRILL_VERBS = {
    (PROBE, "egress-fail-closed"): "egress-revert-drill",
    (PROBE, "egress-fail-closed-control"): "egress-revert-drill-control",
}
CHECKOUT_KEYS = {"persist-credentials", "path", "ref"}
STEP_KEYS_BARE = {"name", "id", "run"}
FINAL_KEYS = {"name", "id", "if", "run"}


def floor_run(rel: str, name: str) -> str:
    prefix = PROBE_PREFIX if rel == PROBE else RUN_PREFIX
    return "sudo " + prefix + DRILL_VERBS.get((rel, name), FLOOR_VERB)


def is_floor_line(run: str) -> bool:
    text = run.strip()
    if not text.startswith("sudo "):
        return False
    rest = text[len("sudo ") :]
    return any(
        rest.startswith(prefix) and rest[len(prefix) :] in {FLOOR_VERB, *DRILL_VERBS.values()}
        for prefix in (RUN_PREFIX, PROBE_PREFIX)
    )


def _is_checkout(step: dict[str, Any]) -> bool:
    return str(step.get("uses", "")).startswith("actions/checkout@")


def floor_problems(rel: str, name: str, job: dict[str, Any]) -> list[str]:
    where = f"{rel}::{name}"
    steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
    out: list[str] = []
    if not steps:
        return out
    with_ = steps[0].get("with") or {}
    if str(with_.get("disable-sudo-and-containers")).lower() != "false":
        out.append(
            f"{where}: the filter's disable-sudo-and-containers must be false: the egress floor revokes sudo and containers"
        )
    floors = [i for i, s in enumerate(steps) if is_floor_line(str(s.get("run", "")))]
    if len(floors) != 1:
        out.append(f"{where}: a covered job needs exactly one egress floor step (found {len(floors)})")
        return out + _final_problems(where, rel, name, steps)
    index = floors[0]
    for step in steps[1:index]:
        if not _is_checkout(step):
            out.append(f"{where}: a step before the egress floor is neither the filter nor a checkout")
        elif set(step.get("with") or {}) - CHECKOUT_KEYS:
            out.append(f"{where}: a checkout before the egress floor has with: inputs beyond {sorted(CHECKOUT_KEYS)}")
    if any(_is_checkout(s) for s in steps[index + 1 :]):
        out.append(f"{where}: a checkout comes after the egress floor step")
    floor = steps[index]
    if str(floor.get("run", "")).strip() != floor_run(rel, name):
        out.append(f"{where}: the egress floor step must run exactly {floor_run(rel, name)}")
    if set(floor) - STEP_KEYS_BARE:
        out.append(f"{where}: the egress floor step has keys beyond name, id and run")
    for i, step in enumerate(steps):
        if i != index and "sudo" in str(step.get("run", "")).split():
            out.append(f"{where}: a step other than the egress floor runs sudo")
    return out + _final_problems(where, rel, name, steps)


def _final_problems(where: str, rel: str, name: str, steps: list[dict[str, Any]]) -> list[str]:
    prefix = PROBE_PREFIX if rel == PROBE else RUN_PREFIX
    last = steps[-1]
    ok = (
        str(last.get("run", "")).strip() == prefix + "egress-self-check"
        and str(last.get("if", "")).strip() == "always()"
        and not set(last) - FINAL_KEYS
    )
    if ok:
        return []
    return [f"{where}: the last step must be the egress self-check with if: always() and no env, shell or working-directory"]
