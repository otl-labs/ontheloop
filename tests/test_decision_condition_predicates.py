"""Tests for scripts.preflight.decision_conditions predicates (Decisions 194 and 212).

A new file, not an addition to tests/test_decision_conditions.py (488 SLOC against the 500 limit
-- Decision 128 decompose-by-default, no budget raise). Covers the predicate's own semantics on a
SYNTHETIC tmp roadmap fixture: fail-loud unknown-id ordering, the 'rehomed' non-satisfaction case,
the legacy bare-string exit-criterion coercion, and one assertion-light live-state case.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.preflight import decision_conditions as dc

_LIVE_ROADMAP = Path(__file__).resolve().parent.parent / "docs" / "ROADMAP-PLATFORM.yaml"

_BASE_DOC: dict = {
    "document": {
        "id": "ROADMAP-TEST",
        "version": 1,
        "status": "draft",
        "filed_via": "pending_log_decision_lambda",
    },
    "tier_items": [],
}


def _write_roadmap(tmp_path: Path, tier_items: list[dict]) -> Path:
    doc = dict(_BASE_DOC)
    doc["tier_items"] = tier_items
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _item(item_id: str, status: str, exit_criteria: list) -> dict:
    return {
        "id": item_id,
        "tier": "TX",
        "name": f"Synthetic item {item_id}",
        "status": status,
        "exit_criteria": exit_criteria,
    }


class TestRegistration:
    def test_predicate_is_registered(self) -> None:
        assert dc._PREDICATE_REGISTRY["roadmap_items_complete"] is dc.roadmap_items_complete


class TestFailLoudOrdering:
    def test_unknown_id_raises_even_when_an_earlier_item_is_incomplete(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(
            tmp_path,
            [_item("TX.incomplete", "not_started", [])],
        )
        with pytest.raises(KeyError, match="TX.missing"):
            dc.roadmap_items_complete(items=["TX.incomplete", "TX.missing"], roadmap_path=roadmap)

    def test_all_missing_ids_are_named_together(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(tmp_path, [])
        with pytest.raises(KeyError, match=r"TX\.a.*TX\.b"):
            dc.roadmap_items_complete(items=["TX.a", "TX.b"], roadmap_path=roadmap)


class TestCompletenessSemantics:
    def test_true_only_when_every_named_item_is_complete_with_every_criterion_met(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(
            tmp_path,
            [
                _item("TX.complete", "complete", [{"id": "c1", "text": "done", "status": "met", "met_by": "PR#1"}]),
                _item(
                    "TX.partial",
                    "complete",
                    [
                        {"id": "c1", "text": "done", "status": "met", "met_by": "PR#1"},
                        {"id": "c2", "text": "not done", "status": "open"},
                    ],
                ),
            ],
        )
        assert dc.roadmap_items_complete(items=["TX.complete"], roadmap_path=roadmap) is True
        assert dc.roadmap_items_complete(items=["TX.partial"], roadmap_path=roadmap) is False
        assert dc.roadmap_items_complete(items=["TX.complete", "TX.partial"], roadmap_path=roadmap) is False

    def test_status_not_complete_fails_even_with_all_criteria_met(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(
            tmp_path,
            [_item("TX.inprogress", "in_progress", [{"id": "c1", "text": "done", "status": "met", "met_by": "PR#1"}])],
        )
        assert dc.roadmap_items_complete(items=["TX.inprogress"], roadmap_path=roadmap) is False

    def test_item_with_no_exit_criteria_is_vacuously_met(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(tmp_path, [_item("TX.nocriteria", "complete", [])])
        assert dc.roadmap_items_complete(items=["TX.nocriteria"], roadmap_path=roadmap) is True


class TestRehomedDoesNotSatisfy:
    def test_rehomed_criterion_returns_false_not_raises(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(
            tmp_path,
            [
                _item(
                    "TX.rehomed",
                    "complete",
                    [{"id": "c1", "text": "moved elsewhere", "status": "rehomed", "met_by": "TX.other"}],
                ),
                _item("TX.other", "not_started", []),
            ],
        )
        assert dc.roadmap_items_complete(items=["TX.rehomed"], roadmap_path=roadmap) is False


class TestLegacyBareStringCriterion:
    def test_bare_string_exit_criterion_coerces_to_open_and_is_not_met(self, tmp_path: Path) -> None:
        roadmap = _write_roadmap(tmp_path, [_item("TX.legacy", "complete", ["a legacy bare-string criterion"])])
        assert dc.roadmap_items_complete(items=["TX.legacy"], roadmap_path=roadmap) is False


class TestLiveState:
    def test_free_tier_items_are_not_complete_today(self) -> None:
        """Assertion-light by design: its inversion coincides with condition (a) firing, which
        drafted Decision 194 clause 2 pre-licenses. Never assert the reverse."""
        assert dc.roadmap_items_complete(items=["T4.22", "T4.23", "T4.24"], roadmap_path=_LIVE_ROADMAP) is False


_LIVE_PORTS = Path(__file__).resolve().parent.parent / "docs" / "contracts" / "ports.yaml"


def _write_ports(tmp_path: Path, statuses: list[str]) -> Path:
    rows = [{"id": f"PC-{i + 1:02d}", "status": s} for i, s in enumerate(statuses)]
    path = tmp_path / "ports.yaml"
    path.write_text(yaml.safe_dump({"coupling_findings": rows}), encoding="utf-8")
    return path


class TestPortsOpenCouplingFindings:
    def test_predicate_is_registered(self) -> None:
        assert dc._PREDICATE_REGISTRY["ports_open_coupling_findings_exceed"] is dc.ports_open_coupling_findings_exceed

    def test_fires_only_above_threshold(self, tmp_path: Path) -> None:
        path = _write_ports(tmp_path, ["open", "open", "accepted"])
        assert dc.ports_open_coupling_findings_exceed(1, contract_path=path) is True
        assert dc.ports_open_coupling_findings_exceed(2, contract_path=path) is False

    def test_missing_contract_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            dc.ports_open_coupling_findings_exceed(10, contract_path=tmp_path / "absent.yaml")

    def test_live_contract_evaluates_to_bool(self) -> None:
        assert isinstance(dc.ports_open_coupling_findings_exceed(10, contract_path=_LIVE_PORTS), bool)
