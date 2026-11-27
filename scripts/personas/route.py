"""Routing on the diff, never on the branch name (T4.2 c12; the merge gate's class from scripts.session.merge_checks).

Changed paths and status come from the compare API against the PR's base branch as it is now (the merge base the merge
gate diffs against at merge time). Persona ids and classes come only from merge_checks, so this module holds no
persona-id string literal (executor-personas.yaml implementation_binding landing_dirs rule).
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

from scripts.personas import execute, rescout, resolve_pr
from scripts.session import merge_checks

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = "tests/fixtures/persona_verdict/fixture_pr/"
DRILL_PATH = FIXTURE_DIR + "fallback_drill.txt"
PLAN_PATH = re.compile(r"docs/plans/PLAN-[^/]+\.yaml")
PLAN_SIZE_CAP = 512 * 1024
COMPARE_LIMIT = 300
SCOPE_CONTRACT = "docs/contracts/implement-scope-boundary.yaml"
PERSONAS_CONTRACT = "docs/contracts/executor-personas.yaml"
ROUTES = Path("prepared") / "routes.json"
PLAN_INPUTS = "plan_inputs.json"
CI_PATH = ".github/workflows/ci.yml"
TRIGGER_PATH = ".github/workflows/persona-verdict-trigger.yml"
OVERRIDE_PATH = resolve_pr.OVERRIDE_WORKFLOW
UPSTREAM_PATHS = {CI_PATH: "ci", TRIGGER_PATH: "trigger", OVERRIDE_PATH: "override"}
CONFIG = "docs/contracts/project-configuration.yaml"
ADVISORY_STATES = ("enabled", "disabled")
VERDICT_TITLE = "verdict "


def changed_files(repo: str, base_ref: str, head_sha: str, token: str | None) -> tuple[list[dict[str, Any]], bool, str]:
    """Return (files, unknown, merge_base_sha). Unknown means the list may be truncated."""
    spec = f"{urllib.parse.quote(base_ref, safe='/')}...{head_sha}"
    files: list[dict[str, Any]] = []
    merge_base = ""
    page = 1
    while True:
        data = resolve_pr.get_json(f"/repos/{repo}/compare/{spec}?per_page=100&page={page}", token)
        batch = data.get("files")
        if not isinstance(batch, list):
            return files, True, merge_base
        merge_base = merge_base or str((data.get("merge_base_commit") or {}).get("sha") or "")
        files.extend(f for f in batch if isinstance(f, dict))
        if len(batch) < 100 or len(files) >= COMPARE_LIMIT:
            break
        page += 1
    return files, len(files) >= COMPARE_LIMIT, merge_base


def path_set(files: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for item in files:
        for key in ("filename", "previous_filename"):
            if item.get(key):
                paths.add(str(item[key]))
    return paths


def _has_alias(text: str) -> bool:
    return any(
        isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None) for ev in yaml.parse(text, Loader=yaml.SafeLoader)
    )


def parse_plan(text: str | None) -> Any:
    """The parsed plan, or None when it is over the size cap, holds an alias or anchor, or does not parse."""
    if text is None or len(text.encode("utf-8")) > PLAN_SIZE_CAP:
        return None
    try:
        if _has_alias(text):
            return None
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def strip_permitted(doc: dict[str, Any], permitted: list[str]) -> dict[str, Any]:
    out = dict(doc)
    for field in permitted:
        if "[]." in field:
            head, _, tail = field.partition("[].")
            rows = out.get(head)
            if isinstance(rows, list):
                out[head] = [{k: v for k, v in r.items() if k != tail} if isinstance(r, dict) else r for r in rows]
        else:
            out.pop(field, None)
    return out


def is_revision(before: str | None, after: str | None, permitted: list[str]) -> bool:
    """True unless both sides parse and differ only in permitted bookkeeping fields (unparseable counts as a revision)."""
    old, new = parse_plan(before), parse_plan(after)
    if old is None or new is None:
        return True
    return strip_permitted(old, permitted) != strip_permitted(new, permitted)


def permitted_fields(root: Path) -> list[str]:
    data = yaml.safe_load((root / SCOPE_CONTRACT).read_text(encoding="utf-8"))
    return list(data["sanction_rows"]["implementing_plan_bookkeeping"]["permitted_field_edits"])


def bound_personas(root: Path) -> set[str]:
    data = yaml.safe_load((root / PERSONAS_CONTRACT).read_text(encoding="utf-8"))
    rows = data.get("personas") or []
    return {
        str(r["persona"])
        for r in rows
        if isinstance(r, dict)
        and r.get("kind") == "verdict"
        and (r.get("implementation") or {}).get("status") == "implemented"
    }


def _fetch_text(repo: str, path: str, ref: str, token: str | None) -> str | None:
    status, raw = resolve_pr.request(
        "GET", f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}", token, accept="application/vnd.github.raw+json"
    )
    if status != 200:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _persona_set(klass: str, draft: bool) -> set[str]:
    return {
        p
        for p, c in merge_checks.PERSONA_CLASSES.items()
        if c == klass and not (draft and p in merge_checks.READY_HEAD_PERSONAS)
    }


def plan_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        f
        for f in files
        if PLAN_PATH.fullmatch(str(f.get("filename"))) and f.get("status") in ("added", "modified", "renamed", "changed")
    ]


def fixture_paths(files: list[dict[str, Any]], unknown: bool) -> list[str]:
    """The added or modified .txt paths of a diff that touches only the fixture directory, sorted; else []."""
    if unknown or not files:
        return []
    if not all(p.startswith(FIXTURE_DIR) for p in path_set(files)):
        return []
    return sorted(
        str(f.get("filename"))
        for f in files
        if f.get("status") in ("added", "modified", "renamed", "changed") and str(f.get("filename")).endswith(".txt")
    )


def fixture_routes(files: list[dict[str, Any]], unknown: bool, root: Path = ROOT) -> list[str]:
    """The index's fixture_kind personas when the diff is fixture-only with a .txt to read, else []."""
    return execute.fixture_personas(root) if fixture_paths(files, unknown) else []


