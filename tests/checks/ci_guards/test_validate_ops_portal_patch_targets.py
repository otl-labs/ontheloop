"""Tests for validate_ops_portal_patch_targets() (rec-2637 caller-aware facade-patch guard)."""

from pathlib import Path
from unittest.mock import patch

from scripts.checks.ci_guards.validate_ops_portal_patch_targets import _find_violations as _ops_portal_patch_violations
from scripts.checks.ci_guards.validate_ops_portal_patch_targets import validate_ops_portal_patch_targets


class TestValidateOpsPortalPatchTargets:
    """Tests for validate_ops_portal_patch_targets() (rec-2637 caller-aware facade-patch guard).

    Exercises the pure _find_violations(paths) core directly on synthetic temp files: a
    stale facade patch on a moved caller (file_decision) is rejected, the corrected
    submodule patch passes, and a facade-resident caller (file_rec) patching the same
    symbol name at the facade namespace is NOT flagged (caller-aware, not symbol-only).
    """

    def _write(self, tmp_path: Path, name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_ops_portal_patch_targets_stale_facade_patch_is_flagged(self, tmp_path: Path) -> None:
        body = (
            "from unittest.mock import patch\n"
            "def test_x():\n"
            "    with patch('scripts.ops_data_portal._ducklake_write'):\n"
            "        file_decision({'title': 'd'}, _skip_sync=True)\n"
        )
        path = self._write(tmp_path, "test_a.py", body)
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = _ops_portal_patch_violations([path])
        assert len(violations) == 1
        assert "file_decision" in violations[0]

    def test_facade_patch_of_gate_write_is_flagged(self, tmp_path: Path) -> None:
        """file_decision and update_decision resolve gate_write at scripts.ops_portal.decisions: a facade patch of it never
        intercepts the call, so a test exercising either that patches scripts.ops_data_portal.gate_write is flagged."""
        for caller in ("file_decision({'title': 'd'}, _skip_sync=True)", "update_decision('dec-001', {'status': 'x'})"):
            body = (
                "from unittest.mock import patch\n"
                "def test_x():\n"
                "    with patch('scripts.ops_data_portal.gate_write'):\n"
                f"        {caller}\n"
            )
            path = self._write(tmp_path, f"test_{caller[:6]}.py", body)
            with patch("scripts.checks._common.ROOT", tmp_path):
                violations = _ops_portal_patch_violations([path])
            assert len(violations) == 1 and "gate_write" in violations[0], caller

    def test_attribute_calls_dynamic_targets_and_unparseable_files_are_tolerated(self, tmp_path: Path) -> None:
        """A moved caller invoked as an attribute is recognised, a non-literal patch target is skipped rather than
        guessed, and a file that does not parse is skipped, not raised."""
        flagged = self._write(
            tmp_path,
            "test_e.py",
            "from unittest.mock import patch\n"
            "def test_x(target):\n"
            "    with patch(target), patch('scripts.ops_data_portal._ducklake_write'):\n"
            "        portal.file_decision({'title': 'd'})\n",
        )
        broken = self._write(tmp_path, "test_f.py", "def test_x(:\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = _ops_portal_patch_violations([flagged, broken])
        assert len(violations) == 1 and "_ducklake_write" in violations[0]

    def test_ops_portal_patch_targets_corrected_submodule_patch_not_flagged(self, tmp_path: Path) -> None:
        body = (
            "from unittest.mock import patch\n"
            "def test_x():\n"
            "    with patch('scripts.ops_portal.decisions._ducklake_write'):\n"
            "        file_decision({'title': 'd'}, _skip_sync=True)\n"
        )
        path = self._write(tmp_path, "test_b.py", body)
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = _ops_portal_patch_violations([path])
        assert violations == []

    def test_ops_portal_patch_targets_flags_stale_orphan_guard_patch(self, tmp_path: Path) -> None:
        """rec-2637 failure mode for the DCG-03 guard: a test exercising backfill_decisions_from_md
        that patches scripts.ops_data_portal._assert_no_orphaned_current_rows (the facade
        namespace) must be flagged -- the caller resolves that symbol at its own submodule scope
        (scripts.ops_portal.decisions), so the facade patch never intercepts the call."""
        body = (
            "from unittest.mock import patch\n"
            "def test_x():\n"
            "    with patch('scripts.ops_data_portal._assert_no_orphaned_current_rows'):\n"
            "        backfill_decisions_from_md()\n"
        )
        path = self._write(tmp_path, "test_d.py", body)
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = _ops_portal_patch_violations([path])
        assert len(violations) == 1
        assert "backfill_decisions_from_md" in violations[0]
        assert "_assert_no_orphaned_current_rows" in violations[0]

    def test_ops_portal_patch_targets_facade_resident_caller_not_flagged(self, tmp_path: Path) -> None:
        """file_rec is facade-resident -- patching scripts.ops_data_portal for the same
        symbol name a moved caller also uses must NOT be flagged (caller-aware)."""
        body = (
            "from unittest.mock import patch\n"
            "def test_x():\n"
            "    with patch('scripts.ops_data_portal._ducklake_write'):\n"
            "        file_rec({'title': 'd'}, _skip_sync=True)\n"
        )
        path = self._write(tmp_path, "test_c.py", body)
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = _ops_portal_patch_violations([path])
        assert violations == []

    def test_registered_in_both_tiers(self) -> None:
        """validate_ops_portal_patch_targets appears in both pre_sequence() and full_sequence()."""
        from scripts.checks import registry

        names = [s.name for s in registry.pre_sequence() + registry.full_sequence()]
        assert names.count("validate_ops_portal_patch_targets") >= 2


class TestOpsPortalPatchTargetsWrapperEmission:
    """The REGISTERED wrapper's own ROOT/tests glob and its single failed.append -- every test
    above drives the pure _find_violations(paths) core directly, so that append runs nowhere."""

    def _run(self, tmp_path: Path, body: str) -> list[str]:
        target = tmp_path / "tests" / "test_subject.py"
        target.parent.mkdir(parents=True)
        target.write_text(body, encoding="utf-8")
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_ops_portal_patch_targets(failed)
        return failed

    def test_stale_facade_patch_under_root_appends_the_guard_failure(self, tmp_path: Path, capsys) -> None:
        failed = self._run(
            tmp_path,
            "from unittest.mock import patch\n"
            "def test_x():\n"
            "    with patch('scripts.ops_data_portal._ducklake_write'):\n"
            "        file_decision({'title': 'd'}, _skip_sync=True)\n",
        )

        assert failed == ["ops_portal facade patch-target guard"]
        assert "Stale facade patch targets found:" in capsys.readouterr().out

    def test_the_wrapper_declares_the_test_files_it_examined(self, tmp_path: Path) -> None:
        from scripts.checks import registry

        assert self._run(tmp_path, "def test_x():\n    pass\n") == []
        declaration = registry._CURRENT_DECLARATION
        assert declaration is not None and declaration.count == 1 and declaration.unit == "test_files"

    def test_corrected_submodule_patch_under_root_appends_nothing(self, tmp_path: Path, capsys) -> None:
        failed = self._run(
            tmp_path,
            "from unittest.mock import patch\n"
            "def test_x():\n"
            "    with patch('scripts.ops_portal.decisions._ducklake_write'):\n"
            "        file_decision({'title': 'd'}, _skip_sync=True)\n",
        )

        assert failed == []
        assert "No stale facade patch targets against moved ops_portal callers found." in capsys.readouterr().out
