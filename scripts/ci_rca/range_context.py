"""ci-rca's range step (Decision 254 cl.4; docs/contracts/full-tier-range.yaml): thin CLI over scripts.ci.last_green.

resolve: the failing run's commit range, recorded else resolved else widened to last-green else head alone.
probe: live read-only check that resolve_prev passes over plans-only main commits.
observe: read-only check that the deployed range step completed on the first ci-rca run after a merge.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from scripts.ci import last_green as lg
from scripts.ci import push_scope
from scripts.ci.deploy_gate import GateError, Git, GithubApi

CI_SLUG = "ci"
RANGE_JSON = "/tmp/ci-rca-range.json"
NOTICE_TITLE = "ci-rca-range"
RANGE_STEP = "Resolve run range"
RCA_JOB = "rca"


def _strict_ancestor(git: Git, prev: Optional[str], head: str) -> bool:
    if not prev or not git.ok("cat-file", "-e", f"{prev}^{{commit}}"):
        return False
    if git.out("rev-parse", f"{prev}^{{commit}}") == git.out("rev-parse", f"{head}^{{commit}}"):
        return False
    return git.ok("merge-base", "--is-ancestor", prev, head)


def _recorded_prev(path: Optional[str]) -> Optional[str]:
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    prev = data.get("prev") if isinstance(data, dict) else None
    return prev if isinstance(prev, str) and prev else None


def compute_range(api: Any, git: Git, head: str, slug: str, recorded_range: Optional[str] = None) -> dict[str, Any]:
    if slug != CI_SLUG:
        return {"head": head, "prev": None, "range_status": "head_only", "commits": [head], "prs": []}
    status, prev = "unresolved", None
    try:
        candidate = _recorded_prev(recorded_range)
        status = "recorded"
        if candidate is None:
            candidate, status = lg.resolve_prev(api, git, head), "resolved"
        if _strict_ancestor(git, candidate, head):
            prev = candidate
        else:
            green = lg.last_green_sha(api, git, ref=head)
            if _strict_ancestor(git, green, head):
                prev, status = green, "widened"
        if prev is None:
            status = "unresolved"
            commits = [head]
        else:
            commits = git.out("rev-list", "--first-parent", "--reverse", f"{prev}..{head}").split() or [head]
    except GateError:
        prev, status, commits = None, "unresolved", [head]
    return {"head": head, "prev": prev, "range_status": status, "commits": commits, "prs": _prs(api, commits)}


def _prs(api: Any, commits: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sha in commits:
        number = None
        try:
            pulls = api.get(f"commits/{sha}/pulls")
            if isinstance(pulls, list) and pulls:
                number = pulls[0].get("number")
        except GateError:
            number = None
        out.append({"sha": sha, "pr": number})
    return out


def _emit_outputs(outputs: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{k}={v}\n" for k, v in outputs.items())


def cmd_resolve(args: argparse.Namespace, api: Any = None, git: Optional[Git] = None) -> int:
    run = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
    head = run["head_sha"]
    api = api or GithubApi(repo=args.repo)
    git = git or Git()
    result = compute_range(api, git, head, args.workflow_slug, args.recorded_range)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    is_ci = args.workflow_slug == CI_SLUG
    if not is_ci:
        diff_base = "HEAD^"
    else:
        diff_base = result["prev"] if result["range_status"] != "unresolved" else ""
    note = ""
    if is_ci:
        note = (
            f" The commit range this CI run covers, and each commit's pull request, is in {RANGE_JSON}; "
            "diagnose over the whole range."
        )
    _emit_outputs({"range_status": result["range_status"], "diff_base": diff_base or "", "prompt_note": note})
    print(f"::notice title={NOTICE_TITLE}::range_status={result['range_status']} diff_base={diff_base or ''}")
    return 0


def cmd_probe(args: argparse.Namespace, api: Any = None, git: Optional[Git] = None) -> int:
    api = api or GithubApi()
    git = git or Git()
    try:
        shas = git.out("rev-list", "--first-parent", "-n", str(args.depth), args.ref).split()
        states = {sha: lg.full_tier_state(api, sha) for sha in shas}
        nearest = next((s for s in shas[1:] if states[s] == "started"), None)
        if lg.resolve_prev(api, git, shas[0], limit=args.depth) != nearest:
            print("probe=fail resolve_prev did not return the nearest started commit")
            return 1
        skipped = started = 0
        for sha in shas:
            state = states[sha]
            if state == "absent":
                continue
            if state == "skipped" and not lg.fast_tier_ran(api, sha):
                continue
            if not git.ok("rev-parse", "--verify", "-q", f"{sha}^"):
                continue
            plans_only = push_scope.confined_to_plans(push_scope.changed_paths(f"{sha}^", sha, git.cwd))
            if state == "started":
                started += 1
                if plans_only:
                    print(f"probe=fail oracle says plans-only for started commit {sha}")
                    return 1
            else:
                skipped += 1
                if not plans_only:
                    print(f"probe=fail oracle says not plans-only for skipped commit {sha}")
                    return 1
    except GateError as exc:
        print(f"probe=fail {exc}")
        return 1
    if skipped == 0 or started == 0:
        print(f"probe=fail window held skipped={skipped} started={started}; widen --depth")
        return 1
    print(f"probe=ok skipped={skipped} started={started}")
    return 0


def _range_notice(api: Any, job_id: int) -> Optional[str]:
    notes = api.get(f"check-runs/{job_id}/annotations?per_page=100")
    for note in notes if isinstance(notes, list) else []:
        if note.get("title") == NOTICE_TITLE or NOTICE_TITLE in str(note.get("message", "")):
            found = re.search(r"range_status=(\w+)", str(note.get("message", "")))
            return found.group(1) if found else ""
    return None


def cmd_observe(args: argparse.Namespace, api: Any = None, git: Optional[Git] = None) -> int:
    api = api or GithubApi()
    git = git or Git()
    try:
        since = git.out("show", "-s", "--format=%cI", args.merge_sha)
        runs: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urllib.parse.urlencode({"status": "completed", "created": f">={since}", "per_page": 100, "page": page})
            batch = api.get(f"actions/workflows/ci-rca.yml/runs?{query}").get("workflow_runs", [])
            runs.extend(r for r in batch if r.get("conclusion") != "skipped")
            if len(batch) < 100:
                break
            page += 1
        for run in sorted(runs, key=lambda r: str(r.get("created_at", ""))):
            jobs = api.get(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100").get("jobs", [])
            job = next((j for j in jobs if j.get("name") == RCA_JOB), None)
            step = lg._step(job, RANGE_STEP) if job else None
            if not job or not step or step.get("conclusion") == "skipped":
                continue
            status = _range_notice(api, job["id"])
            if status is None:
                print(f"observe={run['id']} slug=ci resolve=incomplete")
                return 1
            print(f"observe={run['id']} slug=ci resolve=completed range_status={status}")
            return 0
    except GateError as exc:
        print(f"observe=error {exc}")
        return 1
    print("observe=none-yet")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="range_context")
    sub = parser.add_subparsers(dest="cmd", required=True)
    res = sub.add_parser("resolve")
    res.add_argument("--run-json", required=True)
    res.add_argument("--workflow-slug", required=True)
    res.add_argument("--repo", required=True)
    res.add_argument("--out", default=RANGE_JSON)
    res.add_argument("--recorded-range", default=None)
    probe = sub.add_parser("probe")
    probe.add_argument("--ref", required=True)
    probe.add_argument("--depth", type=int, required=True)
    obs = sub.add_parser("observe")
    obs.add_argument("--merge-sha", required=True)
    args = parser.parse_args(argv)
    return {"resolve": cmd_resolve, "probe": cmd_probe, "observe": cmd_observe}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
