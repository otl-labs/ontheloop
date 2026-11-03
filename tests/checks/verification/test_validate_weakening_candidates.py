"""Mirror test for scripts/checks/verification/validate_weakening_candidates.py (rec-3357)."""

from __future__ import annotations

from pathlib import Path

from scripts.checks import registry
from scripts.checks.verification.validate_weakening_candidates import (
    Candidate,
    _first_display,
    _moved_or_removed_candidates,
    _read_file,
    _read_file_at_ref,
    _Recorder,
    _review_dispositions,
    find_candidates,
    main,
    validate_weakening_candidates,
)


def _find(changed, sources: dict[str, tuple[str | None, str | None]]) -> list[Candidate]:
    return find_candidates(
        changed,
        read_base=lambda p: sources.get(p, (None, None))[0],
        read_head=lambda p: sources.get(p, (None, None))[1],
    )


class TestCandidates:
    def test_assertion_removed(self) -> None:
        base = "def test_a():\n    assert x == 1\n    assert y == 2\n"
        head = "def test_a():\n    assert x == 1\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "assertion-removed" and c.node == "test_a" for c in cands)

    def test_assertion_changed_same_count(self) -> None:
        base = "def test_a():\n    assert x == 1\n"
        head = "def test_a():\n    assert x == 2\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "assertion-changed" and c.node == "test_a" for c in cands)

    def test_negative_strengthened_assertion_not_listed(self) -> None:
        base = "def test_a():\n    assert x == 1\n"
        head = "def test_a():\n    assert x == 1\n    assert y == 2\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert cands == []

    def test_negative_unchanged_test_not_listed(self) -> None:
        src = "def test_a():\n    assert x == 1\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (src, src)})
        assert cands == []

    def test_negative_added_test_not_listed(self) -> None:
        head = "def test_new():\n    assert True\n"
        cands = _find([("??", "tests/t.py")], {"tests/t.py": (None, head)})
        assert cands == []

    def test_test_removed_no_relocation(self) -> None:
        base = "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n"
        head = "def test_b():\n    assert True\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "test-removed" and c.node == "test_a" for c in cands)

    def test_test_file_deleted_and_test_removed_both_listed(self) -> None:
        base = "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n"
        cands = _find([("D", "tests/gone.py")], {"tests/gone.py": (base, None)})
        categories = {c.category for c in cands}
        assert "test-file-deleted" in categories
        assert "test-removed" in categories
        deleted = next(c for c in cands if c.category == "test-file-deleted")
        assert "2" in deleted.detail

    def test_moved_test_with_lost_assertion(self) -> None:
        base_src = "def test_a():\n    assert x == 1\n    assert y == 2\n"
        dest_base = "def test_other():\n    pass\n"
        dest_head = "def test_other():\n    pass\n\ndef test_a():\n    assert x == 1\n"
        cands = _find(
            [("D", "tests/old.py"), ("M", "tests/dest.py")],
            {"tests/old.py": (base_src, None), "tests/dest.py": (dest_base, dest_head)},
        )
        assert any(c.category == "test-moved-weaker" and c.path == "tests/dest.py" for c in cands)
        assert not any(c.category == "test-removed" for c in cands)

    def test_moved_test_gained_skip(self) -> None:
        base_src = "def test_a():\n    assert True\n"
        dest_base = "def test_other():\n    pass\n"
        dest_head = (
            "import pytest\npytestmark = pytest.mark.skip(reason='rec-1')\n\n"
            "def test_other():\n    pass\n\ndef test_a():\n    assert True\n"
        )
        cands = _find(
            [("D", "tests/old.py"), ("M", "tests/dest.py")],
            {"tests/old.py": (base_src, None), "tests/dest.py": (dest_base, dest_head)},
        )
        assert any(c.category == "test-moved-weaker" and "gained skip" in c.detail for c in cands)

    def test_moved_cleanly_no_candidate(self) -> None:
        base_src = "def test_a():\n    assert x == 1\n"
        head_src = "def test_a():\n    assert x == 1\n"
        cands = _find(
            [("D", "tests/old.py"), ("??", "tests/new.py")],
            {"tests/old.py": (base_src, None), "tests/new.py": (None, head_src)},
        )
        assert cands == []

    def test_skip_added_to_existing_file(self) -> None:
        base = "def test_a():\n    pass\n"
        head = 'import pytest\n\n@pytest.mark.skip(reason="rec-1")\ndef test_a():\n    pass\n'
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "skip-added" and c.node == "test_a" for c in cands)

    def test_skip_added_module_level_pytestmark(self) -> None:
        base = "def test_a():\n    pass\n"
        head = 'import pytest\npytestmark = pytest.mark.skip(reason="rec-1")\n\ndef test_a():\n    pass\n'
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "skip-added" and c.node == "*" for c in cands)

    def test_skip_added_fixture_pytest_skip(self) -> None:
        base = "import pytest\n\n@pytest.fixture\ndef fx():\n    pass\n"
        head = 'import pytest\n\n@pytest.fixture\ndef fx():\n    pytest.skip("rec-1")\n'
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "skip-added" for c in cands)

    def test_merged_candidate_for_two_sites_in_one_scope(self) -> None:
        base = "def test_a():\n    assert x == 1\n"
        head = "def test_a():\n    assert x == 2\n    assert y == 3\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        matching = [c for c in cands if c.node == "test_a" and c.category == "assertion-changed"]
        assert len(matching) == 1

    def test_tolerance_widened(self) -> None:
        base = "def test_a():\n    assert x.approx(y, rel=0.01)\n"
        head = "def test_a():\n    assert x.approx(y, rel=0.1)\n"
        cands = _find([("M", "tests/t.py")], {"tests/t.py": (base, head)})
        assert any(c.category == "tolerance-widened" for c in cands)

    def test_constant_changed(self) -> None:
        base = "BUDGET_SECONDS = 240.0\n"
        head = "BUDGET_SECONDS = 270.0\n"
        cands = _find([("M", "scripts/checks/x.py")], {"scripts/checks/x.py": (base, head)})
        assert any(c.category == "constant-changed" and c.node == "BUDGET_SECONDS" for c in cands)

    def test_negative_unchanged_constant(self) -> None:
        src = "BUDGET_SECONDS = 240.0\n"
        cands = _find([("M", "scripts/checks/x.py")], {"scripts/checks/x.py": (src, src)})
        assert cands == []

    def test_negative_non_numeric_constant(self) -> None:
        base = 'NAME = "a"\n'
        head = 'NAME = "b"\n'
        cands = _find([("M", "scripts/checks/x.py")], {"scripts/checks/x.py": (base, head)})
        assert cands == []


