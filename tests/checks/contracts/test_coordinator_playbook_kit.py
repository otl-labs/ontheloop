"""Content test for the adoption-kit rules and templates in docs/contracts/coordinator.yaml."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).parents[3]
_DOC = yaml.safe_load((_ROOT / "docs" / "contracts" / "coordinator.yaml").read_text(encoding="utf-8"))


def _flat(text: str) -> str:
    return " ".join(text.split())


def _dotted(doc: Any, path: str) -> bool:
    for part in path.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return False
        doc = doc[part]
    return True


def test_playbook_kit_rules_and_templates() -> None:
    templates = _DOC["templates"]
    for key in ("goal", "standing_instructions", "memory_seed", "day_one"):
        assert key in templates, key
    standing = _flat(templates["standing_instructions"])
    assert "<pointer_line>" in standing
    assert "the only dispatch is a worker that writes it" in standing
    day_one = _flat(templates["day_one"])
    for needle in (
        "coordinator_operations.setup",
        "surfaces.delivery",
        "liveness.watcher",
        "give the coordinator steps 3-4 in its first message",
    ):
        assert needle in day_one, needle
    for path in ("coordinator_operations.setup", "surfaces.delivery", "liveness.watcher", "routing.plan_defect"):
        assert _dotted(_DOC, path), path
    split_rule = _flat(_DOC["lifecycle"]["split_rule"])
    for needle in ("new planning worker", "a red on main follows halts", "routing.plan_defect"):
        assert needle in split_rule, needle
    row = next(r for r in _DOC["routing"]["table"] if r["situation"] == "CI red on a worker's own PR")
    assert "lifecycle.split_rule" in row["route"]
    proof = [c for c in _DOC["planning_conventions"] if c.startswith("Prove a plan with deterministic checks")]
    assert len(proof) == 1
    assert "leaves its remit" in _flat(_DOC["coordinator_operations"]["operator_side_work"])
    memory = [_flat(item) for item in _DOC["memory"]]
    tags = [m for m in memory if all(t in m for t in ("[contract]", "[harness]", "[local]", "[repo:<path>]"))]
    assert len(tags) == 1
    assert "at close_out" in tags[0] and "next compaction" in tags[0]
    batch = [m for m in memory if m.startswith("Generic rulings land in batches")]
    assert len(batch) == 1
    for needle in (
        "the coordinator finds on any wake",
        "one amendment per owning file",
        "decision_prompt",
        "on yes",
        "naming no open rec",
        "on no",
    ):
        assert needle in batch[0], needle
    beat = [_flat(b) for b in _DOC["liveness"]["watcher"]["beat"] if "[contract] or [repo:<path>]" in b]
    assert len(beat) == 1 and "naming no open rec" in beat[0]
    entry = next(e for e in _DOC["amendment_log"] if "PLAN-coordinator-playbook-kit" in _flat(e["summary"]))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(entry["date"]))
    assert entry["semantic_break"] is True
