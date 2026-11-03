"""Fixtures for the src/common/ducklake_event_reads.py concern-split tests."""

from __future__ import annotations

from typing import Any, Callable

import pytest

from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt

DECLARED_PIN = (rt.TELEMETRY_PROD_META_SCHEMA, rt.TELEMETRY_PROD_DATA_PATH)


@pytest.fixture
def declared_pin() -> Callable[[], tuple[str, str]]:
    """The caller's declared pin: acquirer_for has no default, so every event acquire names the pin it binds."""
    return lambda: DECLARED_PIN


@pytest.fixture
def refusing_pin() -> Callable[[], tuple[str, str]]:
    def refuse() -> tuple[str, str]:
        raise rt.DuckLakeRuntimeError("the declared pin refused")

    return refuse


@pytest.fixture(autouse=True)
def _reset_slots() -> Any:
    rt.reset_warm_connection()
    ev._INTERRUPTED_SLOTS.clear()
    yield
    rt.reset_warm_connection()
    ev._INTERRUPTED_SLOTS.clear()
