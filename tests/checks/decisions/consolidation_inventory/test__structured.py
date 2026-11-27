"""Mirror of _structured.py: each structured kind on small fixtures."""

from __future__ import annotations

from pathlib import Path

from scripts.checks.decisions.consolidation_inventory import _structured as s
from scripts.checks.decisions.consolidation_inventory._model import Gate
from tests.checks.decisions.consolidation_inventory import build_repo


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(fn, root: Path, victims=(9001,)):
    notes: list[dict[str, str]] = []
    return fn(root, tuple(victims), Gate(root), notes), notes


def test_deprecated_records_are_ignored_and_grep_count_records_are_found(tmp_path: Path) -> None:
    record = (
        "check_id: {id}\nprimitive_slot: grep_count\nguard_target: docs/DECISIONS.md\n"
        "check_spec:\n  path: docs/DECISIONS.md\n  pattern: 'Decision 9001'\n"
    )
    _write(tmp_path, "config/agent/verification_registry/entries/live.yaml", record.format(id="live"))
    _write(tmp_path, "config/agent/verification_registry/entries/deprecated/old.yaml", record.format(id="old"))
    _write(
        tmp_path,
        "config/agent/verification_registry/entries/no-log.yaml",
        "check_id: x\ncheck_spec:\n  pattern: 'Decision 9001'\n",
    )
    hits, _ = _run(s.graduated_records, tmp_path)
    assert [h.locator.rsplit(":", 1)[1] for h in hits] == ["live"]


def test_semantics_carried_by_prose_is_not_a_carrier(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/contracts/north-star.yaml",
        "semantics:\n  carried_by: 'the Decisions that carry it (Decision 9001 as prose)'\nmission:\n  carried_by: '9001, 5'\n"
        "principles:\n  - {id: NS.1, carried_by: [7], ratified_via: 'Decision 9001 / PLAN-x'}\n",
    )
    hits, _ = _run(s.north_star, tmp_path)
    assert sorted(h.locator.split(":", 1)[1] for h in hits) == ["NS.1.ratified_via", "mission.carried_by"]


def test_complete_tier_items_are_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/ROADMAP-PLATFORM.yaml",
        "tier_items:\n  - {id: T1, status: complete, exit_criteria: ['Decision 9001'], related_decisions: [9001]}\n"
        "  - {id: T2, status: in_progress, exit_criteria: ['Decision 9001'], related_decisions: [9001]}\n",
    )
    hits, _ = _run(s.roadmap, tmp_path)
    assert sorted(h.locator.split(":", 1)[1] for h in hits) == ["T2.exit_criteria", "T2.related_decisions"]


def test_an_unparseable_or_missing_surface_is_a_note_not_a_crash(tmp_path: Path) -> None:
    _write(tmp_path, "docs/contracts/north-star.yaml", "mission: [\n")
    _write(tmp_path, "docs/decisions-index.json", "{not json")
    hits, notes = _run(s.north_star, tmp_path)
    assert hits == [] and notes == [{"surface": "docs/contracts/north-star.yaml", "reason": "surface file does not parse"}]
    hits, notes = _run(s.chain_edges, tmp_path)
    assert hits == [] and notes[0]["reason"] == "surface file does not parse"
    hits, notes = _run(s.delegated_keys, tmp_path)
    assert hits == [] and notes[0]["reason"] == "surface file is missing"
    for fn in s.SURFACES:
        _run(fn, tmp_path / "empty")


def test_decision_lists_skip_keys_another_kind_reads(tmp_path: Path) -> None:
    _write(tmp_path, "docs/contracts/glossary.yaml", "read_as:\n  noted_decisions: [9001]\n")
    _write(tmp_path, "docs/contracts/d.yaml", "a:\n  related_decision: 9001\n  decisions_cited: [1, 9001]\n  other: [9001]\n")
    hits, _ = _run(s.decision_lists, tmp_path)
    assert sorted(h.locator.split(":", 1)[1] for h in hits) == ["a.decisions_cited", "a.related_decision"]


def test_markers_waivers_census_and_manifests(tmp_path: Path) -> None:
    _write(tmp_path, "scripts/x.sh", "# raise-approved: dec-9001 why\n# raise-approved: dec-5 other\n")
    _write(
        tmp_path,
        "config/decision_supersession_waivers.yaml",
        "waivers:\n  - {superseder: 9001, victim: 4}\n  - {superseder: 5, victim: 6}\n",
    )
    _write(
        tmp_path,
        "audits/standards-cleanup-census.yaml",
        "rows: [{id: R1, root: 9001}]\nexceptions: [{id: E1, victim: [9001]}]\n",
    )
    _write(
        tmp_path,
        "docs/decision-migration/consolidations/m.yaml",
        "supersedes: [9001]\ncarried_forward: [9001]\nunits: [{id: u, held: ['Decision 9001']}]\n",
    )
    assert [h.locator for h in _run(s.markers, tmp_path)[0]] == ["scripts/x.sh:1"]
    assert [h.locator.split(":", 1)[1] for h in _run(s.waivers, tmp_path)[0]] == ["9001->4"]
    assert sorted(h.locator.split(":", 1)[1] for h in _run(s.census, tmp_path)[0]) == ["E1", "R1"]
    assert len(_run(s.manifests, tmp_path)[0]) == 3


