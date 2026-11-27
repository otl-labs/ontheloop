"""The review-context verb: the GitHub-shaped inputs of a code-class route, fetched once and written as prepared data.

Run in the prepare job with GH_TOKEN only when routes.json routes a code-class persona, else a no-op line. It writes
prepared/review-context.json in the shape review_input reads (compare from the merge base to head, merge-base texts of the
changed or removed Python files, the head's check runs with app id and slug, the issue comments, the author's association
and the head ref). Any API error writes failure-<route>.json input_incomplete instead and never a partial file. The one
stdlib GitHub client is resolve_pr.request; nothing here reads a secret but GH_TOKEN, and persona code never calls it.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from scripts.personas import resolve_pr
from scripts.personas import route as route_mod

CONTEXT = Path("prepared") / "review-context.json"
BASE_PREFIXES = ("tests/", "scripts/", "src/")
PAGE = 100
MAX_PAGES = 10
KEEP_BASE = ("modified", "changed", "removed", "renamed")


class FetchError(Exception):
    pass


def _get(path: str, token: str | None, accept: str = "application/vnd.github+json") -> tuple[int, bytes]:
    return resolve_pr.request("GET", path, token, accept=accept)


def _json_pages(path: str, token: str | None, key: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        status, raw = _get(f"{path}{joiner}per_page={PAGE}&page={page}", token)
        if status != 200:
            raise FetchError(f"GET {path} returned {status}")
        data = json.loads(raw)
        batch = data.get(key) if key and isinstance(data, dict) else data
        if not isinstance(batch, list):
            raise FetchError(f"GET {path} did not return a list")
        rows.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < PAGE:
            return rows
    raise FetchError(f"GET {path} has more than {MAX_PAGES} pages")


def _compare_diff(files: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in files:
        name = str(item.get("filename"))
        old = str(item.get("previous_filename") or name)
        patch = item.get("patch")
        if isinstance(patch, str) and patch:
            parts.append(f"diff --git a/{old} b/{name}\n--- a/{old}\n+++ b/{name}\n{patch}")
        else:
            parts.append(f"diff --git a/{old} b/{name}\npatch omitted: {name}")
    return "\n".join(parts)


def _base_paths(files: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in files:
        if item.get("status") not in KEEP_BASE:
            continue
        name = str(item.get("previous_filename") or item.get("filename"))
        if name.endswith(".py") and name.startswith(BASE_PREFIXES) and name not in paths:
            paths.append(name)
    return paths


def _base_text(repo: str, path: str, ref: str, token: str | None) -> str | None:
    status, raw = _get(
        f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}", token, accept="application/vnd.github.raw+json"
    )
    if status == 404:
        return None
    if status != 200:
        raise FetchError(f"GET contents {path} returned {status}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def fetch(repo: str, resolution: dict[str, Any], token: str | None) -> dict[str, Any]:
    head = str(resolution["head_sha"])
    files, truncated, merge_base = route_mod.changed_files(repo, str(resolution["base_ref"]), head, token)
    if not merge_base:
        raise FetchError("the compare read named no merge base")
    texts: dict[str, str] = {}
    for path in _base_paths(files):
        text = _base_text(repo, path, merge_base, token)
        if text is not None:
            texts[path] = text
    runs = _json_pages(f"/repos/{repo}/commits/{head}/check-runs", token, "check_runs")
    checks = [
        {
            "name": str(r.get("name") or ""),
            "status": str(r.get("status") or ""),
            "conclusion": r.get("conclusion"),
            "summary": str((r.get("output") or {}).get("summary") or ""),
            "app_id": (r.get("app") or {}).get("id"),
            "app_slug": (r.get("app") or {}).get("slug"),
        }
        for r in runs
    ]
    number = int(resolution["number"])
    comments = _json_pages(f"/repos/{repo}/issues/{number}/comments", token)
    pull = resolve_pr.get_json(f"/repos/{repo}/pulls/{number}", token)
    pull = pull if isinstance(pull, dict) else {}
    return {
        "merge_base_sha": merge_base,
        "truncated": truncated,
        "files": [
            {k: item[k] for k in ("filename", "status", "previous_filename") if item.get(k) is not None} for item in files
        ],
        "diff": _compare_diff(files),
        "merge_base_texts": texts,
        "check_runs": checks,
        "body_author_association": str(pull.get("author_association") or ""),
        "comments": [
            {"body": str(c.get("body") or ""), "author_association": str(c.get("author_association") or "")} for c in comments
        ],
        "head_ref": str((pull.get("head") or {}).get("ref") or ""),
    }


def main(argv: list[str]) -> int:
    routes_doc = json.loads(route_mod.ROUTES.read_text(encoding="utf-8"))
    code = route_mod.code_personas()
    wanted = [r for r in routes_doc.get("routes", []) if r in code]
    if not wanted:
        print("PERSONA-VERDICT review-context skip: no code-class route")
        return 0
    resolution = json.loads(resolve_pr.RESOLUTION.read_text(encoding="utf-8"))
    CONTEXT.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = fetch(os.environ["GITHUB_REPOSITORY"], resolution, os.environ.get("GH_TOKEN"))
    except Exception as exc:  # noqa: BLE001 - any API or parse fault is input_incomplete, never a partial file
        for route_id in wanted:
            (CONTEXT.parent / f"failure-{route_id}.json").write_text(
                json.dumps({"failure": "input_incomplete", "detail": f"the review context could not be fetched: {exc}"}),
                encoding="utf-8",
            )
        print(f"PERSONA-VERDICT review-context failed: input_incomplete ({exc})")
        return 0
    CONTEXT.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    print(f"PERSONA-VERDICT review-context files={len(doc['files'])} truncated={str(doc['truncated']).lower()}")
    return 0
