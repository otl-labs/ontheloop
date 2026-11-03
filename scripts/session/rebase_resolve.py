"""Rebase onto origin/main and resolve only the mechanical conflicts of docs/contracts/mechanical-conflicts.yaml.

  bin/venv-python -m scripts.session.rebase_resolve      (acts on `git rev-parse --show-toplevel` of the cwd)

Derived files take main's copy and are regenerated, rosters and DECISIONS.md take a union of pure insertions,
and the Decision-numbering race is renumbered (scripts.session.decision_renumber). Any other conflict aborts the
rebase (`RESOLVE STOP`, exit 2). A clean run commits one branch-only commit and prints `RESOLVE CLEAN`; a run
with a bare old number, a roster that no longer parses or an approved-text mismatch leaves its rewrites
uncommitted and prints `RESOLVE NOT CLEAN` (exit 1). Never pushes, never runs the full tier.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.decisions_md import iter_decision_headings
from scripts.session import decision_renumber

CONTRACT = "docs/contracts/mechanical-conflicts.yaml"
ONTO = "origin/main"
_MAX_STOPS = 500


class Stop(Exception):
    """A conflict outside the declared classes (message: path and reason)."""


@dataclass
class Plan:
    derived: dict[str, str] = field(default_factory=dict)
    rosters: list[Any] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)


def load_classes(root: Path) -> Plan:
    """Parse the contract table. Raises ValueError when it is missing or malformed."""
    path = root / CONTRACT
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        classes = data["classes"]
        plan = Plan(
            derived={e["path"]: e["generator"] for e in classes["derived"]["files"]},
            rosters=list(classes["roster"]["files"]),
            decisions=list(classes["decision_numbering"]["files"]),
            excluded={e["path"]: e["reason"] for e in data.get("excluded", [])},
        )
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise ValueError(f"{CONTRACT}: {exc}") from exc
    return plan


def _matches(rel: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(rel, pattern)


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **(env or {})},
    )


def _rebase_in_progress(root: Path) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        path = _git(root, "rev-parse", "--git-path", name).stdout.strip()
        if path and (root / path).exists():
            return True
    return False


def parse_conflicts(text: str) -> list[Any]:
    """Split diff3 text into plain line lists and (ours, base, theirs) tuples."""
    parts: list[Any] = []
    plain: list[str] = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            plain.append(lines[i])
            i += 1
            continue
        sections: list[list[str]] = [[], [], []]
        slot, i = 0, i + 1
        while i < len(lines) and not lines[i].startswith(">>>>>>> "):
            if lines[i].startswith("||||||| "):
                slot = 1
            elif lines[i].rstrip("\r\n") == "=======":
                slot = 2
            else:
                sections[slot].append(lines[i])
            i += 1
        if i >= len(lines):
            raise Stop("unterminated conflict marker")
        i += 1
        parts.append(plain)
        parts.append(tuple(sections))
        plain = []
    parts.append(plain)
    return parts


def _enclosing_key(done: list[str]) -> str:
    for line in reversed(done):
        if line[:1].isalpha() or line[:1] == "_":
            head, sep, _rest = line.partition(":")
            if sep and " " not in head:
                return head
    return ""


def _first_is_heading(lines: list[str]) -> bool:
    body = next((x for x in lines if x.strip()), "")
    return bool(iter_decision_headings(body))


def resolve_text(text: str, mode: str, only_under: str | None = None) -> tuple[str, int]:
    """Resolve every conflict in `text` as pure insertions. mode: roster | roster_branch_first | decisions."""
    done: list[str] = []
    count = 0
    for part in parse_conflicts(text):
        if isinstance(part, list):
            done.extend(part)
            continue
        ours, base, theirs = part
        if any(x.strip() for x in base):
            raise Stop("hunk is not a pure insertion")
        if only_under is not None and _enclosing_key(done) != only_under:
            raise Stop(f"hunk is outside the {only_under} key")
        branch_first = mode == "roster_branch_first" or (
            mode == "decisions" and _first_is_heading(ours) and _first_is_heading(theirs)
        )
        done.extend(theirs + ours if branch_first else ours + theirs)
        count += 1
    return "".join(done), count


def _classify(rel: str, plan: Plan) -> tuple[str, Any]:
    for pattern, reason in plan.excluded.items():
        if _matches(rel, pattern):
            raise Stop(f"excluded file ({reason})")
    if rel in plan.derived:
        return "derived", plan.derived[rel]
    for entry in plan.rosters:
        pattern, only_under = (entry, None) if isinstance(entry, str) else (entry["path"], entry.get("only_under"))
        if _matches(rel, pattern):
            return "roster", only_under
    if rel in plan.decisions:
        return "decisions", None
    raise Stop("file is in no declared class")


def _stop_and_abort(root: Path, rel: str, reason: str) -> int:
    if _rebase_in_progress(root):
        _git(root, "rebase", "--abort")
    print(f"RESOLVE STOP {rel}: {reason} -- surface it to the human")
    return 2


def _resolve_stop(root: Path, plan: Plan, derived: list[str], rosters: list[str], lines: list[str]) -> None:
    unmerged = _git(root, "diff", "--name-only", "--diff-filter=U").stdout.split("\n")
    for rel in [u for u in unmerged if u]:
        try:
            kind, extra = _classify(rel, plan)
        except Stop as exc:
            raise Stop(f"{rel}: {exc}") from exc
        if kind == "derived":
            if _git(root, "checkout", "--ours", "--", rel).returncode != 0:
                raise Stop(f"{rel}: cannot take main's copy")
            derived.append(rel)
            lines.append(f"RESOLVED {rel}: took main's copy, to be regenerated")
        else:
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise Stop(f"{rel}: unreadable ({exc})") from exc
            mode = "decisions" if kind == "decisions" else ("roster_branch_first" if extra == "amendment_log" else "roster")
            try:
                resolved, count = resolve_text(text, mode, extra)
            except Stop as exc:
                raise Stop(f"{rel}: {exc}") from exc
            if count == 0:
                raise Stop(f"{rel}: unmerged with no conflict hunk")
            (root / rel).write_text(resolved, encoding="utf-8")
            if kind == "roster":
                rosters.append(rel)
            lines.append(f"RESOLVED {rel}: {count} pure-insertion hunk(s) ({mode})")
        _git(root, "add", "--", rel)


def _duplicate_key_loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.YAMLError(f"duplicate key {key!r}")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)
    return Loader


def _roster_error(root: Path, rel: str) -> str | None:
    try:
        text = (root / rel).read_text(encoding="utf-8")
        if rel.endswith(".py"):
            names: list[Any] = [
                kw.value.value
                for node in ast.walk(ast.parse(text))
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Entry"
                for kw in node.keywords
                if kw.arg == "name" and isinstance(kw.value, ast.Constant)
            ]
            dupes = sorted({n for n in names if names.count(n) > 1})
            return f"duplicate Entry name {dupes}" if dupes else None
        yaml.load(text, Loader=_duplicate_key_loader())  # noqa: S506 - SafeLoader subclass
    except (OSError, SyntaxError, yaml.YAMLError) as exc:
        return str(exc).replace("\n", " ")
    return None


def _regenerate(root: Path, paths: list[str], plan: Plan, out: list[str]) -> list[str]:
    problems = []
    for rel in paths:
        generator = plan.derived.get(rel)
        if not generator:
            problems.append(f"PARSE {rel}: regeneration failed: no generator declared")
            continue
        done = subprocess.run(
            shlex.split(generator), cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if done.returncode != 0:
            problems.append(f"PARSE {rel}: regeneration failed: {(done.stderr or done.stdout).strip()[-200:]}")
        else:
            out.append(f"REGENERATED {rel}")
    return problems


def _approved_text_messages(root: Path) -> list[str]:
    from scripts.checks._common import resolve_declared_plans  # noqa: PLC0415
    from scripts.roadmap import approved_text  # noqa: PLC0415

    changed = _git(root, "diff", "--name-only", f"{ONTO}...HEAD").stdout.split()
    messages = []
    for plan_rel in resolve_declared_plans(changed, root, ONTO):
        slug = Path(plan_rel).name.removeprefix("PLAN-").removesuffix(".yaml")
        try:
            sidecar = approved_text.load_sidecar_at(ONTO, slug, root)
        except approved_text.ApprovedTextError as exc:
            messages.append(f"APPROVED-TEXT {slug}: {exc}")
            continue
        if sidecar is not None:
            messages.extend(f"APPROVED-TEXT {m}" for m in approved_text.verify(sidecar, root, ONTO))
    return messages


def _finish(root: Path, plan: Plan, base: str, derived: list[str], rosters: list[str], lines: list[str]) -> int:
    renumbered = decision_renumber.renumber(root, ONTO, base)
    for old, new in sorted(renumbered.mapping.items()):
        lines.append(f"RENUMBER Decision {old} -> {new}")
    lines.extend(f"REWRITE {f}:{n} {old} -> {new}" for f, n, old, new in renumbered.rewrites)
    regen = list(dict.fromkeys(derived + (["docs/decisions-index.json"] if renumbered.mapping else [])))
    problems = _regenerate(root, regen, plan, lines)
    problems += [f"PARSE {r}: {e}" for r in rosters if (e := _roster_error(root, r))]
    problems += [f"BARE {f}:{n} {t}" for f, n, t in renumbered.bare]
    problems += _approved_text_messages(root)
    for line in lines + problems:
        print(line)
    if problems:
        print("RESOLVE NOT CLEAN")
        return 1
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        _git(root, "add", "-u")
        body = "\n".join(lines)
        committed = _git(root, "commit", "-m", "rebase_resolve: mechanical conflicts", "-m", body)
        if committed.returncode != 0:
            print(f"RESOLVE STOP commit: {committed.stderr.strip()[-300:]} -- surface it to the human")
            return 2
    print("RESOLVE CLEAN")
    return 0


def main(argv: list[str] | None = None) -> int:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8")
    if top.returncode != 0:
        print("RESOLVE REFUSED not inside a git repository")
        return 2
    root = Path(top.stdout.strip())
    if _rebase_in_progress(root):
        print("RESOLVE REFUSED a rebase is already in progress")
        return 2
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        print("RESOLVE REFUSED the working tree has uncommitted changes")
        return 2
    try:
        plan = load_classes(root)
    except ValueError as exc:
        print(f"RESOLVE REFUSED {exc}")
        return 2
    base = _git(root, "merge-base", ONTO, "HEAD").stdout.strip()
    if not base:
        print(f"RESOLVE REFUSED no merge base with {ONTO}")
        return 2
    derived: list[str] = []
    rosters: list[str] = []
    lines: list[str] = []
    started = _git(root, "-c", "merge.conflictStyle=diff3", "rebase", ONTO, env={"GIT_EDITOR": "true"})
    stops = 0
    while started.returncode != 0:
        if not _rebase_in_progress(root) or stops >= _MAX_STOPS:
            return _stop_and_abort(root, "rebase", (started.stderr or started.stdout).strip()[-300:] or "rebase failed")
        stops += 1
        try:
            _resolve_stop(root, plan, derived, rosters, lines)
        except Stop as exc:
            rel, _sep, reason = str(exc).partition(": ")
            return _stop_and_abort(root, rel, reason or "unresolvable")
        started = _git(root, "-c", "merge.conflictStyle=diff3", "rebase", "--continue", env={"GIT_EDITOR": "true"})
    return _finish(root, plan, base, derived, rosters, lines)


if __name__ == "__main__":
    sys.exit(main())
