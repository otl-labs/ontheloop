"""Regression pin: the two CI-only red graduated records stay retired (rec-4888, Decision 244 cl.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import verification_graduation as vg

RETIRED_IDS = ("data-pipeline-excludes-control-module", "push-context-session-branch-guard")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("check_id", RETIRED_IDS)
def test_retired_id_is_not_a_live_record(check_id: str) -> None:
    assert check_id not in {row.get("check_id") for row in vg.load_entries(ROOT)}


@pytest.mark.parametrize("check_id", RETIRED_IDS)
def test_retired_record_sits_in_deprecated_under_its_own_id(check_id: str) -> None:
    path = ROOT / vg.REGISTRY_ENTRIES_REL / "deprecated" / f"{check_id}.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["check_id"] == check_id
