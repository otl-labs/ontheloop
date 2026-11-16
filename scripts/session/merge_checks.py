"""Forge-neutral required-checks predicate for the merge gate (rec-4080; Decision 234 cl.4).

verdict(required, runs, total_count) judges check names and conclusions only: it never names a pull request, does no
I/O and never fetches. `required` is the set of required check names, or None when the ruleset named none (unknown is
never a pass). `runs` are the head's check runs as dicts (name, status, conclusion, started_at, id, app slug);
`total_count` is the forge's count of runs, so a partial read is UNAVAILABLE.

The persona leg (T4.32 c5, Decision 264 cl.4-5) is the same kind of pure function: verdict_sources reads the
gates.<persona>.verdict_source rows from a config text, pr_class sorts a changed-path set into plan or code,
persona_checks names the persona checks an actions row adds for that class, and switch_backs names the personas a
change returns from actions to in_session. No I/O, no pull request number.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import yaml

from scripts.ci import push_scope

CLEAR = "CLEAR"
REFUSE = "REFUSE"
UNAVAILABLE = "UNAVAILABLE"
APP = "github-actions"
PERSONA_CLASSES = {"code_reviewer": "code", "decision_scout": "plan", "plan_critic": "plan"}
SOURCES = ("in_session", "actions")
_ROW_ID = re.compile(r"^gates\.([A-Za-z0-9_]+)\.verdict_source$")


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


def persona_check_name(persona: str) -> str:
    return f"persona-verdict/{persona}"


def verdict_sources(config_text: str) -> dict[str, str] | str:
    """Persona -> in_session or actions from a project-configuration.yaml text, or a reason string when unreadable."""
    try:
        data = yaml.safe_load(config_text)
    except yaml.YAMLError as exc:
        return "the configuration does not parse: " + (str(exc).splitlines() or ["unreadable"])[0]
    settings = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(settings, list):
        return "the configuration has no settings list"
    rows: dict[str, list[object]] = {}
    for row in settings:
        match = _ROW_ID.match(str(row.get("id"))) if isinstance(row, dict) else None
        if match and isinstance(row, dict):
            rows.setdefault(match.group(1), []).append(row.get("defaults"))
    if not rows:
        return dict.fromkeys(PERSONA_CLASSES, "in_session")
    for persona in sorted(rows):
        if persona not in PERSONA_CLASSES:
            return f"gates.{persona}.verdict_source names no verdict persona"
        if len(rows[persona]) > 1:
            return f"gates.{persona}.verdict_source appears more than once"
    for persona in sorted(PERSONA_CLASSES):
        if persona not in rows:
            return f"gates.{persona}.verdict_source has no row"
    found: dict[str, str] = {}
    for persona, (defaults,) in sorted(rows.items()):
        local = defaults.get("local") if isinstance(defaults, dict) else None
        cloud = defaults.get("cloud") if isinstance(defaults, dict) else None
        if local not in SOURCES or cloud not in SOURCES:
            return f"gates.{persona}.verdict_source defaults are not in_session or actions"
        if local != cloud:
            return f"gates.{persona}.verdict_source defaults.local differs from defaults.cloud"
        found[persona] = str(local)
    return found


def pr_class(changed: Iterable[str] | None) -> str:
    return "plan" if push_scope.confined_to_plans(set(changed) if changed else None) else "code"


def persona_checks(sources: Mapping[str, str], klass: str) -> list[str]:
    return sorted(persona_check_name(p) for p, v in sources.items() if v == "actions" and PERSONA_CLASSES.get(p) == klass)


def switch_backs(base_sources: Mapping[str, str], head_sources: Mapping[str, str]) -> list[str]:
    return sorted(p for p, v in base_sources.items() if v == "actions" and head_sources.get(p) == "in_session")
