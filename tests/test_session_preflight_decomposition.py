#!/usr/bin/env python3
"""Equivalence suite for the session_preflight facade/package decomposition (T-sloc).

Freezes the pre-refactor entry contracts so the movement-only decomposition of
scripts/session/preflight.py into scripts/preflight/ cannot silently change behaviour:

1. Report-schema freeze -- a stubbed main() run's report top-level key set equals the frozen
   frozen key list captured from the pre-refactor code (dict literal + post-assignments).
2. Facade completeness -- every name on the frozen export list (every public function + every
   test-referenced private symbol, including the back-compat _sync_ops_pull
   re-import) is getattr-able on the facade AND importable via
   `from scripts.session.preflight import <name>`.
3. Mock interception -- patching scripts.preflight._common._make_reader and one home-module
   function per domain intercepts through moved bodies invoked via the facade.
4. Full residual-site closure -- scans the sibling test files for any old-namespace reference to
   a migrated symbol across all four mock idioms and asserts zero.
5. Stdout prefix freeze -- the summary block's "Preflight OK -> " first line is frozen,
   line-anchored within whole stdout (never process-stdout line 1, which is a blank line from the
   telemetry-health block).
6. Argparse flag-surface freeze via --help.
7. Decision 88 topology guard -- greps scripts/preflight/ for _make_reader/make_reader bindings
   outside _common.py and asserts zero (single warm-sync/_make_reader topology, Decision 88).
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight import (
    _common,
    alerts,
    aws_infra,
    ci_rca_gauges,
    ci_rca_signals,
    context_docs,
    correlation,
    dependabot,
    env_git,
    priority_queue,
    recs_cache,
    session_history,
    telemetry_health,
)
from scripts.preflight.recs_signals_source import RecsSignalInputs
from scripts.roadmap import platform_roadmap
from tests.fixtures.platform_roadmap_state import live_state_dict
from tests.fixtures.retired_session_cli import RETIRED_PREFLIGHT_FLAGS

ROOT = Path(__file__).resolve().parent.parent

# Load the facade under a PRIVATE handle (mirrors test_session_preflight_cache_serving.py's
# pattern): does not clobber sys.modules["session_preflight"], which test_session_preflight.py
# registers -- collocating both in one process would make one suite's string-target patches
# resolve to the other's module object depending on collection/test order (pytest-randomly).
_MODULE_PATH = ROOT / "scripts" / "session" / "preflight.py"
_spec = importlib.util.spec_from_file_location("session_preflight_decomposition_mod", _MODULE_PATH)
assert _spec and _spec.loader
_preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_preflight)  # type: ignore[union-attr]


# Frozen BEFORE any code movement (plan execution_steps[0]): the pre-refactor main()'s report
# dict literal (37 keys) + 7 post-assignment keys. See PLAN-sloc-session-preflight.yaml.
# Updated deliberately for genuine new report fields since (e.g. dedup_effectiveness_gauge /
# dedup_effectiveness_escalation, PLAN-ci-rca-dedup-fire-and-selftest WS5; decision_conditions,
# PR #603 feat(reversal-condition-monitor) SEQ-02; prose_context, PLAN-prose-context-metric
# ACG-05/ACG-06 slice 3; budget_breach_summary, PLAN-full-tier-engine-hardening VTS-20;
# dependabot_stranded_prs, dependabot-automation-cleanup Component 3) -- this
# constant is a drift guard against SILENT schema changes, not a permanent ban on adding fields;
# bump it (and the count assertion below) in the same commit as a deliberate report-key addition.
FROZEN_REPORT_KEYS = frozenset(
    {
        "venv_ok",
        "branch",
        "uncommitted_changes",
        "stash_entries",
        "main_freshness",
        "creds_status",
        "s3_log_bucket_set",
        "terraform_pending",
        "convergence_health",
        "last_session",
        "session_history",
        "open_recommendations",
        "aging_recommendations",
        "non_automatable_recommendations",
        "priority_queue",
        "priority_queue_source",
        "recs_read_status",
        "recs_read",
        "ci_rca_open_total",
        "ci_rca_recs",
        "ci_rca_unresolved_recs",
        "ci_rca_likely_resolved_recs",
        "ci_rca_dispute_recs",
        "ci_rca_undetermined_recs",
        "ci_rca_undetermined_total",
        "ci_rca_abstention_gauge",
        "ci_rca_escape_mode_gauge",
        "ci_rca_probe_health_escalation",
        "ci_rca_telemetry",
        "ci_rca_back_validation",
        "dedup_effectiveness_gauge",
        "dedup_effectiveness_escalation",
        "telemetry_health",
        "recent_main_commits",
        "friction_patterns",
        "log_sync_result",
        "recommendation_sync",
        "data_quality",
        "context",
        "platform_roadmap",
        "session_start",
        # 10 post-assignments
        "provisional_contracts_due",
        "decision_conditions",
        "non_automatable_softcap_breached",
        # Ad-hoc /orient lane (audit PDB-01, B1-R4 + B4-O1): pure derivations over
        # recs_rows_cache, written unconditionally right after the softcap gauge above.
        "followon_recs",
        "open_critical_recs",
        "ci_rca_liveness_alert",
        "convergence_sensor_liveness_alert",
        "convergence_rca_gap_alert",
        "forward_fix_recursion_alert",
        "budget_bypass_alert",
        "budget_breach_summary",
        "dependabot_stranded_prs",
        "endstate_drift",
        "prose_context",
    }
)
assert len(FROZEN_REPORT_KEYS) == 55, "frozen report key list itself drifted -- fix the constant, not the assertion"

# Frozen export list: every public function + every test-referenced private symbol from the
# pre-refactor module (facade-resident + all 9 domain modules + _common + the back-compat
# dead re-import rec-2210 requires to keep resolving).
FROZEN_EXPORTS = frozenset(
    {
        # _common
        "ROOT",
        "TERRAFORM_DIR",
        "ROADMAP_FILE",
        "ROADMAP_PLATFORM_PATH",
        "DECISIONS_FILE",
        "STRATEGIC_REVIEW_LOOKBACK_DAYS",
        "PRIORITY_QUEUE_FILE",
        "_NON_AUTOMATABLE_SOFTCAP",
        "_READER_SENTINEL",
        "_make_reader",
        "_row_ts",
        "_parse_ts_utc",
        "resolve_aws_profile",
        "get_backend",
        "read_jsonl",
        # env_git
        "_print_activate_hint",
        "check_venv",
        "is_worktree",
        "get_git_status",
        "check_main_freshness",
        "_get_recent_main_commits",
        "run_log_sync",
        "_print_recent_main_commits",
        # aws_infra
        "check_credentials",
        "_handle_credentials_startup",
        "_prime_reader_url",
        "check_terraform_pending",
        # recs_cache
        "_derive_decisions_max_updated",
        "_tally_rec_counts",
        "_count_recommendations_reader",
        "_check_non_automatable_softcap",
        "_get_latest_decision_ts",
        # ci_rca_signals
        "_derive_ci_rca_open",
        "_derive_ci_rca_dispute_open",
        "_derive_ci_rca_undetermined_open",
        "_derive_ci_rca_closed",
        "_derive_ci_rca_since",
        "_fetch_ci_rca_recs",
        "_fetch_ci_rca_dispute_recs",
        "_fetch_ci_rca_undetermined_recs",
        "_fetch_ci_rca_recs_since",
        "_check_ci_rca_liveness",
        "_check_convergence_rca_gap",
        "_CONVERGENCE_RCA_GAP_GRACE_MINUTES",
        "print_ci_rca_recs",
        "print_ci_rca_dispute_recs",
        "print_ci_rca_undetermined_recs",
        # ci_rca_gauges
        "_compute_ci_rca_abstention",
        "_escalate_ci_rca_probe_health",
        "print_ci_rca_abstention_gauge",
        "_CI_RCA_TELEMETRY_WINDOW_DAYS",
        "_CI_RCA_WARN_REJECT_ALERT_THRESHOLD",
        "_CI_RCA_WARN_REJECT_PROMOTION_THRESHOLD",
        "_compute_ci_rca_telemetry",
        "print_ci_rca_telemetry",
        "_derive_ci_rca_back_validation",
        "print_ci_rca_back_validation",
        # alerts
        "_derive_forward_fix_recursion",
        "_check_forward_fix_recursion",
        "_derive_budget_bypass_recent",
        "_check_budget_bypass_alert",
        # priority_queue
        "_shape_priority_queue_rows",
        "_read_priority_queue_cache",
        "read_priority_queue",
        "print_priority_queue",
        # correlation
        "_CI_TITLE_STOPWORDS",
        "_title_jaccard",
        "_file_paths_correlate",
        "correlate_recs_with_commits",
        "correlate_ci_rca_with_main",
        "surface_queue_relevance_triage",
        # context_docs
        "read_context_files",
        "check_data_quality_coverage",
        "_check_endstate_drift",
        "_scan_provisional_contracts",
        # facade-resident
        "main",
        "_slim_roadmap_state",
        "_format_preflight_summary",
        "PREFLIGHT_REPORT",
        # back-compat dead re-import (rec-2210) -- must keep resolving even though nothing calls it
        "_sync_ops_pull",
    }
)

# Symbols that MOVED into the scripts/preflight package (excludes facade-resident names and the
# back-compat dead re-import, which never had a "new home" to migrate to).
_MOVED_SYMBOLS = FROZEN_EXPORTS - {
    "main",
    "_slim_roadmap_state",
    "PREFLIGHT_REPORT",
    "_sync_ops_pull",
}

_FRESHNESS_STUB = {
    "status": "ok",
    "fetched_at": "2026-06-15T00:00:00+00:00",
    "commits_behind": 0,
    "commits_ahead": 0,
    "main_files_changed_since_branch": [],
}


def _warm_sync_stub() -> dict:
    return {
        "pulled": {},
        "rows": {"ops_recommendations": [], "ops_decisions": [], "ops_priority_queue": []},
        "reader_ok": {"ops_recommendations": True, "ops_decisions": True, "ops_priority_queue": True},
    }


def _base_stub_specs(report_path: Path) -> list[tuple[object, str, object]]:
    """Return (target, attr, return_value) triples covering every external dependency main() touches.

    creds_status defaults to "unavailable" so the ci_rca_probe_health escalation early-return path
    is taken (no attempted portal write) and no code path needs real AWS/network access.
    """
    reader = MagicMock()
    reader.named.return_value = []
    reader.current_state.return_value = []
    return [
        (_common, "_make_reader", reader),
        (env_git, "check_venv", True),
        (env_git, "get_git_status", ("claude/test", False, [])),
        (env_git, "check_main_freshness", _FRESHNESS_STUB),
        (env_git, "run_log_sync", {"status": "skipped", "files": []}),
        (env_git, "_get_recent_main_commits", []),
        (aws_infra, "check_terraform_pending", (False, None)),
        (aws_infra, "check_credentials", "unavailable"),
        (recs_cache, "_count_recommendations_reader", (0, 0, 0, [])),
        (recs_cache, "_get_latest_decision_ts", None),
        (ci_rca_signals, "_fetch_ci_rca_recs", []),
        (ci_rca_signals, "_fetch_ci_rca_dispute_recs", []),
        (ci_rca_signals, "_fetch_ci_rca_undetermined_recs", []),
        (ci_rca_signals, "_derive_ci_rca_closed", []),
        (ci_rca_signals, "_check_ci_rca_liveness", None),
        (ci_rca_signals, "_check_convergence_rca_gap", None),
        (ci_rca_gauges, "_compute_ci_rca_abstention", None),
        (ci_rca_gauges, "_escalate_ci_rca_probe_health", None),
        (ci_rca_gauges, "_compute_ci_rca_telemetry", None),
        (ci_rca_gauges, "_derive_ci_rca_back_validation", None),
        (alerts, "_check_forward_fix_recursion", None),
        (alerts, "_check_budget_bypass_alert", None),
        (dependabot, "check_stranded_prs", None),
        (telemetry_health, "check_telemetry_health", {"status": "pass", "catalog": "ducklake_smoke", "tables": {}}),
        (priority_queue, "read_priority_queue", []),
        (correlation, "correlate_ci_rca_with_main", {"unresolved": [], "likely_resolved": []}),
        (
            session_history,
            "read_session_history",
            {"status": "ok", "window_days": 2, "recent_sessions": ["stub-session"], "last_session": "stub-session"},
        ),
        (
            context_docs,
            "read_context_files",
            {
                "roadmap_phase": "unknown",
                "open_decisions_count": 0,
                "strategic_review_due": True,
                "recommendations_count": 0,
            },
        ),
        (context_docs, "check_data_quality_coverage", {"tables_covered": 0, "checks_defined": 0, "last_run": None}),
        (
            context_docs,
            "_check_endstate_drift",
            {"stale": False, "synthesized_hash": None, "current_hash": None, "new_ids": []},
        ),
        (context_docs, "_scan_provisional_contracts", []),
        (platform_roadmap, "compute_state_dict", live_state_dict),
        (_preflight, "PREFLIGHT_REPORT", report_path),
    ]


# Attributes that main()/its callees read as plain VALUES rather than call as functions --
# these are patched via direct replacement (new=value); everything else in _base_stub_specs is a
# function attribute and is patched via return_value=value so calling it yields that value.
_DIRECT_VALUE_ATTRS = {(_preflight, "PREFLIGHT_REPORT"), (platform_roadmap, "compute_state_dict")}


@contextlib.contextmanager
def _stubbed_main_env(report_path: Path, overrides: dict[tuple[object, str], object] | None = None):
    """Patch every external dependency main() touches; `overrides` swaps in distinctive values.

    overrides keys are (module_object, attr_name) pairs matching a row in _base_stub_specs; any
    key not present in the base spec list is patched in addition to the base set (used for
    scripts.sync.ops.warm_sync, which is not a moved symbol and is patched by string target).
    """
    overrides = overrides or {}
    specs = list(_base_stub_specs(report_path))
    consumed = set()
    for i, (target, attr, _default) in enumerate(specs):
        key = (target, attr)
        if key in overrides:
            specs[i] = (target, attr, overrides[key])
            consumed.add(key)
    extra = [(t, a, v) for (t, a), v in overrides.items() if (t, a) not in consumed]

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("scripts.sync.ops.warm_sync", return_value=_warm_sync_stub()))
        ok_inputs = RecsSignalInputs("ok", {"open_count": 0, "aging_count": 0, "non_automatable_count": 0}, [], None, {})
        stack.enter_context(patch.object(_preflight, "fetch_recs_signal_inputs", return_value=ok_inputs))
        for target, attr, value in specs + extra:
            if (target, attr) in _DIRECT_VALUE_ATTRS:
                stack.enter_context(patch.object(target, attr, value))
            else:
                stack.enter_context(patch.object(target, attr, return_value=value))
        yield


def _run_stubbed_main(
    tmp_path: Path, capsys: pytest.CaptureFixture, overrides: dict[tuple[object, str], object] | None = None
) -> tuple[int, dict, str]:
    """Run main() with every external dependency stubbed; return (exit_code, report_dict, stdout)."""
    report_path = tmp_path / ".preflight-report.json"
    with _stubbed_main_env(report_path, overrides):
        exit_code = _preflight.main()
    stdout = capsys.readouterr().out
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return exit_code, report, stdout


class TestReportSchemaFreeze:
    """A stubbed main() run's report top-level key set equals the frozen 55-key list."""

    def test_report_key_set_matches_frozen_keys(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, report, _ = _run_stubbed_main(tmp_path, capsys)
        assert set(report.keys()) == FROZEN_REPORT_KEYS
        assert len(report.keys()) == 55


class TestFacadeCompleteness:
    """Every name on the frozen export list is getattr-able on the module AND importable via
    `from scripts.session.preflight import <name>`."""

    @pytest.mark.parametrize("name", sorted(FROZEN_EXPORTS))
    def test_getattr_on_private_handle(self, name: str) -> None:
        assert hasattr(_preflight, name), f"{name} missing from the facade module namespace"

    def test_every_frozen_export_resolves_via_standard_import(self) -> None:
        # Equivalent to `from scripts.session.preflight import <name>` for each name: Python's
        # import system resolves a plain-attribute `from X import Y` as getattr(import(X), Y).
        mod = importlib.import_module("scripts.session.preflight")
        missing = [n for n in sorted(FROZEN_EXPORTS) if not hasattr(mod, n)]
        assert not missing, f"names not resolvable via `from scripts.session.preflight import <name>`: {missing}"


class TestMockInterceptionThroughFacade:
    """Patching scripts.preflight._common._make_reader and one home-module function per domain
    intercepts through moved bodies invoked via the facade."""

    def test_common_make_reader_intercepts_moved_body(self) -> None:
        sentinel_reader = MagicMock()
        sentinel_reader.named.return_value = [{"id": "rec-common-intercept"}]
        with patch.object(_common, "_make_reader", return_value=sentinel_reader):
            result = _preflight._fetch_ci_rca_recs()
        assert result == [{"id": "rec-common-intercept"}]

    def test_one_home_module_function_per_domain_intercepts_via_main(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        overrides = {
            (env_git, "check_venv"): False,
            (aws_infra, "check_credentials"): "ok",
            (recs_cache, "_count_recommendations_reader"): (7, 1, 0, []),
            (ci_rca_signals, "_fetch_ci_rca_recs"): [{"id": "rec-ci-rca-signals-intercept"}],
            # print_ci_rca_abstention_gauge (real, not mocked) indexes window_days/
            # low_or_undetermined_count/total_count/rate -- keep the shape valid, use an
            # out-of-range count as the marker.
            (ci_rca_gauges, "_compute_ci_rca_abstention"): {
                "window_days": 14,
                "low_or_undetermined_count": 999,
                "total_count": 1000,
                "rate": 0.999,
            },
            (alerts, "_check_forward_fix_recursion"): {"marker": "alerts-intercept"},
            (priority_queue, "read_priority_queue"): [{"marker": "priority-queue-intercept"}],
            (correlation, "correlate_ci_rca_with_main"): {
                "unresolved": [],
                "likely_resolved": [{"marker": "correlation-intercept"}],
            },
            (context_docs, "read_context_files"): {"marker": "context-docs-intercept"},
            (dependabot, "check_stranded_prs"): {"marker": "dependabot-intercept"},
        }
        exit_code, report, _ = _run_stubbed_main(tmp_path, capsys, overrides=overrides)

        assert exit_code == 1  # env_git.check_venv() -> False
        assert report["venv_ok"] is False
        assert report["creds_status"] == "ok"
        assert report["open_recommendations"] == 7
        assert report["ci_rca_recs"] == [{"id": "rec-ci-rca-signals-intercept"}]
        assert report["ci_rca_abstention_gauge"] == {
            "window_days": 14,
            "low_or_undetermined_count": 999,
            "total_count": 1000,
            "rate": 0.999,
        }
        assert report["forward_fix_recursion_alert"] == {"marker": "alerts-intercept"}
        assert report["priority_queue"] == [{"marker": "priority-queue-intercept"}]
        assert report["ci_rca_likely_resolved_recs"] == [{"marker": "correlation-intercept"}]
        assert report["context"] == {"marker": "context-docs-intercept", "recent_sessions": ["stub-session"]}
        assert report["dependabot_stranded_prs"] == {"marker": "dependabot-intercept"}


class TestResidualPatchSiteClosure:
    """Full-inventory scan: zero old-namespace references to migrated symbols remain in the
    sibling test suites, across all four mock idioms."""

    _IDIOM_PATTERNS = [
        re.compile(r'patch\(\s*"session_preflight\.(\w+)'),
        re.compile(r'patch\.object\(\s*_preflight\s*,\s*"(\w+)"'),
        re.compile(r'monkeypatch\.setattr\(\s*"session_preflight\.(\w+)'),
        re.compile(r'monkeypatch\.setattr\(\s*_preflight\s*,\s*"(\w+)"'),
    ]
    # Dynamically derived (glob), not hardcoded, so this scan cannot go stale when a future
    # decomposition wave adds/removes files: rec-2709 Wave 4 (PR #597) deleted
    # test_session_preflight.py and split its content into the tests/session/preflight/ mirror
    # package -- exactly where any residual old-namespace reference would now live, so it must
    # stay in the scan set for the invariant to remain meaningful. Covers (1) any remaining flat
    # test_session_preflight_*.py sibling, excluding this scanner file itself, and (2) every
    # module in the migrated mirror package.
    _SIBLING_FILES = sorted(
        p.relative_to(Path(__file__).resolve().parent)
        for p in (
            list(Path(__file__).resolve().parent.glob("test_session_preflight_*.py"))
            + list((Path(__file__).resolve().parent / "session" / "preflight").glob("*.py"))
        )
        if p.name != Path(__file__).name
    )

    def test_zero_residual_old_namespace_patch_sites(self) -> None:
        tests_dir = Path(__file__).resolve().parent
        residual: list[str] = []
        for fname in self._SIBLING_FILES:
            path = tests_dir / fname
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in self._IDIOM_PATTERNS:
                for m in pattern.finditer(text):
                    sym = m.group(1)
                    if sym in _MOVED_SYMBOLS:
                        line_no = text[: m.start()].count("\n") + 1
                        residual.append(f"{fname}:{line_no}:{sym}")
        assert residual == [], f"residual old-namespace patch sites for migrated symbols: {residual}"


class TestStdoutSummaryPrefixFrozen:
    """The summary block's first line 'Preflight OK -> ' is frozen, line-anchored within whole
    stdout -- never process-stdout line 1, which is a blank line from the telemetry-health block."""

    def test_summary_line_present_and_not_first_stdout_line(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _, _, stdout = _run_stubbed_main(tmp_path, capsys)
        lines = stdout.splitlines()
        assert any(re.match(r"^Preflight OK -> ", line) for line in lines), "frozen summary prefix missing from stdout"
        assert lines, "stdout unexpectedly empty"
        assert not re.match(r"^Preflight OK -> ", lines[0]), "summary must not be process-stdout line 1 (frozen ordering)"


class TestArgparseFlagSurfaceFrozen:
    """CLI flag surface is exactly {--health, --roadmap-detail}; every retired flag is absent."""

    def test_help_lists_exactly_the_frozen_flags(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.session.preflight", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            timeout=30,
        )
        assert result.returncode == 0
        for flag in ("--health", "--roadmap-detail"):
            assert flag in result.stdout, f"{flag} missing from --help output"
        for flag in RETIRED_PREFLIGHT_FLAGS:
            assert flag not in result.stdout, f"retired {flag} is back in --help output"


class TestDecision88ReaderTopologyGuard:
    """Exactly one _make_reader binding lives in scripts/preflight/_common.py -- no other preflight
    module instantiates its own reader or binds make_reader/_make_reader (Decision 88 invariant ii)."""

    def test_no_reader_binding_outside_common(self) -> None:
        """AST-based (not textual): a bare `_make_reader`/`make_reader` Name node -- as opposed to
        the `.attr` string of a `_common._make_reader` Attribute access, which is not an ast.Name --
        indicates a rogue import/binding/call outside the single _common canonical target. This
        naturally ignores comments/docstrings (not part of the AST) and qualified attribute access.
        """
        preflight_dir = ROOT / "scripts" / "preflight"
        offenders: list[str] = []
        watched = {"_make_reader", "make_reader"}
        for path in sorted(preflight_dir.glob("*.py")):
            if path.name == "_common.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in watched:
                    offenders.append(f"{path.name}:{node.lineno}: bare reference to {node.id!r}")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        bound = alias.asname or alias.name
                        if bound in watched:
                            offenders.append(f"{path.name}:{node.lineno}: import binds {bound!r}")
        assert offenders == [], f"reader binding/reference found outside _common.py qualification: {offenders}"
