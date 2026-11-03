"""Pydantic schema for docs/ROADMAP-PLATFORM.yaml, loader, and dependency-graph helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.platform_roadmap_gate_rules import GateRuleEvaluator, GateRuleParser
from scripts.platform_roadmap_models import (
    _GATE_HELPERS,
    CandidateDecision,
    CriterionBlocker,
    CrossTierGate,
    DocumentMeta,
    ExitCriterion,
    FoundationItem,
    GateHelper,
    KnownGap,
    NorthStar,
    OpenQuestion,
    RoadmapDocument,
    TierItem,
)
from scripts.platform_roadmap_state import PlatformRoadmapState, compute_followon_state, compute_state_dict, load

__all__ = [
    "CandidateDecision",
    "CriterionBlocker",
    "CrossTierGate",
    "DocumentMeta",
    "ExitCriterion",
    "FoundationItem",
    "GateHelper",
    "GateRuleEvaluator",
    "GateRuleParser",
    "KnownGap",
    "NorthStar",
    "OpenQuestion",
    "PlatformRoadmapState",
    "RoadmapDocument",
    "TierItem",
    "_GATE_HELPERS",
    "compute_followon_state",
    "compute_state_dict",
    "load",
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Platform roadmap validator")
    parser.add_argument("path", nargs="?", default="docs/ROADMAP-PLATFORM.yaml", help="Path to ROADMAP-PLATFORM.yaml")
    args = parser.parse_args()
    try:
        load(Path(args.path))
        print(f"PASS: {args.path} validates against RoadmapDocument schema.")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        sys.exit(1)
