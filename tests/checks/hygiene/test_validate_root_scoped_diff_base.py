"""Tests for validate_root_scoped_diff_base() (rec-3166 guard).

_scan_file/_scan_tree exercised directly on synthetic temp files (mirrors
test_validate_test_count_coupling.py's precedent): a bare-call negative control, the
`# root-scoped-ok:` waiver escape hatch, and a dogfood assertion that the real
scripts/checks/**/*.py tree reports zero violations after this plan's own fixes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks import _common, registry
from scripts.checks.hygiene.validate_root_scoped_diff_base import (
    _scan_file,
    _scan_tree,
    validate_root_scoped_diff_base,
)


class TestScanFile:
    def _write(self, tmp_path: Path, name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_bare_attribute_call_is_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "a.py",
            "def f(root):\n    return _common.get_changed_files()\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert scanned == 1
        assert len(violations) == 1
        assert "get_changed_files" in violations[0]

    def test_bare_name_call_is_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "b.py",
            "def f(root):\n    return push_context_base()\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert len(violations) == 1
        assert "push_context_base" in violations[0]

    def test_positional_root_forwarding_is_not_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "c.py",
            "def f(root):\n    return _common.get_changed_files(root)\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert violations == []
        assert scanned == 1

    def test_keyword_root_forwarding_is_not_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "d.py",
            "def f(root):\n    return _common.get_status_aware_diff(root=root)\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert violations == []

    def test_function_without_root_param_is_not_scanned(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "e.py",
            "def f():\n    return _common.get_changed_files()\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert violations == []
        assert scanned == 0

    def test_unrelated_call_is_not_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "f.py",
            "def f(root):\n    return _common.run(['git', 'status'], cwd=root)\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert violations == []
        assert scanned == 1

    def test_waived_bare_call_is_not_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "g.py",
            "def f(root):\n    return _common.get_changed_files()  # root-scoped-ok: deliberate tripwire\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert violations == []

    def test_nested_function_scope_is_walked_independently(self, tmp_path: Path) -> None:
        """A root-taking outer function whose bare call lives inside a NESTED function that
        does NOT itself take root is still detected -- _iter_scope_nodes only stops descending
        for accounting purposes at nested defs; the nested body is walked as its own scope
        under the SAME outer node when that outer node itself takes root."""
        path = self._write(
            tmp_path,
            "h.py",
            "def outer(root):\n    def inner():\n        return _common.get_changed_files()\n    return inner\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        # inner() is a separate scope without a `root` param, so it is scanned on its own (and
        # not flagged, since it never claims to take root) -- outer() itself makes no bare call.
        assert violations == []

    def test_bare_status_diff_base_call_is_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "i.py",
            "def f(root):\n    return _common.status_diff_base()\n",
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert len(violations) == 1
        assert "status_diff_base" in violations[0]

    def test_syntax_error_file_yields_no_violations(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "broken.py", "def f(root):\n    (((\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations, scanned = _scan_file(path)
        assert violations == []
        assert scanned == 0


class TestValidateRootScopedDiffBase:
    def test_synthetic_violation_fails(self, tmp_path: Path) -> None:
        checks_dir = tmp_path / "scripts" / "checks"
        checks_dir.mkdir(parents=True)
        (checks_dir / "bad.py").write_text("def f(root):\n    return _common.get_changed_files()\n", encoding="utf-8")

        failed: list[str] = []
        registry.pop_declaration()
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_root_scoped_diff_base(failed)
        assert failed == ["Root-scoped diff-base guard"]
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 1
        assert declaration.unit == "root_taking_functions"

    def test_waiver_marker_suppresses(self, tmp_path: Path) -> None:
        checks_dir = tmp_path / "scripts" / "checks"
        checks_dir.mkdir(parents=True)
        (checks_dir / "ok.py").write_text(
            "def f(root):\n    return _common.get_changed_files()  # root-scoped-ok: deliberate\n", encoding="utf-8"
        )

        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_root_scoped_diff_base(failed)
        assert failed == []

    def test_missing_checks_dir_skips(self, tmp_path: Path) -> None:
        failed: list[str] = []
        registry.pop_declaration()
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_root_scoped_diff_base(failed)
        assert failed == []
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "skipped"

    def test_live_tree_reports_zero_violations(self) -> None:
        """Dogfood: the real repository's scripts/checks/**/*.py tree must be clean after this
        plan's fixes (validate_graduation_completeness, validate_vp_replay,
        validate_handoff_full_tier, validate_decisions_size)."""
        checks_dir = _common.ROOT / "scripts" / "checks"
        paths = sorted(checks_dir.glob("**/*.py"))
        violations, scanned = _scan_tree(paths)
        assert violations == [], f"Live-tree root-scoped-diff-base violations: {violations}"
        assert scanned > 0
