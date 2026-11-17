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

from scripts.personas import resolve_pr
from scripts.session import merge_checks

ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = "tests/fixtures/persona_verdict/fixture_pr/"
PROBE_STEMS = ("proceed", "revise", "fail", "malformed")
PROBE_PREFIX = "verdict_probe."
PLAN_PATH = re.compile(r"docs/plans/PLAN-[^/]+\.yaml")
PLAN_SIZE_CAP = 512 * 1024
COMPARE_LIMIT = 300
SCOPE_CONTRACT = "docs/contracts/implement-scope-boundary.yaml"
PERSONAS_CONTRACT = "docs/contracts/executor-personas.yaml"
ROUTES = Path("prepared") / "routes.json"


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


def probe_routes(files: list[dict[str, Any]], unknown: bool) -> list[str]:
    if unknown or not files:
        return []
    paths = path_set(files)
    if not all(p.startswith(PROBE_DIR) for p in paths):
        return []
    stems = {
        p[len(PROBE_DIR) : -len(".txt")]
        for p in (str(f.get("filename")) for f in files if f.get("status") != "removed")
        if p.endswith(".txt") and "/" not in p[len(PROBE_DIR) :]
    }
    return [PROBE_PREFIX + s for s in PROBE_STEMS if s in stems]


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
    bound = bound_personas(root)
    return sorted(sets & bound) + probe_routes(files, unknown)


def main(argv: list[str]) -> int:
    resolution = json.loads(resolve_pr.RESOLUTION.read_text(encoding="utf-8"))
    token = os.environ.get("GH_TOKEN")
    repo = os.environ["GITHUB_REPOSITORY"]
    routes: list[str] = []
    plans: list[str] = []
    if resolution.get("resolved"):
        files, unknown, merge_base = changed_files(repo, resolution["base_ref"], resolution["head_sha"], token)
        routes = route(files, unknown, bool(resolution["draft"]), repo, resolution["head_sha"], merge_base, token)
        plans = [str(f["filename"]) for f in plan_files(files)]
    ROUTES.parent.mkdir(parents=True, exist_ok=True)
    ROUTES.write_text(json.dumps({"routes": routes, "plan_paths": plans}, sort_keys=True), encoding="utf-8")
    print(f"PERSONA-VERDICT routes={len(routes)} {' '.join(routes)}".rstrip())
    resolve_pr.write_output(routes=str(len(routes)))
    return 0
