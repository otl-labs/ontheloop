"""The one home of the retired workflow session CLI's spellings (rec-4589); stdlib only, no test_ prefix.

Tests that must name a retired flag, symbol or state file import it from here, so the stale-reference sweep excludes
exactly this file among the tests.
"""

from __future__ import annotations

RETIRED_PREFLIGHT_FLAGS = ("--open-session", "--workflow", "--branch")
RETIRED_POSTFLIGHT_FLAGS = ("--close-session", "--outcome", "--files-changed")
RETIRED_SYMBOLS = ("open_telemetry_session", "close_telemetry_session", "TELEMETRY_ACTIVE_SESSION_FILE")
RETIRED_STATE_FILE = ".telemetry-active-session.json"
