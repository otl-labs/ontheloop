"""The /implement merge step as checked verbs (docs/contracts/git-ops.yaml push_pr_ci_merge_flow).

  premerge --slug S --message-file F [--expect HEX]   gate a squash-merge: trailer, acceptance preview,
                                                       approved text, Decision-number collision, stale base;
                                                       prints the HEAD to merge
  readback --slug S --sha SHA                          the merge commit carries the plan's trailer
  recover  --slug S --sha SHA                          verdict-bound close of recs a lost trailer skipped

The expected rec set is the plan's bundled_recommendations when the plan reads implementation_declared
true at the ref checked, else empty. Premerge and readback write nothing; recover closes only through
the Decision 201 verdict gate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
_FATAL_PREVIEW = frozenset({"fails", "out_of_grammar"})
_REMEDY_AMEND_PLAN = "remove it from bundled_recommendations by a plan amendment PR"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _plan_rel(slug: str) -> str:
    return f"docs/plans/PLAN-{slug}.yaml"


def plan_at(ref: str, slug: str, root: Path) -> dict[str, Any] | None:
    result = _git(root, "show", f"{ref}:{_plan_rel(slug)}")
    if result.returncode != 0:
        return None
    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def expected_set(plan: dict[str, Any]) -> list[str]:
    return list(plan.get("bundled_recommendations") or []) if plan.get("implementation_declared") else []


def undeclared_line(slug: str, ref: str) -> str:
    return (
        f"{slug}: implementation_declared is false at {ref} -- set it ('Run the full gate locally first' item 3), "
        "commit, re-run CI; never drop the trailer"
    )


def _fetch_rows(ids: list[str]) -> list[dict[str, Any]]:
    from src.common.ducklake_reader_client import fetch_recs_by_ids, make_reader  # noqa: PLC0415

    rows, _missing = fetch_recs_by_ids(make_reader(), ids)
    return rows


def _rev(root: Path, ref: str) -> str:
    result = _git(root, "rev-parse", ref)
    return result.stdout.strip() if result.returncode == 0 else ""


def _parsed_trailer(message: str) -> list[str]:
    from scripts.rec_trailer import parse_resolves_trailer  # noqa: PLC0415

    return parse_resolves_trailer(message)


def _preview(expected: list[str], root: Path, head: str) -> bool:
    if not expected:
        return True
    try:
        rows = _fetch_rows(expected)
    except Exception as exc:  # noqa: BLE001 -- a reader failure is reported, never a holds verdict
        print(f"PREVIEW unavailable: {exc}")
        return True
    by_id = {r["id"]: r for r in rows}
    ok = True
    live: list[dict[str, Any]] = []
    for rec_id in expected:
        status = (by_id.get(rec_id) or {}).get("status")
        if rec_id not in by_id:
            print(f"PREVIEW {rec_id} missing")
            ok = False
        elif status == "closed":
            print(f"PREVIEW {rec_id} already-closed")
        elif status in ("superseded", "declined"):
            print(f"PREVIEW {rec_id} already-{status}: {_REMEDY_AMEND_PLAN}")
            ok = False
        else:
            live.append(by_id[rec_id])
    if not live:
        return ok
    from scripts import rec_trailer_acceptance as acceptance  # noqa: PLC0415

    entries = acceptance.census([r["id"] for r in live], rows=live, repo_root=root)
    for entry in entries:
        if entry.get("route") == "junit":
            entry["route"] = "static"
    for record in acceptance.evaluate(entries, root, head)["records"]:
        print(f"PREVIEW {record['rec_id']} {record['verdict']} arm={record['arm']}")
        ok = ok and record["verdict"] not in _FATAL_PREVIEW
    return ok


def _sidecar_blocks_stale(sidecar: Any, root: Path) -> list[str]:
    from scripts.roadmap import approved_text  # noqa: PLC0415

    stale = []
    for block in sidecar.blocks:
        shown = _git(root, "show", f"origin/main:{block.file}")
        base_words = approved_text.words(shown.stdout if shown.returncode == 0 else "")
        before = approved_text.words(block.before)
        if before:
            fresh = len(approved_text.find_all(base_words, before)) == 1
        else:
            fresh = not base_words
        if not fresh:
            stale.append(block.id)
    return stale


def _approved_text(slug: str, root: Path, expect: str | None) -> bool:
    from scripts.roadmap import approved_text  # noqa: PLC0415

    base = _git(root, "merge-base", "origin/main", "HEAD").stdout.strip() or "origin/main"
    try:
        sidecar = approved_text.load_sidecar_at(base, slug, root)
    except approved_text.ApprovedTextError as exc:
        print(f"PREMERGE APPROVED-TEXT ERROR {exc}")
        return False
    if sidecar is None:
        return True
    ok = True
    state = approved_text.sidecar_state(slug, root, base)
    if state != "unchanged":
        print(f"PREMERGE APPROVED-TEXT SIDECAR {state}")
        return False
    for message in approved_text.verify(sidecar, root, base):
        print(f"PREMERGE APPROVED-TEXT MISMATCH {message}")
        ok = False
    found = approved_text.digest(sidecar)
    if expect and expect != found:
        print(f"PREMERGE APPROVED-TEXT MISMATCH digest {found} != expected {expect}")
        ok = False
    if ok:
        print(f"PREMERGE APPROVED-TEXT OK {found}")
    for block_id in _sidecar_blocks_stale(sidecar, root):
        print(f"PREMERGE STALE BASE {block_id}: rebase and re-run CI")
        ok = False
    return ok


def _decision_collisions(root: Path) -> bool:
    from scripts.session import decision_renumber  # noqa: PLC0415

    found = decision_renumber.collisions(root)
    for old, new in sorted(found.items()):
        print(
            f"PREMERGE DECISION COLLISION {old} -> {new}: run git fetch origin main && "
            "bin/venv-python -m scripts.session.rebase_resolve, then the full tier, push and wait for CI"
        )
    return not found


def cmd_premerge(args: argparse.Namespace) -> int:
    root = args.root
    _git(root, "fetch", "origin", "main")
    head = _rev(root, "HEAD")
    print(f"PREMERGE HEAD {head}")
    plan = plan_at("HEAD", args.slug, root)
    if plan is None:
        print(f"PREMERGE ERROR no readable {_plan_rel(args.slug)} at HEAD")
        return 2
    expected = expected_set(plan)
    parsed = _parsed_trailer(Path(args.message_file).read_text(encoding="utf-8"))
    ok = True
    if set(parsed) != set(expected):
        ok = False
        if not plan.get("implementation_declared") and parsed:
            print(f"PREMERGE UNDECLARED {undeclared_line(args.slug, 'HEAD')}")
        else:
            print(f"PREMERGE TRAILER MISMATCH expected={expected} parsed={parsed}")
            from scripts.checks.verification.validate_merge_step import trailer_remedy  # noqa: PLC0415

            for remedy in trailer_remedy(expected, parsed):
                print(f"  remedy: {remedy}")
    ok = _preview(expected, root, head) and ok
    ok = _approved_text(args.slug, root, args.expect) and ok
    ok = _decision_collisions(root) and ok
    return 0 if ok else 1


def _readback_state(slug: str, sha: str, root: Path) -> tuple[list[str], list[str], list[str], bool] | None:
    """(expected, missing, extra, declared) at `sha`, or None when the plan is unreadable there."""
    plan = plan_at(sha, slug, root)
    if plan is None:
        return None
    expected = expected_set(plan)
    parsed = _parsed_trailer(_git(root, "log", "-1", "--format=%B", sha).stdout)
    missing = [r for r in expected if r not in parsed]
    extra = [r for r in parsed if r not in expected]
    return expected, missing, extra, bool(plan.get("implementation_declared"))


def cmd_readback(args: argparse.Namespace) -> int:
    root = args.root
    _git(root, "fetch", "origin", "main")
    state = _readback_state(args.slug, args.sha, root)
    if state is None:
        print(f"READBACK ERROR no readable {_plan_rel(args.slug)} at {args.sha}")
        return 2
    expected, missing, extra, declared = state
    if not missing and not extra:
        print(f"READBACK OK {args.sha} recs={expected}")
        return 0
    if missing:
        print(f"READBACK MISSING {args.sha} missing={missing}")
    if extra:
        print(f"READBACK UNEXPECTED {args.sha} extra={extra}")
        if not declared:
            print(f"  remedy: {undeclared_line(args.slug, args.sha)}")
    return 1


def cmd_recover(args: argparse.Namespace) -> int:
    root = args.root
    from scripts.checks import _common  # noqa: PLC0415

    rel = _plan_rel(args.slug)
    sha = _rev(root, args.sha) or args.sha
    if not _common._plan_declared_at_ref(rel, root, sha) or _common._plan_declared_at_ref(rel, root, f"{sha}^"):
        print(f"RECOVER refused: {sha} is not the merge that flipped {args.slug} to implementation_declared")
        return 2
    state = _readback_state(args.slug, sha, root)
    missing = state[1] if state else []
    if not missing:
        print(f"RECOVER refused: readback at {sha} reports no missing recs")
        return 2
    from scripts import rec_trailer_acceptance as acceptance  # noqa: PLC0415
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer  # noqa: PLC0415
    from scripts.verification_graduation import GraduationError, git_worktree  # noqa: PLC0415

    try:
        rows = _fetch_rows(missing)
    except Exception as exc:  # noqa: BLE001
        print(f"RECOVER rec reader unavailable: {exc}")
        return 2
    try:
        with git_worktree(sha, root) as worktree:
            entries = acceptance.census(missing, rows=rows, repo_root=worktree)
            verdict_doc = acceptance.evaluate(entries, worktree, sha)
    except GraduationError as exc:
        print(f"RECOVER worktree failed: {exc}")
        return 2
    verdicts = acceptance.rejoin({"sha": sha, "ids": missing, "entries": entries}, verdict_doc, None)
    for entry in entries:
        if entry.get("route") == "junit":
            print(f"RECOVER {entry['rec_id']} junit-routed: left open for the operator")
    cache = {r["id"]: dict(r) for r in rows}
    return close_recs_from_trailer(
        missing,
        sha,
        "merge_step recover (operator session, no workflow run)",
        cache,
        acceptance_verdicts=verdicts,
        require_acceptance_verdict=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.session.merge_step")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("premerge", cmd_premerge), ("readback", cmd_readback), ("recover", cmd_recover)):
        p = sub.add_parser(name)
        p.add_argument("--slug", required=True)
        p.add_argument("--root", type=Path, default=ROOT)
        p.set_defaults(handler=handler)
        if name == "premerge":
            p.add_argument("--message-file", required=True)
            p.add_argument("--expect", default=None)
        else:
            p.add_argument("--sha", required=True)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
