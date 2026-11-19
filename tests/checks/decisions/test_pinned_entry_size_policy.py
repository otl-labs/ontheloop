"""rec-4744's acceptance file: the per-Decision pin fixture and the contract's two cap keys (Decision 276 clause 4)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks.decisions import entry_measure
from tests.fixtures import decision_entry

REPO = Path(__file__).resolve().parents[3]


class TestPinnedEntryPolicy:
    def _governance(self) -> dict:
        return yaml.safe_load((REPO / "docs/contracts/decision-entry.yaml").read_text(encoding="utf-8"))["size_governance"]

    def test_the_fixtures_authored_is_the_shared_classifier(self) -> None:
        assert decision_entry.authored is entry_measure.authored
        assert decision_entry.authored_bytes is entry_measure.authored_bytes

    def test_cap_bytes_is_the_norm_earlier_entries_landed_under(self) -> None:
        assert decision_entry.cap_bytes() == self._governance()["per_entry_size_norm"]["cap_bytes"]

    def test_the_measured_norm_is_a_distinct_key_the_pins_never_read(self) -> None:
        governance = self._governance()
        assert "measured_size_norm" in governance and "measured_size_norm" not in governance["per_entry_size_norm"]
        assert decision_entry.cap_bytes() != governance["measured_size_norm"]["cap_bytes"]
