"""ISO-8601 / RFC 3339 tz-aware decode helper for the telemetry kernel (Decision 199).

Stdlib only (AGENTS.md plane-neutral rule, Decision 184 cl.2): datetime.fromisoformat's accepted
grammar is Python-version-dependent (it grew basic-format and other forms across 3.11+), so this
module implements a PINNED RFC 3339 profile with its own regex rather than delegating format
acceptance to fromisoformat. The calendar/clock RANGE checks (days-per-month, hour<=23, etc.) are
still performed by the stdlib datetime constructor, which raises ValueError on an invalid value --
this module only pins which STRINGS reach that constructor.

Accepted profile: '<date>[Tt ]<time>[.,<frac>](Z|z|+HH:MM|-HH:MM)'. Required offset (no naive
input); 'T', 't' or a plain space separator; 1-9 fraction digits, truncated (never rounded) to
millisecond precision; '.' or ',' as the decimal mark. Rejected: naive input, date-only input,
basic format (no '-'/':' separators), ISO week dates, '+HH' / '+HHMM' offsets (colon required),
24:00:00, a :60 leap second, and non-str input.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RFC3339_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"[Tt ]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:[.,](?P<frac>\d{1,9}))?"
    r"(?P<offset>Z|z|[+-]\d{2}:\d{2})$"
)

_MS_OVERFLOW_LIMIT = 2**48 - 1


class TimestampError(ValueError):
    """Raised on any rejected timestamp input across this module's functions."""


def parse_iso8601_utc(value: str) -> datetime:
    """Parse *value* against the pinned RFC 3339 profile and return a UTC tz-aware datetime.

    The result is always truncated (floored, never rounded) to millisecond precision -- a
    sub-millisecond fraction (e.g. '.999999') never rounds up to the next millisecond.
    """
    if not isinstance(value, str):
        raise TimestampError(f"expected str, got {type(value).__name__}")

    match = _RFC3339_RE.match(value)
    if match is None:
        raise TimestampError(f"not a valid RFC 3339 timestamp (pinned profile): {value!r}")

    frac = match.group("frac") or ""
    ms_digits = (frac + "000")[:3]
    microsecond = int(ms_digits) * 1000

    offset_token = match.group("offset")
    if offset_token in ("Z", "z"):
        tzinfo = timezone.utc
    else:
        sign = 1 if offset_token[0] == "+" else -1
        off_hour = int(offset_token[1:3])
        off_minute = int(offset_token[4:6])
        tzinfo = timezone(sign * timedelta(hours=off_hour, minutes=off_minute))

    try:
        dt = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            microsecond,
            tzinfo=tzinfo,
        )
    except ValueError as exc:
        raise TimestampError(f"not a valid calendar timestamp: {value!r} ({exc})") from exc

    return dt.astimezone(timezone.utc)


def parse_wire_timestamp(value: str) -> datetime:
    """Parse a writer-edge timestamp: the pinned RFC 3339 profile with EXACTLY three fraction digits.

    Every timestamp column carries its full millisecond value at the writer edge (event_id's 48-bit prefix depends on
    event_timestamp the same way the entity keys depend on session_started_at), so a fraction-less value, which the
    profile alone would silently pad to .000, and any other width are rejected. Decision 96 cl.4.
    """
    if not isinstance(value, str):
        raise TimestampError(f"expected str, got {type(value).__name__}")
    match = _RFC3339_RE.match(value)
    if match is None:
        raise TimestampError(f"not a valid RFC 3339 timestamp (pinned profile): {value!r}")
    frac = match.group("frac")
    if frac is None or len(frac) != 3:
        raise TimestampError(f"wire timestamps carry exactly three fraction digits: {value!r}")
    return parse_iso8601_utc(value)


def require_aware_utc(value: datetime) -> None:
    """Raise TimestampError unless *value* is a tz-aware datetime with a zero UTC offset."""
    if not isinstance(value, datetime):
        raise TimestampError(f"expected datetime, got {type(value).__name__}")
    offset = value.utcoffset()
    if offset is None:
        raise TimestampError("naive datetime is not accepted -- a timezone is required")
    if offset != timedelta(0):
        raise TimestampError(f"datetime must be UTC (zero offset), got offset {offset}")


def truncate_to_ms(value: datetime) -> datetime:
    """Floor *value*'s microsecond field to the nearest lower millisecond. Never rounds."""
    if not isinstance(value, datetime):
        raise TimestampError(f"expected datetime, got {type(value).__name__}")
    floored_micro = (value.microsecond // 1000) * 1000
    return value.replace(microsecond=floored_micro)


def epoch_ms(value: datetime) -> int:
    """Return exact integer milliseconds since the Unix epoch for a UTC tz-aware *value*.

    Uses only integer arithmetic (timedelta.days/seconds/microseconds are all ints) -- never a
    float. Rejects a pre-epoch instant and one beyond 2**48-1 ms (the ULID time-prefix range).
    """
    require_aware_utc(value)
    if value.microsecond % 1000 != 0:
        raise TimestampError(f"datetime carries sub-millisecond precision: {value.microsecond} us")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value - epoch
    total_ms = delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
    if total_ms < 0:
        raise TimestampError(f"pre-epoch timestamp is not accepted: {value.isoformat()}")
    if total_ms > _MS_OVERFLOW_LIMIT:  # pragma: no cover -- unreachable: datetime's year 9999 ceiling never reaches 2**48-1 ms
        raise TimestampError(f"timestamp exceeds the 48-bit ULID time-prefix range: {value.isoformat()}")
    return total_ms
