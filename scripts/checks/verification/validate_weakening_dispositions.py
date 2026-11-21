"""Weakening-disposition completeness gate -- BLOCKING, per diff (PLAN-test-weakening-surfacing, rec-3357,
Decision 181 point 2's narrowed CONTENT residual; re-keyed from the plan flip to the diff by rec-5441).

On every diff, each weakening candidate `validate_weakening_candidates.find_candidates` lists must match
exactly one entry in the `weakening_dispositions` of the plans the diff touches (keyed "[category]
path::node"), carrying one `review_dispositions` key other than "weakening" and a cite that resolves under that
key's own rule (read from docs/contracts/implement-scope-boundary.yaml, no second copy). A diff with candidates that
touches no plan fails, except a tests-only diff (Decision 226 fast lane, report-only, rec-4442). An entry the diff
adds or changes must name a candidate the diff has, unless the diff changes only docs/plans/**; entries already on
the base are history and are never re-checked as stale. No marker, waiver or grandfather. This proves every
candidate received a recorded disposition whose cite resolves -- never that the disposition is RIGHT, nor who wrote
it (Decision 165's existence-versus-authorization distinction).

In push context a multi-merge base..HEAD range is judged one first-parent commit at a time (Decision 254).
"""

from __future__ import annotations

import argparse
import ast
import functools
import re
import sys
from pathlib import Path
from typing import Callable

import yaml

from scripts.checks import _common, registry
from scripts.checks.verification.validate_weakening_candidates import Candidate, find_candidates

_FILE_NAME_TOKEN_RE = re.compile(r"^(?P<file>.+\.py)::(?P<name>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)$")
_PLANNED_CHANGE_RE = re.compile(r"^(?P<field>acceptance_criteria|test_obligations)\[(?P<idx>\d+)\]$")
_REC_TOKEN_RE = re.compile(r"\brec-\d+\b")
_ENV_PREFIX = "env: "


