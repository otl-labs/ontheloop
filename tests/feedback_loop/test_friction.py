"""The friction detector core over synthetic friction_signature_counts rows."""

from __future__ import annotations

from argparse import Namespace
from datetime import date
from typing import Any

import pytest

from scripts import telemetry_usage as tu
from scripts.feedback_loop import friction
from scripts.feedback_loop.friction import COVERAGE, RECURRING
from scripts.feedback_loop.friction import DETECTOR_PARAMS as P
from src.common.ducklake_reader_client import ResponseTooLargeError
from tests.fixtures import feedback_loop_rows as fx

FIRST, LAST = date(2026, 11, 14), date(2026, 11, 20)


def detect(rows: list[dict[str, Any]]) -> friction.Result:
    return friction.detect(rows, FIRST, LAST)


def ids(result: friction.Result, detector: str) -> list[str]:
    return [f.subject_key for f in result.findings if f.detector_id == detector]


def test_recurring_fires_exactly_at_the_seeds() -> None:
    assert ids(detect(fx.recurring(P["min_sessions"], P["min_facts"])), RECURRING) == ["r01|hook:never_on_main"]
    assert ids(detect(fx.recurring(P["min_sessions"] - 1, P["min_facts"])), RECURRING) == []
    assert ids(detect(fx.recurring(P["min_sessions"], P["min_facts"] - 1)), RECURRING) == []


def test_generic_notice_prefix_rule_and_other_version_groups_never_fire() -> None:
    generic = [fx.generic_row(i, 10) for i in range(1, 6)]
    notice = fx.recurring(
        5, 20, signature=None, disposition="status:", rule_id="r10", label="reported_skip", **{"class": "notice"}
    )
    prefix = fx.recurring(5, 20, signature=None, disposition="status:", rule_id="r11", label="reported_failure")
    other = fx.recurring(5, 20, classifier_version=fx.VERSION + 1)
    result = detect(generic + notice + prefix + other)
    assert ids(result, RECURRING) == []
    assert result.other_version_rows == len(other)
    assert any(g["rule_id"] == "r11" and g["fact_count"] == 20 for g in result.groups)


def test_repeat_runs_fire_with_an_opaque_subject_key() -> None:
    (key,) = ids(detect(fx.repeat_run(3, 6)), RECURRING)
    assert key.startswith("run|sha256:") and len(key.rsplit(":", 1)[1]) == 12 and fx.PLANTED_TOOL not in key
    assert not friction.is_published(fx.PLANTED_TOOL)


def test_coverage_fires_above_the_bar_and_is_quiet_at_or_below_it() -> None:
    named = [fx.row(1, fact_count=80)]
    above = detect(named + [fx.generic_row(2, 21)])
    assert above.coverage == "finding" and ids(above, COVERAGE) == ["coverage"]
    at_bar = detect(named + [fx.generic_row(2, 20)])
    assert at_bar.coverage == "ok" and ids(at_bar, COVERAGE) == []


def test_coverage_is_unmeasurable_below_the_fact_floor() -> None:
    result = detect([fx.generic_row(1, P["min_process_event_facts"] - 1)])
    assert result.coverage == "unmeasurable" and result.findings == []


def test_coverage_equals_telemetry_usage_friction() -> None:
    rows = [fx.row(1, fact_count=30), fx.generic_row(2, 12), fx.row(3, classifier_version=fx.VERSION + 1, fact_count=9)]
    expected = tu.friction(rows, Namespace(workflow=None))[str(fx.VERSION)]
    result = detect(rows)
    assert result.process_event_facts == expected["process_event_facts"]
    assert result.unmapped_share == expected["unmapped_failure_share"]


def test_group_sums_across_trees_and_workflows_match_telemetry_usage() -> None:
    rows = [fx.row(1, fact_count=2, session_count=1), fx.row(2, fact_count=4, session_count=2, workflow="plan")]
    (group,) = detect(rows).groups
    (sig,) = tu.friction(rows, Namespace(workflow=None))[str(fx.VERSION)]["signatures"]
    assert (group["fact_count"], group["session_count"]) == (sig["fact_count"], sig["session_count"]) == (6, 3)
    assert group["tree_count"] == 2


def test_friction_trees_counts_trees_at_the_current_version_only() -> None:
    mixed = [fx.row(1), fx.row(2), fx.row(3, classifier_version=fx.VERSION + 1)]
    assert detect(mixed).friction_trees == 2
    assert detect([fx.row(1, classifier_version=fx.VERSION + 1)]).friction_trees == 0


