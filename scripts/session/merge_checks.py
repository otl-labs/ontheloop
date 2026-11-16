"""Forge-neutral required-checks predicate for the merge gate (rec-4080; Decision 234 cl.4).

verdict(required, runs, total_count) judges check names and conclusions only: it never names a pull request, does no
I/O and never fetches. `required` is the set of required check names, or None when the ruleset named none (unknown is
never a pass). `runs` are the head's check runs as dicts (name, status, conclusion, started_at, id, app slug);
`total_count` is the forge's count of runs, so a partial read is UNAVAILABLE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

CLEAR = "CLEAR"
REFUSE = "REFUSE"
UNAVAILABLE = "UNAVAILABLE"
APP = "github-actions"


@dataclass(frozen=True)
class Verdict:
    kind: str
    reasons: tuple[str, ...] = ()


def _slug(run: Mapping[str, object]) -> object:
    app = run.get("app")
    return app.get("slug") if isinstance(app, Mapping) else None


def _newest_key(run: Mapping[str, object]) -> tuple[int, str, int]:
    started = run.get("started_at")
    run_id = run.get("id")
    ident = run_id if isinstance(run_id, int) and not isinstance(run_id, bool) else 0
    if isinstance(started, str) and started:
        return (0, started, ident)
    return (1, "", ident)


def _state(run: Mapping[str, object]) -> str | None:
    if run.get("status") == "completed" and run.get("conclusion") == "success":
        return None
    if run.get("status") != "completed":
        return f"is {run.get('status') or 'in an unknown state'}"
    return f"concluded {run.get('conclusion') or 'with no conclusion'}"


def verdict(required: Iterable[str] | None, runs: Iterable[Mapping[str, object]], total_count: int | None) -> Verdict:
    names = sorted(set(required)) if required is not None else []
    if not names:
        return Verdict(UNAVAILABLE, ("no required status checks were found for main",))
    listed = list(runs)
    if total_count is None or total_count > len(listed):
        return Verdict(UNAVAILABLE, (f"partial check-run read ({len(listed)} of {total_count})",))
    reasons: list[str] = []
    for name in names:
        mine = [r for r in listed if r.get("name") == name and _slug(r) == APP]
        if not mine:
            reasons.append(f"{name} has no {APP} run")
            continue
        problem = _state(max(mine, key=_newest_key))
        if problem is not None:
            reasons.append(f"{name} {problem}")
    return Verdict(REFUSE, tuple(reasons)) if reasons else Verdict(CLEAR)
