"""Mirror test for scripts/checks/hygiene/validate_test_skip_citations.py (rec-3357).

Fixtures are string literals only -- never real skip decorators or calls -- so this file adds no
skip site of its own (it would otherwise fail its own check at --pre time).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.hygiene.validate_test_skip_citations import (
    _read_file,
    _read_file_at_ref,
    main,
    run_check,
    validate_test_skip_citations,
)


def _run(changed: list[tuple[str, str]], base_sources: dict[str, str], head_sources: dict[str, str]) -> list[str]:
    failed: list[str] = []
    run_check(
        failed,
        root=Path("/unused"),
        base="BASE",
        changed=changed,
        read_base=lambda rel: base_sources.get(rel),
        read_head=lambda rel: head_sources.get(rel),
    )
    return failed


_SKIP_DEC = "import pytest\n\n@pytest.mark.skip(reason={reason!r})\ndef test_a():\n    pass\n"
_CLEAN = "import pytest\n\ndef test_a():\n    pass\n"


class TestAddedSkipsMustCiteOrDeclare:
    def test_rec_cited_passes(self) -> None:
        head = _SKIP_DEC.format(reason="rec-123 because flaky")
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
        assert failed == []

    def test_env_label_passes(self) -> None:
        head = _SKIP_DEC.format(reason="env: windows only")
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
        assert failed == []

    def test_uncited_fails(self) -> None:
        head = _SKIP_DEC.format(reason="flaky for now")
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
        assert len(failed) == 1
        assert "tests/t.py:3" in failed[0]

    def test_each_site_kind_without_citation_fails(self) -> None:
        samples = {
            "pytest_skip": "import pytest\n\ndef test_a():\n    pytest.skip('nope')\n",
            "self_skiptest": (
                "import unittest\n\nclass TestFoo(unittest.TestCase):\n    def test_a(self):\n        self.skipTest('nope')\n"
            ),
            "unittest_skip": (
                "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
                "    @unittest.skip('nope')\n    def test_a(self):\n        pass\n"
            ),
            "importorskip": "import pytest\npytest.importorskip('numpy')\n",
        }
        for name, head in samples.items():
            failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
            assert failed, f"{name} should have failed uncited"

    def test_each_site_kind_with_citation_passes(self) -> None:
        samples = {
            "pytest_skip": "import pytest\n\ndef test_a():\n    pytest.skip('rec-1')\n",
            "self_skiptest": (
                "import unittest\n\nclass TestFoo(unittest.TestCase):\n    def test_a(self):\n        self.skipTest('rec-2')\n"
            ),
            "unittest_skip": (
                "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
                "    @unittest.skip('rec-3')\n    def test_a(self):\n        pass\n"
            ),
            "importorskip": "import pytest\npytest.importorskip('numpy', reason='rec-4')\n",
        }
        for name, head in samples.items():
            failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
            assert failed == [], f"{name} should have passed cited: {failed}"


class TestOnlyAddedSitesAreJudged:
    def test_base_ref_site_not_flagged(self) -> None:
        base = _SKIP_DEC.format(reason="uncited already on base")
        failed = _run([("M", "tests/t.py")], {"tests/t.py": base}, {"tests/t.py": base})
        assert failed == []

    def test_changed_reason_is_judged_as_added(self) -> None:
        base = _SKIP_DEC.format(reason="rec-1 fine before")
        head = _SKIP_DEC.format(reason="now uncited")
        failed = _run([("M", "tests/t.py")], {"tests/t.py": base}, {"tests/t.py": head})
        assert len(failed) == 1

    def test_removed_site_passes(self) -> None:
        base = _SKIP_DEC.format(reason="uncited")
        failed = _run([("M", "tests/t.py")], {"tests/t.py": base}, {"tests/t.py": _CLEAN})
        assert failed == []

    def test_rename_carried_site_is_not_judged(self) -> None:
        old = _SKIP_DEC.format(reason="uncited but carried")
        failed = _run(
            [("D", "tests/old.py"), ("??", "tests/new.py")],
            {"tests/old.py": old},
            {"tests/new.py": old},
        )
        assert failed == []

    def test_module_level_skip_carried_into_pre_existing_file_fails(self) -> None:
        old = "import pytest\npytestmark = pytest.mark.skip(reason='uncited')\n"
        dest_base = _CLEAN
        dest_head = "import pytest\npytestmark = pytest.mark.skip(reason='uncited')\n\ndef test_a():\n    pass\n"
        failed = _run(
            [("D", "tests/old.py"), ("M", "tests/dest.py")],
            {"tests/old.py": old, "tests/dest.py": dest_base},
            {"tests/dest.py": dest_head},
        )
        assert len(failed) == 1

    def test_expected_failure_fix_names_xfail_conversion(self) -> None:
        head = (
            "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n    def test_a(self):\n        pass\n"
        )
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
        assert "xfail" in failed[0]

    def test_untracked_file_judged_like_added(self) -> None:
        head = _SKIP_DEC.format(reason="uncited")
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
        assert len(failed) == 1


class TestAccountingAndCli:
    def test_unparseable_head_file_fails(self) -> None:
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": "def f(:\n"})
        assert len(failed) == 1
        assert "not parseable" in failed[0]

    def test_no_test_files_is_examined_zero(self) -> None:
        registry.pop_declaration()
        failed = _run([], {}, {})
        assert failed == []
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.kind == "examined"
        assert decl.count == 0
        assert decl.unit == "test_files"

    def test_examined_counts_test_files_read(self) -> None:
        registry.pop_declaration()
        head = _CLEAN
        failed = _run([("??", "tests/t.py")], {}, {"tests/t.py": head})
        assert failed == []
        decl = registry.pop_declaration()
        assert decl.count == 1
        assert decl.unit == "test_files"

    def test_main_prints_added_skip_line_and_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "t.py").write_text(_SKIP_DEC.format(reason="no cite"), encoding="utf-8")

        import subprocess

        result = subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path))
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(tmp_path))

        from unittest.mock import patch

        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()
        with patch("scripts.checks._common.ROOT", tmp_path):
            code = main(["--base", head_sha])
        captured = capsys.readouterr()
        assert code == 0  # the site is already on the base ref -- not judged
        assert "ADDED-SKIP" not in captured.out

    def test_registered_function_fails_on_an_uncited_added_skip(self, tmp_path: Path) -> None:
        """LSA-03 red-case floor: a direct invocation of the registered check (not the pure
        run_check helper) over a real git fixture, on a genuinely failing input."""
        import subprocess
        from unittest.mock import patch

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8")
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path))
        (tests_dir / "t.py").write_text(_CLEAN, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(tmp_path))

        (tests_dir / "t.py").write_text(_SKIP_DEC.format(reason="no cite"), encoding="utf-8")

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.status_diff_base", return_value="HEAD"),
        ):
            failed: list[str] = []
            validate_test_skip_citations(failed, root=tmp_path)
        assert failed, "an uncited added skip must fail the registered check"

    def test_read_file_missing_returns_none(self, tmp_path: Path) -> None:
        assert _read_file(tmp_path, "nope.py") is None

    def test_read_file_at_ref_bad_ref_returns_none(self, tmp_path: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path))
        assert _read_file_at_ref(tmp_path, "not-a-real-ref", "t.py") is None

    def test_registered_function_skips_when_base_unreachable(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        registry.pop_declaration()
        with patch("scripts.checks._common.status_diff_base", return_value=None):
            failed: list[str] = []
            validate_test_skip_citations(failed, root=tmp_path)
        assert failed == []
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.kind == "skipped"

    def test_main_skips_when_base_unreachable(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.status_diff_base", return_value=None),
        ):
            assert main([]) == 0

    def test_main_with_explicit_head_arg(self, tmp_path: Path) -> None:
        import subprocess
        from unittest.mock import patch

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "t.py").write_text(_CLEAN, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path))
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(tmp_path))
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()

        (tests_dir / "t.py").write_text(_SKIP_DEC.format(reason="no cite"), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=str(tmp_path))
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()

        with patch("scripts.checks._common.ROOT", tmp_path):
            code = main(["--base", base_sha, "--head", head_sha])
        assert code == 1

    def test_main_head_diff_parsing_skips_malformed_lines(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 0
            if cmd[:2] == ["git", "diff"]:
                result.stdout = "no-tab-here\nM\ttests/t.py\n"
            else:
                result.stdout = ""
            return result

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", side_effect=mock_run),
            patch("scripts.checks.hygiene.validate_test_skip_citations._read_file_at_ref", return_value=_CLEAN),
        ):
            code = main(["--base", "BASE", "--head", "HEAD2"])
        assert code == 0

    def test_main_prints_failed_block_on_violation(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        import subprocess
        from unittest.mock import patch

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path))
        (tests_dir / "t.py").write_text(_CLEAN, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(tmp_path))
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8"
        ).stdout.strip()

        (tests_dir / "t.py").write_text(_SKIP_DEC.format(reason="no cite"), encoding="utf-8")

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.status_diff_base", return_value=base_sha),
        ):
            code = main([])
        assert code == 1
        assert "FAILED:" in capsys.readouterr().out
