"""Stock-leg helpers for validate_glossary (Decision 240, rec-4676).

Owner-concern: the two checks on text already in the repository, as opposed to the added-line lint. The note leg finds a
roster Decision whose entry lacks the reads-through note; the whole-file leg finds a live governed line that still uses
a relative word. Pure over a repository root and the parsed contract, so validate_glossary only wires them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from scripts import decisions_md
from scripts.checks import _common
from scripts.checks.contracts import _glossary_words

_DATE = "[0-9]{4}-[0-9]{2}-[0-9]{2}"
_DECISION_FILES = ("DECISIONS.md", "DECISIONS_ARCHIVE.md")


def note_pattern(note: str) -> re.Pattern[str]:
    """The stored note template as a one-line regex: its literal DATE becomes an ISO date."""
    return re.compile("^" + re.escape(note.strip()).replace("DATE", _DATE) + "$", re.MULTILINE)


def _entries(root: Path) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for name in _DECISION_FILES:
        path = root / "docs" / name
        if not path.is_file():
            continue
        for heading, block in decisions_md.iter_decision_sections(path.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(int(heading.group(1)), []).append(block)
    return found


def unnoted(root: Path, data: dict[str, Any]) -> list[str]:
    """One finding per roster Decision whose entry lacks the note, or that neither Decision file holds."""
    block = data["read_as"]
    pattern = note_pattern(block["note"])
    entries = _entries(root)
    out: list[str] = []
    for number in block["noted_decisions"]:
        blocks = entries.get(number)
        if not blocks:
            out.append(
                f"Decision {number} is in read_as.noted_decisions but neither docs/DECISIONS.md nor docs/DECISIONS_ARCHIVE.md "
                "holds it; if it was compacted to a stub (Decision 177 cl.2), drop its number from read_as.noted_decisions "
                "with an amendment_log row"
            )
        elif not any(pattern.search(text) for text in blocks):
            out.append(
                f"Decision {number} lacks the read_as.note reads-through note (a trailing blockquote in its entry); "
                "if it was compacted to a stub (Decision 177 cl.2), drop its number from read_as.noted_decisions "
                "with an amendment_log row"
            )
    return out


def _last_scalar_line(node: yaml.Node) -> int:
    """0-based last line a node's final scalar occupies; a block scalar's end mark sits at column 0 of the next line."""
    while not isinstance(node, yaml.ScalarNode):
        if isinstance(node, yaml.MappingNode):
            children = [pair[1] for pair in node.value]
        else:
            children = list(node.value)
        if not children:
            return node.start_mark.line
        node = children[-1]
    end = node.end_mark
    return end.line - 1 if end.column == 0 and end.line > node.start_mark.line else end.line


def _first_line(node: yaml.Node) -> int:
    if isinstance(node, yaml.SequenceNode) and node.value:
        return node.value[0].start_mark.line
    return node.start_mark.line


def _history_ranges(node: yaml.Node, out: set[int]) -> None:
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "amendment_log":
                is_empty_scalar = isinstance(value, yaml.ScalarNode) and not value.value
                if not is_empty_scalar:
                    out.update(range(_first_line(value) + 1, _last_scalar_line(value) + 2))
            _history_ranges(value, out)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _history_ranges(item, out)


def history_lines(text: str) -> frozenset[int]:
    """1-based lines of every amendment_log key's value at any depth; a file that does not compose has none."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return frozenset()
    found: set[int] = set()
    if root is not None:
        _history_ranges(root, found)
    return frozenset(found)


def _is_record(rel: str, records: Sequence[str]) -> bool:
    return any(rel == entry or (entry.endswith("/") and rel.startswith(entry)) for entry in records)


def live_hits(root: Path, data: dict[str, Any], specs: list[str], in_plan: Callable[[str], bool]) -> tuple[list[str], int]:
    """Findings for every unqualified relative word in a live governed file, and the count of files scanned.

    The files are the lint pathspecs minus read_as.records. An amendment_log row and a line the caller says a plan
    carries are exempt.
    """
    listed = _common.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *specs],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
    )
    names = sorted({n for n in listed.stdout.split("\0") if n}) if listed.returncode == 0 else []
    records = [str(entry) for entry in data["read_as"].get("records") or []]
    known = _glossary_words.identifiers(data)
    hits: list[str] = []
    scanned = 0
    for rel in names:
        if _is_record(rel, records):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        lines = text.splitlines()
        history = history_lines(text) if rel.endswith(".yaml") else frozenset()
        for number, word in _glossary_words.unqualified_uses(text, yaml_keys=rel.endswith(".yaml"), identifiers=known):
            if number in history or in_plan(lines[number - 1].strip()):
                continue
            hint = _glossary_words.say_instead(word, data)
            hits.append(
                f'{rel}:{number}: unqualified "{word}" -- say instead: {hint}, or quote it if it is not the relative word'
            )
    return hits, scanned
