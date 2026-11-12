"""Tests for scripts/import_governance.py -- 100% coverage including anti-vacuous-pass cases."""

from __future__ import annotations

import re
import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.import_governance import (
    _fast_tier_budget_breach_open,
    _kg13_tier_item_filed,
    _normalize_pkg,
    _read_executor_concurrency,
    check_lockfile_sync,
    count_declared_requirements,
    evaluate_bazel_revisit_trigger,
    main,
    parse_declared_requirements,
    run_import_contracts,
)

ROOT = Path(__file__).parent.parent
_FOUR_NAMES = ("requirements.in", "requirements-dev.in", "requirements.txt", "requirements-dev.txt")


# ---------------------------------------------------------------------------
# tests for run_import_contracts
# ---------------------------------------------------------------------------


class TestRunImportContracts:
    def test_passes_on_clean_tree(self) -> None:
        """Contracts pass green on the unmodified repository tree."""
        passed, output = run_import_contracts()
        assert passed, f"Expected contracts to pass, got:\n{output}"
        assert "kept" in output.lower() or "KEPT" in output

    def test_negative_illegal_import_detected(self, tmp_path: Path) -> None:
        """Anti-vacuous-pass: an injected illegal import is detected by lint-imports."""
        # Create a minimal importlinter config with a forbidden contract
        importlinter_cfg = tmp_path / ".importlinter"
        importlinter_cfg.write_text(
            "[importlinter]\n"
            "root_packages =\n"
            "    mypkg\n\n"
            "[importlinter:contract:test-forbidden]\n"
            "name = src.a must not import src.b\n"
            "type = forbidden\n"
            "source_modules =\n"
            "    mypkg.a\n"
            "forbidden_modules =\n"
            "    mypkg.b\n",
            encoding="utf-8",
        )
        # Create a minimal two-module package that violates the contract
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "b.py").write_text("X = 1\n", encoding="utf-8")
        (pkg / "a.py").write_text("from mypkg import b  # forbidden import\n", encoding="utf-8")

        lint_imports_bin = Path(sys.executable).parent / "lint-imports"
        cmd: list[str] = [str(lint_imports_bin)] if lint_imports_bin.exists() else ["lint-imports"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=tmp_path,
        )
        assert result.returncode != 0, "Expected lint-imports to report a violation for the injected forbidden import"
        combined = result.stdout + result.stderr
        assert "broken" in combined.lower() or "BROKEN" in combined

    def test_contracts_invoke_lint_imports(self) -> None:
        """run_import_contracts shells out to lint-imports; verify the returned output is non-empty."""
        passed, output = run_import_contracts()
        assert len(output) > 0, "Expected non-empty output from lint-imports"

    def test_lint_imports_not_found_returns_false(self) -> None:
        """run_import_contracts returns (False, message) when lint-imports binary is missing."""
        with patch("scripts.import_governance.subprocess.run", side_effect=FileNotFoundError("lint-imports: not found")):
            passed, output = run_import_contracts()
        assert not passed
        assert "lint-imports not found" in output


# ---------------------------------------------------------------------------
# check_lockfile_sync
# ---------------------------------------------------------------------------


