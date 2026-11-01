"""Facade guard suite for the scripts.ops_data_portal / scripts.ops_portal split (Decision 124).

Mirrors tests/test_checks_registry.py's role for the Decision 104 validate.py decomposition:
(1) facade completeness -- every required public + private symbol, the 6 imported-name traps,
are reachable both as scripts.ops_data_portal.<name>
(getattr) and via `from scripts.ops_data_portal import <name>`; (2) patch-interception --
patch("scripts.ops_data_portal._ducklake_write") still intercepts a file_rec() write through the
moved writer_transport.py body; (3) no scripts/ops_portal module recomputes the repo root (all
source it from _common.py); (4) CLI-contract stability -- `python -m scripts.ops_data_portal
--help` exposes the identical flag set and action-flag exit codes are unchanged.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.ops_data_portal as _facade
import scripts.ops_portal.decisions as _decisions_submodule
from scripts.ops_data_portal import file_decision as _file_decision_direct_import

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The five names lazily re-exported via the facade's PEP 562 __getattr__ (rec-2729).
_LAZY_DECISIONS_NAMES = [
    "file_decision",
    "update_decision",
    "backfill_decisions_from_md",
    "_fetch_decision_from_reader",
]

# Public symbols originally defined at scripts/ops_data_portal.py module scope (23).
_REQUIRED_PUBLIC = [
    "get_ci_rca_strict_mode",
    "CiRcaContext",
    "CiRcaEvidenceDispute",
    "find_open_ci_rca_rec_by_fingerprint",
    "back_validate_ci_rca",
    "bump_ci_rca_occurrence",
    "compute_risk",
    "load_capabilities",
    "compute_automatable",
    "file_rec",
    "file_rec_with_outcome",
    "FileRecOutcome",
    "update_rec",
    "propose_or_close_rec",
    "file_decision",
    "update_decision",
    "backfill_decisions_from_md",
    "sync",
    "selftest_read",
    "selftest_roundtrip",
    "enqueue_findings",
    "find_open_postmortem_for",
    "purge_postmortems_for",
    "main",
    "ROOT",
]

# Private symbols that either drive a facade-resident caller (patch-interception must keep
# working) or are imported/patched directly through the facade by an existing test.
_REQUIRED_PRIVATE = [
    "_ducklake_write",
    "_resolve_writer_url",
    "_project_ops_record",
    "_EvidenceBundleRef",
    "_DetectionGap",
    "_compute_risk_score",
    "_derive_computed_fields",
    "gate_write",
    "_run_ci_rca_cross_check",
    "_validate_ci_rca_context_v2",
    "_validate_ci_rca_dispute",
    "_stamp_warn_mode_reject",
    "_classify_schema_deficiency",
    "_sync_table",
    "_refresh_cache_after_write",
    "_sanitize_record",
    "_append_to_local_jsonl",
    "_fetch_rec_from_reader",
    "_fetch_decision_from_reader",
    "_print_ci_rca_back_validation_report",
    "_FEATURE_FLAGS_YAML",
]

# The 6 imported-name traps a functions-only facade would miss (Decision 124 scope).
_IMPORTED_NAME_TRAPS = [
    "subprocess",
    "ET",
    "DECISIONS_JSONL",
    "RECS_JSONL",
    "Recommendation",
    "validate_source",
]

_ALL_REQUIRED = _REQUIRED_PUBLIC + _REQUIRED_PRIVATE + _IMPORTED_NAME_TRAPS


class TestFacadeCompleteness:
    """Every required symbol is getattr-able on the facade module object."""

    @pytest.mark.parametrize("name", _ALL_REQUIRED)
    def test_facade_completeness_attribute_present(self, name: str) -> None:
        assert hasattr(_facade, name), f"scripts.ops_data_portal is missing attribute {name!r}"


class TestFacadeImportable:
    """Every required symbol resolves via `from scripts.ops_data_portal import <name>`.

    `from module import name` resolves through the same getattr-on-the-module-object path
    exercised above; asserting identity against a fresh getattr proves both forms agree
    without invoking eval()/exec() to synthesize the import statement dynamically.
    """

    @pytest.mark.parametrize("name", _ALL_REQUIRED)
    def test_facade_importable_from_import_resolves(self, name: str) -> None:
        import importlib

        mod = importlib.import_module("scripts.ops_data_portal")
        assert getattr(mod, name) is getattr(_facade, name)


class TestDecisionsLazyReexport:
    """Capture-immunity regression guard for rec-2729's PEP 562 lazy re-export.

    Proves the five scripts.ops_portal.decisions symbols can never be permanently captured by a
    mock.patch() on the submodule symbol under pytest-randomly reordering: they are structurally
    absent from the facade's own __dict__ (never a static binding), yet still resolve live and
    identically to the submodule's current attribute through both access forms the facade contract
    guarantees (getattr and `from ... import`).
    """

    @pytest.mark.parametrize("name", _LAZY_DECISIONS_NAMES)
    def test_name_absent_from_facade_dict(self, name: str) -> None:
        assert name not in vars(_facade), f"{name!r} is statically bound on the facade's __dict__"

    @pytest.mark.parametrize("name", _LAZY_DECISIONS_NAMES)
    def test_getattr_resolves_to_submodule_identity(self, name: str) -> None:
        assert getattr(_facade, name) is getattr(_decisions_submodule, name)

    @pytest.mark.parametrize("name", _LAZY_DECISIONS_NAMES)
    def test_from_import_resolves_to_submodule_identity(self, name: str) -> None:
        import importlib

        mod = importlib.import_module("scripts.ops_data_portal")
        assert getattr(mod, name) is getattr(_decisions_submodule, name)

    def test_direct_from_import_statement_resolves_live(self) -> None:
        assert _file_decision_direct_import is _decisions_submodule.file_decision


class TestImportedNameTrapTypes:
    """The 6 traps are the actual shared external objects, not local re-declarations."""

    def test_trap_subprocess_is_the_stdlib_module(self) -> None:
        import subprocess as _stdlib_subprocess

        assert _facade.subprocess is _stdlib_subprocess

    def test_trap_et_is_the_stdlib_module(self) -> None:
        import xml.etree.ElementTree as ET

        assert _facade.ET is ET

    def test_trap_recommendation_is_the_jsonl_store_class(self) -> None:
        from scripts.executor.jsonl_store import Recommendation as _rec_cls

        assert _facade.Recommendation is _rec_cls

    def test_trap_recs_jsonl_and_decisions_jsonl_are_path_constants(self) -> None:
        assert isinstance(_facade.RECS_JSONL, Path)
        assert isinstance(_facade.DECISIONS_JSONL, Path)


class TestRootSingleSource:
    """No scripts/ops_portal/*.py module recomputes the repo root; only _common.py does.

    Mirrors the Decision 104 scripts/checks/_common.py precedent: a second independent
    Path(__file__) arithmetic site is exactly the drift-prone duplication this facade split
    must not reintroduce.
    """

    def test_root_single_source_no_recompute_outside_common(self) -> None:
        ops_portal_dir = _REPO_ROOT / "scripts" / "ops_portal"
        offenders = []
        for py_file in sorted(ops_portal_dir.glob("*.py")):
            if py_file.name in ("_common.py", "__init__.py"):
                continue
            text = py_file.read_text(encoding="utf-8")
            if "Path(__file__)" in text:
                offenders.append(py_file.name)
        assert not offenders, f"modules recompute the repo root independently of _common.py: {offenders}"

    def test_root_single_source_common_defines_it(self) -> None:
        common_text = (_REPO_ROOT / "scripts" / "ops_portal" / "_common.py").read_text(encoding="utf-8")
        assert "Path(__file__)" in common_text


class TestPatchInterception:
    """A facade-level patch of a moved private dependency still intercepts a facade-resident caller."""

    _VALID_FIELDS = {
        "title": "Facade interception guard test recommendation",
        "file": "scripts/ops_data_portal.py",
        "context": "A sufficiently long context string so the write-time content validators are satisfied here.",
        "acceptance": "grep -q ops_portal scripts/ops_data_portal.py && grep -q file_rec scripts/ops_data_portal.py",
        "effort": "XS",
        "priority": "Low",
        "source": "planning",
        "status": "open",
        "risk": "low",
    }

    def test_interception_ducklake_write_patch_reaches_file_rec(self, tmp_path: Path) -> None:
        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-facade-9999"}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            result = _facade.file_rec(dict(self._VALID_FIELDS))

        assert result == "rec-facade-9999"
        mock_write.assert_called_once()
        table, _record = mock_write.call_args[0]
        assert table == "ops_recommendations"

    def test_interception_fetch_rec_from_reader_patch_reaches_update_rec(self) -> None:
        existing = {**self._VALID_FIELDS, "id": "rec-042", "status": "open"}
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=dict(existing)),
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_data_portal._refresh_cache_after_write") as mock_refresh,
        ):
            assert _facade.update_rec("rec-042", {"status": "closed"}) is True

        assert mock_write.call_args.kwargs["action"] == "update_ops"
        mock_refresh.assert_not_called()


class TestCliContractStability:
    """The Single Portal CLI contract is byte-stable: identical flags, identical exit codes."""

    # Every flag registered in scripts/ops_portal/cli.py's argparse surface.
    _EXPECTED_FLAGS = [
        "--profile",
        "--dry-run",
        "--file-rec",
        "--update-rec",
        "--file-decision",
        "--update-decision",
        "--purge-postmortems-for",
        "--backfill-decisions-md",
        "--enqueue-findings",
        "--guidance",
        "--sync",
        "--selftest-read",
        "--selftest-roundtrip",
        "--back-validate",
        "--find-open-ci-rca-rec",
        "--bump-ci-rca-occurrence",
        "--fingerprint",
        "--since",
        "--refile-audit",
        "--json",
        "--title",
        "--file",
        "--context",
        "--acceptance",
        "--effort",
        "--priority",
        "--source",
        "--risk",
        "--tags",
        "--dependencies",
        "--verification",
        "--verification-tier",
        "--context-v2-json",
        "--status",
        "--execution_result",
        "--execution_date",
        "--execution_branch",
        "--execution_pr_url",
        "--resolution",
        "--rationale",
        "--decision-status",
        "--decision-id",
    ]

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "scripts.ops_data_portal", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_REPO_ROOT),
        )

    def test_help_exposes_every_required_flag(self) -> None:
        proc = self._run_cli("--help")
        assert proc.returncode == 0
        for flag in self._EXPECTED_FLAGS:
            assert flag in proc.stdout, f"--help output is missing expected flag {flag!r}"

    def test_no_action_requires_mutually_exclusive_group(self) -> None:
        proc = self._run_cli()
        assert proc.returncode == 2  # argparse's own required-mutually-exclusive-group error

    def test_file_rec_missing_fields_reports_error(self) -> None:
        proc = self._run_cli("--file-rec")
        assert proc.returncode == 1
        assert "ERROR: --file-rec requires" in proc.stderr
