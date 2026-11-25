"""Pin the Decision 244 cl.5 retirement of the graduated record d37-governance-surfaces-landed."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.verification_graduation import REGISTRY_ENTRIES_REL, load_entries

ROOT = Path(__file__).resolve().parents[2]
RETIRED_ID = "d37-governance-surfaces-landed"


def test_retired_id_is_not_a_live_record() -> None:
    assert RETIRED_ID not in {e["check_id"] for e in load_entries(ROOT)}


def test_retired_record_sits_in_deprecated_under_its_own_id() -> None:
    shard = ROOT / REGISTRY_ENTRIES_REL / "deprecated" / f"{RETIRED_ID}.yaml"
    assert yaml.safe_load(shard.read_text(encoding="utf-8"))["check_id"] == RETIRED_ID
