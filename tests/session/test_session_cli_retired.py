"""Tombstone for the retired workflow session CLI (rec-4589).

The open/close session flags recorded nothing after PR #969 and printed a success line regardless; they were removed rather
than rewired (turn capture records the session). Every retired spelling comes from tests/fixtures/retired_session_cli.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.postflight import _common, housekeeping
from scripts.session import postflight, preflight
from tests.fixtures.retired_session_cli import (
    RETIRED_POSTFLIGHT_FLAGS,
    RETIRED_PREFLIGHT_FLAGS,
    RETIRED_STATE_FILE,
    RETIRED_SYMBOLS,
)

ROOT = Path(__file__).resolve().parents[2]


def _run(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=60,
    )


def test_preflight_refuses_the_retired_flags() -> None:
    opened, workflow, branch = RETIRED_PREFLIGHT_FLAGS
    result = _run("scripts.session.preflight", opened, workflow, "plan", branch, "b")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    for flag in RETIRED_PREFLIGHT_FLAGS:
        assert flag in result.stderr, flag
    helped = _run("scripts.session.preflight", "--help")
    assert helped.returncode == 0 and not [f for f in RETIRED_PREFLIGHT_FLAGS if f in helped.stdout]


def test_postflight_refuses_the_retired_flags() -> None:
    closed, outcome, changed = RETIRED_POSTFLIGHT_FLAGS
    result = _run("scripts.session.postflight", closed, outcome, "success", changed, "1")
    assert result.returncode == 2
    assert "outcome=" not in result.stdout
    helped = _run("scripts.session.postflight", "--help")
    assert helped.returncode == 0 and not [f for f in RETIRED_POSTFLIGHT_FLAGS if f in helped.stdout]


def test_retired_symbols_are_gone() -> None:
    for module in (preflight, postflight, _common, housekeeping):
        for symbol in RETIRED_SYMBOLS:
            assert not hasattr(module, symbol), (module.__name__, symbol)


def test_no_command_or_skill_invokes_the_retired_cli() -> None:
    needles = (*RETIRED_PREFLIGHT_FLAGS[:1], RETIRED_POSTFLIGHT_FLAGS[0], RETIRED_STATE_FILE)
    docs = [*(ROOT / ".claude" / "commands").glob("*.md"), *(ROOT / ".claude" / "skills").glob("**/SKILL.md")]
    assert docs
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        assert not [n for n in needles if n in text], doc.name
