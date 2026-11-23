#!/usr/bin/env python3
"""Range walk and covering logic for the Decision 201 trailer gate (Decision 254 cl.4; PLAN-trailer-range-closure).

The push run whose full tier covers a merge (its covering run) censuses that merge's still-open junit-routed recs and binds
their junit verdict to the merge commit (closing_sha); a run whose own full tier did not start binds none and leaves those
recs open (DEFERRED), never an acceptance refusal. The trailer parser is passed in as parse_trailer; api, git and reader are
injected; no module-level I/O. `covering --sha M` names the covering run of a merge read-only.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, Optional

from scripts.ci import last_green as lg
from scripts.ci.deploy_gate import GateError, Git, GithubApi
from scripts.ci_rca import range_context

DEFERRED_MARKER = "TRAILER-RANGE DEFERRED"
UNREADABLE_MARKER = "TRAILER-RANGE UNREADABLE"
ANNOTATION_TITLE = "trailer-range"
_ROUTE_JUNIT = "junit"
_STARTED = "started"


def run_covers(api: Any, head: str) -> Optional[bool]:
    try:
        return lg.full_tier_state(api, head) == _STARTED
    except GateError:
        return None


def range_merges(api: Any, git: Git, head: str, *, parse_trailer: Callable[[str], list[str]]) -> dict[str, Any]:
    rng = range_context.compute_range(api, git, head, range_context.CI_SLUG)
    prev, status = rng["prev"], rng["range_status"]
    merges: list[dict[str, Any]] = []
    if status != "unresolved":
        try:
            for sha in [c for c in reversed(rng["commits"]) if c != head]:
                if status == "widened" and lg.full_tier_state(api, sha) == _STARTED:
                    continue
                ids = parse_trailer(git.out("log", "-1", "--format=%B", sha))
                if ids:
                    merges.append({"sha": sha, "ids": ids})
        except GateError:
            return {"prev": prev, "range_status": "unresolved", "merges": []}
    return {"prev": prev, "range_status": status, "merges": merges}


def extend_census(
    head: str,
    ids: list[str],
    entries: list[dict[str, Any]],
    *,
    census_fn: Callable[..., list[dict[str, Any]]],
    parse_trailer: Callable[[str], list[str]],
    reader: Any,
    api: Any,
    git: Git,
) -> dict[str, Any]:
    doc_ids, doc_entries = list(ids), list(entries)
    covers = run_covers(api, head)
    prev, status = None, "not_walked"
    if covers is True:
        found = range_merges(api, git, head, parse_trailer=parse_trailer)
        prev, status = found["prev"], found["range_status"]
        taken = set(doc_ids)
        for merge in found["merges"]:
            new = [i for i in merge["ids"] if i not in taken]
            taken.update(new)
            if not new:
                continue
            rows = [dict(r) for r in reader.named("recs_by_ids", ids=",".join(new)) if r.get("status") == "open"]
            if not rows:
                continue
            kept = [
                e
                for e in census_fn([r["id"] for r in rows], rows=rows, closing_sha=merge["sha"])
                if e["route"] == _ROUTE_JUNIT
            ]
            doc_entries += kept
            doc_ids += [e["rec_id"] for e in kept]
    range_recs = doc_ids[len(ids) :]
    print(f"[trailer_range] covers={covers} prev={prev} range_status={status} range_recs={range_recs}")
    if status == "unresolved":
        print(f"::error title={ANNOTATION_TITLE}::range unresolved; earlier merges' junit recs not censused")
    return {"ids": doc_ids, "entries": doc_entries, "sha": head, "covers": covers, "prev": prev, "range_status": status}


def _announce(line: str) -> None:
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def close_groups(census_doc: dict[str, Any], head: str) -> list[tuple[str, list[str]]]:
    ids = list(census_doc.get("ids") or [])
    entries = {e["rec_id"]: e for e in census_doc.get("entries") or [] if isinstance(e, dict) and e.get("rec_id")}
    covers = census_doc.get("covers", True)
    held: set[str] = set()
    if covers is not True:
        for rec_id in ids:
            if (entries.get(rec_id) or {}).get("route") == _ROUTE_JUNIT:
                held.add(rec_id)
                if covers is False:
                    why = f"run {head} did not run the full tier; its covering run binds it"
                    _announce(f"{DEFERRED_MARKER}: {rec_id} left OPEN -- {why}")
                else:
                    why = f"full-tier state of {head} unreadable; re-run trailer-census"
                    _announce(f"{UNREADABLE_MARKER}: {rec_id} left OPEN -- {why}")
    groups: dict[str, list[str]] = {head: []}
    for rec_id in ids:
        if rec_id not in held:
            groups.setdefault((entries.get(rec_id) or {}).get("closing_sha") or head, []).append(rec_id)
    return [(sha, group) for sha, group in groups.items() if group]


def cmd_covering(args: argparse.Namespace, api: Any = None, git: Optional[Git] = None) -> int:
    api = api or GithubApi()
    git = git or Git()
    try:
        merge = git.out("rev-parse", f"{args.sha}^{{commit}}")
        main_line = git.out("rev-list", "--first-parent", "origin/main").split()
        if merge not in main_line:
            print("COVERING not-on-main")
            return 2
        for sha in reversed(main_line[: main_line.index(merge) + 1]):
            if lg.full_tier_state(api, sha) == _STARTED:
                print(f"COVERING {sha}")
                return 0
    except GateError as exc:
        print(f"COVERING unreadable {exc}")
        return 2
    print("COVERING pending")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="trailer_range")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cov = sub.add_parser("covering")
    cov.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    return cmd_covering(args)


if __name__ == "__main__":
    sys.exit(main())