def _read_file(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_file_at_ref(root: Path, ref: str, rel: str) -> str | None:
    result = _common.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout


def _read_at(root: Path, head: str | None, rel: str) -> str | None:
    return _read_file(root, rel) if head is None else _read_file_at_ref(root, head, rel)


def _plan_doc_at(root: Path, ref: str | None, plan_rel: str) -> dict | None:
    text = _read_at(root, ref, plan_rel)
    if text is None:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _review_dispositions_from_contract(root: Path) -> dict[str, str]:
    doc = _plan_doc_at(root, None, "docs/contracts/implement-scope-boundary.yaml") or {}
    return (doc.get("invariants", {}).get("content", {}).get("review_dispositions")) or {}


def _defines_name(source: str | None, name: str) -> bool:
    if source is None:
        return False
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    parts = name.split("::")
    if len(parts) == 1:
        target = parts[0]
        defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        return any(isinstance(n, defs) and n.name == target for n in tree.body)
    class_name, member = parts
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return any(isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == member for sub in node.body)
    return False


def _cite_resolves(
    disposition: str,
    cite: str,
    candidate: Candidate,
    *,
    root: Path,
    base: str,
    deleted_paths: set[str],
    head: str | None = None,
) -> bool:
    if not cite:
        return False
    if disposition in {"refactor", "growth-safe-pin"}:
        m = _FILE_NAME_TOKEN_RE.match(cite)
        if not m:
            return False
        return _defines_name(_read_at(root, head, m.group("file")), m.group("name"))

    if disposition == "retirement":
        if cite in deleted_paths:
            return True
        m = _FILE_NAME_TOKEN_RE.match(cite)
        if not m:
            return False
        rel, name = m.group("file"), m.group("name")
        own_token = f"{candidate.path}::{candidate.node}"
        if rel == candidate.path and (name == candidate.node or f"{rel}::{name}" == own_token):
            return False
        defined_at_base = _defines_name(_read_file_at_ref(root, base, rel), name)
        defined_now = _defines_name(_read_at(root, head, rel), name)
        return defined_at_base and not defined_now

    if disposition == "owned-skip":
        moved_with_gained_skip = (
            candidate.category == "test-moved-weaker"
            and "gained skip" in candidate.detail
            and "lost assertion" not in candidate.detail
        )
        eligible = candidate.category == "skip-added" or moved_with_gained_skip
        if not eligible:
            return False
        rec_match = _REC_TOKEN_RE.search(cite)
        if rec_match and rec_match.group(0) in candidate.detail:
            return True
        return cite.startswith(_ENV_PREFIX) and cite in candidate.detail

    return False


def _planned_change_resolves(cite: str, root: Path, base: str, resolved_plans: list[str], head: str | None = None) -> bool:
    m = _PLANNED_CHANGE_RE.match(cite)
    if not m:
        return False
    field, idx = m.group("field"), int(m.group("idx"))
    for plan_rel in resolved_plans:
        base_doc = _plan_doc_at(root, base, plan_rel)
        net_new = base_doc is None
        doc = _plan_doc_at(root, head, plan_rel) if net_new else base_doc
        if doc is None:
            continue
        items = doc.get(field) or []
        if 0 <= idx < len(items):
            return True
    return False


def _entries(doc: dict | None) -> list[dict]:
    return [e for e in (doc or {}).get("weakening_dispositions") or [] if isinstance(e, dict)]


def run_check(
    failed: list[str],
    *,
    root: Path,
    base: str,
    status_changed: list[tuple[str, str]],
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
    head: str | None = None,
) -> tuple[int, int]:
    """Judge one diff; returns (candidate count, touched plan count)."""
    candidates = find_candidates(status_changed, read_base=read_base, read_head=read_head)
    deleted_paths = {p for s, p in status_changed if s == "D"}
    touched = _common.plan_paths_from_changed([p for s, p in status_changed if s in {"A", "M", "??"}])
    plans = [p for p in touched if _plan_doc_at(root, head, p) is not None]
    changed_paths = [p for _, p in status_changed]
    candidate_by_key = {c.key(): c for c in candidates}

    if candidates and not plans:
        if all(p.startswith("tests/") for p in changed_paths):
            print("  NOTE: tests-only diff with no plan -- report-only (Decision 226 fast lane, rec-4442).")
        else:
            failed.append(
                f"weakening-dispositions: candidates {sorted(candidate_by_key)!r} on a diff that touches no plan -- record "
                "each in the build's plan weakening_dispositions in this PR (a revert PR: in the reverted build's plan)"
            )
        print(f"  {len(candidates)} candidate(s), 0 touched plan(s) -- {len(failed)} issue(s).")
        return len(candidates), 0

    plans_only = all(p.startswith("docs/plans/") for p in changed_paths)
    recorded: dict[str, list[tuple[str, str, bool]]] = {}
    for plan_rel in plans:
        base_entries = _entries(_plan_doc_at(root, base, plan_rel))
        for entry in _entries(_plan_doc_at(root, head, plan_rel)):
            key = entry.get("candidate")
            if key is None:
                continue
            recorded.setdefault(key, []).append(
                (entry.get("disposition", ""), entry.get("cite", ""), entry not in base_entries)
            )

    review_dispositions = _review_dispositions_from_contract(root)

    for key, entries in recorded.items():
        any_new = any(new for _, _, new in entries)
        if len(entries) > 1 and (key in candidate_by_key or any_new):
            failed.append(
                f"weakening-dispositions: candidate {key!r} has {len(entries)} recorded entries -- exactly one is permitted"
            )
        if any_new and key not in candidate_by_key and not plans_only:
            failed.append(
                f"weakening-dispositions: recorded entry for {key!r} matches no current candidate "
                "(stale -- re-run code review)"
            )

    for candidate in candidates:
        key = candidate.key()
        matched_entries = recorded.get(key)
        if not matched_entries:
            failed.append(
                f"weakening-dispositions: candidate {key!r} has no recorded disposition -- copy code review's "
                "filled-in stub into the plan's weakening_dispositions"
            )
            continue
        disposition, cite, _ = matched_entries[0]
        if disposition not in review_dispositions:
            failed.append(f"weakening-dispositions: candidate {key!r} has unknown disposition {disposition!r}")
            continue
        if disposition == "weakening":
            failed.append(
                f"weakening-dispositions: candidate {key!r} is recorded as weakening -- restore the check, or "
                "route it to a recommendation via the ops portal; never inline"
            )
            continue
        resolves = (
            _planned_change_resolves(cite, root, base, plans, head)
            if disposition == "planned-change"
            else _cite_resolves(disposition, cite, candidate, root=root, base=base, deleted_paths=deleted_paths, head=head)
        )
        if not resolves:
            failed.append(
                f"weakening-dispositions: candidate {key!r} disposition {disposition!r} cite {cite!r} does not resolve"
            )

    print(f"  {len(candidates)} candidate(s), {len(plans)} touched plan(s) -- {len(failed)} issue(s).")
    return len(candidates), len(plans)


def _name_status_between(root: Path, old: str, new: str) -> list[tuple[str, str]]:
    result = _common.run(
        ["git", "diff", "--name-status", "--no-renames", old, new],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
    )
    pairs = (line.split("\t", 1) for line in result.stdout.splitlines() if "\t" in line)
    return [(status, path) for status, path in pairs if status in {"A", "M", "D"}]


def _judge_range(failed: list[str], root: Path, range_base: str) -> int:
    listed = _common.run(
        ["git", "rev-list", "--first-parent", "--reverse", f"{range_base}..HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
    )
    commits = listed.stdout.split()
    for commit in commits:
        print(f"weakening-dispositions: judged {commit[:8]}")
        parent = f"{commit}^"
        run_check(
            failed,
            root=root,
            base=parent,
            status_changed=_name_status_between(root, parent, commit),
            read_base=functools.partial(_read_file_at_ref, root, parent),
            read_head=functools.partial(_read_file_at_ref, root, commit),
            head=commit,
        )
    return len(commits)


def _range_commit_count(root: Path, range_base: str) -> int:
    listed = _common.run(
        ["git", "rev-list", "--first-parent", "--count", f"{range_base}..HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
    )
    return int(listed.stdout.strip() or 0)


@registry.register("validate_weakening_dispositions", owner="platform")
def validate_weakening_dispositions(
    failed: list[str],
    changed_files: list[str] | None = None,
    root: Path | None = None,
) -> None:
    del changed_files
    print("\n=== Weakening disposition completeness (per-diff gate, rec-5441) ===")
    root = root if root is not None else _common.ROOT
    base = _common.status_diff_base(root)
    if base is None:
        print("  SKIP: diff base unreachable.")
        registry.skipped("diff base unreachable")
        return

    range_base = _common.push_context_base(root)
    if range_base is not None and _range_commit_count(root, range_base) > 1:
        registry.examined(_judge_range(failed, root, range_base), unit="first_parent_commits")
        return

    status_changed = _common.get_status_aware_diff(root)
    n_candidates, n_plans = run_check(
        failed,
        root=root,
        base=base,
        status_changed=status_changed,
        read_base=lambda rel: _read_file_at_ref(root, base, rel),
        read_head=lambda rel: _read_file(root, rel),
    )
    if n_plans:
        registry.examined(n_plans, unit="touched_plans")
    elif n_candidates:
        registry.examined(n_candidates, unit="candidates")
    else:
        print("  PASS: no candidates and no touched plan -- nothing to judge.")
        registry.examined(0, unit="touched_plans")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weakening disposition completeness gate (rec-3357, rec-5441).")
    args = parser.parse_args(argv)
    del args

    root = _common.ROOT
    failed: list[str] = []
    validate_weakening_dispositions(failed, root=root)
    if failed:
        print("\nFAILED:")
        for f in failed:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