class TestCheckLockfileSync:
    """The gate reads FLOORS from the requirements*.in inputs and PINS from the compiled
    requirements*.txt outputs, driven through the public `paths` parameter (declarations first,
    compiled outputs second)."""

    @staticmethod
    def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        return (
            tmp_path / "requirements.in",
            tmp_path / "requirements-dev.in",
            tmp_path / "requirements.txt",
            tmp_path / "requirements-dev.txt",
        )

    def _gate(
        self,
        tmp_path: Path,
        floors: str,
        pins: str,
        dev_floors: str = "",
        dev_pins: str = "",
    ) -> tuple[bool, str]:
        paths = self._paths(tmp_path)
        for path, text in zip(paths, (floors, dev_floors, pins, dev_pins), strict=True):
            path.write_text(text, encoding="utf-8")
        return check_lockfile_sync(paths=paths)

    def test_passes_on_committed_outputs(self) -> None:
        """check_lockfile_sync passes against the live tree with no arguments."""
        in_sync, message = check_lockfile_sync()
        assert in_sync, f"Expected the compiled outputs to be in sync, got: {message}"
        assert "pins all" in message
        assert "requirements.in" in message and "requirements-dev.in" in message, message
        assert "requirements.txt" in message and "requirements-dev.txt" in message, message

    def test_missing_dev_pin_fails(self, tmp_path: Path) -> None:
        in_sync, message = self._gate(tmp_path, "requests>=2.0\n", "requests==2.31.0\n", dev_floors="pytest>=9.0\n")
        assert not in_sync
        assert "pytest" in message

    def test_extras_pin_fails(self, tmp_path: Path) -> None:
        """A compiled pin carrying extras fails: pip rejects extras in constraints files."""
        in_sync, message = self._gate(tmp_path, "requests>=2.0\n", "requests==2.31.0\npyjwt[crypto]==2.13.0\n")
        assert not in_sync
        assert "extras" in message
        assert "pyjwt[crypto]==2.13.0" in message

    @pytest.mark.parametrize("absent_index", [0, 1, 2, 3])
    def test_every_one_of_the_four_files_is_required(self, tmp_path: Path, absent_index: int) -> None:
        """All FOUR files are required -- a missing one is a hard failure, not an optional-dev skip.

        The `-c requirements.txt` constraint form makes requirements-dev.txt a committed compiled
        output that is always present, so the pre-migration optional-dev tolerance is gone.
        """
        paths = self._paths(tmp_path)
        for index, path in enumerate(paths):
            if index != absent_index:
                path.write_text("", encoding="utf-8")
        in_sync, msg = check_lockfile_sync(paths=paths)
        assert not in_sync, msg
        assert "not found" in msg and paths[absent_index].name in msg, msg

    def test_cross_output_disagreement_fails(self, tmp_path: Path) -> None:
        """One distribution pinned at two versions across the compiled outputs is a hard failure.

        This replaces the coherence the retired single-resolve lockfile provided: anyio is reached
        by mcp on the prod side and by httpx/openai on the dev side, so a one-sided recompile would
        otherwise install two versions of one distribution without any gate noticing.
        """
        in_sync, msg = self._gate(
            tmp_path,
            "mcp>=1.28.0\n",
            "mcp==1.28.1\nanyio==4.14.2\n",
            dev_floors="litellm>=1.0\n",
            dev_pins="litellm==1.93.0\nanyio==4.0.0\n",
        )
        assert not in_sync, msg
        assert "disagree across outputs" in msg and "anyio" in msg, msg
        assert "4.14.2" in msg and "4.0.0" in msg, msg

    def test_cross_output_agreement_passes(self, tmp_path: Path) -> None:
        """Control: the same distribution at the SAME version in both outputs is not a disagreement."""
        in_sync, msg = self._gate(
            tmp_path,
            "mcp>=1.28.0\n",
            "mcp==1.28.1\nanyio==4.14.2\n",
            dev_floors="litellm>=1.0\n",
            dev_pins="litellm==1.93.0\nanyio==4.14.2\n",
        )
        assert in_sync, msg

    def test_wrong_path_count_fails(self, tmp_path: Path) -> None:
        in_sync, msg = check_lockfile_sync(paths=(tmp_path / "requirements.in",))
        assert not in_sync
        assert "expects 4 paths" in msg, msg

    def test_missing_top_level_package_fails(self, tmp_path: Path) -> None:
        """A declared floor with no compiled pin fails."""
        in_sync, msg = self._gate(tmp_path, "requests>=2.0\nmypackage>=1.0\n", "requests==2.31.0\n")
        assert not in_sync
        assert "mypackage" in msg and "missing" in msg.lower()

    def test_extras_are_normalized(self, tmp_path: Path) -> None:
        """A floor with extras matches its stripped compiled pin.

        The pin side must be extras-free (pip rejects extras in constraints files; see
        test_extras_pin_fails) -- pip-compile --strip-extras pins the bare name, and the sync
        check matches it against the extras-carrying declaration.
        """
        in_sync, msg = self._gate(tmp_path, "uvicorn[standard,http2]>=0.11.1\n", "uvicorn==0.11.1\n")
        assert in_sync, f"Expected extras-normalized package to be found; got: {msg}"

    def test_incompatible_major_pin_fails(self, tmp_path: Path) -> None:
        in_sync, msg = self._gate(tmp_path, "mcp>=1.28.0,<2\n", "mcp==2.0.0\n")
        assert not in_sync
        assert "mcp<2,>=1.28.0 rejects 2.0.0" in msg

    def test_constraint_option_line_is_not_a_declaration(self, tmp_path: Path) -> None:
        """requirements-dev.in's leading `-c requirements.txt` is an option, never a floor."""
        in_sync, msg = self._gate(
            tmp_path,
            "requests>=2.0\n",
            "requests==2.31.0\n",
            dev_floors="-c requirements.txt\npytest>=9.0\n",
            dev_pins="pytest==9.1.1\n",
        )
        assert in_sync, msg
        assert "pins all 2 declared floors" in msg, msg

    @pytest.mark.parametrize("requirements_file", ["requirements.in", "requirements-fast.txt"])
    def test_mcp_declaration_admits_the_compiled_pin(self, requirements_file: str) -> None:
        """The declared floor must admit whatever mcp version is actually pinned in the compiled
        requirements.txt. The probe is an INDEPENDENT oracle taken from the compiled pin itself --
        never re-derived from the declared floor, which would make this tautological for every
        `>=floor,<2` form (Decision 181 clause 1) and would go green-by-construction the moment the
        floor bumps alongside it."""
        from packaging.requirements import Requirement
        from packaging.version import Version

        declaration = next(
            line for line in Path(requirements_file).read_text(encoding="utf-8").splitlines() if line.startswith("mcp")
        )
        pin_line = next(
            line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.startswith("mcp==")
        )
        pin = Version(pin_line.split("==")[1].strip())
        assert pin in Requirement(declaration).specifier, f"declared floor {declaration!r} rejects compiled pin {pin}"

    @pytest.mark.parametrize("requirements_file", ["requirements.in", "requirements-fast.txt"])
    def test_mcp_declaration_rejects_the_next_major(self, requirements_file: str) -> None:
        from packaging.requirements import Requirement
        from packaging.version import Version

        declaration = next(
            line for line in Path(requirements_file).read_text(encoding="utf-8").splitlines() if line.startswith("mcp")
        )
        pin_line = next(
            line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.startswith("mcp==")
        )
        pin = Version(pin_line.split("==")[1].strip())
        assert Version(f"{pin.major + 1}.0.0") not in Requirement(declaration).specifier

    def test_pytz_is_a_direct_requirement_not_a_transitive_survivor(self) -> None:
        """pytz must be DECLARED in requirements.in and pinned in the compiled requirements.txt.

        duckdb soft-imports pytz when it converts tz-aware timestamps (the DuckLake read path
        scripts/session/preflight.py serves from cache), but declares no hard dependency on it --
        the same soft-import scripts/build_lambda_config.py's DUCKLAKE_DEPS already pins for the
        Lambda layer. While pytz survived in the pinned closure only as another package's
        transitive pin, the repo cleanse that removed that parent silently deleted pytz too, and
        every CI install lost the module (red main-validate on c19328d). Asserting the
        `-r requirements.in` provenance -- not merely the pin's presence -- is what stops pytz from
        regressing back to a transitive survivor.
        """
        from packaging.requirements import Requirement
        from packaging.version import Version

        declaration = next(
            line for line in (ROOT / "requirements.in").read_text(encoding="utf-8").splitlines() if line.startswith("pytz")
        )
        specifier = Requirement(declaration.split("#")[0].strip()).specifier

        compiled = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        pin_index = next(i for i, line in enumerate(compiled) if line.startswith("pytz=="))
        pinned = Version(compiled[pin_index].split("==")[1].strip())
        assert pinned in specifier, f"compiled pin {pinned} does not satisfy requirements.in {specifier}"
        assert compiled[pin_index + 1].strip() == "# via -r requirements.in", (
            f"pytz must be pinned as a direct requirement, not a transitive survivor: got {compiled[pin_index + 1]!r}"
        )

    def test_comments_and_blanks_skipped(self, tmp_path: Path) -> None:
        """Comments and blank lines in the .in inputs are ignored."""
        in_sync, msg = self._gate(
            tmp_path, "# core\nrequests>=2.0\n\n# dev\npytest>=7.0\n", "requests==2.31.0\npytest==7.4.0\n"
        )
        assert in_sync

    @pytest.mark.parametrize("invalid_pin_line", ["not a requirement", "requests==not-a-version", "requests==1.*"])
    def test_invalid_compiled_entries_do_not_count_as_pins(self, tmp_path: Path, invalid_pin_line: str) -> None:
        in_sync, msg = self._gate(tmp_path, "requests>=2.0\n", f"{invalid_pin_line}\n")
        assert not in_sync
        assert "missing pins for: requests" in msg

    def test_absurd_bump_with_trailing_comment_fails(self, tmp_path: Path) -> None:
        """An inline comment must not hide an absurd floor from the gate (the pre-fix silent skip)."""
        in_sync, msg = self._gate(tmp_path, "duckdb>=99.0  # inline comment\n", "duckdb==1.5.4\n")
        assert not in_sync, msg
        assert "incompatible pins" in msg and "duckdb>=99.0 rejects 1.5.4" in msg, msg

    def test_absurd_bump_without_comment_fails_identically(self, tmp_path: Path) -> None:
        """Control: the uncommented form already fails through the specifier-compatibility branch."""
        in_sync, msg = self._gate(tmp_path, "duckdb>=99.0\n", "duckdb==1.5.4\n")
        assert not in_sync, msg
        assert "incompatible pins" in msg and "duckdb>=99.0 rejects 1.5.4" in msg, msg

    def test_unparseable_declaration_fails_not_skipped(self, tmp_path: Path) -> None:
        """A line packaging cannot parse is a FAIL naming the line, never a silent skip."""
        in_sync, msg = self._gate(tmp_path, "not a requirement\nrequests>=2.0\n", "requests==2.31.0\n")
        assert not in_sync, msg
        assert "not a requirement" in msg, msg

    def test_hash_without_preceding_whitespace_is_not_a_comment(self, tmp_path: Path) -> None:
        """pip's comment rule needs whitespace before '#': `foo>=1.0#x` is unparseable, so the gate FAILS."""
        in_sync, msg = self._gate(tmp_path, "foo>=1.0#x\n", "foo==1.0.0\n")
        assert not in_sync, msg
        assert "foo>=1.0#x" in msg, msg

    def test_commented_declarations_are_counted(self, tmp_path: Path) -> None:
        """The four real inline-comment shapes are parsed and counted, not skipped."""
        floors = (
            "psycopg2-binary>=2.9.12  # Neon catalog connections\n"
            "duckdb>=1.5.4  # generated by scripts/sync/ducklake_version.py\n"
            "python-ulid>=3.1.0  # monotonic ULID generation\n"
            "pytz>=2026.2  # duckdb soft-imports pytz\n"
        )
        pins = "psycopg2-binary==2.9.12\nduckdb==1.5.4\npython-ulid==3.1.0\npytz==2026.2\n"
        in_sync, msg = self._gate(tmp_path, floors, pins)
        assert in_sync, msg
        assert "pins all 4 declared floors" in msg, msg

    def test_a_missing_in_input_is_skipped_by_the_parser_not_raised(self, tmp_path: Path) -> None:
        """REGRESSION (code-review round 1, Medium): the parser must SKIP a missing input rather than
        raise. check_lockfile_sync hard-fails on it separately (its `absent` guard), but the
        registered wrapper calls the shared count helper for its accounting declaration on exactly
        that failure path -- raising there aborts the check before it can append to `failed`."""
        present = tmp_path / "requirements-dev.in"
        present.write_text("pytest>=9.0\n", encoding="utf-8")
        declared, unparseable = parse_declared_requirements((tmp_path / "requirements.in", present))
        assert set(declared) == {"pytest"}
        assert unparseable == []

    def test_the_count_helper_is_zero_when_no_input_exists(self, tmp_path: Path) -> None:
        with patch("scripts.import_governance._LOCKFILE_PATHS", tuple(tmp_path / n for n in _FOUR_NAMES)):
            assert count_declared_requirements() == 0

    def test_live_declarations_all_parsed(self) -> None:
        """Every declaration in the live .in inputs is parsed: declared count == gate-reported count."""
        comment = re.compile(r"(^|\s+)#.*$")
        declared = 0
        for name in ("requirements.in", "requirements-dev.in"):
            for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
                line = comment.sub("", raw).strip()
                if line and not line.startswith("-"):
                    declared += 1
        in_sync, msg = check_lockfile_sync()
        assert in_sync, msg
        assert f"pins all {declared} declared floors" in msg, f"declared {declared}: {msg}"
        assert count_declared_requirements() == declared, "the shared helper must agree with the gate"


