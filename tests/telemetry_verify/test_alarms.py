"""Mirror tests for scripts/telemetry_verify/alarms.py over a fake portal and injected rec rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.telemetry_verify import ALARM_MIN_INTERVAL_S, ALARM_SOURCE, raise_alarms
from scripts.telemetry_verify.alarms import _CONTEXT_NOTE

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


class Portal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, action: str, fields: dict[str, Any]) -> str:
        self.calls.append((action, fields))
        return "rec-9000"


def _rec(rec_id: str, context: str) -> dict[str, Any]:
    return {"id": rec_id, "source": ALARM_SOURCE, "status": "open", "context": context}


def _raise(findings: dict[str, int], portal: Portal, **over: Any) -> Any:
    args: dict[str, Any] = {
        "scope": "abc12345",
        "alarm_at": None,
        "now": NOW,
        "portal": portal,
        "file": "scripts/ducklake_smoke/lambda_telemetry_golden_gates.py",
        "acceptance_for": lambda cls: f"grep -q {cls} scripts/telemetry_verify/alarms.py",
        "rows": [],
        "parser_version": 3,
    }
    return raise_alarms(findings, **{**args, **over})


def test_one_episode_per_alarm_class() -> None:
    portal = Portal()
    outcome = _raise({"golden_replay_error": 1, "golden_replay_error_extra": 4, "quiet": 0}, portal)
    assert (outcome.filed, outcome.updated, outcome.unchanged) == (2, 0, 0)
    assert [c[0] for c in portal.calls] == ["file", "file"]
    assert sorted(c[1]["title"] for c in portal.calls) == [
        "telemetry verification alarm: golden_replay_error",
        "telemetry verification alarm: golden_replay_error_extra",
    ]
    fields = portal.calls[0][1]
    assert fields["source"] == ALARM_SOURCE and fields["priority"] == "High" and fields["status"] == "open"

    portal = Portal()
    rows = [_rec("rec-1", "finding_class=golden_replay_error_extra count=4 scope=old parser_version=3")]
    outcome = _raise({"golden_replay_error": 1, "golden_replay_error_extra": 5}, portal, rows=rows)
    assert outcome.actions == {"golden_replay_error": "file", "golden_replay_error_extra": "update"}
    update = next(c for c in portal.calls if c[0] == "update")
    assert update[1]["id"] == "rec-1" and "count=5" in update[1]["context"]
    assert all(call[0] != "close" for call in portal.calls)


def test_an_unchanged_context_writes_nothing() -> None:
    portal = Portal()
    rows = [_rec("rec-1", f"finding_class=a count=2 scope=s1 parser_version=3 {_CONTEXT_NOTE}")]
    outcome = _raise({"a": 2}, portal, scope="s1", rows=rows)
    assert outcome.unchanged == 1 and portal.calls == []


def test_declared_classes_are_counted_never_filed() -> None:
    portal = Portal()
    outcome = _raise({"owned": 3, "real": 1}, portal, declared=("owned",))
    assert (outcome.declared, outcome.filed) == (1, 1) and [c[1]["title"][-4:] for c in portal.calls] == ["real"]


def test_a_class_is_rate_limited_by_the_callers_map() -> None:
    portal, seen = Portal(), {}
    assert _raise({"a": 1}, portal, alarm_at=seen).filed == 1 and seen == {"a": NOW}
    again = _raise({"a": 1, "b": 1}, portal, alarm_at=seen, now=NOW + timedelta(seconds=ALARM_MIN_INTERVAL_S - 1))
    assert (again.rate_limited, again.filed) == (1, 1)
    later = _raise({"a": 1}, portal, alarm_at=seen, now=NOW + timedelta(seconds=ALARM_MIN_INTERVAL_S))
    assert later.rate_limited == 0 and later.filed == 1
    assert _raise({"a": 1}, portal, alarm_at=None).rate_limited == 0


def test_rec_text_carries_counts_classes_and_scope_only() -> None:
    portal = Portal()
    _raise({"golden_replay_mismatch:sessions_window": 2}, portal)
    fields = portal.calls[0][1]
    assert fields["context"] == (
        "finding_class=golden_replay_mismatch:sessions_window count=2 scope=abc12345 parser_version=3 " + _CONTEXT_NOTE
    )
    assert set(fields) == {
        "title",
        "file",
        "status",
        "source",
        "priority",
        "effort",
        "risk",
        "verification_tier",
        "context",
        "acceptance",
    }


def test_telemetry_verify_source_is_registered_and_projected() -> None:
    from scripts.executor.rec_write_guidance import validate_source  # noqa: PLC0415

    validate_source(ALARM_SOURCE)
    with pytest.raises(Exception):  # noqa: B017, PT011 -- any refusal of an unregistered neighbour
        validate_source(ALARM_SOURCE + "_x")
    projection = yaml.safe_load((ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml").read_text("utf-8"))
    for table, column in (("ops_recommendations", "source"), ("telemetry_agents", "agent_type")):
        assert ALARM_SOURCE in projection["ops_tables"][table]["columns"][column]["accepted_values"]


def _callers() -> list[tuple[str, Any, int | None]]:
    from scripts.ducklake_smoke import lambda_telemetry_golden_gates as golden  # noqa: PLC0415
    from scripts.telemetry_capture import verify as capture  # noqa: PLC0415
    from scripts.telemetry_verify import __main__ as cli  # noqa: PLC0415

    return [
        (cli.MONITOR_FILE, cli.acceptance_for, None),
        (cli.COMPLETENESS_FILE, cli.acceptance_for, None),
        (capture.FILE, capture.acceptance_for, capture.PARSER_VERSION),
        ("scripts/ducklake_smoke/lambda_telemetry_golden_gates.py", lambda _cls: golden.ACCEPTANCE, None),
    ]


@pytest.mark.parametrize("caller", range(4), ids=["monitor", "completeness", "capture", "golden"])
@pytest.mark.parametrize(
    ("cls", "scope"),
    [("a", "s"), ("completeness_listed_not_captured", "2026-10-18"), ("golden_replay_error", "abc12345")],
)
def test_every_alarm_record_passes_the_portal_row_gate(caller: int, cls: str, scope: str) -> None:
    """build_fields' record clears file_rec's client-side gates (rec-4515: an under-80-character context was refused)."""
    from scripts.executor.jsonl_store import Recommendation  # noqa: PLC0415
    from scripts.executor.rec_write_guidance import validate_source  # noqa: PLC0415
    from scripts.ops_portal.risk_scoring import _derive_computed_fields  # noqa: PLC0415
    from scripts.ops_portal.write_validators import gate_write  # noqa: PLC0415

    file, acceptance_for, parser_version = _callers()[caller]
    portal = Portal()
    _raise({cls: 1}, portal, scope=scope, file=file, acceptance_for=acceptance_for, parser_version=parser_version)
    fields = dict(portal.calls[0][1])
    _derive_computed_fields(fields)
    validate_source(fields["source"])
    gate_write("ops_recommendations", fields, insert=True)
    Recommendation.model_validate({**fields, "id": "rec-0", "date": "2026-10-26"})


def test_an_old_format_rec_is_still_found_and_rewritten_once() -> None:
    portal = Portal()
    rows = [_rec("rec-1", "finding_class=a count=2 scope=s1 parser_version=3")]
    outcome = _raise({"a": 2}, portal, scope="s1", rows=rows)
    assert outcome.actions == {"a": "update"} and portal.calls[0][1]["context"].endswith(_CONTEXT_NOTE)
    rows = [_rec("rec-1", portal.calls[0][1]["context"])]
    assert _raise({"a": 2}, Portal(), scope="s1", rows=rows).unchanged == 1


def test_the_fixed_note_alone_clears_the_context_floor() -> None:
    projection = yaml.safe_load((ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml").read_text("utf-8"))
    floor = projection["ops_tables"]["ops_recommendations"]["columns"]["context"]["min_length"]
    assert len(_CONTEXT_NOTE.strip()) >= floor
