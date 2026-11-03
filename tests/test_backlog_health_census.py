"""Mirror test for scripts/backlog_health/census.py (PLAN-backlog-health-detection)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.backlog_health import census


class TestClassifyCommand:
    def test_empty_is_prose_only(self) -> None:
        assert census.classify_command("") == census.PROSE_ONLY
        assert census.classify_command(None) == census.PROSE_ONLY

    def test_na_marker_is_prose_only(self) -> None:
        assert census.classify_command("N/A -- manual review") == census.PROSE_ONLY
        assert census.classify_command("Manual review by a human") == census.PROSE_ONLY

    def test_destructive_shape_is_unsafe(self, tmp_path: Path) -> None:
        assert census.classify_command("rm -rf /tmp/foo") == census.UNPROBEABLE_UNSAFE
        assert census.classify_command("sudo systemctl restart x") == census.UNPROBEABLE_UNSAFE
        assert census.classify_command("curl -X POST https://example.com") == census.UNPROBEABLE_UNSAFE
        assert census.classify_command("git push origin main --force") == census.UNPROBEABLE_UNSAFE

    def test_missing_pytest_file_is_expected_fail(self, tmp_path: Path) -> None:
        cmd = "bin/venv-python -m pytest tests/test_does_not_exist.py::test_foo -q"
        assert census.classify_command(cmd, repo_root=tmp_path) == census.EXPECTED_FAIL_MISSING_NODE

    def test_missing_pytest_node_in_existing_file_is_expected_fail(self, tmp_path: Path) -> None:
        test_file = tmp_path / "tests"
        test_file.mkdir()
        (test_file / "test_x.py").write_text("def test_present():\n    assert True\n")
        cmd = "bin/venv-python -m pytest tests/test_x.py::test_absent -q"
        assert census.classify_command(cmd, repo_root=tmp_path) == census.EXPECTED_FAIL_MISSING_NODE

    def test_existing_pytest_node_is_probeable(self, tmp_path: Path) -> None:
        test_file = tmp_path / "tests"
        test_file.mkdir()
        (test_file / "test_x.py").write_text("def test_present():\n    assert True\n")
        cmd = "bin/venv-python -m pytest tests/test_x.py::test_present -q"
        assert census.classify_command(cmd, repo_root=tmp_path) == census.PROBEABLE

    def test_existing_pytest_class_method_node_is_probeable(self, tmp_path: Path) -> None:
        test_file = tmp_path / "tests"
        test_file.mkdir()
        (test_file / "test_x.py").write_text("class TestFoo:\n    def test_bar(self):\n        assert True\n")
        cmd = "bin/venv-python -m pytest tests/test_x.py::TestFoo::test_bar -q"
        assert census.classify_command(cmd, repo_root=tmp_path) == census.PROBEABLE

    def test_parametrized_node_id_strips_bracket_suffix(self, tmp_path: Path) -> None:
        test_file = tmp_path / "tests"
        test_file.mkdir()
        (test_file / "test_x.py").write_text("def test_present():\n    assert True\n")
        cmd = "bin/venv-python -m pytest 'tests/test_x.py::test_present[case-1]' -q"
        assert census.classify_command(cmd, repo_root=tmp_path) == census.PROBEABLE

    def test_pytest_file_only_no_node_checks_file_existence(self, tmp_path: Path) -> None:
        cmd = "bin/venv-python -m pytest tests/test_missing_module.py -q"
        assert census.classify_command(cmd, repo_root=tmp_path) == census.EXPECTED_FAIL_MISSING_NODE

    def test_python_c_oneliner_is_unprobeable_shape(self) -> None:
        cmd = 'bin/venv-python -c "import sys; sys.exit(0)"'
        assert census.classify_command(cmd) == census.UNPROBEABLE_SHAPE

    def test_multiline_command_is_unprobeable_shape(self) -> None:
        cmd = "echo one\necho two"
        assert census.classify_command(cmd) == census.UNPROBEABLE_SHAPE

    def test_overlong_command_is_unprobeable_shape(self) -> None:
        cmd = "grep -q " + "x" * 600
        assert census.classify_command(cmd) == census.UNPROBEABLE_SHAPE

    def test_cap_length_false_lifts_only_the_length_check(self) -> None:
        assert census.classify_command("grep -q " + "x" * 600, cap_length=False) == census.PROBEABLE
        assert census.classify_command("echo one\necho two", cap_length=False) == census.UNPROBEABLE_SHAPE

    def test_safe_grep_is_probeable(self) -> None:
        assert census.classify_command("grep -q 'foo' bar.txt") == census.PROBEABLE

    def test_python_c_as_quoted_search_string_is_not_flagged(self) -> None:
        # Mirrors validate_recommendations_schema.py's own exclusion for a grep-quoted literal.
        cmd = "grep -q 'python -c' scripts/foo.py"
        assert census.classify_command(cmd) != census.UNPROBEABLE_SHAPE


class TestPytestNodeExists:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert census.pytest_node_exists(tmp_path, "tests/nope.py", None) is False

    def test_file_present_no_node(self, tmp_path: Path) -> None:
        (tmp_path / "t.py").write_text("x = 1\n")
        assert census.pytest_node_exists(tmp_path, "t.py", None) is True

    def test_unparseable_file_returns_false(self, tmp_path: Path) -> None:
        (tmp_path / "t.py").write_text("def broken(:\n")
        assert census.pytest_node_exists(tmp_path, "t.py", "test_x") is False

    def test_bracket_only_node_with_no_name_before_it_is_treated_as_no_node(self, tmp_path: Path) -> None:
        (tmp_path / "t.py").write_text("x = 1\n")
        assert census.pytest_node_exists(tmp_path, "t.py", "[param]") is True


class TestParsePytestTarget:
    def test_no_pytest_keyword_returns_none(self) -> None:
        assert census.parse_pytest_target("grep -q foo bar.txt") is None

    def test_no_py_path_returns_none(self) -> None:
        assert census.parse_pytest_target("pytest --version") is None

    def test_extracts_path_and_node(self) -> None:
        result = census.parse_pytest_target("bin/venv-python -m pytest tests/test_x.py::test_y -q")
        assert result == ("tests/test_x.py", "test_y")

    def test_target_is_anchored_to_the_pytest_invocation(self) -> None:
        grep_guarded = census.parse_pytest_target(
            "grep -q rec_id tests/test_x.py && bin/venv-python -m pytest tests/test_x.py::test_y -q"
        )
        assert grep_guarded == ("tests/test_x.py", "test_y")

        no_invocation = census.parse_pytest_target("bin/venv-python scripts/checks/_pytest_diff.py")
        assert no_invocation is None

        directory_form = census.parse_pytest_target("bin/venv-python -m pytest tests/checks/ci_guards/ -q")
        assert directory_form is None


class TestMakeReader:
    def test_delegates_to_ducklake_reader_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.common import ducklake_reader_client

        sentinel = object()
        called = {}

        def fake_make_reader(profile=None):
            called["profile"] = profile
            return sentinel

        monkeypatch.setattr(ducklake_reader_client, "make_reader", fake_make_reader)
        result = census._make_reader(profile="agent_platform")
        assert result is sentinel
        assert called["profile"] == "agent_platform"


class TestReadOpenRecs:
    def test_asserts_status_open_invariant(self) -> None:
        rows = [{"id": "rec-1", "status": "open"}, {"id": "rec-2", "status": "closed"}]
        with pytest.raises(RuntimeError, match="expected 'open'"):
            census.read_open_recs(rows=rows)

    def test_asserts_required_keys_present(self) -> None:
        rows = [{"status": "open"}]
        with pytest.raises(RuntimeError, match="missing required key"):
            census.read_open_recs(rows=rows)

    def test_passthrough_of_injected_rows(self) -> None:
        rows = [{"id": "rec-1", "status": "open"}]
        assert census.read_open_recs(rows=rows) == rows

    def test_uses_injected_reader_when_rows_absent(self) -> None:
        reader = MagicMock()
        reader.current_state.return_value = [{"id": "rec-1", "status": "open"}]
        result = census.read_open_recs(reader=reader)
        reader.current_state.assert_called_once_with("ops_recommendations", row_filter="status = 'open'")
        assert result == [{"id": "rec-1", "status": "open"}]


class TestCollectRecentCommits:
    def test_parses_git_log_output(self) -> None:
        stdout = (
            "\x01abc123\x022026-06-01T00:00:00+00:00\nfile_a.py\nfile_b.py\n"
            "\x01def456\x022026-06-02T00:00:00+00:00\nfile_c.py\n"
        )
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout=stdout, stderr=""))
        commits = census.collect_recent_commits(runner=runner)
        assert commits == [
            {"sha": "abc123", "date": "2026-06-01T00:00:00+00:00", "files": ["file_a.py", "file_b.py"]},
            {"sha": "def456", "date": "2026-06-02T00:00:00+00:00", "files": ["file_c.py"]},
        ]

    def test_uses_fixed_literal_argv(self) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        census.collect_recent_commits(runner=runner)
        called_argv = runner.call_args[0][0]
        assert called_argv == ["git", "log", "--pretty=format:\x01%H\x02%cI", "--name-only"]

    def test_raises_on_nonzero_exit(self) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="boom"))
        with pytest.raises(RuntimeError, match="git log failed"):
            census.collect_recent_commits(runner=runner)


class TestRunCensus:
    def test_classifies_and_emits_probe_payload_with_sha256(self, tmp_path: Path) -> None:
        rows = [
            {"id": "rec-1", "status": "open", "file": "a.py", "acceptance": "grep -q foo a.py"},
            {"id": "rec-2", "status": "open", "file": "b.py", "acceptance": ""},
            {"id": "rec-3", "status": "open", "file": "c.py", "acceptance": "sudo rm -rf /"},
        ]
        result = census.run_census(rows=rows, recent_commits=[], repo_root=tmp_path)
        assert result["buckets"][census.PROBEABLE] == ["rec-1"]
        assert result["buckets"][census.PROSE_ONLY] == ["rec-2"]
        assert result["buckets"][census.UNPROBEABLE_UNSAFE] == ["rec-3"]
        assert result["counts"][census.PROBEABLE] == 1
        assert len(result["probe_payload"]) == 1
        entry = result["probe_payload"][0]
        assert entry["id"] == "rec-1"
        assert entry["acceptance_sha256"] == census.hashlib.sha256(b"grep -q foo a.py").hexdigest()

    def test_never_places_unsafe_or_prose_in_probe_payload(self, tmp_path: Path) -> None:
        rows = [
            {"id": "rec-1", "status": "open", "file": "a.py", "acceptance": "sudo rm -rf /"},
            {"id": "rec-2", "status": "open", "file": "b.py", "acceptance": "N/A"},
        ]
        result = census.run_census(rows=rows, recent_commits=[], repo_root=tmp_path)
        probed_ids = {entry["id"] for entry in result["probe_payload"]}
        assert probed_ids.isdisjoint({"rec-1", "rec-2"})

    def test_bucket_counts_partition_open_rows(self, tmp_path: Path) -> None:
        rows = [{"id": f"rec-{i}", "status": "open", "file": "a.py", "acceptance": "grep -q foo a.py"} for i in range(5)]
        result = census.run_census(rows=rows, recent_commits=[], repo_root=tmp_path)
        assert sum(result["counts"].values()) == len(rows)

    def test_defaults_recent_commits_via_collector(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {}

        def fake_collector(*, repo_root: Path) -> list:
            called["repo_root"] = repo_root
            return [{"sha": "x", "date": "2026-01-01", "files": []}]

        monkeypatch.setattr(census, "collect_recent_commits", fake_collector)
        result = census.run_census(rows=[], repo_root=tmp_path)
        assert called["repo_root"] == tmp_path
        assert result["recent_commits"] == [{"sha": "x", "date": "2026-01-01", "files": []}]
