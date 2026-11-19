"""Pure span and byte measures over one Decision entry block (Decision 276 clause 4, rec-4744).

Every function takes the heading-inclusive raw block scripts.decisions_md's iter_decision_sections yields and reads
no file; none raises on text. `authored` is the one classifier the per-Decision pin fixture and the size check share.
"""

from __future__ import annotations

import re
from typing import Iterable

METADATA_ENVELOPE = "metadata_envelope"
REVERSAL_CONDITIONS = "reversal_conditions"
OPTIONS_CONSIDERED = "options_considered"
DATED_ANNOTATIONS = "dated_annotations"
OPTIONS_MARKER = "**Options considered:**"

_DATE = r"\d{4}-\d{2}(?:-\d{2})?"
_BRACKET_OPENER = re.compile(rf"\[Amendment {_DATE}\b")
_BLOCKQUOTE = re.compile(rf"> \*\*[^*\n]*?{_DATE}[^*\n]*?:\*\*")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_ENVELOPE_FENCE = re.compile(r"\A#{2,3}[^\n]*\n+(```yaml\n.*?\n```)", re.DOTALL)
_STANZA_FENCE = re.compile(r"^```yaml reversal-conditions\n.*?\n```", re.DOTALL | re.MULTILINE)
_LABELLED_BLOCKQUOTE = re.compile(r"> \*\*[^\n]*?:\*\*")
_BRACKET_ANY = re.compile(r"\[Amendment\b")
_OPTIONS_END = re.compile(r"^(?:\*\*|```|#)", re.MULTILINE)

Span = tuple[int, int]


def _closes_at_end(paragraph: str) -> bool:
    depth = 0
    for index, char in enumerate(paragraph):
        depth += {"[": 1, "]": -1}.get(char, 0)
        if depth == 0:
            return index == len(paragraph) - 1
    return False


def _is_trailer(paragraph: str) -> bool:
    if _BRACKET_OPENER.match(paragraph):
        return _closes_at_end(paragraph)
    return _BLOCKQUOTE.match(paragraph) is not None and all(line.startswith(">") for line in paragraph.splitlines())


def authored(entry: str) -> str:
    """The entry without its trailing dated annotations, closing separator kept; an entry with none is unchanged."""
    core = entry.rstrip()
    if core.endswith("\n---"):
        core = core.removesuffix("---").rstrip()
    end = len(core)
    for brk in reversed(list(_PARAGRAPH_BREAK.finditer(core))):
        if not _is_trailer(core[brk.end() : end].strip()):
            break
        end = brk.start()
    return entry if end == len(core) else core[:end] + entry[len(core) :]


def authored_bytes(entry: str) -> int:
    return len(authored(entry).encode("utf-8"))


def envelope_span(entry: str) -> Span | None:
    """The bare ```yaml fence right after the heading, fence lines included."""
    match = _ENVELOPE_FENCE.match(entry)
    return match.span(1) if match else None


def stanza_span(entry: str) -> Span | None:
    """The ```yaml reversal-conditions fence, fence lines included."""
    match = _STANZA_FENCE.search(entry)
    return match.span() if match else None


def options_span(entry: str, marker: str) -> Span | None:
    """From the marker to the next line starting with "**", a fence or a heading (or the entry's end)."""
    start = entry.find(marker)
    if start == -1:
        return None
    line_end = entry.find("\n", start)
    stop = _OPTIONS_END.search(entry, line_end + 1) if line_end != -1 else None
    return start, stop.start() if stop else len(entry)


def options_block(entry: str, marker: str) -> str | None:
    span = options_span(entry, marker)
    return entry[span[0] : span[1]] if span else None


def dated_annotation_spans(entry: str, dated_only: bool = False) -> list[Span]:
    """Every annotation paragraph (with the blank line before it) anywhere in the body, dated or not.

    The form is amendment_forms general_rule: a paragraph whose first line is a "> **Label:**" and whose every line
    starts with ">", or a bracketed "[Amendment " paragraph. It is deliberately wider than `authored`'s trailing dated
    run, which the pin tests keep. `dated_only` keeps the paragraphs whose first line carries a date.
    """
    spans: list[Span] = []
    cursor = 0
    lead = 0
    for brk in [*_PARAGRAPH_BREAK.finditer(entry), None]:
        end = brk.start() if brk else len(entry)
        paragraph = entry[cursor:end]
        stripped = paragraph.lstrip("\n")
        if stripped.rstrip().endswith("\n---"):
            stripped = stripped.rstrip().removesuffix("---").rstrip()
        if (
            stripped
            and (
                (_LABELLED_BLOCKQUOTE.match(stripped) and all(line.startswith(">") for line in stripped.splitlines()))
                or _BRACKET_ANY.match(stripped)
            )
            and (not dated_only or re.search(_DATE, stripped.splitlines()[0]))
        ):
            offset = cursor + (len(paragraph) - len(paragraph.lstrip("\n")))
            spans.append((lead, offset + len(stripped)))
        cursor = brk.end() if brk else len(entry)
        lead = brk.start() if brk else len(entry)
    return spans


def measured_bytes(entry: str, excluded: Iterable[str], marker: str = OPTIONS_MARKER, dated_only: bool = False) -> int:
    """UTF-8 bytes of the entry with the named spans removed."""
    wanted = set(excluded)
    spans: list[Span] = []
    if METADATA_ENVELOPE in wanted and (span := envelope_span(entry)):
        spans.append(span)
    if REVERSAL_CONDITIONS in wanted and (span := stanza_span(entry)):
        spans.append(span)
    if OPTIONS_CONSIDERED in wanted:
        if span := options_span(entry, marker):
            spans.append(span)
    if DATED_ANNOTATIONS in wanted:
        spans.extend(dated_annotation_spans(entry, dated_only))
    kept: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        if start < cursor:
            end = max(end, cursor)
            start = cursor
        kept.append(entry[cursor:start])
        cursor = max(cursor, end)
    kept.append(entry[cursor:])
    return len("".join(kept).encode("utf-8"))