def test_ranking_puts_coverage_first_then_sessions_then_facts() -> None:
    rows = (
        fx.recurring(3, 9, signature="hook:merge_gate", rule_id="r07", label="merge_gate_block")
        + fx.recurring(4, 5)
        + fx.recurring(4, 9, signature="hook:protected_action_gate", rule_id="r09", label="protected_action_block")
        + [fx.generic_row(9, 100)]
    )
    keys = [f.subject_key for f in detect(rows).findings]
    assert keys == [
        "coverage",
        "r09|hook:protected_action_gate",
        "r01|hook:never_on_main",
        "r07|hook:merge_gate",
    ]


def finding(rows: list[dict[str, Any]], recs: list[dict[str, Any]]) -> friction.Finding:
    result = detect(rows)
    friction.apply_rec_states(result, recs)
    return next(f for f in result.findings if f.detector_id == RECURRING)


SUBJECT = "r01|hook:never_on_main"
ROWS = fx.recurring(3, 6)


@pytest.mark.parametrize(("status", "state"), [("open", "filed"), ("declined", "rejected"), ("superseded", "covered")])
def test_rec_states_map_status(status: str, state: str) -> None:
    got = finding(ROWS, [fx.rec("rec-10", status, RECURRING, SUBJECT)])
    assert (got.state, got.rec_id, got.proposed) == (state, "rec-10", False)


def test_no_match_is_new_and_proposed() -> None:
    got = finding(ROWS, [fx.rec("rec-10", "open", RECURRING, "r07|hook:merge_gate")])
    assert (got.state, got.rec_id, got.proposed) == ("new", None, True)


def test_newest_matching_rec_wins() -> None:
    recs = [
        fx.rec("rec-9", "declined", RECURRING, SUBJECT, created="2026-11-01"),
        fx.rec("rec-12", "open", RECURRING, SUBJECT, created="2026-11-05"),
    ]
    assert finding(ROWS, recs).rec_id == "rec-12"
    tie = [fx.rec("rec-9", "declined", RECURRING, SUBJECT), fx.rec("rec-12", "open", RECURRING, SUBJECT)]
    assert finding(ROWS, tie).rec_id == "rec-12"


@pytest.mark.parametrize("status", ["closed", "failed"])
def test_closed_or_failed_recurs_only_when_the_window_starts_after_the_last_update(status: str) -> None:
    after = fx.rec("rec-10", status, RECURRING, SUBJECT, updated="2026-11-10T08:00:00Z")
    got = finding(ROWS, [after])
    assert (got.state, got.rec_id, got.proposed) == ("recurred", "rec-10", True)
    same_day = fx.rec("rec-10", status, RECURRING, SUBJECT, updated="2026-11-14T08:00:00Z")
    assert (finding(ROWS, [same_day]).state, finding(ROWS, [same_day]).proposed) == ("closing", False)


def test_null_or_non_object_context_is_skipped_and_counted() -> None:
    bad = [{**fx.rec("rec-1", "open", RECURRING, SUBJECT), "context_v2_json": v} for v in (None, "[1]", "not json", 5)]
    result = detect(ROWS)
    friction.apply_rec_states(result, bad)
    assert result.unparsed_recs == 4 and result.findings[0].state == "new"


def test_rec_read_failure_raises_and_missing_keys_raise() -> None:
    with pytest.raises(RuntimeError, match="lacks keys"):
        friction.rec_states(fx.Reader(recs=[{"id": "rec-1", "status": "open", "source": "feedback_loop"}]))
    with pytest.raises(ConnectionError):
        friction.rec_states(fx.Reader(rec_error=ConnectionError("down")))
    for key in friction.REC_KEYS:
        row = fx.rec("rec-1", "open", RECURRING, SUBJECT)
        del row[key]
        with pytest.raises(RuntimeError):
            friction.rec_states(fx.Reader(recs=[row]))


def test_rec_read_uses_the_proposals_source() -> None:
    from scripts.feedback_loop import proposals

    reader = fx.Reader()
    friction.rec_states(reader)
    assert reader.calls == [("ops_recommendations", {"row_filter": f"source = '{proposals.SOURCE}'"})]


def test_read_window_halves_on_a_413() -> None:
    class Halving(fx.Reader):
        def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            self.calls.append((verb, params))
            if (
                params["day_from"] != params["day_to"]
                and (date.fromisoformat(params["day_to"]) - date.fromisoformat(params["day_from"])).days >= 6
            ):
                raise ResponseTooLargeError("named", "too large", None)
            return [fx.row(1)]

    rows, calls = friction.read_window(Halving(), FIRST, LAST)
    assert calls == 3 and len(rows) == 2
