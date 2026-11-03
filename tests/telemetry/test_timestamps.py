"""Mirror test for src/telemetry/timestamps.py (100% line coverage)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.telemetry.timestamps import (
    TimestampError,
    epoch_ms,
    parse_iso8601_utc,
    parse_wire_timestamp,
    require_aware_utc,
    truncate_to_ms,
)


class TestParseIso8601Utc:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-09-25T10:00:00Z", datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)),
            ("2026-09-25t10:00:00z", datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)),
            ("2026-09-25 10:00:00Z", datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)),
            ("1970-01-01T00:00:00Z", datetime(1970, 1, 1, tzinfo=timezone.utc)),
            ("9999-12-31T23:59:59.999Z", datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)),
        ],
    )
    def test_accepts_the_pinned_profile(self, raw: str, expected: datetime) -> None:
        assert parse_iso8601_utc(raw) == expected

    def test_fraction_truncated_never_rounded(self) -> None:
        result = parse_iso8601_utc("2026-09-25T10:00:00.999999Z")
        assert result.microsecond == 999000

    def test_comma_decimal_mark_accepted(self) -> None:
        result = parse_iso8601_utc("2026-09-25T10:00:00,5Z")
        assert result.microsecond == 500000

    def test_single_fraction_digit_accepted(self) -> None:
        result = parse_iso8601_utc("2026-09-25T10:00:00.1Z")
        assert result.microsecond == 100000

    def test_nonutc_offset_normalised_to_utc(self) -> None:
        result = parse_iso8601_utc("2026-09-25T12:30:00+02:30")
        assert result == datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)
        assert result.tzinfo == timezone.utc

    def test_negative_offset_normalised_to_utc(self) -> None:
        result = parse_iso8601_utc("2026-09-25T05:00:00-05:00")
        assert result == datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        "raw",
        [
            "2026-09-25T10:00:00",  # naive
            "2026-09-25",  # date-only
            "20260925T100000Z",  # basic format
            "2026-W39-5",  # week date
            "2026-09-25T10:00:00+02",  # +HH offset
            "2026-09-25T10:00:00+0200",  # +HHMM offset
            "2026-09-25T24:00:00Z",  # 24:00:00
            "2026-09-25T10:00:60Z",  # :60 leap second
            "not-a-timestamp",
            "",
        ],
    )
    def test_rejects_forms_fromisoformat_would_accept(self, raw: str) -> None:
        with pytest.raises(TimestampError):
            parse_iso8601_utc(raw)

    def test_rejects_non_str(self) -> None:
        with pytest.raises(TimestampError):
            parse_iso8601_utc(12345)  # type: ignore[arg-type]

    def test_rejects_invalid_calendar_date(self) -> None:
        with pytest.raises(TimestampError):
            parse_iso8601_utc("2026-02-30T10:00:00Z")


class TestRequireAwareUtc:
    def test_accepts_utc_aware(self) -> None:
        require_aware_utc(datetime(2026, 9, 25, tzinfo=timezone.utc))

    def test_rejects_naive(self) -> None:
        with pytest.raises(TimestampError):
            require_aware_utc(datetime(2026, 9, 25))

    def test_rejects_non_utc_offset(self) -> None:
        with pytest.raises(TimestampError):
            require_aware_utc(datetime(2026, 9, 25, tzinfo=timezone(timedelta(hours=1))))

    def test_rejects_non_datetime(self) -> None:
        with pytest.raises(TimestampError):
            require_aware_utc("2026-09-25")  # type: ignore[arg-type]


class TestTruncateToMs:
    def test_floors_never_rounds(self) -> None:
        dt = datetime(2026, 9, 25, 10, 0, 0, 999999, tzinfo=timezone.utc)
        assert truncate_to_ms(dt).microsecond == 999000

    def test_exact_ms_unchanged(self) -> None:
        dt = datetime(2026, 9, 25, 10, 0, 0, 500000, tzinfo=timezone.utc)
        assert truncate_to_ms(dt).microsecond == 500000

    def test_rejects_non_datetime(self) -> None:
        with pytest.raises(TimestampError):
            truncate_to_ms("not-a-datetime")  # type: ignore[arg-type]


class TestEpochMs:
    def test_epoch_is_zero(self) -> None:
        assert epoch_ms(datetime(1970, 1, 1, tzinfo=timezone.utc)) == 0

    def test_exact_integer_arithmetic(self) -> None:
        dt = datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert epoch_ms(dt) == 1000

    def test_far_future_within_range(self) -> None:
        dt = datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)
        assert epoch_ms(dt) == 253402300799999
        assert epoch_ms(dt) <= 2**48 - 1

    def test_rejects_pre_epoch(self) -> None:
        with pytest.raises(TimestampError):
            epoch_ms(datetime(1969, 12, 31, 23, 59, 59, tzinfo=timezone.utc))

    def test_rejects_sub_millisecond_precision(self) -> None:
        with pytest.raises(TimestampError):
            epoch_ms(datetime(2026, 9, 25, 10, 0, 0, 999999, tzinfo=timezone.utc))

    def test_rejects_naive(self) -> None:
        with pytest.raises(TimestampError):
            epoch_ms(datetime(2026, 9, 25))

    def test_max_representable_datetime_stays_within_48_bit_range(self) -> None:
        # datetime's own year-9999 ceiling never reaches the 2**48-1 ms overflow bound, so that
        # branch in epoch_ms is defensive/unreachable (pragma: no cover) -- this test documents
        # the invariant instead: the latest ms-precision datetime is still comfortably in range.
        latest = datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)
        assert epoch_ms(latest) <= 2**48 - 1


def test_wire_timestamp_requires_exactly_three_fraction_digits() -> None:
    assert parse_wire_timestamp("2026-09-25T10:00:00.123Z") == datetime(2026, 9, 25, 10, 0, 0, 123000, tzinfo=timezone.utc)
    assert parse_wire_timestamp("2026-09-25T12:00:00.000+02:00") == datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)
    assert parse_wire_timestamp("2026-09-25 10:00:00,456z").microsecond == 456000
    for rejected in (
        "2026-09-25T10:00:00Z",
        "2026-09-25T10:00:00.1Z",
        "2026-09-25T10:00:00.12Z",
        "2026-09-25T10:00:00.1234Z",
        "2026-09-25T10:00:00.123456789Z",
        "2026-09-25T10:00:00.123",
        "2026-09-25",
        "not a timestamp",
        "2026-02-30T10:00:00.000Z",
    ):
        with pytest.raises(TimestampError):
            parse_wire_timestamp(rejected)
    for non_string in (None, 5, datetime(2026, 9, 25, tzinfo=timezone.utc)):
        with pytest.raises(TimestampError, match="expected str"):
            parse_wire_timestamp(non_string)  # type: ignore[arg-type]
