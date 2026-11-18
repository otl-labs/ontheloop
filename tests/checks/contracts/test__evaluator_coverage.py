"""Mirror tests for scripts/checks/contracts/_evaluator_coverage.py (Decision 131) over tmp_path trees."""

from __future__ import annotations

from pathlib import Path

from scripts.checks.contracts import _evaluator_coverage as cov
from scripts.checks.contracts._population import module_contains_literal

_ROOT = Path(__file__).resolve().parents[3]
_ARCH = "docs/contracts/instruction-architecture.yaml"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _arch(root: Path, extra: str = "") -> cov.LiveTable:
    _write(
        root,
        _ARCH,
        "layers:\n  - layer: 1\n    content_locations:\n      - AGENTS.md\n      - '.claude/skills/*/SKILL.md'\n"
        "consumers:\n  coord:\n    loads:\n      - docs/contracts/coord-*.yaml\n  plain:\n    consumes_layers: [1]\n" + extra,
    )
    table = cov.load_live_table(root)
    assert isinstance(table, cov.LiveTable)
    return table


def _contract(root: Path, name: str, surface: str) -> None:
    _write(root, f"docs/contracts/{name}", f"contract:\n  class: D\n  evaluator:\n    agent_surface: {surface}\n")


def test_layer_glob_and_consumer_load_are_live(tmp_path: Path) -> None:
    table = _arch(tmp_path)
    assert cov.surface_liveness("x.yaml", "AGENTS.md", tmp_path, table)[0]
    assert cov.surface_liveness("x.yaml", ".claude/skills/a/SKILL.md", tmp_path, table)[0]
    assert cov.surface_liveness("x.yaml", "docs/contracts/coord-one.yaml", tmp_path, table)[0]
    live, detail = cov.surface_liveness("x.yaml", "docs/DECISIONS.md", tmp_path, table)
    assert not live and "not in instruction-architecture.yaml's live table" in detail


def test_retired_path_is_not_live(tmp_path: Path) -> None:
    table = _arch(tmp_path, "retired_surfaces:\n  paths:\n    - .claude/skills/old/SKILL.md\n")
    assert not cov.surface_liveness("x.yaml", ".claude/skills/old/SKILL.md", tmp_path, table)[0]
    assert cov.surface_liveness("x.yaml", ".claude/skills/new/SKILL.md", tmp_path, table)[0]


def test_one_contract_chain_is_live(tmp_path: Path) -> None:
    table = _arch(tmp_path)
    _contract(tmp_path, "a.yaml", "AGENTS.md")
    live, detail = cov.surface_liveness("c.yaml", "docs/contracts/a.yaml", tmp_path, table)
    assert live and detail == "live via a.yaml"


def test_chain_deeper_than_one_contract_is_not_live(tmp_path: Path) -> None:
    table = _arch(tmp_path)
    _contract(tmp_path, "a.yaml", "AGENTS.md")
    _contract(tmp_path, "b.yaml", "docs/contracts/a.yaml")
    live, detail = cov.surface_liveness("c.yaml", "docs/contracts/b.yaml", tmp_path, table)
    assert not live
    assert detail == "chain deeper than one contract: c.yaml -> b.yaml -> docs/contracts/a.yaml"


def test_loop_and_self_pointer_are_not_live(tmp_path: Path) -> None:
    table = _arch(tmp_path)
    _contract(tmp_path, "a.yaml", "docs/contracts/b.yaml")
    _contract(tmp_path, "b.yaml", "docs/contracts/a.yaml")
    live, detail = cov.surface_liveness("a.yaml", "docs/contracts/b.yaml", tmp_path, table)
    assert not live and detail.startswith("loop:")
    live, detail = cov.surface_liveness("a.yaml", "docs/contracts/a.yaml", tmp_path, table)
    assert not live and detail.startswith("loop:")