# ---------------------------------------------------------------------------
# evaluate_bazel_revisit_trigger
# ---------------------------------------------------------------------------


class TestEvaluateBazelRevisitTrigger:
    def test_dormant_at_concurrency_one(self) -> None:
        """Trigger is dormant when executor concurrency == 1 (current state)."""
        with patch("scripts.import_governance._read_executor_concurrency", return_value=1):
            fired, msg = evaluate_bazel_revisit_trigger()
        assert not fired
        assert "DORMANT" in msg
        assert "concurrency=1" in msg

    def test_does_not_fire_when_concurrency_gt1_but_no_second_condition(self) -> None:
        """Trigger stays dormant when concurrency > 1 but neither KG.13 nor breach is present."""
        with (
            patch("scripts.import_governance._read_executor_concurrency", return_value=2),
            patch("scripts.import_governance._kg13_tier_item_filed", return_value=False),
            patch("scripts.import_governance._fast_tier_budget_breach_open", return_value=False),
        ):
            fired, msg = evaluate_bazel_revisit_trigger()
        assert not fired
        assert "DORMANT" in msg

    def test_fires_when_concurrency_gt1_and_kg13_filed(self) -> None:
        """Trigger fires when concurrency > 1 AND KG.13 is filed."""
        with (
            patch("scripts.import_governance._read_executor_concurrency", return_value=2),
            patch("scripts.import_governance._kg13_tier_item_filed", return_value=True),
            patch("scripts.import_governance._fast_tier_budget_breach_open", return_value=False),
        ):
            fired, msg = evaluate_bazel_revisit_trigger()
        assert fired
        assert "ADVISORY" in msg
        assert "KG.13" in msg

    def test_fires_when_concurrency_gt1_and_budget_breach(self) -> None:
        """Trigger fires when concurrency > 1 AND a budget breach is open."""
        with (
            patch("scripts.import_governance._read_executor_concurrency", return_value=3),
            patch("scripts.import_governance._kg13_tier_item_filed", return_value=False),
            patch("scripts.import_governance._fast_tier_budget_breach_open", return_value=True),
        ):
            fired, msg = evaluate_bazel_revisit_trigger()
        assert fired
        assert "ADVISORY" in msg
        assert "breach" in msg.lower() or "budget" in msg.lower()

    def test_advisory_message_does_not_auto_act(self) -> None:
        """Fired trigger message is advisory only -- no auto-action language."""
        with (
            patch("scripts.import_governance._read_executor_concurrency", return_value=2),
            patch("scripts.import_governance._kg13_tier_item_filed", return_value=True),
            patch("scripts.import_governance._fast_tier_budget_breach_open", return_value=False),
        ):
            fired, msg = evaluate_bazel_revisit_trigger()
        assert fired
        assert "No automatic action" in msg or "Decision 55" in msg


