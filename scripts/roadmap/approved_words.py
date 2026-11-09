"""The approved-text approval surface's small closure (rec-4769).

Holds the sidecar models (an optional `tldr` line, outside both digests, that `render` requires and shows as context)
and matcher, the wording-check digest, the approved-words digest the operator approves on GitHub, and the `render`
CLI the approval workflow runs. Imports only the standard library, yaml, pydantic and
review_page, so the workflow's render and wake jobs execute a small, code-owned set of local files.

CLI:
  render --sidecar-file F --slug S --base-file-dir D --expect-approved H --fingerprint W   Markdown for the run page
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scripts.roadmap import review_page
from scripts.roadmap.review_page import fingerprint as fingerprint

SIDECAR_DIR = "docs/plans/approved"
_PLACEHOLDERS = {r"\{\{N\}\}": r"\d+", r"\{\{DATE\}\}": r"\d{4}-\d{2}-\d{2}"}
_SLUG_RE = re.compile(r"[a-z0-9-]+")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_SUMMARY_CAP_BYTES = 1 << 20
_TLDR_MAX_CHARS = 600
_HEADING_RE = re.compile(r"#{1,6}\s+\S")
_YAML_KEY_RE = re.compile(r"[A-Za-z_][\w.-]*:")


class ApprovedTextError(Exception):
    """A sidecar block cannot be applied to its base text (message names the block id)."""


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    file: str
    before: str
    after: str


class Sidecar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    slug: str
    tldr: str | None = None
    blocks: list[Block]

    @model_validator(mode="after")
    def _check_blocks(self) -> Sidecar:
        if not self.blocks:
            raise ValueError("blocks must not be empty")
        ids = [b.id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("block ids must be unique")
        return self


def words(text: str) -> list[str]:
    return text.split()


def sidecar_rel(slug: str) -> str:
    return f"{SIDECAR_DIR}/{slug}.yaml"


def parse_sidecar(text: str) -> Sidecar:
    try:
        return Sidecar.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ApprovedTextError(f"sidecar does not parse: {exc}") from exc


def _token_matches(approved: str, actual: str) -> bool:
    if "{{" not in approved:
        return approved == actual
    pattern = re.escape(approved)
    for escaped, regex in _PLACEHOLDERS.items():
        pattern = pattern.replace(escaped, regex)
    return re.fullmatch(pattern, actual) is not None


def find_all(haystack: list[str], needle: list[str]) -> list[int]:
    n = len(needle)
    return [i for i in range(len(haystack) - n + 1) if haystack[i : i + n] == needle]


def digest(sidecar: Sidecar) -> str:
    payload = [{"file": b.file, "before": words(b.before), "after": words(b.after)} for b in sidecar.blocks]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _changed_words(block: Block) -> tuple[list[str], list[str]]:
    before, after = words(block.before), words(block.after)
    suffix = 0
    while suffix < min(len(before), len(after)) and before[len(before) - 1 - suffix] == after[len(after) - 1 - suffix]:
        suffix += 1
    before, after = before[: len(before) - suffix], after[: len(after) - suffix]
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    return before[prefix:], after[prefix:]


def _placeholder_filled(removed: list[str], added: list[str]) -> list[str]:
    """A removed word that the aligned added placeholder's pattern matches is recorded as that placeholder."""
    out = list(removed)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, removed, added, autojunk=False).get_opcodes():
        if tag != "replace" or i2 - i1 != j2 - j1:
            continue
        for offset in range(i2 - i1):
            wanted = added[j1 + offset]
            if "{{" in wanted and _token_matches(wanted, removed[i1 + offset]):
                out[i1 + offset] = wanted
    return out


def approved_digest(sidecar: Sidecar) -> str:
    """What the operator approves: per block, its file, the words removed and the words added.

    Shared anchor words and a number or date a placeholder replaces drop out, so an anchor-only re-anchor keeps the
    digest; block order and ids do not count; each block's removal stays paired with its addition.
    """
    rows = []
    for block in sidecar.blocks:
        removed, added = _changed_words(block)
        rows.append([block.file, _placeholder_filled(removed, added), added])
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False))
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class RenderRefused(Exception):
    """An input the run page must not render (the workflow's render job fails, so approve never waits)."""


def _unsafe_code_point(text: str) -> str | None:
    for char in text:
        category = unicodedata.category(char)
        if (category == "Cc" and char not in "\n\t") or category == "Cf":
            return f"U+{ord(char):04X}"
    return None