def test_retired_marker_must_be_listed(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/skills/old/SKILL.md", "---\nname: old\ndescription: Being retired; do not run.\n---\nbody\n")
    _write(tmp_path, ".claude/skills/ok/SKILL.md", "---\nname: ok\ndescription: Does things.\n---\nbody\n")
    errors = cov.retired_marker_errors(tmp_path, cov.LiveTable())
    assert len(errors) == 1 and ".claude/skills/old/SKILL.md" in errors[0]
    listed = cov.LiveTable(retired=(".claude/skills/old/SKILL.md",))
    assert cov.retired_marker_errors(tmp_path, listed) == []
    missing = cov.LiveTable(retired=(".claude/skills/old/SKILL.md", ".claude/commands/gone.md"))
    errors = cov.retired_marker_errors(tmp_path, missing)
    assert len(errors) == 1 and "gone.md" in errors[0] and "does not exist" in errors[0]


def test_trigger_must_occur_and_name_the_basename() -> None:
    text = "Read `docs/contracts/x.yaml`\n  in full before   editing."
    assert cov.triggers_resolve(text, "x.yaml", ["Read `docs/contracts/x.yaml` in full before editing."])[0]
    assert not cov.triggers_resolve(text, "x.yaml", ["Read x.yaml and then dance"])[0]
    assert not cov.triggers_resolve(text, "x.yaml", ["in full before editing."])[0]


def test_anchor_names_instructed_key() -> None:
    body = ["alpha", "beta"]
    keys, errors = cov.instructed_keys("x.yaml", ["see x.yaml#alpha.sub"], body)
    assert keys == {"alpha"} and errors == []
    keys, errors = cov.instructed_keys("x.yaml", ["see x.yaml"], body)
    assert keys == {"alpha", "beta"} and errors == []
    keys, errors = cov.instructed_keys("x.yaml", ["see x.yaml#nope"], body)
    assert keys == set() and "anchor names no top-level key" in errors[0]


def test_asserted_key_needs_literal_evidence(tmp_path: Path) -> None:
    module = tmp_path / "check.py"
    module.write_text('KEY = "present"\nENV = "status"\n', encoding="utf-8")
    body = ["present", "absent"]

    def run(key: str) -> bool:
        return cov.asserts_resolve("x.yaml", [key], body, [module], module_contains_literal)[0]

    assert run("present")
    assert not run("absent")
    assert not run("status")
    assert not run("contract")


def test_coverage_table_cross_check() -> None:
    kinds = {"a.yaml": "agent_surface", "b.yaml": "check"}
    rows = {
        "ghost.yaml": cov.CoverageRow(triggers=("t",)),
        "b.yaml": cov.CoverageRow(triggers=("t",)),
        "a.yaml": cov.CoverageRow(asserts=("k",)),
    }
    errors = cov.coverage_table_errors(rows, kinds)
    assert len(errors) == 3
    assert any("ghost.yaml" in e for e in errors)
    assert any("b.yaml" in e and "triggers" in e for e in errors)
    assert any("a.yaml" in e and "asserts" in e for e in errors)


def test_coverage_line_format() -> None:
    line = cov.coverage_line("x.yaml", ["a", "b", "c"], {"b", "zz"}, {"a"})
    assert line == "Coverage: x.yaml asserted=[b] instructed=[a] neither=[c]"
    assert cov.coverage_line("x.yaml", [], set(), set()) == "Coverage: x.yaml asserted=[] instructed=[] neither=[]"


def test_live_table_load_fails_closed(tmp_path: Path) -> None:
    assert isinstance(cov.load_live_table(tmp_path), str)
    _write(tmp_path, _ARCH, "layers: nope\n")
    assert isinstance(cov.load_live_table(tmp_path), str)
    _write(tmp_path, _ARCH, "retired_surfaces:\n  paths: a-string\n")
    assert isinstance(cov.load_live_table(tmp_path), str)


def test_real_tree_retired_and_live() -> None:
    table = cov.load_live_table(_ROOT)
    assert isinstance(table, cov.LiveTable)
    assert all(not cov.directly_live(path, table) for path in table.retired)
    assert cov.directly_live(".claude/skills/planning/SKILL.md", table)
    assert cov.retired_marker_errors(_ROOT, table) == []


def test_body_keys_drops_envelope_and_tolerates_non_mapping() -> None:
    assert cov.body_keys({"contract": {}, "status": "x", "rules": 1, "alpha": 2}) == ["alpha", "rules"]
    assert cov.body_keys(["not", "a", "mapping"]) == []


def test_live_table_malformed_variants_each_return_an_error(tmp_path: Path) -> None:
    bad = [
        "- a\n- b\n",
        "layers:\n  - content_locations: nope\n",
        "consumers: nope\n",
        "consumers:\n  c:\n    loads: nope\n",
        "retired_surfaces: nope\n",
        "layers: [: bad yaml",
    ]
    for text in bad:
        _write(tmp_path, _ARCH, text)
        assert isinstance(cov.load_live_table(tmp_path), str), text


def test_surface_that_is_not_a_class_d_contract_is_not_live(tmp_path: Path) -> None:
    table = _arch(tmp_path)
    _write(tmp_path, "docs/contracts/plain.yaml", "contract:\n  class: A\n")
    live, detail = cov.surface_liveness("x.yaml", "docs/contracts/plain.yaml", tmp_path, table)
    assert not live and "live table" in detail
    live, _ = cov.surface_liveness("x.yaml", "docs/contracts/missing.yaml", tmp_path, table)
    assert not live


def test_retired_marker_ignores_unparseable_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/skills/a/SKILL.md", "no frontmatter\n")
    _write(tmp_path, ".claude/skills/b/SKILL.md", "---\nname: b\n")
    _write(tmp_path, ".claude/skills/c/SKILL.md", "---\n: [bad\n---\nbody\n")
    _write(tmp_path, ".claude/skills/d/SKILL.md", "---\nname: d\n---\nbody\n")
    skill = tmp_path / ".claude" / "skills" / "e" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"\xff\xfe\x00bad")
    assert cov.retired_marker_errors(tmp_path, cov.LiveTable()) == []


def test_coverage_loader_accepts_rows_and_rejects_malformed_tables(tmp_path: Path) -> None:
    pop = "docs/contracts/contract-population.yaml"
    assert isinstance(cov.load_coverage(tmp_path), str)
    _write(tmp_path, pop, "other: 1\n")
    assert cov.load_coverage(tmp_path) == {}
    _write(tmp_path, pop, "evaluator_coverage:\n  a.yaml: {triggers: [t1]}\n  b.yaml: {asserts: [k]}\n")
    rows = cov.load_coverage(tmp_path)
    assert rows == {"a.yaml": cov.CoverageRow(triggers=("t1",)), "b.yaml": cov.CoverageRow(asserts=("k",))}
    for text in (
        "evaluator_coverage: nope\n",
        "evaluator_coverage:\n  a.yaml: nope\n",
        "evaluator_coverage:\n  a.yaml: {other: [x]}\n",
        "evaluator_coverage:\n  a.yaml: {triggers: []}\n",
        "evaluator_coverage:\n  a.yaml: {triggers: [x], asserts: [y]}\n",
        "evaluator_coverage:\n  a.yaml: {}\n",
    ):
        _write(tmp_path, pop, text)
        assert isinstance(cov.load_coverage(tmp_path), str), text
