"""Mirror tests for scripts/checks/roadmap/validate_roadmap_liveness.py -- the three-leg roadmap
blocking-graph liveness ratchet (PLAN-roadmap-blocking-liveness-detector; rec-3395, rec-3394)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks import _common, registry
from scripts.checks.roadmap.validate_roadmap_liveness import (
    _leg_b_rehome_coverage,
    _leg_c_prose_blocking_edges,
    _load_baseline_entries,
    _load_blocking_phrases,
    validate_roadmap_liveness,
)
from scripts.platform_roadmap_models import RoadmapDocument

_DOC_META = {"id": "ROADMAP-TEST", "version": 1, "status": "draft", "filed_via": "pending_log_decision_lambda"}

_VALID_CONTRACT = """
contract:
  id: roadmap-liveness
  class: D
  contract_version: 1
  status: provisional_v0
  description: test fixture
  subject: roadmap-liveness
  evaluator:
    check: validate_roadmap_liveness
amendment_log: []
leg_c_blocking_phrase_grammar:
  phrases:
    - "blocked by"
    - "blocked on"
    - "gated on"
    - "requires .* ratified"
"""


def _item(item_id: str, tier: str = "T9", depends_on: list | None = None, status: str = "not_started", exit_criteria=None):
    return {
        "id": item_id,
        "tier": tier,
        "name": f"Test item {item_id}",
        "depends_on": depends_on or [],
        "files_in_scope": [],
        "exit_criteria": exit_criteria or [],
        "effort": "S",
        "status": status,
    }


def _cd(cd_id: str, state: str = "pending", gates: list | None = None):
    return {"id": cd_id, "title": f"Decision {cd_id}", "state": state, "gates": gates or []}


def _criterion(crit_id: str, text: str = "criterion text", status: str = "open", met_by=None, blocked_by=None):
    d: dict = {"id": crit_id, "text": text, "status": status}
    if met_by is not None:
        d["met_by"] = met_by
    if blocked_by is not None:
        d["blocked_by"] = blocked_by
    return d


def _doc_dict(tier_items: list | None = None, candidate_decisions: list | None = None) -> dict:
    return {"document": _DOC_META, "tier_items": tier_items or [], "candidate_decisions": candidate_decisions or []}


def _build(tier_items: list | None = None, candidate_decisions: list | None = None) -> RoadmapDocument:
    return RoadmapDocument.model_validate(_doc_dict(tier_items, candidate_decisions))


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_root_fixtures(tmp_path: Path, roadmap: dict, baseline_entries: list[str]) -> None:
    _write_yaml(tmp_path / "docs" / "ROADMAP-PLATFORM.yaml", yaml.safe_dump(roadmap))
    entries_yaml = "entries:\n" + "".join(f"  - {e}\n" for e in baseline_entries) if baseline_entries else "entries: []\n"
    _write_yaml(tmp_path / "config" / "roadmap_liveness_baseline.yaml", entries_yaml)
    _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)


class TestCoverage:
    """Leg A(a) -- the PRIMARY, unconditional, base-independent assertion."""

    def test_new_toxic_node_absent_from_baseline_fails(self, tmp_path: Path) -> None:
        """A synthetic roadmap that closes a fresh completion-demanding ring must FAIL even
        though it touches no config file and produces no baseline growth -- the assertion that
        makes leg A a liveness gate rather than a config-file lint."""
        roadmap = _doc_dict(
            tier_items=[
                _item("T9.a", depends_on=["T9.b"]),
                _item("T9.b", exit_criteria=[_criterion("c1", blocked_by=[{"ref": "T9.a", "until": "complete"}])]),
            ]
        )
        _write_root_fixtures(tmp_path, roadmap, [])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=None),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert "Roadmap blocking-graph liveness ratchet" in failed

    def test_baselined_toxic_ring_passes(self, tmp_path: Path) -> None:
        roadmap = _doc_dict(
            tier_items=[
                _item("T4.9", depends_on=["T4.9a"]),
                _item("T4.9a", exit_criteria=[_criterion("c1", blocked_by=[{"ref": "T4.9", "until": "complete"}])]),
            ]
        )
        _write_root_fixtures(tmp_path, roadmap, ["T4.9", "T4.9a"])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=None),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert failed == []


class TestRatchet:
    def test_unseeded_id_addition_fails(self, tmp_path: Path) -> None:
        """Adding an id absent from the frozen _BASELINE_SEED fails even though the config edit
        alone is well-formed (no diff-relative growth: base matches head)."""
        roadmap = _doc_dict()
        _write_root_fixtures(tmp_path, roadmap, ["T9.unseeded"])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value="entries:\n  - T9.unseeded\n"),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert "Roadmap blocking-graph liveness ratchet" in failed

    def test_drained_id_must_be_removed(self, tmp_path: Path) -> None:
        """A baselined id (in the real _BASELINE_SEED) the live roadmap no longer measures toxic
        FAILS under leg A(d) until removed."""
        roadmap = _doc_dict()
        _write_root_fixtures(tmp_path, roadmap, ["T4.9"])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value="entries:\n  - T4.9\n"),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert "Roadmap blocking-graph liveness ratchet" in failed

    def test_drained_id_removed_passes(self, tmp_path: Path) -> None:
        roadmap = _doc_dict()
        _write_root_fixtures(tmp_path, roadmap, [])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value="entries: []\n"),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert failed == []

    def test_missing_base_skips_only_subleg_c_without_treating_as_empty(self, tmp_path: Path, capsys) -> None:
        """An unreadable diff base SKIPs sub-leg (c) loudly rather than treating "missing" as
        "empty" (which would read every baselined id as grown and FAIL a seeding PR) or disabling
        the base-independent legs -- legs A(a)/A(b)/A(d), B and C keep enforcing."""
        roadmap = _doc_dict(
            tier_items=[
                _item("T4.9", depends_on=["T4.9a"]),
                _item("T4.9a", exit_criteria=[_criterion("c1", blocked_by=[{"ref": "T4.9", "until": "complete"}])]),
            ]
        )
        _write_root_fixtures(tmp_path, roadmap, ["T4.9", "T4.9a"])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=None),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        out = capsys.readouterr().out
        assert "SKIP (leg A sub-leg c only)" in out
        assert failed == []

    def test_baseline_growth_relative_to_base_fails(self, tmp_path: Path) -> None:
        """Isolates leg A(c): a reachable base whose baseline content differs from HEAD (net
        growth) fails even when both ids are legitimately seeded and currently measured toxic."""
        roadmap = _doc_dict(
            tier_items=[
                _item("T4.9", depends_on=["T4.9a"]),
                _item("T4.9a", exit_criteria=[_criterion("c1", blocked_by=[{"ref": "T4.9", "until": "complete"}])]),
            ]
        )
        _write_root_fixtures(tmp_path, roadmap, ["T4.9", "T4.9a"])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value="entries:\n  - T4.9\n"),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert "Roadmap blocking-graph liveness ratchet" in failed


class TestRehomeCoverage:
    def test_rehome_target_must_carry_open_criterion(self) -> None:
        doc = _build(
            tier_items=[
                _item("T9.source", exit_criteria=[_criterion("c1", status="rehomed", met_by="T9.target")]),
                _item("T9.target", exit_criteria=[_criterion("c1", status="met", met_by="some-commit-sha")]),
            ]
        )
        violations = _leg_b_rehome_coverage(doc)
        assert len(violations) == 1
        assert "T9.source" in violations[0]
        assert "T9.target" in violations[0]

    def test_rehome_target_with_open_criterion_passes(self) -> None:
        doc = _build(
            tier_items=[
                _item("T9.source", exit_criteria=[_criterion("c1", status="rehomed", met_by="T9.target")]),
                _item("T9.target", exit_criteria=[_criterion("c1", status="open")]),
            ]
        )
        assert _leg_b_rehome_coverage(doc) == []

    def test_bare_string_criteria_normalize_to_open_via_the_loader(self) -> None:
        """Bare-string criteria normalize to status 'open' via TierItem's own field_validator --
        never a naive yaml.safe_load + isinstance(dict) path, which would miss them entirely."""
        doc = _build(
            tier_items=[
                _item("T9.source", exit_criteria=[_criterion("c1", status="rehomed", met_by="T9.target")]),
                {**_item("T9.target"), "exit_criteria": ["a bare-string criterion"]},
            ]
        )
        assert _leg_b_rehome_coverage(doc) == []

    def test_live_roadmap_reports_zero_violations(self) -> None:
        from scripts.platform_roadmap_state import load

        doc = load(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")
        assert _leg_b_rehome_coverage(doc) == []


class TestLegC:
    def test_added_criterion_with_blocking_phrase_and_no_blocked_by_fails(self, tmp_path: Path) -> None:
        base_roadmap = _doc_dict(tier_items=[_item("T9.x")])
        doc = _build(
            tier_items=[
                _item("T9.x", exit_criteria=[_criterion("c1", text="This work is blocked on T9.1 before it can land.")]),
                _item("T9.1"),
            ]
        )
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert len(violations) == 1
        assert "T9.x" in violations[0] and "T9.1" in violations[0]

    def test_changed_criterion_with_no_blocking_phrase_yields_no_findings(self, tmp_path: Path) -> None:
        base_roadmap = _doc_dict(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text="original text")])])
        doc = _build(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text="an ordinary, unrelated criterion")])])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert violations == []

    def test_unchanged_criterion_text_is_out_of_scope(self, tmp_path: Path) -> None:
        text = "This work is blocked on T9.1 before it can land."
        base_roadmap = _doc_dict(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text=text)])])
        doc = _build(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text=text)]), _item("T9.1")])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert violations == []

    def test_existing_blocked_by_entry_suppresses_the_finding(self, tmp_path: Path) -> None:
        base_roadmap = _doc_dict(tier_items=[_item("T9.x")])
        doc = _build(
            tier_items=[
                _item(
                    "T9.x",
                    exit_criteria=[
                        _criterion("c1", text="blocked on T9.1", blocked_by=[{"ref": "T9.1", "until": "complete"}])
                    ],
                ),
                _item("T9.1"),
            ]
        )
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert violations == []

    def test_missing_base_skips_leg_c(self, tmp_path: Path, capsys) -> None:
        doc = _build(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text="blocked on T9.1")]), _item("T9.1")])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=None),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert violations == []
        assert "SKIP (leg C only)" in capsys.readouterr().out

    def test_no_phrases_declared_yields_no_findings(self, tmp_path: Path) -> None:
        base_roadmap = _doc_dict(tier_items=[_item("T9.x")])
        doc = _build(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text="blocked on T9.1")]), _item("T9.1")])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(
                tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml",
                "leg_c_blocking_phrase_grammar:\n  phrases: []\n",
            )
            violations = _leg_c_prose_blocking_edges(doc)

        assert violations == []

    def test_malformed_base_item_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """A base-era item that fails TierItem.model_validate (e.g. missing required fields) is
        skipped, not fatal -- its criteria read as having no prior text, so a criterion that
        happens to match is treated as added/changed."""
        base_roadmap = {"document": _DOC_META, "tier_items": [{"id": "T9.broken"}], "candidate_decisions": []}
        doc = _build(tier_items=[_item("T9.broken", exit_criteria=[_criterion("c1", text="blocked on T9.1")]), _item("T9.1")])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert len(violations) == 1

    def test_ratified_until_is_suggested_for_a_cd_ref(self, tmp_path: Path) -> None:
        base_roadmap = _doc_dict(tier_items=[_item("T9.x")])
        doc = _build(
            tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text="gated on CD.5 ratifying")])],
            candidate_decisions=[_cd("CD.5")],
        )
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert len(violations) == 1
        assert "until: ratified" in violations[0]

    def test_wildcard_phrase_is_matched_as_regex_not_literal_substring(self, tmp_path: Path) -> None:
        """docs/contracts/roadmap-liveness.yaml declares several phrases with regex wildcards
        (e.g. "requires .* ratified") -- no real prose contains a literal ".*", so phrase
        matching must be re.search, never `phrase in text` substring containment."""
        base_roadmap = _doc_dict(tier_items=[_item("T9.x")])
        doc = _build(
            tier_items=[
                _item("T9.x", exit_criteria=[_criterion("c1", text="This sub-task requires CD.5 to be ratified first.")])
            ],
            candidate_decisions=[_cd("CD.5")],
        )
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert len(violations) == 1


class TestLegCArchivedBase:
    """Decision 227: a stub-bearing diff base is hydrated leniently from its own archive, read through a
    PATH-AWARE base reader (the stub-less TestLegC mocks above stay single-valued and untouched)."""

    _TEXT = "This work is blocked on T9.1 before it can land."

    def _violations(self, tmp_path: Path, head_text: str) -> list[str]:
        record = {**_item("T9.x", status="complete"), "exit_criteria": [_criterion("c1", text=self._TEXT)]}
        stub = {key: record[key] for key in ("id", "tier", "name", "depends_on", "status")} | {"archived": True}
        texts = {
            "docs/ROADMAP-PLATFORM.yaml": yaml.safe_dump(_doc_dict(tier_items=[stub, _item("T9.1")])),
            "docs/ROADMAP-PLATFORM-ARCHIVE.yaml": yaml.safe_dump(
                {"archive_of": "docs/ROADMAP-PLATFORM.yaml", "tier_items": [record]}
            ),
        }
        head = {**record, "exit_criteria": [_criterion("c1", text=head_text)]}
        doc = _build(tier_items=[head, _item("T9.1")])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", side_effect=texts.get) as reader,
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)
        assert [c.args[0] for c in reader.call_args_list] == [
            "docs/ROADMAP-PLATFORM.yaml",
            "docs/ROADMAP-PLATFORM-ARCHIVE.yaml",
        ]
        return violations

    def test_an_unchanged_archived_criterion_reports_nothing(self, tmp_path: Path) -> None:
        assert self._violations(tmp_path, self._TEXT) == []

    def test_an_archived_criterion_whose_text_changed_is_still_evaluated(self, tmp_path: Path) -> None:
        violations = self._violations(tmp_path, "This work is now blocked on T9.1 before it can land.")

        assert len(violations) == 1
        assert "T9.x" in violations[0] and "T9.1" in violations[0]


class TestInsertionRobustness:
    def test_head_insertion_reports_exactly_one_added_criterion(self, tmp_path: Path) -> None:
        """Inserting a criterion at the head of a bare-string list must report exactly one added
        criterion -- not one per downstream sibling whose synthetic index id merely shifted."""
        base_roadmap = _doc_dict(tier_items=[{**_item("T9.x"), "exit_criteria": ["first", "second", "third"]}])
        doc = _build(
            tier_items=[
                {**_item("T9.x"), "exit_criteria": ["blocked on T9.1 before anything else", "first", "second", "third"]},
                _item("T9.1"),
            ]
        )
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert len(violations) == 1


class TestRemedyMessages:
    def test_leg_c_message_names_the_resequence_remedy(self, tmp_path: Path) -> None:
        base_roadmap = _doc_dict(tier_items=[_item("T9.x")])
        doc = _build(tier_items=[_item("T9.x", exit_criteria=[_criterion("c1", text="blocked on T9.1")]), _item("T9.1")])
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=yaml.safe_dump(base_roadmap)),
        ):
            _write_yaml(tmp_path / "docs" / "contracts" / "roadmap-liveness.yaml", _VALID_CONTRACT)
            violations = _leg_c_prose_blocking_edges(doc)

        assert len(violations) == 1
        assert "blocked_by" in violations[0]
        assert "ref: T9.1" in violations[0]

    def test_leg_a_d_message_names_baseline_removal(self, tmp_path: Path, capsys) -> None:
        roadmap = _doc_dict()
        _write_root_fixtures(tmp_path, roadmap, ["T4.9"])

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._marker_guard.default_base_reader", return_value=None),
        ):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        out = capsys.readouterr().out
        assert "remove the drained id" in out
        assert "config/roadmap_liveness_baseline.yaml" in out


class TestAccounting:
    def test_every_reachable_exit_path_declares_examined_or_skipped(self) -> None:
        from scripts.checks.hygiene._declaring_coverage import is_fully_declared, measure_check

        row = measure_check("validate_roadmap_liveness", validate_roadmap_liveness)
        assert is_fully_declared(row), (
            f"validate_roadmap_liveness: {row.success_exits} success exit(s), {row.declared} declared, "
            f"{row.undeclared} undeclared, undecidable_reason={row.undecidable_reason!r} -- every "
            "reachable exit path must reach an examined()/skipped() call."
        )

    def test_check_is_registered_and_resolves_through_the_registry(self) -> None:
        assert registry.resolve("validate_roadmap_liveness") is validate_roadmap_liveness

    def test_check_is_not_in_the_check_accounting_baseline(self) -> None:
        baseline_text = (_common.ROOT / "config" / "check_accounting_baseline.yaml").read_text(encoding="utf-8")
        assert "validate_roadmap_liveness" not in _load_baseline_entries(baseline_text)


class TestTranscription:
    def test_cd41_ratified_edge_adds_no_toxic_scc(self) -> None:
        from scripts.platform_roadmap_liveness import toxic_sccs
        from scripts.platform_roadmap_state import load

        doc = load(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")
        for scc in toxic_sccs(doc):
            assert "T2.53" not in scc
            assert "T2.51" not in scc
            assert "CD.41" not in scc


class TestDrainedCores:
    def test_t2_t3_core_absent_from_live_toxic_set(self) -> None:
        from scripts.platform_roadmap_liveness import toxic_sccs
        from scripts.platform_roadmap_state import load

        drained_ids = {"CD.40", "T2.18", "T2.19", "T2.26", "T2.36", "T3.2", "T3.20"}
        doc = load(_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml")
        for scc in toxic_sccs(doc):
            assert not (drained_ids & scc), f"drained id(s) resurfaced in a toxic SCC: {drained_ids & scc}"


class TestUnloadableRoadmapSkips:
    def test_skips_when_roadmap_is_unloadable(self, tmp_path: Path, capsys) -> None:
        _write_yaml(tmp_path / "docs" / "ROADMAP-PLATFORM.yaml", "not: [valid, yaml")
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_roadmap_liveness(failed)

        assert failed == []
        assert "SKIP" in capsys.readouterr().out


class TestLoadHelpers:
    def test_load_blocking_phrases_returns_empty_when_contract_missing(self, tmp_path: Path) -> None:
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert _load_blocking_phrases() == []

    def test_load_baseline_entries_parses_entries_key(self) -> None:
        assert _load_baseline_entries("entries:\n  - a\n  - b\n") == ["a", "b"]

    def test_load_baseline_entries_handles_empty_text(self) -> None:
        assert _load_baseline_entries("") == []
