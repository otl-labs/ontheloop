"""Approved-text binding (docs/contracts/git-ops.yaml approved_text_binding).

An operator-approved sidecar (docs/plans/approved/<slug>.yaml) binds a build's edits to a set of
files word for word: for each bound file the head words must equal the base words with every
block's `before` words replaced by its `after` words. Whitespace and line wrapping are ignored; the
only tolerated difference is a filled `{{N}}` or `{{DATE}}` placeholder.

CLI:
  record --slug S                          print `digest=<hex> blocks=<n>`, `approved=<hex>` and `fingerprint=<words>`
  verify --slug S [--base REF] [--expect H] check the working tree against the sidecar read AT THE BASE
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.roadmap import review_page
from scripts.roadmap.approved_words import (
    _PLACEHOLDERS as _PLACEHOLDERS,
)
from scripts.roadmap.approved_words import (
    SIDECAR_DIR as SIDECAR_DIR,
)
from scripts.roadmap.approved_words import (
    ApprovedTextError as ApprovedTextError,
)
from scripts.roadmap.approved_words import (
    Block as Block,
)
from scripts.roadmap.approved_words import (
    Sidecar as Sidecar,
)
from scripts.roadmap.approved_words import (
    _token_matches as _token_matches,
)
from scripts.roadmap.approved_words import (
    approved_digest as approved_digest,
)
from scripts.roadmap.approved_words import (
    digest as digest,
)
from scripts.roadmap.approved_words import (
    find_all as find_all,
)
from scripts.roadmap.approved_words import (
    parse_sidecar as parse_sidecar,
)
from scripts.roadmap.approved_words import (
    sidecar_rel as sidecar_rel,
)
from scripts.roadmap.approved_words import (
    words as words,
)

ROOT = Path(__file__).resolve().parents[2]
_CONTEXT_WORDS = 8


def _git_show(ref: str, rel: str, root: Path) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel}"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout if result.returncode == 0 else None


def _read_tree(rel: str, root: Path) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError:
        return None


def load_sidecar_at(ref: str, slug: str, root: Path = ROOT) -> Sidecar | None:
    text = _git_show(ref, sidecar_rel(slug), root)
    return None if text is None else parse_sidecar(text)


def load_sidecar(slug: str, root: Path = ROOT) -> Sidecar:
    text = _read_tree(sidecar_rel(slug), root)
    if text is None:
        raise ApprovedTextError(f"no sidecar at {sidecar_rel(slug)}")
    return parse_sidecar(text)


def sidecar_state(slug: str, root: Path, base: str) -> str:
    """unchanged | added | changed | deleted | absent: the working-tree sidecar against its base copy."""
    rel = sidecar_rel(slug)
    base_text, tree_text = _git_show(base, rel, root), _read_tree(rel, root)
    if base_text is None and tree_text is None:
        return "absent"
    if base_text is None:
        return "added"
    if tree_text is None:
        return "deleted"
    return "unchanged" if base_text == tree_text else "changed"


def apply(base_words: list[str], blocks: list[Block]) -> list[str]:
    """Replace each block's `before` words (exactly once in the base, never overlapping another
    block's) with its `after` words. An empty `before` is allowed only for an empty base, as the
    file's only block."""
    if any(not words(b.before) for b in blocks):
        if base_words or len(blocks) != 1:
            empty = next(b for b in blocks if not words(b.before))
            raise ApprovedTextError(f"block {empty.id}: empty `before` needs an absent file and no sibling block")
        return words(blocks[0].after)
    spans: list[tuple[int, int, Block]] = []
    for block in blocks:
        before = words(block.before)
        found = find_all(base_words, before)
        if len(found) != 1:
            raise ApprovedTextError(f"block {block.id}: `before` occurs {len(found)} times in the base text, expected 1")
        spans.append((found[0], found[0] + len(before), block))
    spans.sort(key=lambda s: s[0])
    for (_, prev_end, prev), (start, _, cur) in zip(spans, spans[1:]):
        if start < prev_end:
            raise ApprovedTextError(f"blocks {prev.id} and {cur.id} overlap")
    out: list[str] = []
    cursor = 0
    for start, end, block in spans:
        out += base_words[cursor:start] + words(block.after)
        cursor = end
    return out + base_words[cursor:]


def _first_difference(expected: list[str], head: list[str]) -> str:
    pos = next(
        (i for i, (e, h) in enumerate(zip(expected, head)) if not _token_matches(e, h)),
        min(len(expected), len(head)),
    )
    lo = max(0, pos - _CONTEXT_WORDS)

    def ctx(seq: list[str]) -> str:
        return " ".join(seq[lo : pos + _CONTEXT_WORDS])

    return f"first difference at word {pos}: approved `{ctx(expected)}` vs found `{ctx(head)}`"


def verify(sidecar: Sidecar, root: Path, base_ref: str) -> list[str]:
    """One message per failing bound file: head words differ from the approved result, or a bound
    YAML file no longer parses."""
    files: dict[str, list[Block]] = {}
    for block in sidecar.blocks:
        files.setdefault(block.file, []).append(block)
    messages: list[str] = []
    for rel, blocks in files.items():
        head_text = _read_tree(rel, root) or ""
        base_words = words(_git_show(base_ref, rel, root) or "")
        try:
            expected = apply(base_words, blocks)
        except ApprovedTextError as exc:
            messages.append(f"{rel}: {exc}")
            continue
        head_words = words(head_text)
        if len(expected) != len(head_words) or not all(_token_matches(e, h) for e, h in zip(expected, head_words)):
            messages.append(f"{rel}: {_first_difference(expected, head_words)}")
        if rel.endswith(".yaml"):
            try:
                yaml.safe_load(head_text)
            except yaml.YAMLError as exc:
                messages.append(f"{rel}: does not parse as YAML at HEAD: {exc}")
    return messages


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        sidecar = load_sidecar(args.slug, args.root)
    except ApprovedTextError as exc:
        print(f"APPROVED-TEXT ERROR {exc}")
        return 2
    print(f"digest={digest(sidecar)} blocks={len(sidecar.blocks)}")
    approved = approved_digest(sidecar)
    print(f"approved={approved}")
    print(f"fingerprint={review_page.fingerprint(approved)}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    base = args.base
    if base is None:
        from scripts.checks import _common  # noqa: PLC0415

        base = _common.status_diff_base(args.root) or "origin/main"
    try:
        sidecar = load_sidecar_at(base, args.slug, args.root)
    except ApprovedTextError as exc:
        print(f"APPROVED-TEXT ERROR {exc}")
        return 2
    if sidecar is None:
        print(f"APPROVED-TEXT ERROR no sidecar for {args.slug} at {base}")
        return 2
    state = sidecar_state(args.slug, args.root, base)
    if state != "unchanged":
        print(f"APPROVED-TEXT SIDECAR {state}")
        return 1
    failures = [f"APPROVED-TEXT MISMATCH {m}" for m in verify(sidecar, args.root, base)]
    found = digest(sidecar)
    if args.expect and args.expect != found:
        failures.append(f"APPROVED-TEXT MISMATCH digest {found} != expected {args.expect}")
    for line in failures:
        print(line)
    if failures:
        return 1
    print(f"APPROVED-TEXT OK {found}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.roadmap.approved_text")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("record", _cmd_record), ("verify", _cmd_verify)):
        p = sub.add_parser(name)
        p.add_argument("--slug", required=True)
        p.add_argument("--root", type=Path, default=ROOT)
        p.set_defaults(handler=handler)
        if name == "verify":
            p.add_argument("--base", default=None)
            p.add_argument("--expect", default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