# ---------------------------------------------------------------------------
# helper functions
# ---------------------------------------------------------------------------


class TestNormalizePkg:
    def test_lowercases(self) -> None:
        assert _normalize_pkg("Requests") == "requests"

    def test_replaces_hyphens(self) -> None:
        assert _normalize_pkg("import-linter") == "import_linter"

    def test_replaces_dots(self) -> None:
        assert _normalize_pkg("zope.interface") == "zope_interface"


class TestReadExecutorConcurrency:
    def test_returns_one_when_no_capabilities(self, tmp_path: Path) -> None:
        with patch("scripts.import_governance.ROOT", tmp_path):
            val = _read_executor_concurrency()
        assert val == 1

    def test_reads_concurrency_from_yaml(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "config" / "agent" / "executor"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "capabilities.yaml").write_text("concurrency: 4\n", encoding="utf-8")
        with patch("scripts.import_governance.ROOT", tmp_path):
            val = _read_executor_concurrency()
        assert val == 4

    def test_defaults_to_one_on_parse_error(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "config" / "agent" / "executor"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "capabilities.yaml").write_text("not: valid: yaml: :\n", encoding="utf-8")
        with patch("scripts.import_governance.ROOT", tmp_path):
            val = _read_executor_concurrency()
        assert val == 1


