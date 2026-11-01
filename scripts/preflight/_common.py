"""Shared primitives for the scripts/preflight package.

SOLE source of shared constants, path primitives, and reader/timestamp helpers used by every
session_preflight domain module. No dependency on scripts.session.preflight (no import cycle).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.aws_profile import resolve_aws_profile  # noqa: F401
from scripts.s3_log_store import get_backend, read_jsonl  # noqa: F401
from src.common.ducklake_reader_client import make_reader as _make_reader  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent.parent
TERRAFORM_DIR = ROOT / "terraform"
ROADMAP_FILE = ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
ROADMAP_PLATFORM_PATH = ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
DECISIONS_FILE = ROOT / "docs" / "DECISIONS.md"
STRATEGIC_REVIEW_LOOKBACK_DAYS = 30
PRIORITY_QUEUE_FILE = ROOT / "logs" / "priority-queue" / ".priority-queue.jsonl"
_NON_AUTOMATABLE_SOFTCAP = 250


def _make_event_reader(profile: str | None = None) -> Any:
    """The telemetry event reader: the routed reader serving the writer's catalog (scripts/telemetry_capture/read_route.py)."""
    from scripts.telemetry_capture import read_route  # noqa: PLC0415

    return read_route.make_routed_reader(profile)


# Sentinel default distinguishing "no cache rows supplied -> use the reader (back-compat / tests)"
# from "cache rows supplied but None -> reader pull failed -> degrade" (a real None is meaningful).
_READER_SENTINEL: object = object()


def _row_ts(row: dict, field: str = "created_timestamp") -> datetime | None:
    """Parse a row timestamp field (ISO string or datetime) into a UTC-aware datetime, or None."""
    val = row.get(field)
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if hasattr(val, "isoformat"):  # date / other temporal
        try:
            return datetime.fromisoformat(val.isoformat()).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    return _parse_ts_utc(str(val))


def _parse_ts_utc(ts: str) -> datetime | None:
    """Parse an ISO-like timestamp string into a UTC-aware datetime, or None on failure."""
    ts = ts.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
