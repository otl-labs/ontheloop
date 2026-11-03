"""Added-skip citation gate (rec-3357, Decision 181 point 2's narrowed CONTENT residual).

Every skip/xfail/importorskip SITE added against the diff base inside `tests/**/*.py` must cite a
rec (a `\\brec-\\d+\\b` token anywhere in its reason) or declare itself an environment guard (a
reason starting with "env: "). Exceptionless: no waiver, marker or grandfather (Decision 163 point
1) -- a site already present on the base ref is never re-judged (scripts.checks._weakening_ast.
added_skip_sites is the single home for that distinction, shared with
scripts.checks.verification.validate_weakening_candidates).

Proves presence of the token only -- whether a cited rec is real, or an "env: " label honest, is
code review's judgement (scripts.checks.verification.validate_weakening_candidates lists every
skip added to an EXISTING test file, or gained by a test moved out of one, for that review).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Callable

from scripts.checks import _common, _weakening_ast, registry

_REC_TOKEN_RE = re.compile(r"\brec-\d+\b")
_ENV_PREFIX = "env: "


def _reason_is_cited(reason: str) -> bool:
    return bool(_REC_TOKEN_RE.search(reason)) or reason.startswith(_ENV_PREFIX)


def _read_file(root: Path, rel: str) -> str | None:
    path = root / rel
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_file_at_ref(root: Path, ref: str, rel: str) -> str | None:
    result = _common.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout


def _is_test_file(path: str) -> bool:
    return path.startswith("tests/") and path.endswith(".py")


def run_check(
    failed: list[str],
    *,
    root: Path,
    base: str | None,
    changed: list[tuple[str, str]],
    read_base: Callable[[str], str | None],
    read_head: Callable[[str], str | None],
) -> None:
    """Pure-ish core: `changed` is a list of (status, path) tuples restricted to callers'
    taste (production passes get_status_aware_diff's full output); `read_base`/`read_head` are
    callables (path) -> str | None.
    """
    test_files = sorted({path for status, path in changed if status in {"A", "M", "??"} and _is_test_file(path)})
    deleted_test_files = sorted({path for status, path in changed if status == "D" and _is_test_file(path)})
    if not test_files:
        print("  No added/modified tests/**/*.py file(s) in this diff -- nothing to judge.")
        registry.examined(0, unit="test_files")
        return

    files: dict[str, tuple[str | None, str | None]] = {}
    unparseable: list[str] = []
    for rel in test_files:
        base_src = None if base is None else read_base(rel)
        head_src = read_head(rel)
        if head_src is not None:
            try:
                ast.parse(head_src)
            except (SyntaxError, ValueError):
                unparseable.append(rel)
        files[rel] = (base_src, head_src)
    for rel in deleted_test_files:
        # Carry correlation needs every REMOVED site in this diff, including from a deleted file
        # -- the destination never judges this path (no head content), but added_skip_sites must
        # see its removed sites to tell a carried rename from a genuine addition elsewhere.
        base_src = None if base is None else read_base(rel)
        files[rel] = (base_src, None)

    added = _weakening_ast.added_skip_sites(files)

    judged = 0
    for rel in test_files:
        if rel in unparseable:
            failed.append(f"{rel}: head version is not parseable Python -- cannot judge its added skip sites")
            continue
        for site in added.get(rel, []):
            judged += 1
            print(f"ADDED-SKIP {rel}:{site.line} {site.kind} -- {site.reason!r}")
            if not _reason_is_cited(site.reason):
                kind_hint = (
                    "convert it to pytest.mark.xfail(reason=...)"
                    if site.kind == "unittest.expectedFailure"
                    else 'cite a rec filed through the ops portal, or label it an environment guard ("env: <condition>")'
                )
                failed.append(
                    f"{rel}:{site.line}: added {site.kind} site has neither a rec citation nor an env: label "
                    f"(reason={site.reason!r}) -- {kind_hint}"
                )

    registry.examined(len(test_files), unit="test_files")


@registry.register("validate_test_skip_citations", owner="platform")
def validate_test_skip_citations(failed: list[str], root: Path | None = None) -> None:
    print("\n=== Added-skip citation gate (rec-3357, Decision 181 point 2) ===")
    root = root if root is not None else _common.ROOT
    base = _common.status_diff_base(root)
    if base is None:
        print("  SKIP: diff base unreachable.")
        registry.skipped("diff base unreachable")
        return
    changed = _common.get_status_aware_diff(root)
    run_check(
        failed,
        root=root,
        base=base,
        changed=changed,
        read_base=lambda rel: _read_file_at_ref(root, base, rel),
        read_head=lambda rel: _read_file(root, rel),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Added-skip citation gate (rec-3357).")
    parser.add_argument("--base", default=None, help="Diff base ref (default: status_diff_base(root)).")
    parser.add_argument("--head", default=None, help="Diff head ref (default: the working tree).")
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
        read_head = lambda rel: _read_file_at_ref(root, args.head, rel)  # noqa: E731
    else:
        changed = _common.get_status_aware_diff(root)
        read_head = lambda rel: _read_file(root, rel)  # noqa: E731

    failed: list[str] = []
    run_check(
        failed,
        root=root,
        base=base,
        changed=changed,
        read_base=lambda rel: _read_file_at_ref(root, base, rel),
        read_head=read_head,
    )
    if failed:
        print("\nFAILED:")
        for f in failed:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