def fallback_drill(files: list[dict[str, Any]], unknown: bool) -> bool:
    """True only for a fixture-only diff that adds or modifies the drill marker."""
    if not fixture_paths(files, unknown):
        return False
    return any(f.get("filename") == DRILL_PATH and f.get("status") in ("added", "modified") for f in files)


def route(
    files: list[dict[str, Any]],
    unknown: bool,
    draft: bool,
    repo: str,
    head_sha: str,
    merge_base: str,
    token: str | None,
    root: Path = ROOT,
) -> list[str]:
    paths = path_set(files)
    sets: set[str] = set()
    if unknown:
        sets = _persona_set("plan", draft) | _persona_set("code", draft)
    else:
        klass = merge_checks.pr_class(paths)
        sets = _persona_set(klass, draft)
        plans = plan_files(files)
        permitted = permitted_fields(root)

        def revised(item: dict[str, Any]) -> bool:
            if item.get("status") != "modified":
                return True
            name = str(item["filename"])
            return is_revision(_fetch_text(repo, name, merge_base, token), _fetch_text(repo, name, head_sha, token), permitted)

        if klass == "code" and any(revised(f) for f in plans):
            sets |= _persona_set("plan", draft)
        if klass == "plan" and plans and len(plans) == len(files) and not any(revised(f) for f in plans):
            sets |= _persona_set("code", draft)
    fixtures = fixture_routes(files, unknown, root)
    if fixtures:
        return fixtures
    return sorted(sets & bound_personas(root))


def code_personas() -> set[str]:
    """The code-class personas, from merge_checks; these are the ones the CI upstream routes."""
    return _persona_set("code", False)


