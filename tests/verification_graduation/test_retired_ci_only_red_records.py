"""Pin the Decision 244 cl.5 retirement of the two records that were red only on the CI runner."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.verification_graduation import REGISTRY_ENTRIES_REL, load_entries

ROOT = Path(__file__).resolve().parents[2]
RETIRED_IDS = ("data-pipeline-excludes-control-module", "push-context-session-branch-guard")


@pytest.mark.parametrize("check_id", RETIRED_IDS)
def test_retired_id_is_not_a_live_record(check_id: str) -> None:
    assert check_id not in {e["check_id"] for e in load_entries(ROOT)}


@pytest.mark.parametrize("check_id", RETIRED_IDS)
def test_retired_record_sits_in_deprecated_under_its_own_id(check_id: str) -> None:
    shard = ROOT / REGISTRY_ENTRIES_REL / "deprecated" / f"{check_id}.yaml"
    assert yaml.safe_load(shard.read_text(encoding="utf-8"))["check_id"] == check_id
