"""The shared Decision pin measure (rec-4730): trailing dated annotations are excluded, the entry's own text is not."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from scripts.checks.decisions.validate_decisions_size import _PER_ENTRY_CAP_BYTES
from scripts.decisions_md import iter_decision_sections
from tests.fixtures.decision_entry import authored, authored_bytes, cap_bytes, decision_entry

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
_BODY = "## Decision 7: x (Decided)\n\n**Decision:**\n\n1. One.\n\n**Related:** Decision 1.\n\n"
_TAIL = "---\n\n"
_MARKER_LINE = re.compile(r"^\*\*[^*\n]+:\*\*", re.MULTILINE)
_CAP_LITERAL = re.compile(r"\b6[_,]?144\b")
_EXEMPT = {"tests/checks/decisions/test_validate_decisions_size.py", "tests/checks/decisions/test_decision_pin_measure.py"}


@pytest.mark.parametrize(
    "trailers",
    [
        "[Amendment 2026-10-29, Decision 9: one.]\n\n",
        "[Amendment 2026-10-04, rec-4158: wraps\nacross lines.]\n\n[Amendment 2026-10-29: no source.]\n\n",
        "> **Update (2026-10-29, PLAN-x, rec-1):** wraps\n> across lines.\n\n",
        "> **Amended by Decision 9 (2026-05):** one.\n\n[Amendment 2026-10-29, Decision 9: two.]\n\n",
    ],
)
def test_trailing_dated_annotations_are_excluded(trailers: str) -> None:
    assert authored(_BODY + trailers + _TAIL) == _BODY + _TAIL


def test_entry_without_trailers_measures_as_the_validator_span() -> None:
    entry = _BODY + _TAIL
    assert authored(entry) == entry
    blocks = [block for _match, block in iter_decision_sections("preamble\n\n" + entry + "## Decision 8: y\n")]
    assert authored_bytes(entry) == len(blocks[0].encode("utf-8"))


@pytest.mark.parametrize(
    "paragraph",
    [
        "> **Note:** authored.\n\n",
        "> **Note:** on 2026-10-01 the **Clause 2:** text.\n\n",
        "[Amendment 2026-10-29: x.] more body [ref]\n\n",
    ],
)
def test_paragraphs_that_are_not_dated_annotations_count(paragraph: str) -> None:
    assert authored(_BODY + paragraph + _TAIL) == _BODY + paragraph + _TAIL


def test_body_after_a_trailer_counts() -> None:
    mid = _BODY + "[Amendment 2026-10-29, Decision 9: one.]\n\nMore body.\n\n" + _TAIL
    assert authored(mid) == mid


def test_dated_annotation_inside_the_body_counts() -> None:
    entry = _BODY.replace("**Related:**", "> **Update (2026-10-29):** an Intent clarification.\n\n**Related:**") + _TAIL
    assert authored(entry) == entry


def test_growth_of_the_entry_text_counts_byte_for_byte() -> None:
    trailer = "[Amendment 2026-10-29, Decision 9: one.]\n\n"
    grown = _BODY.replace("1. One.", "1. One, and longer.")
    assert authored_bytes(grown + trailer + _TAIL) - authored_bytes(_BODY + trailer + _TAIL) == len(", and longer")


def test_cap_is_the_contract_cap() -> None:
    contract = yaml.safe_load((_REPO / "docs/contracts/decision-entry.yaml").read_text(encoding="utf-8"))
    assert cap_bytes() == contract["size_governance"]["per_entry_size_norm"]["cap_bytes"] == _PER_ENTRY_CAP_BYTES


def test_decision_entry_requires_exactly_one_heading() -> None:
    text = "## Decision 1: a\n\nx\n\n## Decision 1: b\n\ny\n"
    with pytest.raises(AssertionError):
        decision_entry(text, 1)
    with pytest.raises(AssertionError):
        decision_entry(text, 2)


def test_live_entries_lose_only_dated_annotations() -> None:
    text = (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
    for match, block in iter_decision_sections(text):
        kept = authored(block).rstrip().removesuffix("---").rstrip()
        stripped = block.rstrip().removesuffix("---").rstrip()[len(kept) :]
        assert block.startswith(kept), match.group(1)
        assert "```" not in stripped and not _MARKER_LINE.search(stripped), match.group(1)
        for paragraph in re.split(r"\n[ \t]*\n", stripped.strip()):
            assert not paragraph.strip() or paragraph.strip().startswith(("[Amendment ", "> **")), match.group(1)


def _measures_encoded_length(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            for side in (node.left, *node.comparators):
                if (
                    isinstance(side, ast.Call)
                    and isinstance(side.func, ast.Name)
                    and side.func.id == "len"
                    and side.args
                    and isinstance(side.args[0], ast.Call)
                    and isinstance(side.args[0].func, ast.Attribute)
                    and side.args[0].func.attr == "encode"
                ):
                    return True
    return False


def test_no_test_pins_a_decision_by_its_whole_span() -> None:
    offenders = []
    for path in sorted((_REPO / "tests").rglob("test_*.py")):
        rel = path.relative_to(_REPO).as_posix()
        source = path.read_text(encoding="utf-8")
        if rel in _EXEMPT or "DECISIONS.md" not in source:
            continue
        if _CAP_LITERAL.search(source) or _measures_encoded_length(source):
            offenders.append(rel)
    assert offenders == [], "measure a Decision pin with tests.fixtures.decision_entry.authored_bytes and cap_bytes"