def _event() -> dict[str, Any]:
    target = os.environ.get("GITHUB_EVENT_PATH")
    if not target:
        return {}
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def advisory_state(persona: str, root: Path) -> tuple[str, str]:
    """(enabled or disabled or bad, reason) for gates.<persona>.advisory_runs; an absent row reads enabled."""
    row_id = f"gates.{persona}.advisory_runs"
    try:
        data = yaml.safe_load((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return "bad", "the configuration is unreadable"
    settings = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(settings, list):
        return "bad", "the configuration has no settings list"
    rows = [r for r in settings if isinstance(r, dict) and r.get("id") == row_id]
    if not rows:
        return "enabled", ""
    if len(rows) > 1:
        return "bad", f"{row_id} appears more than once"
    defaults = rows[0].get("defaults")
    local = defaults.get("local") if isinstance(defaults, dict) else None
    cloud = defaults.get("cloud") if isinstance(defaults, dict) else None
    if local != cloud or type(local) is not str or local not in ADVISORY_STATES:
        return "bad", f"{row_id} defaults are not the same enabled or disabled string"
    return local, ""


def _existing_verdict(repo: str, token: str | None, head_sha: str, persona: str, app: int) -> bool:
    name = merge_checks.persona_check_name(persona)
    page = 1
    while page <= 10:
        query = f"check_name={urllib.parse.quote(name, safe='')}&per_page=100&page={page}"
        listed = resolve_pr.get_json(f"/repos/{repo}/commits/{head_sha}/check-runs?{query}", token)
        rows = listed.get("check_runs") if isinstance(listed, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("check_runs is not a list")
        for row in rows:
            if not isinstance(row, dict) or row.get("name") != name or (row.get("app") or {}).get("id") != app:
                continue
            if str((row.get("output") or {}).get("title") or "").startswith(VERDICT_TITLE):
                return True
        if len(rows) < 100:
            return False
        page += 1
    return False


def _gate_ci_routes(routes: list[str], root: Path, repo: str, token: str | None, head_sha: str) -> list[str]:
    kept: list[str] = []
    for persona in routes:
        state, reason = advisory_state(persona, root)
        if state == "disabled":
            print(f"PERSONA-VERDICT skip {persona}: advisory_runs disabled")
            continue
        if state == "bad":
            print(f"PERSONA-VERDICT skip {persona}: advisory_runs bad value ({reason})")
            continue
        app: Any
        try:
            app = merge_checks.verdict_app_id((root / CONFIG).read_text(encoding="utf-8"))
        except OSError:
            app = "the configuration is unreadable"
        if not isinstance(app, int) or isinstance(app, bool):
            print(f"PERSONA-VERDICT note {persona}: re-run skip not applied, verdict app id unavailable")
            kept.append(persona)
            continue
        try:
            done = _existing_verdict(repo, token, head_sha, persona, app)
        except Exception:  # noqa: BLE001 - a failed read applies no skip
            print(f"PERSONA-VERDICT note {persona}: re-run skip not applied, the check-runs read failed")
            kept.append(persona)
            continue
        if done:
            print(f"PERSONA-VERDICT skip {persona}: this head already carries a verdict check")
            continue
        kept.append(persona)
    return kept


def filter_upstream(
    upstream: str | None,
    conclusion: str,
    routes: list[str],
    path: str,
    root: Path,
    repo: str,
    token: str | None,
    head_sha: str,
) -> tuple[list[str], bool]:
    """Apply the upstream rules to route()'s result; returns (routes, fixture_allowed)."""
    code = code_personas()
    if upstream not in ("ci", "trigger"):
        print(f"PERSONA-VERDICT skip: undeclared upstream {path or 'none'}")
        return [], False
    if upstream == "trigger":
        return [r for r in routes if r not in code], True
    if conclusion == "cancelled":
        print("PERSONA-VERDICT skip: the CI run was cancelled")
        return [], False
    kept = [r for r in routes if r in code]
    if routes and not kept:
        print("PERSONA-VERDICT skip: the CI upstream routes code-class personas only")
    return _gate_ci_routes(kept, root, repo, token, head_sha), False


def is_keyed(routes: list[str], upstream: str | None, root: Path) -> bool:
    if len(routes) != 1:
        return False
    if upstream == "trigger":
        return routes[0] in execute.fixture_personas(root)
    return upstream == "ci" and routes[0] in code_personas()


def main(argv: list[str]) -> int:
    resolution = json.loads(resolve_pr.RESOLUTION.read_text(encoding="utf-8"))
    token = os.environ.get("GH_TOKEN")
    repo = os.environ["GITHUB_REPOSITORY"]
    routes: list[str] = []
    plans: list[str] = []
    fixture = False
    drill = False
    paths: list[str] = []
    carried: list[dict[str, Any]] = []
    rescout_map: dict[str, str] = {}
    inputs: dict[str, Any] | None = None
    upstream: str | None = None
    main_root = Path(os.environ["GITHUB_WORKSPACE"]) / "main" if os.environ.get("GITHUB_WORKSPACE") else ROOT
    if resolution.get("resolved") and not resolution.get("override"):
        wr = _event().get("workflow_run")
        wr = wr if isinstance(wr, dict) else {}
        path = str(wr.get("path") or "")
        upstream = UPSTREAM_PATHS.get(path)
        files, unknown, merge_base = changed_files(repo, resolution["base_ref"], resolution["head_sha"], token)
        routes = route(files, unknown, bool(resolution["draft"]), repo, resolution["head_sha"], merge_base, token)
        routes, fixture_ok = filter_upstream(
            upstream, str(wr.get("conclusion") or ""), routes, path, main_root, repo, token, resolution["head_sha"]
        )
        plans = [str(f["filename"]) for f in plan_files(files)]
        paths = fixture_paths(files, unknown)
        fixture = fixture_ok and bool(fixture_routes(files, unknown))
        drill = fixture and fallback_drill(files, unknown)
        decision = rescout.decide(repo, token, resolution, routes, plans, unknown, main_root)
        routes, carried, rescout_map, inputs = decision.routes, decision.carried, decision.rescout, decision.inputs
    ROUTES.parent.mkdir(parents=True, exist_ok=True)
    doc = {"routes": routes, "plan_paths": plans, "fixture_paths": paths, "carried": carried, "rescout": rescout_map}
    ROUTES.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    if inputs is not None:
        (ROUTES.parent / PLAN_INPUTS).write_text(json.dumps(inputs, sort_keys=True), encoding="utf-8")
    print(f"PERSONA-VERDICT routes={len(routes)} {' '.join(routes)}".rstrip() + f" carried={len(carried)}")
    keyed = is_keyed(routes, upstream, main_root)
    resolve_pr.write_output(
        routes=str(len(routes)),
        fixture="true" if fixture else "false",
        fallback_drill="true" if drill else "false",
        keyed="true" if keyed else "false",
    )
    return 0
