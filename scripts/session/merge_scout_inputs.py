"""Forge-neutral scout-input predicate keyed on refs (the merge gate's second-ordered predicate, rec-5654).

  python -m scripts.session.merge_scout_inputs --head-ref REF --base-ref REF [--merge-base SHA] [--root DIR]
      one line `MERGE-SCOUT-INPUTS <KIND> path=<p> candidates=<n> elapsed=<s>s ...`; exit 0 CLEAR, 1 STALE, 2 UNAVAILABLE

STALE means a plan the re-scout barrier examines on the PR has a different scout-input fingerprint on the PR head than on
base_ref merged with the PR. Every fingerprint value comes from scripts.roadmap.plan_fingerprint, imported only on the
computed path. It never fetches, never writes into the repository (the merge it computes writes only unreachable git
objects) and fails closed: every failure to compute is UNAVAILABLE.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CLEAR = "CLEAR"
STALE = "STALE"
UNAVAILABLE = "UNAVAILABLE"
DEFAULT_TIMEOUT = 15.0
TREE_FILES: tuple[str, ...] = (
    "docs/DECISIONS.md",
    "docs/DECISIONS_ARCHIVE.md",
    "docs/ROADMAP-PLATFORM.yaml",
    "docs/ROADMAP-PLATFORM-ARCHIVE.yaml",
    "docs/contracts/north-star.yaml",
)
PLAN_PATH_RE = re.compile(r"^docs/plans/PLAN-([^/]+)\.yaml$")
FINGERPRINT_MIN_SCHEMA_VERSION = 6
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")
_clock = time.monotonic


@dataclass(frozen=True)
class Verdict:
    kind: str
    plans: tuple[str, ...] = ()
    reason: str = ""
    path: str = ""
    candidates: int = 0
    elapsed: float = 0.0


class _Unavailable(Exception):
    pass


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = _clock() + seconds

    def left(self, step: str) -> float:
        remaining = self.end - _clock()
        if remaining <= 0:
            raise _Unavailable(f"deadline spent ({step})")
        return remaining


def _git(root: Path, *args: str, dl: _Deadline | None = None, step: str = "git") -> subprocess.CompletedProcess[bytes]:
    timeout = dl.left(step) if dl is not None else None
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise _Unavailable(f"deadline spent ({step})") from exc
    except OSError as exc:
        raise _Unavailable(f"git could not run ({step}): {exc}") from exc


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").strip()


def _show(root: Path, rev: str, rel: str, dl: _Deadline, step: str) -> bytes | None:
    listed = _git(root, "ls-tree", "-z", "--name-only", rev, "--", rel, dl=dl, step=step)
    if listed.returncode != 0:
        raise _Unavailable(f"git ls-tree {rev} failed: {_text(listed.stderr)[:200]}")
    if rel.encode("utf-8") not in listed.stdout.split(b"\0"):
        return None
    shown = _git(root, "show", f"{rev}:{rel}", dl=dl, step=step)
    if shown.returncode != 0:
        raise _Unavailable(f"git show {rev}:{rel} failed: {_text(shown.stderr)[:200]}")
    return shown.stdout


def _quiet_diff(root: Path, merge_base: str, ref: str, paths: list[str], dl: _Deadline | None) -> bool:
    """True when ref changed none of paths since merge_base."""
    result = _git(root, "diff", "--quiet", merge_base, ref, "--", *paths, dl=dl, step="main diff")
    if result.returncode not in (0, 1):
        raise _Unavailable(f"git diff failed: {_text(result.stderr)[:200]}")
    return result.returncode == 0


def _plan_candidates(root: Path, merge_base: str, head_ref: str) -> list[tuple[str, str]]:
    result = _git(root, "diff", "--name-status", "--no-renames", merge_base, head_ref, "--", "docs/plans")
    if result.returncode != 0:
        raise _Unavailable(f"git diff failed: {_text(result.stderr)[:200]}")
    found: list[tuple[str, str]] = []
    for line in result.stdout.decode("utf-8", errors="replace").strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        status, path = parts[0].strip()[:1], parts[1].strip()
        if status in ("A", "M") and PLAN_PATH_RE.match(path):
            found.append((status, path))
    return found


def _schema_version(plan: Any, name: str) -> int:
    from pydantic import TypeAdapter, ValidationError  # noqa: PLC0415

    value = plan.get("schema_version") if isinstance(plan, Mapping) else None
    if isinstance(value, bool) or value is None:
        raise _Unavailable(f"{name}: schema_version is absent or not an integer")
    try:
        return TypeAdapter(int).validate_python(value)
    except ValidationError as exc:
        raise _Unavailable(f"{name}: schema_version {value!r} is not an integer") from exc


def _load_plan(raw: bytes, name: str) -> Mapping[str, Any]:
    import yaml  # noqa: PLC0415

    try:
        plan = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise _Unavailable(f"{name}: plan does not parse: {exc}") from exc
    if not isinstance(plan, Mapping):
        raise _Unavailable(f"{name}: plan is not a mapping")
    return plan


def _sidecar_rel(plan: Mapping[str, Any], name: str) -> str:
    slug = plan.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise _Unavailable(f"{name}: plan has no usable slug (the sidecar lookup needs it)")
    return f"docs/plans/approved/{slug}.yaml"


def _materialise(root: Path, rev: str, rels: list[str], dest: Path, dl: _Deadline) -> None:
    for rel in rels:
        data = _show(root, rev, rel, dl, "materialise")
        if data is None:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _conflicts(root: Path, merge_base: str, head_ref: str, base_ref: str, dl: _Deadline) -> tuple[str, set[str]]:
    result = _git(
        root,
        "merge-tree",
        "--write-tree",
        f"--merge-base={merge_base}",
        "-z",
        "--name-only",
        "--no-messages",
        base_ref,
        head_ref,
        dl=dl,
        step="merge-tree",
    )
    if result.returncode not in (0, 1):
        raise _Unavailable(f"git merge-tree --write-tree exited {result.returncode}: {_text(result.stderr)[:200]}")
    fields = result.stdout.split(b"\0")
    tree = fields[0].decode("ascii", errors="replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        raise _Unavailable(f"git merge-tree printed no tree id: {tree[:80]!r}")
    return tree, {f.decode("utf-8", errors="replace") for f in fields[1:] if f}


def _projection(fp: Any, plan: Mapping[str, Any], tree: Any, sidecar_text: bytes | None) -> dict[str, Any]:
    from scripts.roadmap import approved_words  # noqa: PLC0415

    sidecar = approved_words.parse_sidecar(sidecar_text.decode("utf-8")) if sidecar_text is not None else None
    return dict(fp.projection(plan, tree, sidecar))


def _read_tree_plan(base: Path, rel: str, name: str) -> Mapping[str, Any]:
    path = base / rel
    if not path.is_file():
        raise _Unavailable(f"{name}: plan is absent from the compared tree")
    return _load_plan(path.read_bytes(), name)


def _sidecar_bytes(base: Path, plan: Mapping[str, Any], name: str) -> bytes | None:
    path = base / _sidecar_rel(plan, name)
    return path.read_bytes() if path.is_file() else None


def _compare(
    root: Path, merge_base: str, head_ref: str, tree_id: str, candidates: list[tuple[str, str]], dl: _Deadline
) -> list[tuple[str, list[str]]]:
    from scripts.roadmap import plan_fingerprint as fp  # noqa: PLC0415

    plans = {path: _load_plan(_must(_show(root, head_ref, path, dl, "head plan"), path), path) for _, path in candidates}
    sidecars = [_sidecar_rel(plan, path) for path, plan in plans.items()]
    rels = list(TREE_FILES) + [path for _, path in candidates] + sidecars
    with tempfile.TemporaryDirectory() as head_dir, tempfile.TemporaryDirectory() as merged_dir:
        head_base, merged_base = Path(head_dir), Path(merged_dir)
        _materialise(root, head_ref, rels, head_base, dl)
        _materialise(root, tree_id, rels, merged_base, dl)
        head_tree, merged_tree = fp.load_tree(head_base), fp.load_tree(merged_base)
        moved: list[tuple[str, list[str]]] = []
        for status, path in candidates:
            name = Path(path).name
            head_plan = _read_tree_plan(head_base, path, name)
            head_payload = _projection(fp, head_plan, head_tree, _sidecar_bytes(head_base, head_plan, name))
            head_fp = fp.fingerprint(head_payload)
            if status == "M" and _unchanged_plan(root, merge_base, path, name, fp, head_tree, head_fp, dl):
                continue
            merged_plan = _read_tree_plan(merged_base, path, name)
            merged_payload = _projection(fp, merged_plan, merged_tree, _sidecar_bytes(merged_base, merged_plan, name))
            if fp.fingerprint(merged_payload) != head_fp:
                keys = sorted(
                    k for k in set(head_payload) | set(merged_payload) if head_payload.get(k) != merged_payload.get(k)
                )
                moved.append((name, keys))
        return moved


def _must(data: bytes | None, path: str) -> bytes:
    if data is None:
        raise _Unavailable(f"{path}: plan is absent from the PR head")
    return data


def _unchanged_plan(
    root: Path, merge_base: str, path: str, name: str, fp: Any, head_tree: Any, head_fp: str, dl: _Deadline
) -> bool:
    """The barrier's compared-plan rule: the base version and base sidecar give the head's fingerprint on the head tree."""
    raw = _show(root, merge_base, path, dl, "base plan")
    if raw is None:
        return False
    base_plan = _load_plan(raw, name)
    sidecar = _show(root, merge_base, _sidecar_rel(base_plan, name), dl, "base sidecar")
    return bool(fp.fingerprint(_projection(fp, base_plan, head_tree, sidecar)) == head_fp)


def _guard_conflicts(conflicts: set[str], candidates: list[tuple[str, str]], root: Path, head_ref: str, dl: _Deadline) -> None:
    watched = set(TREE_FILES) | {path for _, path in candidates}
    for _, path in candidates:
        raw = _show(root, head_ref, path, dl, "conflict check")
        if raw is not None:
            watched.add(_sidecar_rel(_load_plan(raw, path), path))
    hit = sorted(conflicts & watched)
    if hit:
        raise _Unavailable(
            f"a scout input conflicts between main and this PR ({', '.join(hit)}); rebase locally with "
            "scripts.session.rebase_resolve per git-ops commit_flow_time (rebase_resolve)"
        )


def _judge(root: Path, head_ref: str, base_ref: str, merge_base: str, timeout: float) -> Verdict:
    entries = _plan_candidates(root, merge_base, head_ref)
    if not entries:
        return Verdict(CLEAR, path="no-plan")
    if _quiet_diff(root, merge_base, base_ref, [*TREE_FILES, "docs/plans/"], None):
        return Verdict(CLEAR, path="main-unchanged")
    dl = _Deadline(timeout)
    candidates: list[tuple[str, str]] = []
    plans: dict[str, Mapping[str, Any]] = {}
    for status, path in entries:
        plan = _load_plan(_must(_show(root, head_ref, path, dl, "schema"), path), path)
        if _schema_version(plan, path) >= FINGERPRINT_MIN_SCHEMA_VERSION:
            candidates.append((status, path))
            plans[path] = plan
    if not candidates:
        return Verdict(CLEAR, path="no-candidate")
    watched = [*TREE_FILES]
    for _, path in candidates:
        watched += [path, _sidecar_rel(plans[path], path)]
    if _quiet_diff(root, merge_base, base_ref, watched, dl):
        return Verdict(CLEAR, path="inputs-unchanged", candidates=len(candidates))
    tree_id, conflicts = _conflicts(root, merge_base, head_ref, base_ref, dl)
    _guard_conflicts(conflicts, candidates, root, head_ref, dl)
    moved = _compare(root, merge_base, head_ref, tree_id, candidates, dl)
    if moved:
        reason = "; ".join(f"{name}: scout inputs moved on main ({', '.join(keys)})" for name, keys in moved)
        return Verdict(STALE, tuple(name for name, _ in moved), reason, "computed", len(candidates))
    return Verdict(CLEAR, path="computed", candidates=len(candidates))


def judge(root: Path, head_ref: str, base_ref: str, merge_base: str, timeout: float = DEFAULT_TIMEOUT) -> Verdict:
    started = _clock()
    try:
        verdict = _judge(Path(root), head_ref, base_ref, merge_base, timeout)
    except _Unavailable as exc:
        verdict = Verdict(UNAVAILABLE, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - fail closed: every failure to compute refuses, never a crash
        verdict = Verdict(UNAVAILABLE, reason=f"{type(exc).__name__}: {exc}")
    return Verdict(verdict.kind, verdict.plans, verdict.reason, verdict.path, verdict.candidates, _clock() - started)


def format_line(verdict: Verdict) -> str:
    line = f"MERGE-SCOUT-INPUTS {verdict.kind} path={verdict.path or 'none'} candidates={verdict.candidates}"
    line += f" elapsed={verdict.elapsed:.1f}s"
    return f"{line} reason={verdict.reason}" if verdict.reason else line


_EXIT: dict[str, int] = {CLEAR: 0, STALE: 1, UNAVAILABLE: 2}


def _merge_base(root: Path, base_ref: str, head_ref: str) -> str:
    result = _git(root, "merge-base", base_ref, head_ref)
    if result.returncode != 0 or not _text(result.stdout):
        raise _Unavailable(f"no merge-base of {base_ref} and {head_ref}: {_text(result.stderr) or 'unresolved'}")
    return _text(result.stdout)


def main(argv: list[str] | None = None, *, run: Callable[..., Verdict] = judge) -> int:
    parser = argparse.ArgumentParser(prog="merge_scout_inputs")
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--merge-base")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    try:
        merge_base = args.merge_base or _merge_base(args.root, args.base_ref, args.head_ref)
    except _Unavailable as exc:
        verdict = Verdict(UNAVAILABLE, reason=str(exc))
    else:
        verdict = run(args.root, args.head_ref, args.base_ref, merge_base, args.timeout)
    print(format_line(verdict))
    return _EXIT[verdict.kind]


if __name__ == "__main__":
    sys.exit(main())