def test_every_surface_reads_the_fixture_repo(tmp_path: Path) -> None:
    root = build_repo(tmp_path)
    kinds = set()
    for fn in s.SURFACES:
        hits, notes = _run(fn, root, (9001, 9002))
        assert notes == []
        kinds |= {h.kind for h in hits}
    edited = [h for fn in s.SURFACES for h in _run(fn, root, (9001, 9002))[0]]
    budgets = s.size_budgets(root, edited, Gate(root))
    assert {h.locator for h in budgets} == {"config/structural_size_budgets.yaml:tests/test_pin.py"} or budgets == []
    assert {"chain_edge", "delegated_key", "authorizing_marker", "graduated_record", "decision_list"} <= kinds
    assert [h.victim for h in _run(s.delegated_keys, root, (9001, 9002))[0]] == [9001]
    assert [h.locator for h in _run(s.chain_edges, root, (9001, 9003))[0]] == []


def test_size_budget_rows_follow_the_files_hits_edit(tmp_path: Path) -> None:
    _write(tmp_path, "config/prose_budgets.yaml", "  docs/CLAUDE.md: 100\n")
    _write(tmp_path, "config/structural_size_budgets.yaml", '  "docs/x.yaml": 7\n  docs/other.yaml: 9\n')
    from scripts.checks.decisions.consolidation_inventory._model import Evidence, make_hit

    edited = [
        make_hit("census_row", 9001, "docs/CLAUDE.md:r", [Evidence("structured_key")]),
        make_hit("test_node", 9001, "docs/x.yaml::t", [Evidence("cooccurrence")]),
        make_hit("chain_edge", 9001, "docs/other.yaml:1.amends", [Evidence("structured_key")]),
    ]
    rows = s.size_budgets(tmp_path, edited, Gate(tmp_path))
    assert sorted(h.locator for h in rows) == [
        "config/prose_budgets.yaml:docs/CLAUDE.md",
        "config/structural_size_budgets.yaml:docs/x.yaml",
    ]


def test_odd_shapes_are_skipped_without_raising(tmp_path: Path) -> None:
    assert s._ints(True) == set() and s._ints(3.5) == set() and s._ints("a 7, 8") == {7, 8}
    assert s._strings(5) == [] and s._strings({"a": ["x", {"b": "y"}]}) == ["x", "y"]
    _write(tmp_path, "config/agent/verification_registry/entries/list.yaml", "- a\n- b\n")
    _write(tmp_path, "config/decision_supersession_waivers.yaml", "waivers: [1, {superseder: 9001, victim: 2}]\n")
    _write(tmp_path, "audits/standards-cleanup-census.yaml", "rows: [1]\nexceptions: [2]\n")
    _write(tmp_path, "docs/decision-migration/consolidations/m.yaml", "- not a mapping\n")
    _write(tmp_path, "docs/decisions-index.json", '{"decisions": "no"}')
    _write(tmp_path, "docs/ROADMAP-PLATFORM.yaml", "tier_items: [1, {id: T1, status: x}]\n")
    _write(
        tmp_path,
        "docs/contracts/north-star.yaml",
        "mission: 1\nprinciples: [2]\ncontract: {ratified_via: 'Decision 9001 / x'}\n",
    )
    _write(tmp_path, "docs/contracts/d.yaml", "decision_x: some text\nflag_decision: true\nnest:\n  - decisions: [9001]\n")
    _write(tmp_path, "docs/contracts/bad.yaml", "a: [\n")
    _write(tmp_path, "config/.venv/skip.yaml", "decision: 9001\n")
    _write(tmp_path, "config/skip/.git/hook.sh", "# raise-approved: dec-9001 x\n")
    _write(tmp_path, "config/notes.txt", "# raise-approved: dec-9001 not a marker suffix\n")
    for fn in s.SURFACES:
        _run(fn, tmp_path)
    assert _run(s.decision_lists, tmp_path)[1] == [
        {"surface": "docs/contracts/bad.yaml", "reason": "surface file does not parse"}
    ]
    assert [h.locator for h in _run(s.north_star, tmp_path)[0]] == ["docs/contracts/north-star.yaml:contract.ratified_via"]
    assert [h.locator.split(":", 1)[1] for h in _run(s.decision_lists, tmp_path)[0]] == ["nest[0].decisions"]
    assert _run(s.markers, tmp_path)[0] == []
    assert [h.locator.split(":", 1)[1] for h in _run(s.waivers, tmp_path)[0]] == ["9001->2"]


def test_inbound_manifest_references_to_a_victim_are_listed(tmp_path: Path) -> None:
    stub = "## Decision {n}: s\n\n**Status:** Superseded\n\n**Superseded by: Decision 9005**\n\n"
    live = "## Decision 9005: v\n\n```yaml\nnumber: 9005\nsupersedes: [9001]\n```\n\n**Status:** Decided\n"
    _write(tmp_path, "docs/DECISIONS.md", "# Decisions\n\n" + stub.format(n=9001) + stub.format(n=9006) + live)
    _write(
        tmp_path,
        "docs/decision-migration/consolidations/m.yaml",
        "supersedes: [4]\ncarried_forward: [9001, 9006, 4]\nunits:\n"
        "  - {id: u1, held: ['Decision 9001'], token: T1}\n  - {id: u2, held: ['Decision 9005'], token: T2}\n"
        "  - {id: u3, held: ['Decision 9006'], token: T3}\n  - {id: u4, held: ['x.yaml#k'], token: T4}\n  - not-a-mapping\n",
    )
    hits, _ = _run(s.manifests, tmp_path, (9005,))
    assert sorted((h.kind, h.effect, h.locator.split(":", 1)[1], h.evidence[0].detail) for h in hits) == [
        ("manifest_held_token", "successor_must_carry_token", "units.u1.held", "held Decision 9001 (-> 9005) token 'T1'"),
        ("manifest_held_token", "successor_must_carry_token", "units.u2.held", "held Decision 9005 token 'T2'"),
        ("manifest_reference", "resolves_to_successor", "carried_forward", "carried_forward Decision 9001 (-> 9005)"),
    ]