class TestKg13TierItemFiled:
    def test_false_when_no_roadmap(self, tmp_path: Path) -> None:
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert not _kg13_tier_item_filed()

    def test_true_when_id_present(self, tmp_path: Path) -> None:
        roadmap = tmp_path / "docs" / "ROADMAP-PLATFORM.yaml"
        roadmap.parent.mkdir(parents=True)
        roadmap.write_text("tier_items:\n  - id: KG.13\n    name: foo\n", encoding="utf-8")
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert _kg13_tier_item_filed()

    def test_false_when_id_absent(self, tmp_path: Path) -> None:
        roadmap = tmp_path / "docs" / "ROADMAP-PLATFORM.yaml"
        roadmap.parent.mkdir(parents=True)
        roadmap.write_text("tier_items:\n  - id: T3.11\n    name: foo\n", encoding="utf-8")
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert not _kg13_tier_item_filed()

    def test_false_when_roadmap_read_fails(self, tmp_path: Path) -> None:
        roadmap = tmp_path / "docs" / "ROADMAP-PLATFORM.yaml"
        roadmap.parent.mkdir(parents=True)
        roadmap.touch()
        with (
            patch("scripts.import_governance.ROOT", tmp_path),
            patch.object(Path, "read_text", side_effect=OSError("unreadable")),
        ):
            assert not _kg13_tier_item_filed()


