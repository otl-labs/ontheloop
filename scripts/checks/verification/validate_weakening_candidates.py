"""Weakening-candidate surfacer -- REPORT ONLY (PLAN-test-weakening-surfacing, rec-3357,
Decision 181 point 2's narrowed CONTENT residual).

Lists every syntactic shape a diff could use to quietly weaken test coverage: a removed or
changed assertion, a removed or weaker-moved test, a deleted test file, a skip added to an
EXISTING test file, a widened tolerance, or a changed numeric constant in scripts/ or src/. NEVER
appends to `failed` here -- making any category blocking is out of scope (Decision 163 point 1,
Decision 187 reversal c1): telling a genuinely-corrected assertion/tolerance from a quietly
loosened one is the test-oracle problem, which this module does not attempt to solve. It only
surfaces candidates; code review disposes each one (see validate_weakening_dispositions, the
implement-PR leg that makes recording a disposition mandatory).

find_candidates() is pure -- no I/O -- over injected (status, path) pairs and readers, so it is
exercised identically by the mirror test, the real-history replay (VP step 7), and the registered
check (which threads real git reads through `read_base`/`read_head`).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.checks import _common, registry
from scripts.checks import _weakening_ast as wa

ReadFn = Callable[[str], "str | None"]

_DETAIL_CAP = 160


@dataclass(frozen=True)
class Candidate:
    category: str
    path: str
    node: str
    detail: str

    def key(self) -> str:
        return f"[{self.category}] {self.path}::{self.node}"


def _is_test_path(path: str) -> bool:
    return path.startswith("tests/") and path.endswith(".py")


def _is_source_path(path: str) -> bool:
    return path.startswith(("scripts/", "src/")) and path.endswith(".py") and not _is_test_path(path)


def _bare_name(key: str) -> str:
    return key.split("::", 1)[-1]


class _Recorder:
    def __init__(self) -> None:
        self._by_key: dict[str, Candidate] = {}

    def add(self, category: str, path: str, node: str, detail: str) -> None:
        candidate = Candidate(category, path, node, detail[:_DETAIL_CAP])
        key = candidate.key()
        existing = self._by_key.get(key)
        if existing is None:
            self._by_key[key] = candidate
        else:
            merged = f"{existing.detail}; {detail}"[:_DETAIL_CAP]
            self._by_key[key] = Candidate(category, path, node, merged)

    def result(self) -> list[Candidate]:
        return sorted(self._by_key.values(), key=lambda c: c.key())


def _first_display(counter) -> str:  # noqa: ANN001 -- Counter[tuple[str, str]]
    for (_, display), _count in counter.items():
        return display
    return ""


def _skip_added_candidates(
    test_m: list[str], base_src: dict, head_src: dict, all_test_paths: list[str], rec: _Recorder
) -> None:
    skip_files = {p: (base_src[p], head_src[p]) for p in all_test_paths}
    added_sites = wa.added_skip_sites(skip_files)
    for p in test_m:
        for site in added_sites.get(p, []):
            rec.add("skip-added", p, site.scope, f"{site.kind} -- {site.reason}" if site.reason else site.kind)


def _newly_added_keys(paths: set[str], base_tests: dict, head_tests: dict) -> dict[str, list[tuple[str, str]]]:
    newly_added: dict[str, list[tuple[str, str]]] = {}
    for p in paths:
        for key in head_tests.get(p, {}):
            if key not in base_tests.get(p, {}):
                newly_added.setdefault(_bare_name(key), []).append((p, key))
    return newly_added


def _assertion_and_tolerance_candidates(test_m: list[str], base_tests: dict, head_tests: dict, rec: _Recorder) -> None:
    for p in test_m:
        shared_keys = set(base_tests[p]) & set(head_tests[p])
        for key in sorted(shared_keys):
            base_node = base_tests[p][key]
            head_node = head_tests[p][key]
            _record_assertion_delta(rec, p, key, base_node, head_node)
            _record_tolerance_delta(rec, p, key, base_node, head_node)


def _record_assertion_delta(rec: _Recorder, path: str, key: str, base_node, head_node) -> None:
    base_assertions = wa.assertions(base_node)
    head_assertions = wa.assertions(head_node)
    lost = base_assertions - head_assertions
    if not lost:
        return
    display = _first_display(lost)
    category = "assertion-removed" if sum(head_assertions.values()) < sum(base_assertions.values()) else "assertion-changed"
    rec.add(category, path, key, f"lost assertion: {display}")


def _record_tolerance_delta(rec: _Recorder, path: str, key: str, base_node, head_node) -> None:
    base_tol = wa.tolerances(base_node)
    head_tol = wa.tolerances(head_node)
    for tkey, base_val in base_tol.items():
        head_val = head_tol.get(tkey)
        if head_val is not None and abs(head_val) > abs(base_val):
            callee, kw, _occ = tkey
            rec.add("tolerance-widened", path, key, f"{callee}.{kw}: {base_val} -> {head_val}")


def _removed_sources(test_m: list[str], test_d: list[str], base_tests: dict, head_tests: dict) -> dict[str, list[str]]:
    removed: dict[str, list[str]] = {}
    for p in test_m:
        for key in set(base_tests[p]) - set(head_tests[p]):
            removed.setdefault(p, []).append(key)
    for p in test_d:
        for key in base_tests.get(p, {}):
            removed.setdefault(p, []).append(key)
    return removed


def _moved_weaker_detail(lost_assertions, gained_skips) -> str:
    detail = "moved with"
    if lost_assertions:
        detail += f" a lost assertion ({_first_display(lost_assertions)})"
    if gained_skips:
        kind, reason = next(iter(gained_skips))
        joiner = " and" if lost_assertions else ""
        detail += f"{joiner} a gained skip ({kind} -- {reason})"
    return detail


def _record_moved_or_removed(
    rec: _Recorder,
    src_path: str,
    key: str,
    *,
    test_d: list[str],
    newly_added_keys: dict,
    base_tests: dict,
    head_tests: dict,
    base_src: dict,
    head_src: dict,
    deleted_file_missing_counts: dict,
) -> None:
    bare = _bare_name(key)
    destinations = [(dp, dk) for dp, dk in newly_added_keys.get(bare, []) if dp != src_path]
    if not destinations:
        rec.add("test-removed", src_path, key, "test removed; bare name not re-added elsewhere in this diff")
        if src_path in test_d:
            deleted_file_missing_counts[src_path] = deleted_file_missing_counts.get(src_path, 0) + 1
        return
    for dest_path, dest_key in destinations:
        src_node = base_tests[src_path].get(key)
        dest_node = head_tests[dest_path].get(dest_key)
        if src_node is None or dest_node is None:
            continue
        lost_assertions = wa.assertions(src_node) - wa.assertions(dest_node)
        dest_applicable = {(s.kind, s.reason) for s in wa.applicable_skip_sites(head_src[dest_path] or "", dest_key)}
        src_applicable = {(s.kind, s.reason) for s in wa.applicable_skip_sites(base_src.get(src_path) or "", key)}
        gained_skips = dest_applicable - src_applicable
        if lost_assertions or gained_skips:
            rec.add("test-moved-weaker", dest_path, dest_key, _moved_weaker_detail(lost_assertions, gained_skips))


def _moved_or_removed_candidates(
    test_m: list[str],
    test_d: list[str],
    base_tests: dict,
    head_tests: dict,
    base_src: dict,
    head_src: dict,
    newly_added_keys: dict,
    rec: _Recorder,
) -> None:
    removed_sources = _removed_sources(test_m, test_d, base_tests, head_tests)
    deleted_file_missing_counts: dict[str, int] = {}
    for src_path, keys in removed_sources.items():
        for key in keys:
            _record_moved_or_removed(
                rec,
                src_path,
                key,
                test_d=test_d,
                newly_added_keys=newly_added_keys,
                base_tests=base_tests,
                head_tests=head_tests,
                base_src=base_src,
                head_src=head_src,
                deleted_file_missing_counts=deleted_file_missing_counts,
            )
    for p, count in deleted_file_missing_counts.items():
        rec.add("test-file-deleted", p, "*", f"{count} test(s) not found at head")


def _constant_changed_candidates(source_m: list[str], read_base: ReadFn, read_head: ReadFn, rec: _Recorder) -> None:
    for p in source_m:
        b = read_base(p)
        h = read_head(p)
        if b is None or h is None:
            continue
        base_consts = wa.numeric_constants(b)
        head_consts = wa.numeric_constants(h)
        for name, base_val in base_consts.items():
            head_val = head_consts.get(name)
            if head_val is not None and head_val != base_val:
                rec.add("constant-changed", p, name, f"{base_val} -> {head_val}")


def find_candidates(changed: list[tuple[str, str]], read_base: ReadFn, read_head: ReadFn) -> list[Candidate]:
    rec = _Recorder()

    test_m = sorted({p for s, p in changed if s == "M" and _is_test_path(p)})
    test_d = sorted({p for s, p in changed if s == "D" and _is_test_path(p)})
    test_added = sorted({p for s, p in changed if s in {"A", "??"} and _is_test_path(p)})
    source_m = sorted({p for s, p in changed if s == "M" and _is_source_path(p)})

    all_test_paths = sorted(set(test_m) | set(test_d) | set(test_added))

    base_src: dict[str, str | None] = {}
    head_src: dict[str, str | None] = {}
    base_tests: dict[str, dict] = {}
    head_tests: dict[str, dict] = {}
    for p in all_test_paths:
        b = read_base(p) if p in test_m or p in test_d else None
        h = read_head(p) if p in test_m or p in test_added else None
        base_src[p] = b
        head_src[p] = h
        base_tests[p] = wa.test_functions(b) if b is not None else {}
        head_tests[p] = wa.test_functions(h) if h is not None else {}

    _skip_added_candidates(test_m, base_src, head_src, all_test_paths, rec)
    newly_added_keys = _newly_added_keys(set(test_m) | set(test_added), base_tests, head_tests)
    _assertion_and_tolerance_candidates(test_m, base_tests, head_tests, rec)
    _moved_or_removed_candidates(test_m, test_d, base_tests, head_tests, base_src, head_src, newly_added_keys, rec)
    _constant_changed_candidates(source_m, read_base, read_head, rec)

    return rec.result()


def _review_dispositions() -> dict[str, str]:
    import yaml  # noqa: PLC0415

    contract_path = _common.ROOT / "docs" / "contracts" / "implement-scope-boundary.yaml"
    try:
        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return (doc.get("invariants", {}).get("content", {}).get("review_dispositions")) or {}


def _print_report(candidates: list[Candidate]) -> None:
    for c in candidates:
        print(f"CANDIDATE [{c.category}] {c.path}::{c.node} -- {c.detail}")
        print(f'  - {{candidate: "[{c.category}] {c.path}::{c.node}", disposition: <label>, cite: "<reference>"}}')
    dispositions = _review_dispositions()
    for label, rule in dispositions.items():
        print(f"  disposition {label}: {rule}")


def _read_file_at_ref(root: Path, ref: str, rel: str) -> str | None:
    result = _common.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout


def _read_file(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


@registry.register("validate_weakening_candidates", owner="platform")
def validate_weakening_candidates(
    failed: list[str],
    *,
    root: Path | None = None,
    base: str | None = None,
) -> None:
    """REPORT-ONLY weakening-candidate classifier -- NEVER appends to `failed`."""
    del failed  # never appended to -- report-only by construction
    print("\n=== Weakening candidates (report only; rec-3357) ===")
    root = root if root is not None else _common.ROOT
    base = base if base is not None else _common.status_diff_base(root)
    if base is None:
        print("  SKIP: diff base unreachable.")
        registry.skipped("diff base unreachable")
        return

    changed = _common.get_status_aware_diff(root)
    candidates = find_candidates(
        changed,
        read_base=lambda rel: _read_file_at_ref(root, base, rel),
        read_head=lambda rel: _read_file(root, rel),
    )
    _print_report(candidates)
    examined_files = len({p for _s, p in changed if p.endswith(".py")})
    registry.examined(examined_files, unit="changed_files")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weakening-candidate classifier -- REPORT ONLY (rec-3357).")
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default=None)
    args = parser.parse_args(argv)

    root = _common.ROOT
    base = args.base if args.base is not None else _common.status_diff_base(root)
    if base is None:
        print("SKIP: diff base unreachable.")
        return 0

    if args.head is not None:
        diff_result = _common.run(
            ["git", "diff", "--name-status", "--no-renames", f"{base}..{args.head}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=root,
        )
        changed: list[tuple[str, str]] = []
        for line in (diff_result.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            status, path = parts[0].strip()[:1], parts[1].strip()
            if status in {"A", "M", "D"}:
                changed.append((status, path))
        read_head: ReadFn = lambda rel: _read_file_at_ref(root, args.head, rel)  # noqa: E731
    else:
        changed = _common.get_status_aware_diff(root)
        read_head = lambda rel: _read_file(root, rel)  # noqa: E731

    candidates = find_candidates(
        changed,
        read_base=lambda rel: _read_file_at_ref(root, base, rel),
        read_head=read_head,
    )
    _print_report(candidates)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
