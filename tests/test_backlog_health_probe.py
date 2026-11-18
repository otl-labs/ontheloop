"""Mirror test for scripts/backlog_health/probe.py (PLAN-backlog-health-detection).

Every node is hermetic (injected runner/isolation_check/clock) except test_timeout_kills_descendants,
test_proc_agrees_with_sandbox_pids and test_probed_command_is_not_namespace_init, which need the real
unshare primitive and skip without it, including in every CI job that does not run the AppArmor sysctl;
so in CI the kill guarantee is exercised live only by the weekly backlog-health probe job and ci.yml's
trailer-acceptance-evaluate, with the argv-token nodes as the unit guard.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from scripts.backlog_health import probe


class TestIsPytestCommand:
    def test_detects_pytest(self) -> None:
        assert probe.is_pytest_command("bin/venv-python -m pytest tests/test_x.py -q") is True

    def test_non_pytest(self) -> None:
        assert probe.is_pytest_command("grep -q foo bar.txt") is False


class TestIsolationAvailable:
    def test_true_on_zero_exit(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0))
        assert probe.isolation_available(tmp_path, runner=runner) is True

    def test_false_on_nonzero_exit(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=1))
        assert probe.isolation_available(tmp_path, runner=runner) is False

    def test_false_on_oserror(self, tmp_path: Path) -> None:
        runner = MagicMock(side_effect=OSError("unshare not found"))
        assert probe.isolation_available(tmp_path, runner=runner) is False

    def test_false_on_timeout(self, tmp_path: Path) -> None:
        runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="unshare", timeout=5))
        assert probe.isolation_available(tmp_path, runner=runner) is False

    def test_probes_pid_namespace_prefix(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0))
        probe.isolation_available(tmp_path, runner=runner)
        _assert_ns_tokens(runner.call_args.args[0])

    def test_binds_repo_root_not_slash(self, tmp_path: Path) -> None:
        """Regression (VP step 11, live workflow_dispatch): binding `/` onto itself is not a
        safe proxy for binding a real subdirectory -- some filesystems reject the former while
        happily accepting the latter, producing a false ISOLATION_UNAVAILABLE."""
        runner = MagicMock(return_value=MagicMock(returncode=0))
        probe.isolation_available(tmp_path, runner=runner)
        argv = runner.call_args.args[0]
        inner = argv[-1]
        assert str(tmp_path) in inner
        assert f"mount --bind -o ro {tmp_path} {tmp_path}" in inner


_TOKENS = ["-rpmn", "--fork", "--kill-child", "--mount-proc"]


def _assert_ns_tokens(argv: list[str]) -> None:
    sep = argv.index("--")
    head = argv[:sep]
    assert head[0] == "unshare"
    positions = [head.index(t) for t in _TOKENS]
    assert positions == sorted(positions)


def _real_primitive(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    if not probe.isolation_available(repo):
        import pytest

        pytest.skip("env: unprivileged user/pid namespaces unavailable")
    return repo


class TestRunOne:
    def test_argv_uses_pid_namespace_prefix(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0))
        probe.run_one("echo hi", repo_root=tmp_path, runner=runner)
        _assert_ns_tokens(runner.call_args[0][0])

    def test_inner_program_keeps_bash_as_init(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0))
        probe.run_one("echo hi", repo_root=tmp_path, runner=runner)
        assert runner.call_args[0][0][-1].startswith("trap : EXIT && ")

    def test_scratch_removal_retries_then_succeeds(self, tmp_path: Path, monkeypatch) -> None:
        import tempfile

        calls = {"n": 0}
        real = tempfile.TemporaryDirectory.cleanup

        def flaky(self) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(39, "Directory not empty")
            real(self)

        monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", flaky)
        monkeypatch.setattr(probe.time, "sleep", lambda s: None)
        seen = {}

        def fake_runner(*a, **kw):
            seen["scratch"] = kw["env"]["PYTHONPYCACHEPREFIX"]
            return MagicMock(returncode=0)

        assert probe.run_one("true", repo_root=tmp_path, runner=fake_runner) == probe.PASS
        assert calls["n"] == 2
        assert not Path(seen["scratch"]).parent.exists()

    def test_scratch_removal_raises_after_bounded_retries(self, tmp_path: Path, monkeypatch) -> None:
        import shutil
        import tempfile

        calls = {"n": 0}
        real = tempfile.TemporaryDirectory.cleanup
        seen = {}

        def always(self) -> None:
            calls["n"] += 1
            seen["name"] = self.name
            raise OSError(39, "Directory not empty")

        monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", always)
        monkeypatch.setattr(probe.time, "sleep", lambda s: None)
        try:
            import pytest

            with pytest.raises(OSError):
                probe.run_one("true", repo_root=tmp_path, runner=lambda *a, **k: MagicMock(returncode=0))
            assert calls["n"] == 10
        finally:
            monkeypatch.undo()
            if "name" in seen:
                shutil.rmtree(seen["name"], ignore_errors=True)
            del real

    def test_timeout_kills_descendants(self, tmp_path: Path) -> None:
        repo = _real_primitive(tmp_path)
        marker = uuid.uuid4().hex
        beat = tmp_path / "beat.txt"
        script = tmp_path / f"d_{marker}.sh"
        script.write_text(
            f"while true; do echo x >> {beat}; "
            'mkdir -p "$PYTHONPYCACHEPREFIX"; echo y > "$PYTHONPYCACHEPREFIX/w"; sleep 0.05; done\n'
        )
        cmd = f"{{ bash {script} {marker} & }}; sleep 100"
        try:
            assert probe.run_one(cmd, repo_root=repo, timeout=2) == probe.TIMEOUT
            time.sleep(1)
            assert beat.exists() and beat.stat().st_size > 0
            for pid in filter(str.isdigit, os.listdir("/proc")):
                try:
                    data = Path(f"/proc/{pid}/cmdline").read_bytes()
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
                assert marker.encode() not in data, f"descendant {pid} survived"
            size = beat.stat().st_size
            time.sleep(0.5)
            assert beat.stat().st_size == size
        finally:
            subprocess.run(["pkill", "-f", marker], check=False)

    def test_proc_agrees_with_sandbox_pids(self, tmp_path: Path) -> None:
        repo = _real_primitive(tmp_path)
        cmd = 'read p _ < /proc/self/stat; test "$p" = "$BASHPID"'
        assert probe.run_one(cmd, repo_root=repo) == probe.PASS

    def test_probed_command_is_not_namespace_init(self, tmp_path: Path) -> None:
        repo = _real_primitive(tmp_path)
        cmd = "sh -c 'read p _ < /proc/self/stat; test \"$p\" != 1'"
        assert probe.run_one(cmd, repo_root=repo) == probe.PASS

    def test_pass_on_zero_exit(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0))
        assert probe.run_one("echo hi", repo_root=tmp_path, runner=runner) == probe.PASS

    def test_fail_on_nonzero_exit(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=1))
        assert probe.run_one("false", repo_root=tmp_path, runner=runner) == probe.FAIL

    def test_timeout_on_timeout_expired(self, tmp_path: Path) -> None:
        runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="sleep", timeout=10))
        assert probe.run_one("sleep 100", repo_root=tmp_path, runner=runner) == probe.TIMEOUT

    def test_fail_never_silently_passes_on_oserror(self, tmp_path: Path) -> None:
        runner = MagicMock(side_effect=OSError("no such sandbox"))
        assert probe.run_one("echo hi", repo_root=tmp_path, runner=runner) == probe.FAIL

    def test_uses_isolated_argv(self, tmp_path: Path) -> None:
        runner = MagicMock(return_value=MagicMock(returncode=0))
        probe.run_one("echo hi", repo_root=tmp_path, runner=runner)
        argv = runner.call_args[0][0]
        assert argv[0] == "unshare"
        assert "-rpmn" in argv
        assert "echo hi" in argv[-1]

    def test_pytest_command_gets_30s_timeout(self, tmp_path: Path) -> None:
        captured = {}

        def fake_runner(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return MagicMock(returncode=0)

        probe.run_one("bin/venv-python -m pytest tests/test_x.py -q", repo_root=tmp_path, runner=fake_runner)
        assert captured["timeout"] == 30

    def test_non_pytest_command_gets_10s_timeout(self, tmp_path: Path) -> None:
        captured = {}

        def fake_runner(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return MagicMock(returncode=0)

        probe.run_one("grep -q foo bar.txt", repo_root=tmp_path, runner=fake_runner)
        assert captured["timeout"] == 10

    def test_scratch_env_redirects_pycache_and_pytest_cache(self, tmp_path: Path) -> None:
        captured = {}

        def fake_runner(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return MagicMock(returncode=0)

        probe.run_one("echo hi", repo_root=tmp_path, runner=fake_runner)
        env = captured["env"]
        assert env is not None
        assert "PYTHONPYCACHEPREFIX" in env
        assert "PYTEST_ADDOPTS" in env
        assert "cache_dir=" in env["PYTEST_ADDOPTS"]

    def test_compound_command_runs_every_segment(self, tmp_path: Path) -> None:
        """Proves AND/OR-list semantics, not absence of a token: extracts the inner bash program
        run_one builds, strips the known mount/ulimit prefix, and executes the remaining command
        portion through a REAL `bash -c` whose command writes a marker file from its SECOND `&&`
        segment -- asserting the marker exists. A test that merely greps the argv for "exec"
        would pass against a reintroduction that re-execs under a different spelling (e.g.
        `exec bash -c ...`), since it never proves the second segment actually runs."""
        marker = tmp_path / "marker.txt"
        cmd = f"true && touch {marker}"
        captured = {}

        def fake_runner(argv, **kwargs):
            captured["argv"] = argv
            return MagicMock(returncode=0)

        probe.run_one(cmd, repo_root=tmp_path, runner=fake_runner)
        inner = captured["argv"][-1]
        prefix = f"trap : EXIT && mount --bind -o ro {tmp_path} {tmp_path} && ulimit -u {probe._PID_CAP} && "
        assert inner.startswith(prefix), inner
        command_portion = inner[len(prefix) :]
        assert command_portion == cmd, "the mount/ulimit prefix must be followed by the raw command, never `exec {cmd}`"

        assert not marker.exists()
        result = subprocess.run(["bash", "-c", command_portion], capture_output=True, text=True)
        assert result.returncode == 0
        assert marker.exists(), "the second `&&` segment never ran -- the command was truncated"


class TestRunAll:
    def test_isolation_unavailable_marks_every_entry_and_never_runs(self, tmp_path: Path) -> None:
        payload = [{"id": "rec-1", "acceptance": "echo a"}, {"id": "rec-2", "acceptance": "echo b"}]
        runner = MagicMock()
        result = probe.run_all(
            payload,
            repo_root=tmp_path,
            main_sha="deadbeef",
            isolation_check=lambda root: False,
            runner=runner,
        )
        assert result["isolation_available"] is False
        assert result["verdicts"] == {"rec-1": probe.ISOLATION_UNAVAILABLE, "rec-2": probe.ISOLATION_UNAVAILABLE}
        runner.assert_not_called()

    def test_stamps_main_sha_unconditionally(self, tmp_path: Path) -> None:
        result = probe.run_all([], repo_root=tmp_path, main_sha="cafef00d", isolation_check=lambda root: True)
        assert result["main_sha"] == "cafef00d"

    def test_budget_exhausted_marks_remaining_entries(self, tmp_path: Path) -> None:
        payload = [{"id": "rec-1", "acceptance": "echo a"}, {"id": "rec-2", "acceptance": "echo b"}]
        clock_values = iter([0.0, 0.0, 100.0])  # start, first-entry check (in-budget), second-entry check (over)
        runner = MagicMock(return_value=MagicMock(returncode=0))
        result = probe.run_all(
            payload,
            repo_root=tmp_path,
            main_sha="sha",
            global_budget_s=10,
            isolation_check=lambda root: True,
            runner=runner,
            clock=lambda: next(clock_values),
        )
        assert result["verdicts"]["rec-1"] == probe.PASS
        assert result["verdicts"]["rec-2"] == probe.BUDGET_EXHAUSTED

    def test_all_five_verdicts_are_distinct_members_of_vocabulary(self) -> None:
        assert probe.VERDICTS == {probe.PASS, probe.FAIL, probe.TIMEOUT, probe.BUDGET_EXHAUSTED, probe.ISOLATION_UNAVAILABLE}
        assert len(probe.VERDICTS) == 5

    def test_never_records_a_pass_for_a_failing_probe(self, tmp_path: Path) -> None:
        payload = [{"id": "rec-1", "acceptance": "false"}]
        runner = MagicMock(return_value=MagicMock(returncode=1))
        result = probe.run_all(payload, repo_root=tmp_path, main_sha="sha", isolation_check=lambda root: True, runner=runner)
        assert result["verdicts"]["rec-1"] != probe.PASS
        assert result["verdicts"]["rec-1"] == probe.FAIL