class TestFastTierBudgetBreachOpen:
    def test_false_when_no_log(self, tmp_path: Path) -> None:
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert not _fast_tier_budget_breach_open()

    def test_true_when_open_budget_breach_rec(self, tmp_path: Path) -> None:
        import json

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log = log_dir / ".recommendations-log.jsonl"
        log.write_text(
            json.dumps({"status": "open", "title": "fast tier budget breach exceeded"}) + "\n",
            encoding="utf-8",
        )
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert _fast_tier_budget_breach_open()

    def test_false_when_rec_is_closed(self, tmp_path: Path) -> None:
        import json

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log = log_dir / ".recommendations-log.jsonl"
        log.write_text(
            json.dumps({"status": "closed", "title": "fast tier budget breach exceeded"}) + "\n",
            encoding="utf-8",
        )
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert not _fast_tier_budget_breach_open()

    def test_invalid_json_line_is_ignored(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / ".recommendations-log.jsonl").write_text("not-json\n", encoding="utf-8")
        with patch("scripts.import_governance.ROOT", tmp_path):
            assert not _fast_tier_budget_breach_open()

    def test_false_when_log_read_fails(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log = log_dir / ".recommendations-log.jsonl"
        log.touch()
        with (
            patch("scripts.import_governance.ROOT", tmp_path),
            patch.object(Path, "open", side_effect=OSError("unreadable")),
        ):
            assert not _fast_tier_budget_breach_open()


class TestMain:
    def test_check_contracts_dispatch(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(sys, "argv", ["import_governance", "--check-contracts"])
        with patch("scripts.import_governance.run_import_contracts", return_value=(True, "contracts ok\n")):
            with pytest.raises(SystemExit, match="0"):
                main()
        assert capsys.readouterr().out == "contracts ok\n"

    def test_check_lockfile_dispatch_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["import_governance", "--check-lockfile"])
        with patch("scripts.import_governance.check_lockfile_sync", return_value=(False, "lock drift")):
            with pytest.raises(SystemExit, match="1"):
                main()
        assert capsys.readouterr().out == "lock drift\n"

    def test_revisit_trigger_dispatch(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(sys, "argv", ["import_governance", "--revisit-trigger"])
        with patch("scripts.import_governance.evaluate_bazel_revisit_trigger", return_value=(False, "dormant")):
            with pytest.raises(SystemExit, match="0"):
                main()
        assert capsys.readouterr().out == "dormant\n"

    @pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
    def test_module_entrypoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["import_governance", "--revisit-trigger"])
        with pytest.raises(SystemExit, match="0"):
            runpy.run_module("scripts.import_governance", run_name="__main__")
