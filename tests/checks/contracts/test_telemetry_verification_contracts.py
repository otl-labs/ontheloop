"""The contract surfaces PLAN-telemetry-verification-monitor changes name the orphan monitor and its residual owner."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
COVERAGE_REC = "rec-4439"
ORIGINAL_C3 = (
    "Orphan/coverage DQ check registered in config/agent/data_quality/ops.yaml (alarm-not-gate) "
    "for the turn-observation <-> transcript join"
)
RESIDUALS = (
    "(1) trees whose root started more than 28 days before their quiet day",
    "(2) a quiet day whose scheduled run is dropped, until a dispatch backfills it",
    (
        "(3) a backfill's positional next_skip, where a tree entering or leaving the quiet set between a run and its "
        "backfill can shift trees out of both"
    ),
    "(4) child sessions past the monitor's per-tree cap",
    "(5) collided trees (roots_in_tree above 1)",
    "(6) a parent observation in another session of the same tree, which the tree-scoped verb does not flag",
    "(7) which node a process_event parents to, since the verb checks only that a parent exists",
    (
        "(8) sessions the in-session source check never compares: no resume and fewer than 10 sent passes, past its three "
        "checks a day, above its page cap, producer_modified, or never settling"
    ),
)


def norm(text: Any) -> str:
    return " ".join(str(text).split())


def load(rel: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_t3_20_c3_names_the_verb_based_orphan_monitor() -> None:
    roadmap = load("docs/ROADMAP-PLATFORM.yaml")
    item = next(i for i in roadmap["tier_items"] if i["id"] == "T3.20")
    c3 = norm(next(c for c in item["exit_criteria"] if c["id"] == "c3")["text"])
    assert c3.startswith("[Re-grounded 2026-10-18, PLAN-telemetry-verification-monitor:")
    assert c3.endswith(ORIGINAL_C3)
    assert "telemetry_orphan_counts" in c3 and "telemetry-verification workflow" in c3 and "alarm-not-gate" in c3
    assert f"owned by {COVERAGE_REC}:" in c3
    for residual in RESIDUALS:
        assert residual in c3
    assert len(re.findall(r"\(\d\)", c3)) == len(RESIDUALS)


def test_decision_210_coverage_names_the_discharged_residual() -> None:
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    start = text.index("## Decision 210:")
    section = norm(text[start : text.index("\n## Decision ", start + 10)])
    assert "Update (2026-10-18)" in section
    update = section[section.index("Update (2026-10-18)") :]
    assert "rec-4101 residual is enforced" in update and "telemetry_orphan_counts" in update
    assert "telemetry-verification workflow" in update and COVERAGE_REC in update and "No clause changes" in update
    assert section.index("Amendment 2026-10-15") < section.index("Update (2026-10-18)")


def test_rec_4101_owner_notes_name_the_verb_based_monitor() -> None:
    field = load("docs/contracts/parent-observation-id.yaml")["fields"]["parent_observation_id"]
    validation, governance = norm(field["write_time_validation"]), norm(field["governance_notes"])
    for text in (validation, governance):
        assert "telemetry_orphan_counts" in text and "telemetry-verification workflow" in text
        assert "rec-4101" in text and "quiescent" in text.lower() and "IDLE_THRESHOLD_SECONDS" in text
        assert f"residual owned by {COVERAGE_REC}" in text
    assert "That monitor does not exist yet" not in governance and "owned by follow-on rec-4101" not in validation
    assert any(e["date"] == "2026-10-18" and e["semantic_break"] is False for e in field["amendment_log"])

    local = norm(
        load("docs/contracts/telemetry_observations.yaml")["fields"]["parent_observation_id"]["governance_notes_local"]
    )
    assert "its DQ intent belongs to rec-4101 (Decision 210 cl.2)" in local
    assert "telemetry_orphan_counts" in local and "telemetry-verification workflow" in local
    assert f"residual owned by {COVERAGE_REC}" in local