def _check_block_file(rel: str) -> None:
    path = PurePosixPath(rel)
    if path.is_absolute() or rel.startswith(("/", "\\")) or ".." in path.parts or ".git" in path.parts or "\\" in rel:
        raise RenderRefused(f"block file {rel!r} is absolute or holds `..` or `.git/`")


def _check_tldr(tldr: str | None) -> None:
    if tldr is None or not tldr.strip():
        raise RenderRefused("sidecar carries no tldr")
    if len(tldr) > _TLDR_MAX_CHARS:
        raise RenderRefused(f"tldr is over {_TLDR_MAX_CHARS} characters")
    bad = _unsafe_code_point(tldr)
    if bad:
        raise RenderRefused(f"tldr carries a control, bidi or zero-width code point {bad}")


def _validate_render_inputs(sidecar: Sidecar, slug: str, expected: str, finger: str) -> None:
    if not _SLUG_RE.fullmatch(slug):
        raise RenderRefused(f"slug {slug!r} is not [a-z0-9-]+")
    if not _HEX64_RE.fullmatch(expected):
        raise RenderRefused("expected digest is not 64 lowercase hex characters")
    found = approved_digest(sidecar)
    if found != expected:
        raise RenderRefused(f"approved-words digest {found} differs from the approved {expected}")
    if finger != fingerprint(expected):
        raise RenderRefused("fingerprint does not match the digest")
    _check_tldr(sidecar.tldr)
    for block in sidecar.blocks:
        _check_block_file(block.file)
        for text in (block.id, block.file, block.before, block.after):
            bad = _unsafe_code_point(text)
            if bad:
                raise RenderRefused(f"block {block.id}: wording carries a control, bidi or zero-width code point {bad}")


def _line_of_word(text: str, word_index: int) -> int:
    seen = 0
    for number, line in enumerate(text.splitlines()):
        count = len(line.split())
        if word_index < seen + count:
            return number
        seen += count
    return max(0, len(text.splitlines()) - 1)


def _nearest_heading(rel: str, text: str, line_no: int) -> str:
    lines = text.splitlines()
    for number in range(min(line_no, len(lines) - 1), -1, -1):
        line = lines[number]
        if rel.endswith(".md") and _HEADING_RE.match(line):
            return line.strip()[:100]
        if rel.endswith((".yaml", ".yml")) and _YAML_KEY_RE.match(line):
            return line.strip()[:100]
    return "before the first heading"


def _match_at(base_words: list[str], needle: list[str]) -> int | None:
    n = len(needle)
    for i in range(len(base_words) - n + 1):
        if all(_token_matches(a, b) for a, b in zip(needle, base_words[i : i + n])):
            return i
    return None


def _locate(block: Block, base_dir: Path, sha: str) -> str:
    try:
        text = (base_dir / block.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"place not found at main {sha}"
    base_words = words(text)
    for needle in (words(block.before), words(block.after)):
        if needle and (index := _match_at(base_words, needle)) is not None:
            return _nearest_heading(block.file, text, _line_of_word(text, index))
    return f"place not found at main {sha}"


def _main_sha(base_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(base_dir), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout.strip()[:12] if result.returncode == 0 and result.stdout.strip() else "unknown"


def render_page(sidecar: Sidecar, slug: str, expected: str, finger: str, base_dir: Path) -> str:
    _validate_render_inputs(sidecar, slug, expected, finger)
    sha = _main_sha(base_dir)
    locations = {block.id: _locate(block, base_dir, sha) for block in sidecar.blocks}
    page = review_page.render(
        sidecar.blocks, locations, slug=slug, approved=expected, wording=digest(sidecar), tldr=sidecar.tldr
    )
    if len(page.encode("utf-8")) > _SUMMARY_CAP_BYTES:
        raise RenderRefused("page is over 1 MiB, the step summary cap")
    return page


def _cmd_render(args: argparse.Namespace) -> int:
    try:
        sidecar = parse_sidecar(Path(args.sidecar_file).read_text(encoding="utf-8"))
        page = render_page(sidecar, args.slug, args.expect_approved, args.fingerprint, Path(args.base_file_dir))
    except (ApprovedTextError, RenderRefused, OSError, UnicodeDecodeError) as exc:
        print(f"APPROVED-WORDS REFUSED {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(page)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.roadmap.approved_words")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("render")
    p.add_argument("--sidecar-file", required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--base-file-dir", required=True)
    p.add_argument("--expect-approved", required=True)
    p.add_argument("--fingerprint", required=True)
    p.set_defaults(handler=_cmd_render)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
