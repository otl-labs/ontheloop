"""rec-5627's acceptance node and the hermetic fixture suite for the consolidation coupling inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.checks.decisions.consolidation_inventory import inventory
from scripts.checks.decisions.consolidation_inventory.__main__ import main
from tests.checks.decisions.consolidation_inventory import build_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path)


def _by(inv, kind):
    return [h for h in inv.hits if h.kind == kind]


def test_planted_couplings_found_per_kind(repo: Path) -> None:
    inv = inventory(repo, [9001, 9002], include_citations=True)
    expected = {
        ("test_node", 9002): ("tests/test_pin.py::test_body_pin", "probable", "retarget_or_retire_test", "R-TEST"),
        ("graduated_record", 9002): ("pin-record", "possible", "retire_and_replace", "R-RECORD"),
        ("north_star_carrier", 9001): ("mission.carried_by", "confirmed", "move_carrier", "R-NS"),
        ("north_star_carrier", 9002): ("NS.9.carried_by", "confirmed", "move_carrier", "R-NS"),
        ("supersession_waiver", 9001): ("9003->9001", "confirmed", "delete_orphan_waiver", "R-WAIVER"),
        ("census_row", 9002): ("S5-9002", "confirmed", "remove_row", "R-CENSUS"),
        ("manifest_reference", 9001): ("supersedes", "confirmed", "resolves_to_successor", "R-MANIFEST"),
        ("manifest_held_token", 9002): (
            "units.9001 pt1.held",
            "confirmed",
            "successor_must_carry_token",
            "R-MANIFEST-HELD",
        ),
        ("chain_edge", 9001): ("9003.amends", "confirmed", "victim_or_carry_forward", "R-CHAIN"),
        ("delegated_key", 9001): ("delegates:docs/contracts/thing.yaml#knob", "confirmed", "redelegate", "R-DELEG"),
        ("authorizing_marker", 9001): (
            "config/limits.yaml:1",
            "confirmed",
            "successor_must_mention_not_mechanism",
            "R-MARKER",
        ),
        ("roadmap_citation", 9001): ("T9.1.exit_criteria", "confirmed", "restate_not_retire", "R-ROADMAP"),
        ("roadmap_related", 9002): ("T9.1.related_decisions", "confirmed", "stale_citation", "R-ROADREL"),
        ("size_budget", 9002): ("tests/test_pin.py", "confirmed", "ratchet_after_edit", "R-SIZE"),
        ("glossary_noted", 9001): ("read_as.noted_decisions", "confirmed", "drop_from_noted_with_log_row", "R-GLOSS"),
        ("decision_list", 9001): ("decisions_cited", "confirmed", "review_list", "R-LIST"),
        ("anchored_citation", 9001): ("AGENTS.md:1", "confirmed", "stale_anchor", "R-ANCHOR"),
        ("citation", 9002): ("AGENTS.md:2", "confirmed", "stale_citation", "R-CITE"),
    }
    for (kind, victim), (locator_part, tier, effect, rule) in expected.items():
        found = [h for h in _by(inv, kind) if h.victim == victim and locator_part in h.locator]
        assert found, f"{kind} {victim} {locator_part} not found in {[h.locator for h in _by(inv, kind)]}"
        hit = found[0]
        assert (hit.tier, hit.effect, hit.rule_id) == (tier, effect, rule), (kind, victim, hit.tier)
    assert {h.kind for h in inv.hits} == {k for k, _ in expected} | {"test_node"}
    in_log = [h for h in _by(inv, "anchored_citation") if h.locator.startswith("docs/DECISIONS.md:")]
    assert in_log and all(h.gated for h in in_log)
    assert not any(h.gated for h in _by(inv, "census_row"))
    assert [h for h in _by(inv, "decision_list") if h.gated], (
        "yaml_keys gate must flag docs/contracts/other.yaml decisions_cited"
    )
    assert all(h.victim in (9001, 9002) for h in inv.hits)


def test_false_positives_stay_possible(repo: Path) -> None:
    inv = inventory(repo, [9001])
    nodes = {h.locator: h for h in _by(inv, "test_node")}
    assert not any("test_unrelated" in loc for loc in nodes)
    plain = nodes["tests/test_pin.py::test_plain_number"]
    assert plain.tier == "possible" and plain.evidence[0].type == "cooccurrence"
    deep = nodes["tests/test_pin.py::test_red_before"]
    assert deep.tier == "probable" and deep.evidence[0].type == "dataflow_literal"


def test_unrelated_entry_leaves_output_unchanged(repo: Path) -> None:
    before = json.dumps(inventory(repo, [9001, 9002]).as_dict(), sort_keys=True)
    log = repo / "docs/DECISIONS.md"
    log.write_text(
        log.read_text(encoding="utf-8") + "\n## Decision 9500: Unrelated (Decided)\n\n**Decision:** nothing.\n",
        encoding="utf-8",
    )
    after = json.dumps(inventory(repo, [9001, 9002]).as_dict(), sort_keys=True)
    assert before == after


def test_renumbered_victim_moves_its_hits(repo: Path) -> None:
    first = inventory(repo, [9001])
    second = inventory(repo, [9002])
    assert {h.victim for h in first.hits} == {9001}
    assert {h.victim for h in second.hits} == {9002}
    assert {h.locator for h in first.hits if h.kind == "census_row"} == set()
    assert {h.locator for h in second.hits if h.kind == "census_row"}


def test_output_is_byte_identical_across_runs(repo: Path) -> None:
    runs = [json.dumps(inventory(repo, [9002, 9001]).as_dict(), sort_keys=True) for _ in range(2)]
    assert runs[0] == runs[1]
    assert json.dumps(inventory(repo, [9001, 9002, 9001]).as_dict(), sort_keys=True) == runs[0]


def test_unknown_victim_exits_1(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["4242", "--root", str(repo)]) == 1
    assert "no Decision header" in capsys.readouterr().err


def test_citations_counted_unless_included(repo: Path) -> None:
    counted = [h for h in inventory(repo, [9002]).hits if h.kind == "citation"]
    each = [h for h in inventory(repo, [9002], include_citations=True).hits if h.kind == "citation"]
    agents = [h for h in counted if h.locator == "AGENTS.md"]
    assert len(agents) == 1 and agents[0].evidence[0].detail.startswith("2 citations")
    assert len(each) > len(counted)
    assert any(h.locator == "AGENTS.md:2" for h in each)
