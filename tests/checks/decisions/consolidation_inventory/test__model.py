"""Mirror of _model.py: derived tiers, rule table, probe overrides and the gated-set reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks.decisions.consolidation_inventory._model import (
    RULES,
    STUB_TEMPLATE,
    Evidence,
    Gate,
    Inventory,
    load_yaml,
    make_hit,
    note,
    with_probe,
)

KINDS = {
    "test_node", "graduated_record", "north_star_carrier", "supersession_waiver", "census_row", "manifest_reference",
    "manifest_held_token", "chain_edge", "delegated_key", "authorizing_marker", "roadmap_citation", "roadmap_related",
    "size_budget", "glossary_noted", "decision_list", "anchored_citation", "citation",
}  # fmt: skip


def test_rules_has_one_row_per_kind() -> None:
    assert set(RULES) == KINDS
    assert len({rule for rule, _, _ in RULES.values()}) == len(KINDS)


@pytest.mark.parametrize(
    ("types", "tier"),
    [
        (["structured_key"], "confirmed"),
        (["probe_breaks"], "confirmed"),
        (["dataflow_literal"], "probable"),
        (["cooccurrence"], "possible"),
        (["probe_survives"], "possible"),
        (["probe_red_before"], "possible"),
        (["cooccurrence", "probe_breaks"], "confirmed"),
        (["dataflow_literal", "probe_survives"], "probable"),
    ],
)
def test_tier_is_derived_from_evidence(types: list[str], tier: str) -> None:
    assert make_hit("test_node", 1, "t.py::x", [Evidence(t) for t in types]).tier == tier


def test_probe_result_overrides_rule_effect() -> None:
    hit = make_hit("test_node", 1, "t.py::x", [Evidence("dataflow_literal")])
    assert with_probe(hit, "breaks").effect == "retarget_or_retire_test"
    assert with_probe(hit, "breaks").tier == "confirmed"
    assert with_probe(hit, "survives").effect == "survives"
    assert with_probe(hit, "not_run").effect == "retarget_or_retire_test"
    assert with_probe(hit, "red_before").probe == "red_before"
    assert with_probe(hit, "bogus") is hit


def test_inventory_filters_by_tier_and_serializes_sorted() -> None:
    inv = Inventory(victims=(1,))
    inv.hits = [
        make_hit("test_node", 1, "b.py::x", [Evidence("cooccurrence")]),
        make_hit("census_row", 1, "a.yaml:S1", [Evidence("structured_key")]),
    ]
    assert [h.kind for h in inv.sorted_hits()] == ["census_row", "test_node"]
    assert [h.kind for h in inv.sorted_hits("confirmed")] == ["census_row"]
    data = inv.as_dict()
    assert data["schema_version"] == 2 and data["stub_template"] == "compacted-stub-v2" and data["probe"] is None
    assert "{amenders}" in STUB_TEMPLATE
    assert "{successor}" in STUB_TEMPLATE


def test_note_dedupes_and_load_yaml_never_raises(tmp_path: Path) -> None:
    notes: list[dict[str, str]] = []
    note(notes, "a", "b")
    note(notes, "a", "b")
    assert len(notes) == 1
    (tmp_path / "bad.yaml").write_text("a: [", encoding="utf-8")
    assert repr(load_yaml(tmp_path, "bad.yaml")) == "BAD" and repr(load_yaml(tmp_path, "none.yaml")) == "MISSING"


def _gate(tmp_path: Path, routing: str, files: dict[str, str] | None = None) -> Gate:
    (tmp_path / "docs/contracts").mkdir(parents=True)
    (tmp_path / "docs/contracts/approval-routing.yaml").write_text(routing, encoding="utf-8")
    for rel, text in (files or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return Gate(tmp_path)


def test_gate_reads_files_globs_keys_and_demotions(tmp_path: Path) -> None:
    gate = _gate(
        tmp_path,
        "gated:\n  files: [AGENTS.md, '**/CLAUDE.md']\n  yaml_keys:\n    a.yaml: [lifecycle]\n"
        "demotions:\n  rows: ['scripts/x.py', {file: 'scripts/y.py'}]\n",
    )
    assert gate.is_gated("AGENTS.md") and gate.is_gated("docs/CLAUDE.md") and gate.is_gated("CLAUDE.md")
    assert gate.is_gated("a.yaml", "lifecycle.sub") and not gate.is_gated("a.yaml", "other")
    assert gate.is_gated("scripts/x.py") and gate.is_gated("scripts/y.py") and not gate.is_gated("scripts/z.py")


def test_gate_reads_heading_and_list_item_sections(tmp_path: Path) -> None:
    doc = "# Top\n\n## Gated heading\nline 4\nline 5\n\n## Next\nline 8\n\n1. first\n2. 12p. rule\ncontinued\n3. third\n"
    gate = _gate(
        tmp_path,
        "gated:\n  md_sections:\n    s.md: ['## Gated heading', '2. 12p.']\n",
        {"s.md": doc},
    )
    assert gate.is_gated("s.md", None, 4) and gate.is_gated("s.md", None, 5)
    assert not gate.is_gated("s.md", None, 8)
    assert gate.is_gated("s.md", None, 12) or gate.is_gated("s.md", None, 13)
    assert not gate.is_gated("s.md", None, 14)


def test_gate_tolerates_a_missing_or_malformed_map(tmp_path: Path) -> None:
    assert not Gate(tmp_path).is_gated("AGENTS.md")
    assert not _gate(tmp_path / "x", "gated: [1, 2]\n").is_gated("AGENTS.md")


def test_gate_sections_edge_cases(tmp_path: Path) -> None:
    gate = _gate(tmp_path, "gated:\n  md_sections:\n    missing.md: ['## X']\n    s.md: [5, '## Absent']\n", {"s.md": "# t\n"})
    assert not gate.is_gated("missing.md", None, 1)
    assert not gate.is_gated("s.md", None, 1)


def test_demotion_rows_of_other_shapes_are_ignored(tmp_path: Path) -> None:
    gate = _gate(tmp_path, "demotions:\n  rows: [7, {file: 5}, {other: x}]\n")
    assert gate.demoted == set()
