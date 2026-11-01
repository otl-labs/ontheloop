"""The relative-word matcher shared by validate_glossary and the rec portal's glossary_lint rule (Decision 240).

Owner-concern: decide, for one text, which tokens are unqualified uses of "platform" or "product". Pure functions over
text and the contract's identifiers; no file reads, so the check and the portal never disagree.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_TOKEN = re.compile(r"(?<![\w./:])(platforms?|products?)(?![\w/]|\.\w)", re.IGNORECASE)
_RUN_CHARS = r"[\w./:-]"
_OWNER = re.compile(r"owner\s*[=:]\s*[\"']?$")
_ADOPTER = re.compile(r"adopter(?:s|'s|s')?\s+$", re.IGNORECASE)
_YAML_KEY = re.compile(r"^\s*(?:-\s+)?([^\s:]+):(?:\s|$)")
_QUOTES = "\"'`"
_OPEN_AFTER = " \t([{:,"
_CLOSE_BEFORE = re.compile(r"[\s.,;:!?)\]}]")
_MAX_SPAN_CHARS = 60
_MAX_SPAN_WORDS = 6
_BARE = frozenset({"platform", "platforms", "product", "products"})


def identifiers(contract: dict[str, Any]) -> tuple[str, ...]:
    """Every identifier token the contract's relative_words.identifiers text lists."""
    text = str(contract.get("relative_words", {}).get("identifiers", ""))
    found: list[str] = []
    for raw in text.split():
        token = raw.rstrip(",.")
        lowered = token.lower()
        if ("platform" in lowered or "product" in lowered) and lowered not in _BARE:
            found.append(token)
    return tuple(found)


def say_instead(word: str, contract: dict[str, Any]) -> str:
    """relative_words.platform or .product say_instead, folding plurals and case."""
    stem = word.lower()
    stem = stem[:-1] if stem.endswith("s") else stem
    return str(contract["relative_words"][stem]["say_instead"])


def _run(line: str, start: int, end: int) -> str:
    left, right = start, end
    while left > 0 and re.match(_RUN_CHARS, line[left - 1]):
        left -= 1
    while right < len(line) and re.match(_RUN_CHARS, line[right]):
        right += 1
    return line[left:right].rstrip(".:")


def _in_quoted_span(line: str, start: int, end: int) -> bool:
    for i, mark in enumerate(line):
        if mark not in _QUOTES or i > start or not (i == 0 or line[i - 1] in _OPEN_AFTER):
            continue
        for j in range(i + 1, len(line)):
            if line[j] != mark or not (j + 1 == len(line) or _CLOSE_BEFORE.match(line[j + 1])):
                continue
            span = line[i : j + 1]
            if j >= end and len(span) <= _MAX_SPAN_CHARS and len(span[1:-1].split()) <= _MAX_SPAN_WORDS:
                return True
            break
    return False


def _identifier_run(run: str, known: Sequence[str]) -> bool:
    if "-" not in run:
        return False
    lowered = run.lower()
    for entry in known:
        entry = entry.lower()
        if entry.endswith("*"):
            prefix = entry[:-1]
            if lowered.startswith(prefix) or lowered + "-" == prefix:
                return True
        elif lowered == entry:
            return True
    if any(c in run for c in "/._:"):
        return True
    if len([p for p in run.split("-") if p]) >= 3:
        return True
    letters = [c for c in run if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _exempt(line: str, match: re.Match[str], *, yaml_keys: bool, known: Sequence[str]) -> bool:
    start, end = match.span()
    word = match.group(1)
    if _OWNER.search(line[:start]) or word.isupper():
        return True
    if yaml_keys:
        key = _YAML_KEY.match(line)
        if key and key.start(1) <= start and end <= key.end(1):
            return True
    if _in_quoted_span(line, start, end):
        return True
    if word.lower().startswith("product") and _ADOPTER.search(line[:start]):
        return True
    return _identifier_run(_run(line, start, end), known)


def unqualified_uses(text: str, *, yaml_keys: bool, identifiers: Sequence[str]) -> list[tuple[int, str]]:
    """The 1-based line number and matched word of every token in *text* that is a use of a relative word."""
    uses: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _TOKEN.finditer(line):
            if not _exempt(line, match, yaml_keys=yaml_keys, known=identifiers):
                uses.append((number, match.group(1)))
    return uses