class TestReportOnly:
    def test_never_fails(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_weakening_candidates(failed, root=tmp_path, base="deadbeef")
        assert failed == []

    def test_unreachable_base_skips(self, tmp_path: Path) -> None:
        registry.pop_declaration()
        failed: list[str] = []
        import unittest.mock as mock

        with mock.patch("scripts.checks._common.status_diff_base", return_value=None):
            validate_weakening_candidates(failed, root=tmp_path, base=None)
        assert failed == []
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.kind == "skipped"

    def test_main_exits_zero_even_with_candidates(self, capsys) -> None:
        code = main(["--base", "72fb926e^", "--head", "72fb926e"])
        assert code == 0

    def test_main_prints_disposition_stub(self, capsys) -> None:
        main(["--base", "8107c227^", "--head", "8107c227"])
        out = capsys.readouterr().out
        assert "disposition: <label>" in out

    def test_examined_declares_changed_files(self, tmp_path: Path) -> None:
        registry.pop_declaration()
        failed: list[str] = []
        validate_weakening_candidates(failed, root=tmp_path, base="HEAD")
        decl = registry.pop_declaration()
        assert decl is not None
        assert decl.unit == "changed_files"


class TestEdgeCases:
    def test_recorder_merges_two_adds_on_same_key(self) -> None:
        rec = _Recorder()
        rec.add("skip-added", "tests/t.py", "test_a", "first detail")
        rec.add("skip-added", "tests/t.py", "test_a", "second detail")
        result = rec.result()
        assert len(result) == 1
        assert "first detail" in result[0].detail
        assert "second detail" in result[0].detail

    def test_first_display_empty_counter(self) -> None:
        from collections import Counter

        assert _first_display(Counter()) == ""

    def test_moved_or_removed_skips_when_node_lookup_fails(self) -> None:
        rec = _Recorder()
        newly_added_keys = {"test_a": [("tests/dest.py", "test_a")]}
        base_tests = {"tests/old.py": {"test_a": object()}}
        head_tests = {"tests/old.py": {}, "tests/dest.py": {}}
        _moved_or_removed_candidates(
            ["tests/old.py"],
            [],
            base_tests,
            head_tests,
            {"tests/old.py": None},
            {"tests/dest.py": None},
            newly_added_keys,
            rec,
        )
        assert rec.result() == []

    def test_constant_changed_skips_unreadable_source(self) -> None:
        cands = _find([("M", "scripts/checks/x.py")], {})
        assert cands == []

    def test_review_dispositions_handles_bad_yaml(self, tmp_path: Path) -> None:
        import unittest.mock as mock

        with mock.patch("scripts.checks._common.ROOT", tmp_path):
            assert _review_dispositions() == {}

    def test_read_file_at_ref_bad_ref(self, tmp_path: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path))
        assert _read_file_at_ref(tmp_path, "not-a-real-ref", "t.py") is None

    def test_read_file_missing(self, tmp_path: Path) -> None:
        assert _read_file(tmp_path, "nope.py") is None

    def test_main_skips_when_base_unreachable(self, tmp_path: Path) -> None:
        import unittest.mock as mock

        with (
            mock.patch("scripts.checks._common.ROOT", tmp_path),
            mock.patch("scripts.checks._common.status_diff_base", return_value=None),
        ):
            assert main([]) == 0

    def test_main_without_head_arg_uses_working_tree_diff(self, tmp_path: Path) -> None:
        import subprocess
        import unittest.mock as mock

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(tmp_path))
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path))
        (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path))
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(tmp_path))

        with (
            mock.patch("scripts.checks._common.ROOT", tmp_path),
            mock.patch("scripts.checks._common.status_diff_base", return_value="HEAD"),
        ):
            assert main([]) == 0

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
            patch("scripts.checks.verification.validate_weakening_candidates._read_file_at_ref", return_value="x = 1\n"),
        ):
            assert main(["--base", "BASE", "--head", "HEAD2"]) == 0
