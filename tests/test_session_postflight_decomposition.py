#!/usr/bin/env python3
"""Equivalence suite for the session_postflight facade/package decomposition (SLOC decomposition,
PLAN-sloc-session-postflight).

Freezes the pre-refactor entry contracts so the movement-only decomposition of
scripts/session/postflight.py into scripts/postflight/ cannot silently change behaviour:

1. Facade completeness -- every name on the frozen export list (every public function + every
   test-referenced private symbol) is getattr-able on the facade AND importable via
   `from scripts.session.postflight import <name>`.
2. Mock interception -- patching scripts.postflight._common._run intercepts through a moved body
   (remote.run_push) AND through a facade-resident body (run_commit); patching
   scripts.postflight._common.LOGS_DIR intercepts through a moved body
   (housekeeping.prune_telemetry_logs).
3. Full-inventory residual-site closure -- scans tests/test_session_postflight.py for any
   old-namespace reference to a migrated symbol across both mock idioms present in that suite
   (string-form patch("session_postflight.X"), including line-wrapped call sites, and object-form
   patch.object(_postflight, "X", ...)) and asserts zero.
4. Argparse flag-surface freeze via --help (all 13 flags).
5. Behavioural self-reinvocation contract -- patches scripts.postflight._common._run, calls
   run_close(), and asserts the recorded argv is exactly
   [PYTHON, "scripts/session/postflight.py", "--pre-commit-sanity"]. This is deliberately
   behavioural rather than textual: a plain source-grep for the callsite (VP step 5) would also
   match the module docstring's "python scripts/session/postflight.py ..." usage lines if not
   carefully anchored; observing the actual recorded subprocess argv has no such ambiguity.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.postflight import _common, housekeeping
from tests.fixtures.retired_session_cli import RETIRED_POSTFLIGHT_FLAGS

ROOT = Path(__file__).resolve().parent.parent

# Load the facade under a PRIVATE handle (mirrors test_session_preflight_decomposition.py's
# pattern): does not clobber sys.modules["session_postflight"], which test_session_postflight.py
# registers -- collocating both in one process would make one suite's string-target patches
# resolve to the other's module object depending on collection/test order (pytest-randomly).
_MODULE_PATH = ROOT / "scripts" / "session" / "postflight.py"
_spec = importlib.util.spec_from_file_location("session_postflight_decomposition_mod", _MODULE_PATH)
assert _spec and _spec.loader
_postflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_postflight)  # type: ignore[union-attr]


# Frozen export list: every public function + every test-referenced private symbol from the
# pre-refactor module (facade-resident + _common + housekeeping + remote).
FROZEN_EXPORTS = frozenset(
    {
        # _common
        "ROOT",
        "PYTHON",
        "_SSO_PROFILE",
        "MAX_COMMIT_RETRIES",
        "CI_POLL_INTERVAL_SECONDS",
        "CI_POLL_TIMEOUT_SECONDS",
        "DEFAULT_MAX_AGE_DAYS",
        "LOGS_DIR",
        "ARCHIVE_DIR",
        "_PRUNE_SKIP_NAMES",
        "logger",
        "_run",
        "_current_branch",
        "find_plan_file",
        "clear_checkpoint",
        # housekeeping
        "_load_max_age_days",
        "prune_telemetry_logs",
        "_stage_document_derived_tables",
        "run_metrics",
        "run_log_housekeeping",
        # remote
        "run_push",
        # facade-resident
        "main",
        "run_validate",
        "run_pre_commit_sanity",
        "run_commit",
        "run_close",
        "run_auto",
        "_parse_scope_table",
        "_get_changed_files",
        "_normalize_path",
        "_paths_match",
    }
)

# Symbols that MOVED into the scripts/postflight package (excludes facade-resident names, which
# never had a "new home" to migrate to).
_MOVED_SYMBOLS = FROZEN_EXPORTS - {
    "main",
    "run_validate",
    "run_pre_commit_sanity",
    "run_commit",
    "run_close",
    "run_auto",
    "_parse_scope_table",
    "_get_changed_files",
    "_normalize_path",
    "_paths_match",
}


class TestFacadeCompleteness:
    """Every name on the frozen export list is getattr-able on the module AND importable via
    `from scripts.session.postflight import <name>`."""

    @pytest.mark.parametrize("name", sorted(FROZEN_EXPORTS))
    def test_getattr_on_private_handle(self, name: str) -> None:
        assert hasattr(_postflight, name), f"{name} missing from the facade module namespace"

    def test_every_frozen_export_resolves_via_standard_import(self) -> None:
        # Equivalent to `from scripts.session.postflight import <name>` for each name: Python's
        # import system resolves a plain-attribute `from X import Y` as getattr(import(X), Y).
        mod = importlib.import_module("scripts.session.postflight")
        missing = [n for n in sorted(FROZEN_EXPORTS) if not hasattr(mod, n)]
        assert not missing, f"names not resolvable via `from scripts.session.postflight import <name>`: {missing}"


class TestMockInterceptionThroughFacade:
    """Patching scripts.postflight._common._run intercepts through a moved body (remote.run_push)
    AND a facade-resident body (run_commit); patching _common.LOGS_DIR intercepts through a moved
    body (housekeeping.prune_telemetry_logs)."""

    def test_common_run_intercepts_facade_resident_body(self) -> None:
        """run_commit stays facade-resident; it must still resolve _run via _common."""
        with patch.object(
            _common, "_run", return_value=MagicMock(returncode=0, stdout="1 file changed", stderr="")
        ) as mock_run:
            rc = _postflight.run_commit("feat: intercept test")
        assert rc == 0
        first_call_cmd = mock_run.call_args_list[0].args[0]
        assert first_call_cmd[:2] == ["git", "add"]

    def test_common_run_intercepts_moved_body(self) -> None:
        """run_push moved to remote.py; patching _common._run must still intercept its git-push
        call. A single uniform nonzero returncode exercises the immediate push_failed early-return
        path (run_push's own _current_branch() call is also _run-mediated internally, and falls
        back to "unknown" on the same nonzero returncode), so no other _common primitive needs
        stubbing."""
        with patch.object(_common, "_run", return_value=MagicMock(returncode=1, stdout="", stderr="no upstream")) as mock_run:
            rc = _postflight.run_push()
        assert rc == 1
        push_calls = [c.args[0] for c in mock_run.call_args_list if c.args[0][:2] == ["git", "push"]]
        assert len(push_calls) == 1, f"expected exactly one git push call, got {mock_run.call_args_list}"

    def test_common_logs_dir_intercepts_moved_prune(self, tmp_path: Path) -> None:
        """prune_telemetry_logs moved to housekeeping.py; patching _common.LOGS_DIR/ARCHIVE_DIR
        (the single canonical target) must still intercept its behaviour."""
        logs = tmp_path / "logs"
        logs.mkdir()
        archive = logs / "archive"
        old_line = json.dumps({"date": "2020-01-01", "msg": "old"})
        (logs / ".intercept-test.jsonl").write_text(old_line + "\n", encoding="utf-8")
        with (
            patch.object(_common, "LOGS_DIR", logs),
            patch.object(_common, "ARCHIVE_DIR", archive),
        ):
            result = housekeeping.prune_telemetry_logs(max_age_days=90)
        assert ".intercept-test.jsonl" in result["pruned"]
        archived = list(archive.glob("*.jsonl"))
        assert len(archived) == 1
        assert "2020-01-01" in archived[0].read_text(encoding="utf-8")


class TestResidualPatchSiteClosure:
    """Full-inventory scan: zero old-namespace references to migrated symbols remain in
    tests/test_session_postflight.py, across both mock idioms actually present in that suite."""

    _IDIOM_PATTERNS = [
        re.compile(r'patch\(\s*"session_postflight\.(\w+)'),
        re.compile(r'patch\.object\(\s*_postflight\s*,\s*"(\w+)"'),
    ]

    def test_zero_residual_old_namespace_patch_sites(self) -> None:
        tests_dir = Path(__file__).resolve().parent
        sibling_files = sorted((tests_dir / "session" / "postflight").glob("test_*.py"))
        assert sibling_files, "expected >=1 decomposed postflight test file under tests/session/postflight/"
        residual: list[str] = []
        for fpath in sibling_files:
            text = fpath.read_text(encoding="utf-8")
            for pattern in self._IDIOM_PATTERNS:
                for m in pattern.finditer(text):
                    sym = m.group(1)
                    if sym in _MOVED_SYMBOLS:
                        line_no = text[: m.start()].count("\n") + 1
                        residual.append(f"{fpath.name}:{line_no}:{sym}")
        assert residual == [], f"residual old-namespace patch sites for migrated symbols: {residual}"


class TestArgparseFlagSurfaceFrozen:
    """CLI flag surface is exactly the 11 surviving flags; every retired flag is absent."""

    def test_help_lists_exactly_the_frozen_flags(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.session.postflight", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            timeout=30,
        )
        assert result.returncode == 0
        for flag in (
            "--validate",
            "--pre-commit-sanity",
            "--commit",
            "--push",
            "--metrics",
            "--close",
            "--evidence",
            "--log-housekeeping",
            "--auto",
            "--steps-total",
            "--steps-friction",
        ):
            assert flag in result.stdout, f"{flag} missing from --help output"
        for flag in RETIRED_POSTFLIGHT_FLAGS:
            assert flag not in result.stdout, f"retired {flag} is back in --help output"


class TestSelfReinvocationBehavioural:
    """run_close's self-reinvocation (`_run([PYTHON, "scripts/session/postflight.py",
    "--pre-commit-sanity"])`) is proven BEHAVIOURALLY here, not just by source grep (VP step 5) --
    a bare textual grep for the argv fragments would also match the module docstring's usage
    lines; recording the actual subprocess argv at call time has no such false-positive risk.
    """

    def test_run_close_reinvokes_with_frozen_argv(self) -> None:
        recorded: list[list[str]] = []

        def fake_run(cmd: list[str], cwd: Path | None = None, capture: bool = True) -> MagicMock:
            recorded.append(cmd)
            result = MagicMock()
            result.returncode = 0
            if cmd[:1] == ["git"]:
                result.stdout = "1 file changed"
                result.stderr = ""
            else:
                result.stdout = json.dumps({"status": "PASS"})
                result.stderr = ""
            return result

        with (
            patch.object(_common, "_run", side_effect=fake_run),
            patch.object(_common, "find_plan_file", return_value=None),
            patch.object(_common, "_current_branch", return_value="claude/test"),
        ):
            rc = _postflight.run_close()

        assert rc == 0
        reinvocations = [c for c in recorded if len(c) >= 2 and c[1] == "scripts/session/postflight.py"]
        assert len(reinvocations) == 1, f"expected exactly one self-reinvocation call, got {recorded}"
        assert reinvocations[0] == [_common.PYTHON, "scripts/session/postflight.py", "--pre-commit-sanity"]
