"""V2 tests for validate_prose_limits() -- Decision 43/127 ambient-prose byte-budget gate.

Exercises the REAL scripts.preflight.prose_context.measure_prose_context() code path against
controlled tmp-tree fixtures: scripts.checks._common.ROOT (this module's own budget-file lookup)
and scripts.preflight.prose_context._common.ROOT (the measurement resolvers) are both patched to
tmp_path, and the S2 resolver (the only one that depends on git) is patched directly to bypass the
git-repo dependency -- per the plan's "patch _common.ROOT + prose_context resolvers" strategy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks import registry
from scripts.checks.prose.prose_limits import validate_prose_limits


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_budgets(tmp_path: Path, body: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "prose_budgets.yaml").write_text(body, encoding="utf-8")


class TestValidateProseLimits:
    """Tests for validate_prose_limits() over controlled tmp-tree fixtures."""

    def _write_s1(self, tmp_path: Path, s1_bytes: int = 10) -> None:
        """Minimal S1 root load-set: a CLAUDE.md with no @-imports."""
        _write(tmp_path / "CLAUDE.md", "x" * s1_bytes)

    def _run(self, tmp_path: Path, failed: list[str], s2_files: list[Path] | None = None) -> None:
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.preflight.prose_context._common.ROOT", tmp_path),
            patch(
                "scripts.preflight.prose_context._resolve_s2_directory_claude_files",
                return_value=s2_files or [],
            ),
        ):
            validate_prose_limits(failed)

    def test_s1_over_budget_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path, s1_bytes=100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S1 root_ambient_load_set: 100 bytes exceeds budget 50" in capsys.readouterr().out
        assert "Prose size budgets" in failed[0]

    def test_s1_exactly_at_budget_passes_silently(self, tmp_path: Path) -> None:
        self._write_s1(tmp_path, s1_bytes=10)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert failed == []

    def test_s1_unregistered_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path, s1_bytes=10)
        _write_budgets(tmp_path, "S2:\n  x: 1\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S1 root_ambient_load_set: 10 bytes has no entry" in capsys.readouterr().out

    def test_no_budgets_file_fails_as_unregistered(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """No config/prose_budgets.yaml at all -- the S1 aggregate is unregistered."""
        self._write_s1(tmp_path, s1_bytes=10)

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S1 root_ambient_load_set: 10 bytes has no entry" in capsys.readouterr().out

    def test_empty_budgets_file_is_treated_as_all_unregistered(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._write_s1(tmp_path, s1_bytes=10)
        _write_budgets(tmp_path, "")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S1 root_ambient_load_set: 10 bytes has no entry" in capsys.readouterr().out

    def test_null_valued_surface_group_treated_as_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A present-but-null surface group (e.g. 'S2:' with no children) behaves like an
        absent one -- covers _load_prose_budgets()'s `v or {}` normalisation."""
        self._write_s1(tmp_path, s1_bytes=10)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 5)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS2:\n")

        failed: list[str] = []
        self._run(tmp_path, failed, s2_files=[tmp_path / "config" / "CLAUDE.md"])

        assert len(failed) == 1  # config/CLAUDE.md unregistered

    def test_s2_over_budget_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS2:\n  config/CLAUDE.md: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed, s2_files=[tmp_path / "config" / "CLAUDE.md"])

        assert len(failed) == 1
        assert "S2 config/CLAUDE.md: 100 bytes exceeds budget 50" in capsys.readouterr().out

    def test_s2_unregistered_surface_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\n")

        failed: list[str] = []
        self._run(tmp_path, failed, s2_files=[tmp_path / "config" / "CLAUDE.md"])

        assert len(failed) == 1
        assert "S2 config/CLAUDE.md: 100 bytes has no entry" in capsys.readouterr().out

    def test_s2_at_budget_passes(self, tmp_path: Path) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 50)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS2:\n  config/CLAUDE.md: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed, s2_files=[tmp_path / "config" / "CLAUDE.md"])

        assert failed == []

    def test_s1_member_deduped_out_of_s2_pass(self, tmp_path: Path) -> None:
        """A file returned by BOTH the S1 and S2 resolvers is gated once (via S1), never
        double-gated -- and never flagged as an unregistered S2 surface -- in the S2 pass."""
        self._write_s1(tmp_path, s1_bytes=10)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\n")  # deliberately no S2 entry

        failed: list[str] = []
        # Same file the S1 resolver already returned -- would FAIL as unregistered without dedup.
        self._run(tmp_path, failed, s2_files=[tmp_path / "CLAUDE.md"])

        assert failed == []

    def test_s4_over_budget_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / ".claude" / "skills" / "planning" / "SKILL.md", "x" * 100)
        _write_budgets(
            tmp_path,
            "S1:\n  root_ambient_load_set: 10\nS4:\n  .claude/skills/planning/SKILL.md: 50\n",
        )

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S4 .claude/skills/planning/SKILL.md: 100 bytes exceeds budget 50" in capsys.readouterr().out

    def test_s4_unregistered_surface_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / ".claude" / "skills" / "planning" / "SKILL.md", "x" * 100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S4 .claude/skills/planning/SKILL.md: 100 bytes has no entry" in capsys.readouterr().out

    def test_s8_over_budget_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / "docs" / "PROJECT_CONTEXT.md", "x" * 100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS8:\n  docs/PROJECT_CONTEXT.md: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S8 docs/PROJECT_CONTEXT.md: 100 bytes exceeds budget 50" in capsys.readouterr().out

    def test_s8_unregistered_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_s1(tmp_path)
        _write(tmp_path / "docs" / "PROJECT_CONTEXT.md", "x" * 100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert len(failed) == 1
        assert "S8 docs/PROJECT_CONTEXT.md: 100 bytes has no entry" in capsys.readouterr().out

    def test_all_four_surfaces_registered_and_within_budget_passes(self, tmp_path: Path) -> None:
        self._write_s1(tmp_path, s1_bytes=10)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 20)
        _write(tmp_path / ".claude" / "skills" / "planning" / "SKILL.md", "x" * 30)
        _write(tmp_path / "docs" / "PROJECT_CONTEXT.md", "x" * 40)
        _write_budgets(
            tmp_path,
            "S1:\n  root_ambient_load_set: 10\n"
            "S2:\n  config/CLAUDE.md: 20\n"
            "S4:\n  .claude/skills/planning/SKILL.md: 30\n"
            "S8:\n  docs/PROJECT_CONTEXT.md: 40\n",
        )

        failed: list[str] = []
        self._run(tmp_path, failed, s2_files=[tmp_path / "config" / "CLAUDE.md"])

        assert failed == []

    def test_relief_valve_message_names_relocate_defer_raise_never_split_or_decompose(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._write_s1(tmp_path, s1_bytes=100)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        out = capsys.readouterr().out.lower()
        assert len(failed) == 1
        assert "relocate" in out
        assert "defer" in out
        assert "raise" in out
        assert "split" not in out
        assert "decompose" not in out


class TestUnderBudgetFails:
    """An entry measured below its budget FAILS and names the exact reseed value (rec-4779)."""

    @staticmethod
    def _run(tmp_path: Path, failed: list[str], s2_files: list[Path] | None = None) -> None:
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.preflight.prose_context._common.ROOT", tmp_path),
            patch("scripts.preflight.prose_context._resolve_s2_directory_claude_files", return_value=s2_files or []),
        ):
            validate_prose_limits(failed)

    def test_s1_under_budget_fails_with_reseed_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 10)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 500\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        out = capsys.readouterr().out
        assert len(failed) == 1
        assert "S1 root_ambient_load_set: 10 bytes below budget 500" in out
        assert "lower the entry to 10 in config/prose_budgets.yaml" in out

    def test_s2_under_budget_fails_with_reseed_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 10)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 20)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS2:\n  config/CLAUDE.md: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed, s2_files=[tmp_path / "config" / "CLAUDE.md"])

        out = capsys.readouterr().out
        assert len(failed) == 1
        assert "S2 config/CLAUDE.md: 20 bytes below budget 50" in out
        assert "lower the entry to 20" in out

    def test_s4_under_budget_fails_with_reseed_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 10)
        _write(tmp_path / ".claude" / "skills" / "planning" / "SKILL.md", "x" * 50)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS4:\n  .claude/skills/planning/SKILL.md: 100\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        out = capsys.readouterr().out
        assert len(failed) == 1
        assert "S4 .claude/skills/planning/SKILL.md: 50 bytes below budget 100" in out
        assert "lower the entry to 50" in out

    def test_s8_under_budget_fails_with_reseed_value(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 10)
        _write(tmp_path / "docs" / "PROJECT_CONTEXT.md", "x" * 10)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\nS8:\n  docs/PROJECT_CONTEXT.md: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        out = capsys.readouterr().out
        assert len(failed) == 1
        assert "S8 docs/PROJECT_CONTEXT.md: 10 bytes below budget 50" in out
        assert "lower the entry to 10" in out

    def test_exactly_at_budget_passes_without_output_of_below_budget(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 10)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 10\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        assert failed == []
        assert "below budget" not in capsys.readouterr().out

    def test_over_budget_and_unregistered_still_fail_with_relief_valves(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 100)
        _write(tmp_path / "docs" / "PROJECT_CONTEXT.md", "x" * 10)
        _write_budgets(tmp_path, "S1:\n  root_ambient_load_set: 50\n")

        failed: list[str] = []
        self._run(tmp_path, failed)

        out = capsys.readouterr().out
        assert len(failed) == 1
        assert "S1 root_ambient_load_set: 100 bytes exceeds budget 50" in out
        assert "S8 docs/PROJECT_CONTEXT.md: 10 bytes has no entry" in out
        assert out.count("Relief valves:") == 2


