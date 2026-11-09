"""Plain-reading review page for operator approvals (rec-4769; reused by gated applies, rec-4905).

Pure functions, no I/O and no repository import: blocks are read by their `file`, `before` and `after`
attributes, so scripts.roadmap.approved_words imports this module without a cycle.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from typing import Any

_FINGERPRINT_WORDS = """
acorn adder alder amber anchor anvil apple arrow aspen atlas badge baker basil beach beacon birch bison bloom boat
bolt bonus bottle bridge brook bugle cabin cable cactus camel candle canyon cargo cedar chalk cherry cliff clover
cobalt comet coral cotton crane creek crown cypress daisy delta denim desert dingo dock dolphin dragon drum eagle
echo elbow ember engine falcon fennel ferry fiddle finch flint forest fossil fox galaxy garden garnet geyser ginger
glacier globe goose granite grove guitar harbor hazel heron hickory hollow honey island ivory jacket jade jasper
jelly jungle kayak kettle kiwi ladder lagoon lantern laurel lemon lilac linen lotus lunar magnet mango maple marble
meadow melon mirror mist mocha moose mortar nectar needle nickel noble north nutmeg oasis ocean olive onyx orchid
otter oxide paddle panda pearl pebble pepper piano pilot pine planet plum polar poppy prairie prism pulse puzzle
quartz quill rabbit radar raven reef ribbon river robin rocket saddle saffron sage sailor salmon sand scarlet
shadow shell silver sketch slate sparrow spice spruce squid stable star stone storm summit swan tablet tango thistle
thunder tiger timber topaz tower trail tulip tundra turtle umber valley velvet violet walnut water willow window
winter wolf yarrow yellow zebra zenith zephyr zinc comma crest dune ebony flare frost gleam haven
aqua azure bamboo basalt bay bell birdie bronze buckle cairn cave chime cinder cloud copper cove dawn
dusk elm fern fir flax fjord gale
gravel gust hail hawk heath hill iris jewel kelp knoll lark larch ledge lime loam lynx mallow marsh moss mesa
""".split()

_PLACEHOLDER_SHOWN = {"{{N}}": "[N]", "{{DATE}}": "[date]"}
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+!|~>])")
_CHANGED_RUN_LIMIT = 4
_ANCHOR_WORDS = 8


def fingerprint(hex_digest: str) -> str:
    """Four words from a fixed 256-word list, one per byte of the first four bytes; for eyeballing only."""
    data = bytes.fromhex(hex_digest[:8])
    return "-".join(_FINGERPRINT_WORDS[b] for b in data)


def _show(token: str) -> str:
    for raw, shown in _PLACEHOLDER_SHOWN.items():
        token = token.replace(raw, "\x00" + shown + "\x00")
    parts = token.split("\x00")
    return "".join(part if part in _PLACEHOLDER_SHOWN.values() else _escape(part) for part in parts)


def _escape(text: str) -> str:
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text.replace("&", "&amp;").replace("<", "&lt;"))


def _prose(tokens: Sequence[str]) -> str:
    return " ".join(_show(t) for t in tokens)


def _changed_ops(before: list[str], after: list[str]) -> list[tuple[str, int, int, int, int]]:
    return [op for op in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes() if op[0] != "equal"]


def _marked(before: list[str], after: list[str]) -> str:
    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(_prose(before[i1:i2]))
            continue
        if i2 > i1:
            out.append("<del>" + _prose(before[i1:i2]) + "</del>")
        if j2 > j1:
            out.append("<ins>" + _prose(after[j1:j2]) + "</ins>")
    return " ".join(part for part in out if part)


def _context(words: list[str], start: int, end: int) -> tuple[str, str]:
    return _prose(words[max(0, start - _ANCHOR_WORDS) : start]), _prose(words[end : end + _ANCHOR_WORDS])


def _form(before: list[str], after: list[str]) -> str:
    ops = _changed_ops(before, after)
    if not ops:
        return "**No change**"
    if all(op[0] == "insert" for op in ops):
        lines = ["**New text**"]
        for _, i1, _, j1, j2 in ops:
            ahead, behind = _context(before, i1, i1)
            added = _prose(after[j1:j2])
            if ahead:
                lines.append(f"> {added}\n\nFollows: ...{ahead}")
            else:
                lines.append(f"> {added}\n\nPrecedes: {behind}...")
        return "\n\n".join(lines)
    if all(op[0] == "delete" for op in ops):
        lines = ["**Deleted**"]
        for _, i1, i2, _, _ in ops:
            lines.append("> <del>" + _prose(before[i1:i2]) + "</del>")
        return "\n\n".join(lines)
    if len(ops) <= _CHANGED_RUN_LIMIT:
        return "**Edited in place**\n\n> " + _marked(before, after)
    return "**Was**\n\n> " + _prose(before) + "\n\n**Becomes**\n\n> " + _prose(after)


def render(
    blocks: Sequence[Any],
    locations: Mapping[str, str],
    *,
    slug: str,
    approved: str,
    wording: str,
    tldr: str | None = None,
) -> str:
    """Markdown for the run page. Each block shows its file, the place its caller found, and one form."""
    lines = [f"# Approve wording: {_escape(slug)}", ""]
    if tldr:
        lines += [
            f"**TLDR:** {_escape(' '.join(tldr.split()))}",
            "",
            "_Context only: the TLDR is not part of the approved words._",
            "",
        ]
    lines += [
        f"Fingerprint: **{fingerprint(approved)}**",
        "",
        f"- Approved-words digest: `{approved}`",
        f"- Wording digest: `{wording}`",
        f"- Blocks: {len(blocks)}",
        "",
    ]
    for block in blocks:
        place = locations.get(block.id, "place not found")
        lines += [
            f"## {_escape(block.id)}",
            "",
            f"Location: `{block.file.replace('`', '')}`, {_escape(place)}",
            "",
            _form(block.before.split(), block.after.split()),
            "",
        ]
    return "\n".join(lines)
