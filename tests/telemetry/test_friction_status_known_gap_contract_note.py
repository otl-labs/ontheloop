"""telemetry_observations retires the status known gap that friction classifier_version 2 closed (rec-5193)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.common.ducklake_event_read_verbs import FRICTION_RULES

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "contracts" / "telemetry_observations.yaml"
PLAN = "PLAN-friction-status-known-gap"
RETIRED = (
    "friction rules gain a status row",
    "r91 labels a status warning hook_fault",
)


def flat(value: Any) -> str:
    return " ".join(str(value).split())


def rule(rule_id: str) -> tuple[Any, ...]:
    return next(r for r in FRICTION_RULES["rules"] if r[0] == rule_id)


def test_observations_retires_the_status_known_gap() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    name = contract["fields"]["name"]
    semantics = flat(name["semantics"])
    for phrase in RETIRED:
        assert phrase not in semantics
    match = re.search(
        r"From classifier_version (\d+) \(rec-4605\) the reader's friction rules label a status warning (\w+) "
        r"\((\w+) class, outside exception_total\) and a status error (\w+) \((\w+) class\)",
        semantics,
    )
    assert match, "name semantics must carry the classifier_version sentence"
    version, warn_label, warn_class, err_label, err_class = match.groups()
    r10, r11 = rule("r10"), rule("r11")
    assert int(version) == 2
    assert FRICTION_RULES["classifier_version"] >= 2
    assert (warn_label, warn_class) == (r10[5], r10[6])
    assert (err_label, err_class) == (r11[5], r11[6])
    logged = [e for e in name["amendment_log"] if PLAN in str(e.get("summary")) and "rec-5193" in str(e.get("summary"))]
    assert logged
    assert "PLAN-reader-verbs-decision-notes (rec-4324)" not in CONTRACT.read_text(encoding="utf-8")
