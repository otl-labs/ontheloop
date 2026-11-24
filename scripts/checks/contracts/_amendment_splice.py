"""Locate and splice top-level amendment_log rows by whole lines (rec-5452).

A row's span runs from the line holding its "- " indicator to its last content line. A node's end_mark runs on over
trailing blank lines and comments up to the next token, so it is never the span end. Comments are found with the
scalar-range rule of validate_tier_a_amendment_guard: a '#' after whitespace outside every scalar. Never raises on
input the YAML parser accepts; unparseable text returns None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

_LINES = re.compile(r"[^\r\n\x85  ]*(?:\r\n|[\r\n\x85  ])|[^\r\n\x85  ]+")
_EOL = re.compile(r"(?:\r\n|[\r\n\x85  ])$")
_KEYS = ("date", "semantic_break", "change_class")


@dataclass(frozen=True)
class RowSpan:
    first: int
    last: int
    indent: str
    texts: tuple[str, str, str] | None
    refusal: str | None


def split_lines(text: str) -> list[str]:
    """Lines with their endings, split exactly where the YAML reader counts a line."""
    return _LINES.findall(text)


def _scalar_ranges(node: yaml.Node, starts: list[int], out: list[tuple[int, int]]) -> None:
    if isinstance(node, yaml.ScalarNode):
        begin = node.start_mark.index
        if node.style in ("|", ">"):
            nxt = node.start_mark.line + 1
            begin = starts[nxt] if nxt < len(starts) else node.end_mark.index
        out.append((begin, node.end_mark.index))
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _scalar_ranges(child, starts, out)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            _scalar_ranges(key, starts, out)
            _scalar_ranges(value, starts, out)


def _end_lines(node: yaml.Node, lines: list[str], out: list[int]) -> None:
    if isinstance(node, yaml.ScalarNode):
        end = node.end_mark
        last = end.line
        if node.style in ("|", ">"):
            head = lines[end.line][: end.column] if end.line < len(lines) else ""
            last = end.line - 1 if not head.strip() else end.line
            while last > node.start_mark.line and not lines[last].strip():
                last -= 1
        out.append(last)
        return
    if node.flow_style:
        out.append(node.end_mark.line)
    if isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _end_lines(child, lines, out)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            _end_lines(key, lines, out)
            _end_lines(value, lines, out)


def _has_comment(lines: list[str], starts: list[int], span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    for number in range(span[0], span[1] + 1):
        for match in re.finditer("#", lines[number]):
            at = match.start()
            if (at == 0 or lines[number][at - 1].isspace()) and not any(a <= starts[number] + at < b for a, b in ranges):
                return True
    return False


def _anchor_lines(text: str) -> set[int]:
    found: set[int] = set()
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            found.add(event.start_mark.line)
    return found


def _texts(node: yaml.MappingNode, text: str) -> tuple[str, str, str] | None:
    found: dict[str, str] = {}
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value in _KEYS and isinstance(value, yaml.ScalarNode):
            raw = text[value.start_mark.index : value.end_mark.index]
            if raw and "\n" not in raw and "\r" not in raw:
                found[key.value] = raw
    return (found["date"], found["semantic_break"], found["change_class"]) if len(found) == len(_KEYS) else None


def _measure(
    item: yaml.Node,
    node_ids: list[int],
    lines: list[str],
    starts: list[int],
    ranges: list[tuple[int, int]],
    anchors: set[int],
) -> tuple[int, int, str, str | None]:
    """(first, last, indent, refusal) of one item: whole lines, refused when the splice could not be exact."""
    first = item.start_mark.line
    ends: list[int] = []
    _end_lines(item, lines, ends)
    last = max([first, *ends])
    head = lines[first][: item.start_mark.column] if first < len(lines) else ""
    indent = head[: len(head) - len(head.lstrip())]
    refusal = None
    if not isinstance(item, yaml.MappingNode) or node_ids.count(id(item)) > 1:
        refusal = "malformed"
    elif head.strip() != "-" or last >= len(lines):
        refusal = "span_comment"
    elif any(first <= line <= last for line in anchors):
        refusal = "malformed"
    elif _has_comment(lines, starts, (first, last), ranges):
        refusal = "span_comment"
    return first, last, indent, refusal


def locate_log(text: str) -> list[RowSpan] | None:
    """One RowSpan per top-level amendment_log item, or None when the text has no such sequence."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        anchors = _anchor_lines(text)
    except yaml.YAMLError:
        return None
    if not isinstance(root, yaml.MappingNode):
        return None
    seq = None
    for key, value in root.value:
        if isinstance(key, yaml.ScalarNode) and key.value == "amendment_log":
            seq = value
    if not isinstance(seq, yaml.SequenceNode):
        return None
    lines = split_lines(text)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    ranges: list[tuple[int, int]] = []
    _scalar_ranges(root, starts, ranges)
    node_ids = [id(item) for item in seq.value]
    raw = [_measure(item, node_ids, lines, starts, ranges, anchors) for item in seq.value]
    spans: list[RowSpan] = []
    for i, (first, last, indent, refusal) in enumerate(raw):
        if refusal is None and ((i > 0 and first <= raw[i - 1][1]) or (i + 1 < len(raw) and last >= raw[i + 1][0])):
            refusal = "span_comment"
        item = seq.value[i]
        texts = _texts(item, text) if isinstance(item, yaml.MappingNode) else None
        if refusal is None and texts is None:
            refusal = "malformed"
        spans.append(RowSpan(first, last, indent, texts, refusal))
    return spans


def replacement_line(span: RowSpan, summary: str, eol: str) -> str:
    date, broke, change = span.texts or ("", "", "")
    escaped = summary.replace("\\", "\\\\").replace('"', '\\"')
    return f'{span.indent}- {{date: {date}, semantic_break: {broke}, change_class: {change}, summary: "{escaped}"}}{eol}'


def splice(text: str, spans: dict[int, RowSpan], summaries: dict[int, str]) -> str:
    """text with each chosen row's line range replaced by one flow-mapping line; everything else is untouched."""
    lines = split_lines(text)
    for index in sorted(summaries, key=lambda i: spans[i].first, reverse=True):
        span = spans[index]
        match = _EOL.search(lines[span.last])
        lines[span.first : span.last + 1] = [replacement_line(span, summaries[index], match.group() if match else "")]
    return "".join(lines)


def lines_match(old: str, new: str, chosen: list[RowSpan]) -> bool:
    """Proof (a): new equals old with each chosen range replaced by exactly one line, byte for byte elsewhere."""
    before, after = split_lines(old), split_lines(new)
    ranges = {span.first: span for span in chosen}
    i = j = 0
    while i < len(before):
        span = ranges.get(i)
        if span is not None:
            if j >= len(after) or not after[j].startswith(f"{span.indent}- {{"):
                return False
            i, j = span.last + 1, j + 1
        else:
            if j >= len(after) or after[j] != before[i]:
                return False
            i, j = i + 1, j + 1
    return j == len(after) and len(after) == len(before) - sum(s.last - s.first for s in chosen)
