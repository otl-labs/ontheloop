"""Standing absence guard for the retired session-log surface (T3.20 c6)."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from scripts.checks.contracts import _retired_identity
from scripts.preflight import _common, context_docs
from tests.fixtures.session_preflight_module import preflight as session_preflight

ROOT = Path(__file__).resolve().parents[2]


def test_session_log_file_is_deleted() -> None:
    assert not (ROOT / "docs" / "SESSION_LOG.md").exists()
    assert (ROOT / "docs" / "SESSION_LOG_ARCHIVE.md").exists()


def test_preflight_session_log_reader_is_gone() -> None:
    assert not hasattr(_common, "SESSION_LOG_FILE")
    assert not hasattr(context_docs, "parse_last_session")
    assert not hasattr(context_docs, "_read_session_blocks")
    assert not hasattr(session_preflight, "SESSION_LOG_FILE")
    assert not hasattr(session_preflight, "parse_last_session")


def test_north_star_tracker_is_gone() -> None:
    assert importlib.util.find_spec("scripts.north_star_tracker") is None


def test_prompt_compliance_has_no_session_log_proxy() -> None:
    prompt_compliance = importlib.import_module("scripts.prompt_compliance")
    assert not hasattr(prompt_compliance, "check_plan_compliance")


def test_retired_identity_does_not_exempt_the_deleted_log() -> None:
    assert "docs/SESSION_LOG.md" not in _retired_identity._EXEMPT_FILES
    assert "docs/SESSION_LOG_ARCHIVE.md" in _retired_identity._EXEMPT_FILES