class TestExaminedDeclaration:
    """validate_prose_limits declares the surfaces it examined on the passing and failing exit alike."""

    @staticmethod
    def _declaration(tmp_path: Path, failed: list[str]) -> tuple[str, str, int]:
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.preflight.prose_context._common.ROOT", tmp_path),
            patch(
                "scripts.preflight.prose_context._resolve_s2_directory_claude_files",
                return_value=[tmp_path / "config" / "CLAUDE.md"],
            ),
            registry.outcome_scope("validate_prose_limits"),
        ):
            validate_prose_limits(failed)
            declaration = registry.pop_declaration()
        assert declaration is not None
        return declaration.kind, declaration.unit, declaration.count

    @staticmethod
    def _fixture(tmp_path: Path, s2_budget: int) -> None:
        _write(tmp_path / "CLAUDE.md", "x" * 10)
        _write(tmp_path / "config" / "CLAUDE.md", "x" * 20)
        _write(tmp_path / ".claude" / "skills" / "planning" / "SKILL.md", "x" * 30)
        _write(tmp_path / "docs" / "PROJECT_CONTEXT.md", "x" * 40)
        _write_budgets(
            tmp_path,
            "S1:\n  root_ambient_load_set: 10\n"
            f"S2:\n  config/CLAUDE.md: {s2_budget}\n"
            "S4:\n  .claude/skills/planning/SKILL.md: 30\n"
            "S8:\n  docs/PROJECT_CONTEXT.md: 40\n",
        )

    def test_passing_run_declares_all_four_examined(self, tmp_path: Path) -> None:
        self._fixture(tmp_path, s2_budget=20)
        failed: list[str] = []
        assert self._declaration(tmp_path, failed) == ("examined", "prose_surfaces", 4)
        assert failed == []

    def test_failing_run_still_declares_its_count(self, tmp_path: Path) -> None:
        self._fixture(tmp_path, s2_budget=5)
        failed: list[str] = []
        assert self._declaration(tmp_path, failed) == ("examined", "prose_surfaces", 4)
        assert len(failed) == 1
