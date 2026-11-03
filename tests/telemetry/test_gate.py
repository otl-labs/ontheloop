"""Mirror test for src/telemetry/gate.py: strict-type rejections, NOT NULL, derived-column
rejection, intra-batch collapse/conflict.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.telemetry.gate import (
    AppendError,
    GateError,
    GrainConflictError,
    RowRuleGateError,
    check_and_normalize_value,
    check_not_null,
    collapse_or_reject_duplicates,
    differing_columns,
    reject_unknown_or_derived_columns,
)

STRUCTURAL = frozenset({"created_timestamp", "producer_version"})


class TestRejectUnknownOrDerivedColumns:
    def test_accepts_known_columns(self) -> None:
        reject_unknown_or_derived_columns({"a": 1, "b": 2}, frozenset({"a", "b"}), frozenset({"c"}))

    def test_rejects_unknown_column(self) -> None:
        with pytest.raises(AppendError):
            reject_unknown_or_derived_columns({"z": 1}, frozenset({"a"}), frozenset({"c"}))

    def test_rejects_caller_supplied_derived_column(self) -> None:
        with pytest.raises(AppendError):
            reject_unknown_or_derived_columns({"event_id": "x"}, frozenset({"event_id"}), frozenset({"event_id"}))


class TestCheckNotNull:
    def test_passes_when_present(self) -> None:
        check_not_null({"a": 1}, frozenset({"a"}))

    def test_rejects_missing_key(self) -> None:
        with pytest.raises(AppendError):
            check_not_null({}, frozenset({"a"}))

    def test_rejects_none_value(self) -> None:
        with pytest.raises(AppendError):
            check_not_null({"a": None}, frozenset({"a"}))


class TestCheckAndNormalizeValue:
    def test_varchar_accepts_str(self) -> None:
        assert check_and_normalize_value("c", "hello", "VARCHAR") == "hello"

    def test_varchar_rejects_non_str(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", 1, "VARCHAR")

    def test_bigint_accepts_int(self) -> None:
        assert check_and_normalize_value("c", 5, "BIGINT") == 5

    def test_bigint_rejects_bool(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", True, "BIGINT")

    def test_bigint_rejects_float(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", 1.5, "BIGINT")

    def test_bigint_rejects_out_of_int64_range(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", 2**63, "BIGINT")
        with pytest.raises(AppendError):
            check_and_normalize_value("c", -(2**63) - 1, "BIGINT")

    def test_double_accepts_int_and_float(self) -> None:
        assert check_and_normalize_value("c", 1, "DOUBLE") == 1.0
        assert check_and_normalize_value("c", 1.5, "DOUBLE") == 1.5

    def test_double_rejects_bool(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", False, "DOUBLE")

    def test_double_rejects_nan_and_inf(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", float("nan"), "DOUBLE")
        with pytest.raises(AppendError):
            check_and_normalize_value("c", float("inf"), "DOUBLE")

    def test_boolean_accepts_bool_only(self) -> None:
        assert check_and_normalize_value("c", True, "BOOLEAN") is True
        with pytest.raises(AppendError):
            check_and_normalize_value("c", 1, "BOOLEAN")

    def test_varchar_array_accepts_list_of_str(self) -> None:
        assert check_and_normalize_value("c", ["a", "b"], "VARCHAR[]") == ["a", "b"]

    def test_varchar_array_rejects_non_list(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", "a", "VARCHAR[]")

    def test_varchar_array_rejects_non_str_elements(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", ["a", 1], "VARCHAR[]")

    def test_timestamp_accepts_aware_datetime_and_normalises(self) -> None:
        dt = datetime(2026, 9, 25, 10, 0, 0, 999999, tzinfo=timezone.utc)
        result = check_and_normalize_value("c", dt, "TIMESTAMP WITH TIME ZONE")
        assert result.microsecond == 999000
        assert result.tzinfo == timezone.utc

    def test_timestamp_rejects_naive(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", datetime(2026, 9, 25), "TIMESTAMP WITH TIME ZONE")

    def test_timestamp_rejects_non_datetime(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", "2026-09-25", "TIMESTAMP WITH TIME ZONE")

    def test_unsupported_sql_type_rejected(self) -> None:
        with pytest.raises(AppendError):
            check_and_normalize_value("c", 1, "JSON")


class TestCollapseOrRejectDuplicates:
    def test_no_duplicates_passthrough(self) -> None:
        rows = [{"event_id": "a", "parser_version": 1, "x": 1}, {"event_id": "b", "parser_version": 1, "x": 2}]
        deduped, collapsed = collapse_or_reject_duplicates(rows, ("event_id", "parser_version"), STRUCTURAL)
        assert deduped == rows
        assert collapsed == 0

    def test_identical_duplicates_collapse(self) -> None:
        row = {"event_id": "a", "parser_version": 1, "x": 1}
        deduped, collapsed = collapse_or_reject_duplicates([row, dict(row)], ("event_id", "parser_version"), STRUCTURAL)
        assert deduped == [row]
        assert collapsed == 1

    def test_conflicting_duplicates_reject(self) -> None:
        rows = [
            {"event_id": "a", "parser_version": 1, "x": 1},
            {"event_id": "a", "parser_version": 1, "x": 2},
        ]
        with pytest.raises(AppendError):
            collapse_or_reject_duplicates(rows, ("event_id", "parser_version"), STRUCTURAL)

    def test_differing_parser_version_is_not_a_duplicate(self) -> None:
        rows = [
            {"event_id": "a", "parser_version": 1, "x": 1},
            {"event_id": "a", "parser_version": 2, "x": 1},
        ]
        deduped, collapsed = collapse_or_reject_duplicates(rows, ("event_id", "parser_version"), STRUCTURAL)
        assert len(deduped) == 2
        assert collapsed == 0


def test_grain_key_includes_producer() -> None:
    """The telemetry grain key is (producer, event_id, parser_version) (Decision-cited in
    data-modeling-standard.yaml's grain-enforced-at-write rule): two producers sharing event_id
    and parser_version both survive; a producer_version-only difference collapses; any other
    content difference raises.
    """
    grain = ("producer", "event_id", "parser_version")
    rows = [
        {"producer": "claude_code", "event_id": "a", "parser_version": 1, "producer_version": "1.0", "x": 1},
        {"producer": "litellm", "event_id": "a", "parser_version": 1, "producer_version": "2.0", "x": 1},
    ]
    deduped, collapsed = collapse_or_reject_duplicates(rows, grain, STRUCTURAL)
    assert len(deduped) == 2
    assert collapsed == 0

    same_producer = [
        {"producer": "claude_code", "event_id": "a", "parser_version": 1, "producer_version": "1.0", "x": 1},
        {"producer": "claude_code", "event_id": "a", "parser_version": 1, "producer_version": "1.1", "x": 1},
    ]
    deduped2, collapsed2 = collapse_or_reject_duplicates(same_producer, grain, STRUCTURAL)
    assert len(deduped2) == 1
    assert collapsed2 == 1

    conflicting = [
        {"producer": "claude_code", "event_id": "a", "parser_version": 1, "x": 1},
        {"producer": "claude_code", "event_id": "a", "parser_version": 1, "x": 2},
    ]
    with pytest.raises(AppendError):
        collapse_or_reject_duplicates(conflicting, grain, STRUCTURAL)


def test_grain_key_null_column_rejected() -> None:
    rows = [{"producer": None, "event_id": "a", "parser_version": 1, "x": 1}]
    with pytest.raises(AppendError):
        collapse_or_reject_duplicates(rows, ("producer", "event_id", "parser_version"), STRUCTURAL)


def test_gate_errors_are_typed() -> None:
    assert issubclass(GateError, AppendError) and issubclass(GrainConflictError, AppendError)
    assert not issubclass(GateError, GrainConflictError) and not issubclass(GrainConflictError, GateError)
    with pytest.raises(GateError):
        reject_unknown_or_derived_columns({"z": 1}, frozenset({"a"}), frozenset())
    with pytest.raises(GateError):
        reject_unknown_or_derived_columns({"d": 1}, frozenset({"d"}), frozenset({"d"}))
    with pytest.raises(GateError):
        check_not_null({"a": None}, frozenset({"a"}))
    with pytest.raises(GateError):
        check_and_normalize_value("c", 1.5, "VARCHAR")
    with pytest.raises(GateError):
        check_and_normalize_value("c", 1, "JSON")
    with pytest.raises(GateError):
        collapse_or_reject_duplicates([{"k": None}], ("k",), STRUCTURAL)
    with pytest.raises(GrainConflictError):
        collapse_or_reject_duplicates([{"k": 1, "x": 1}, {"k": 1, "x": 2}], ("k",), STRUCTURAL)


def test_within_batch_compare_honours_the_specs_exclusions() -> None:
    first = {"k": 1, "content": "a", "content_sha256": "s", "producer_version": "1"}
    second = {"k": 1, "content": "b", "content_sha256": "s", "producer_version": "2"}
    assert differing_columns(first, second, ignore=STRUCTURAL) == ["content"]
    excluded = STRUCTURAL | {"content"}
    deduped, collapsed = collapse_or_reject_duplicates([first, second], ("k",), excluded)
    assert deduped == [first] and collapsed == 1
    with pytest.raises(GrainConflictError, match="content_sha256"):
        collapse_or_reject_duplicates([first, {**second, "content_sha256": "t"}], ("k",), excluded)
    with pytest.raises(GrainConflictError, match="content"):
        collapse_or_reject_duplicates([first, second], ("k",), STRUCTURAL)


def test_gate_errors_carry_column_and_reason() -> None:
    def raised(call) -> GateError:
        with pytest.raises(GateError) as caught:
            call()
        return caught.value

    cases = [
        (
            lambda: reject_unknown_or_derived_columns({"event_id": 1}, frozenset(), frozenset({"event_id"})),
            "event_id",
            "derived_column",
        ),
        (lambda: reject_unknown_or_derived_columns({"zz": 1}, frozenset({"a"}), frozenset()), None, "unknown_column"),
        (lambda: check_not_null({"a": None}, frozenset({"a"})), "a", "not_null"),
        (lambda: check_and_normalize_value("c", 1, "VARCHAR"), "c", "type"),
        (lambda: check_and_normalize_value("c", 2**63, "BIGINT"), "c", "range"),
        (lambda: check_and_normalize_value("c", float("nan"), "DOUBLE"), "c", "range"),
        (lambda: check_and_normalize_value("c", 1, "BOOLEAN"), "c", "type"),
        (lambda: check_and_normalize_value("c", [1], "VARCHAR[]"), "c", "type"),
        (lambda: check_and_normalize_value("c", datetime(2026, 1, 1), "TIMESTAMP WITH TIME ZONE"), "c", "type"),
        (lambda: check_and_normalize_value("c", 1, "NOPE"), "c", "type"),
        (lambda: collapse_or_reject_duplicates([{"k": 1}, {"k": None}], ("k",), STRUCTURAL), "k", "grain_key_null"),
    ]
    for call, column, reason in cases:
        err = raised(call)
        assert (err.column, err.reason) == (column, reason), call
        assert (err.table, err.row.__class__) == (None, err.row.__class__)
    assert raised(cases[-1][0]).row == 1


def test_gate_messages_never_echo_the_value() -> None:
    secret_int = 2**70 + 123456789
    for value, sql_type in ((secret_int, "BIGINT"), (float("inf"), "DOUBLE"), (10**400, "DOUBLE")):
        with pytest.raises(GateError) as caught:
            check_and_normalize_value("c", value, sql_type)
        assert str(secret_int) not in str(caught.value) and "inf" not in str(caught.value)


def test_row_rule_gate_error_carries_the_rule() -> None:
    err = RowRuleGateError("t.c: row rule r violated", rule="r", table="t", row=3, column="c")
    assert isinstance(err, GateError)
    assert (err.rule, err.table, err.row, err.column, err.reason) == ("r", "t", 3, "c", "row_rule")


def test_in_batch_grain_conflict_names_event_id_and_row() -> None:
    rows = [{"k": "a", "v": 1, "event_id": "E0"}, {"k": "b", "v": 1, "event_id": "E1"}, {"k": "a", "v": 2, "event_id": "E2"}]
    with pytest.raises(GrainConflictError) as caught:
        collapse_or_reject_duplicates(rows, ("k",), frozenset({"event_id"}))
    assert (caught.value.event_id, caught.value.row, caught.value.table) == ("E2", 2, None)


def test_double_overflow_is_a_gate_error() -> None:
    with pytest.raises(GateError) as caught:
        check_and_normalize_value("c", 10**400, "DOUBLE")
    assert (caught.value.column, caught.value.reason) == ("c", "range")
    assert isinstance(caught.value.__cause__, OverflowError)
