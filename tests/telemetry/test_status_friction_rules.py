"""rec-4605 acceptance and rule-shape guards (PLAN-friction-rules-v2) over the oracle and FRICTION_RULES (no duckdb)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from scripts.telemetry_verify import derive
from src.common import ducklake_event_read_sql as sql
from src.common.ducklake_event_read_verbs import FRICTION_RULES

_STAMP = "2026-09-25T10:00:00+00:00"
_RULES_DIGESTS = {2: "1b7014d3626527369a6e48ac54ff413b3080585ced3e8d3ea0aa664b1b415b93"}  # pragma: allowlist secret


def _event(obs_id: str, name: str, severity: str, t: int) -> dict[str, Any]:
    return {
        "producer": "claude_code",
        "parser_version": 1,
        "created_timestamp": _STAMP,
        "session_id": "s",
        "session_started_at": _STAMP,
        "event_id": f"ev-{obs_id}",
        "event_kind": "point",
        "event_timestamp": datetime(2026, 9, 25, 10, 0, t, tzinfo=timezone.utc),
        "source_ordinal": t,
        "observation_id": obs_id,
        "observation_type": "process_event",
        "name": name,
        "severity": severity,
        "outcome": None,
        "parent_observation_id": "A",
    }


def _facts(row: dict[str, Any]) -> list[tuple[str, str, str]]:
    return derive.friction_facts([row])


def test_status_skip_is_not_hook_fault() -> None:
    (fact,) = _facts(_event("s1", "status:ops_portal.file_rec.skipped", "warning", 1))
    assert fact[1:] == ("reported_skip", "notice")
    (row,) = derive.session_friction_rollup({"telemetry_observations": [_event("s1", "status:x.skipped", "warning", 1)]}, "s")
    assert row["exception_total"] == 0
    assert row["label_counts"] == [{"label": "reported_skip", "n": 1}]


def test_status_failure_has_its_own_label() -> None:
    (fact,) = _facts(_event("s1", "status:ops_portal.file_rec.failed", "error", 1))
    assert fact[1:] == ("reported_failure", "exception")


def test_rule_precedence_exact_prefix_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    rules = (
        ("a1", "process_event", None, None, "error", "generic_label", "exception"),
        ("b1", "process_event", None, "status:", "error", "prefix_label", "exception"),
        ("c1", "process_event", "status:x.failed", None, "error", "exact_label", "exception"),
    )
    monkeypatch.setattr(derive, "FRICTION_RULES", dict(FRICTION_RULES, rules=rules))
    assert _facts(_event("e1", "status:x.failed", "error", 1))[0][1] == "exact_label"
    assert _facts(_event("e2", "status:y.failed", "error", 1))[0][1] == "prefix_label"
    assert _facts(_event("e3", "other", "error", 1))[0][1] == "generic_label"
    assert "starts_with" in sql.FRICTION_CTES
    assert " LIKE " not in sql.FRICTION_CTES


def test_rules_set_at_most_one_signature_form() -> None:
    for rule in FRICTION_RULES["rules"]:
        assert len(rule) == 7, rule
        assert rule[2] is None or rule[3] is None, rule


def test_rules_digest_matches_classifier_version() -> None:
    digest = hashlib.sha256(json.dumps(FRICTION_RULES["rules"], sort_keys=True).encode()).hexdigest()
    assert _RULES_DIGESTS[FRICTION_RULES["classifier_version"]] == digest
