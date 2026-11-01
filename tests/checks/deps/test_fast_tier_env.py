"""CI-shaped fast-tier environment (scripts/checks/deps/fast_tier_env.py).

No network and no package install: the builder's subprocess runner and os.execve are injected, except
TestConsoleScriptsRunFromFinalPath, which creates a real (offline) venv.
"""

from __future__ import annotations

import os
import re
import runpy
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from scripts.checks.deps import fast_tier_env as fte

REPO = Path(__file__).resolve().parents[3]
DAY = "2026-10-30"


class _NtOs:
    """Stand-in for the os module whose name is "nt"; patching os.name itself would break pathlib."""

    name = "nt"

    def __getattr__(self, attr: str) -> object:
        return getattr(os, attr)


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str], cwd: Path) -> None:
        self.commands.append(list(command))
        if command[1] == "venv" or command[1:3] == ["-m", "venv"]:
            python = fte.env_python(Path(command[-1]))
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in fte.INSTALL_REQUIREMENTS:
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    return root


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def use_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fte.shutil, "which", lambda name: "/opt/uv" if name == "uv" else None)


@pytest.fixture
def no_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fte.shutil, "which", lambda name: None)


def _ensure(repo: Path, cache: Path, runner: FakeRunner, today: str = DAY) -> tuple[Path, str]:
    return fte.ensure_env(repo, cache_root=cache, runner=runner, today=today)


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


class TestReexecDecision:
    ARGV = ["validate", "--pre", "--flag"]

    @pytest.fixture
    def exec_calls(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        calls = MagicMock()
        monkeypatch.setattr(fte, "ensure_env", lambda root: (Path("/e/bin/python"), "fp1"))
        monkeypatch.setattr(fte, "flush_and_exec", calls)
        return calls

    @pytest.mark.parametrize(
        ("argv", "environ"),
        [
            (["validate"], {}),
            (["validate", "--pre", "--full-env"], {}),
            (["validate", "--pre", "--ignore-budget"], {}),
            (ARGV, {"CI": "true"}),
            (ARGV, {"PYTEST_CURRENT_TEST": "x"}),
            (ARGV, {"_VALIDATE_DEPTH": "1"}),
            (ARGV, {"OTL_FAST_TIER_ENV": "2020-01-01-previous-utc-date"}),
        ],
    )
    def test_no_op_conditions(self, exec_calls: MagicMock, argv: list[str], environ: dict[str, str]) -> None:
        fte.reexec_pre_if_needed(argv, environ)

        exec_calls.assert_not_called()

    @pytest.mark.parametrize("argv0", ["validate", "scripts/validate.py"])
    def test_reexecs_with_argv_tail(self, exec_calls: MagicMock, capsys: pytest.CaptureFixture, argv0: str) -> None:
        fte.reexec_pre_if_needed([argv0, "--pre", "--flag"], {"PATH": "/usr/bin", "_VALIDATE_DEPTH": "0"})

        command, env, cwd = exec_calls.call_args.args
        assert command == ["/e/bin/python", "-m", "scripts.validate", "--pre", "--flag"]
        assert cwd == fte.REPO_ROOT
        assert env["OTL_VENV_PYTHON"] == "/e/bin/python"
        out = capsys.readouterr().out
        assert "CI-parity: running --pre in the CI-shaped fast-tier environment /e/bin/python;" in out
        assert "predicts PR CI's fast tier only, not the full tier" in out

    def test_env_build_failure_exits_1_naming_cause_and_escape(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        def boom(root: Path) -> tuple[Path, str]:
            raise fte.FastTierEnvError("uv pip install failed: no index")

        monkeypatch.setattr(fte, "ensure_env", boom)

        with pytest.raises(SystemExit) as exc:
            fte.reexec_pre_if_needed(self.ARGV, {})

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "uv pip install failed: no index" in err
        assert "--full-env runs in the current interpreter (not CI-predictive)" in err

    def test_missing_requirements_file_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(fte, "REPO_ROOT", tmp_path)

        with pytest.raises(SystemExit) as exc:
            fte.reexec_pre_if_needed(self.ARGV, {})

        assert exc.value.code == 1
        assert "requirements-fast.txt" in capsys.readouterr().err


class TestBannerSurvivesExec:
    def test_line_printed_before_exec_reaches_a_pipe(self) -> None:
        script = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from scripts.checks.deps import fast_tier_env as f\n"
            "print('banner-line')\n"
            "f.flush_and_exec([sys.executable, '-c', 'pass'], os.environ, Path.cwd())\n"
        )

        result = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True, encoding="utf-8")

        assert result.returncode == 0
        assert "banner-line" in result.stdout


class TestFlushAndExec:
    def test_posix_chdirs_then_execs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        chdir, execve = MagicMock(), MagicMock()
        monkeypatch.setattr(fte.os, "chdir", chdir)
        monkeypatch.setattr(fte.os, "execve", execve)

        fte.flush_and_exec(["/e/python", "-m", "x"], {"A": "1"}, tmp_path)

        chdir.assert_called_once_with(tmp_path)
        execve.assert_called_once_with("/e/python", ["/e/python", "-m", "x"], {"A": "1"})


class TestWindowsReexec:
    def test_runs_child_with_env_and_cwd_and_propagates_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fte, "os", _NtOs())
        monkeypatch.setattr(fte, "ensure_env", lambda root: (Path("C:/e/Scripts/python.exe"), "fp1"))
        run = MagicMock(return_value=SimpleNamespace(returncode=7))
        execve = MagicMock()
        monkeypatch.setattr(fte.subprocess, "run", run)
        monkeypatch.setattr(_NtOs, "execve", execve, raising=False)

        with pytest.raises(SystemExit) as exc:
            fte.reexec_pre_if_needed(["validate", "--pre"], {"PATH": "p"})

        assert exc.value.code == 7
        command = run.call_args.args[0]
        assert command[1:] == ["-m", "scripts.validate", "--pre"]
        assert run.call_args.kwargs["cwd"] == fte.REPO_ROOT
        assert run.call_args.kwargs["env"]["OTL_FAST_TIER_ENV"] == "fp1"
        execve.assert_not_called()


class TestChildEnv:
    ENVIRON = {
        "PATH": "/usr/bin",
        "FOO": "bar",
        "PYTHONPATH": "/leak",
        "PYTHONHOME": "/leak",
        "VIRTUAL_ENV": "/repo/.venv",
        "AWS_ACCESS_KEY_ID": "k",
        "AWS_SECRET_ACCESS_KEY": "s",
        "AWS_SESSION_TOKEN": "t",
        "AWS_PROFILE": "agent_platform",
        "AWS_REGION": "eu-west-2",
        "AWS_DEFAULT_REGION": "eu-west-2",
    }

    def test_overrides_and_scrubs(self) -> None:
        env = fte.child_env(self.ENVIRON, Path("/e/bin/python"), "fp1")

        assert env["PATH"] == f"/e/bin{os.pathsep}/usr/bin"
        assert env["OTL_VENV_PYTHON"] == "/e/bin/python"
        assert env["OTL_FAST_TIER_ENV"] == "fp1"
        assert env["FOO"] == "bar"
        assert "CI" not in env
        for gone in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            assert gone not in env

    def test_aws_credentials_stripped_region_kept(self) -> None:
        env = fte.child_env(self.ENVIRON, Path("/e/bin/python"), "fp1")

        aws = {k: v for k, v in env.items() if k.startswith("AWS_")}
        assert aws == {
            "AWS_REGION": "eu-west-2",
            "AWS_DEFAULT_REGION": "eu-west-2",
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            "AWS_EC2_METADATA_DISABLED": "true",
        }

    def test_empty_path_is_just_the_env_bin(self) -> None:
        assert fte.child_env({}, Path("/e/bin/python"), "fp1")["PATH"] == "/e/bin"


class TestRunCommand:
    def test_success(self, tmp_path: Path) -> None:
        fte.run_command([sys.executable, "-c", "pass"], tmp_path)

    def test_failure_names_command_and_output(self, tmp_path: Path) -> None:
        with pytest.raises(fte.FastTierEnvError, match="boom-output"):
            fte.run_command([sys.executable, "-c", "import sys; print('boom-output'); sys.exit(3)"], tmp_path)

    def test_missing_executable(self, tmp_path: Path) -> None:
        with pytest.raises(fte.FastTierEnvError, match="setup failed"):
            fte.run_command([str(tmp_path / "absent")], tmp_path)


class TestDefaultCacheRoot:
    def test_xdg_cache_home(self) -> None:
        assert fte.default_cache_root({"XDG_CACHE_HOME": "/x"}) == Path("/x/ontheloop/fast-tier-env")

    def test_home_cache_fallback(self) -> None:
        assert fte.default_cache_root({}) == Path.home() / ".cache" / "ontheloop" / "fast-tier-env"

    def test_reads_process_environment_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", "/y")
        assert fte.default_cache_root() == Path("/y/ontheloop/fast-tier-env")


class TestEnvBuild:
    def test_uv_path_builds_in_place_with_two_sequential_installs(self, repo: Path, cache: Path, use_uv: None) -> None:
        runner = FakeRunner()

        python, fp = _ensure(repo, cache, runner)

        env_dir = cache / fp
        assert python == env_dir / "bin" / "python"
        assert runner.commands == [
            ["/opt/uv", "venv", "--python", sys.executable, str(env_dir)],
            ["/opt/uv", "pip", "install", "--python", str(python), "-r", str(repo / "requirements-fast.txt")],
            ["/opt/uv", "pip", "install", "--python", str(python), "-r", str(repo / "requirements-dev.txt")],
        ]
        assert (env_dir / ".ready").read_text(encoding="utf-8").strip() == fp
        assert sorted(p.name for p in cache.iterdir() if p.is_dir()) == [fp]

    def test_pip_fallback_path(self, repo: Path, cache: Path, no_uv: None) -> None:
        runner = FakeRunner()

        python, fp = _ensure(repo, cache, runner)

        assert runner.commands == [
            [sys.executable, "-m", "venv", str(cache / fp)],
            [str(python), "-m", "pip", "install", "-r", str(repo / "requirements-fast.txt")],
            [str(python), "-m", "pip", "install", "-r", str(repo / "requirements-dev.txt")],
        ]

    def test_reuses_a_matching_ready_stamp(self, repo: Path, cache: Path, use_uv: None) -> None:
        first = _ensure(repo, cache, FakeRunner())
        runner = FakeRunner()

        assert _ensure(repo, cache, runner) == first
        assert runner.commands == []

    def test_fingerprint_is_date_bucketed(self, repo: Path, cache: Path, use_uv: None) -> None:
        _, today_fp = _ensure(repo, cache, FakeRunner())
        runner = FakeRunner()

        _, tomorrow_fp = _ensure(repo, cache, runner, today="2026-10-31")

        assert tomorrow_fp != today_fp
        assert len(runner.commands) == 3

    def test_requirements_change_changes_fingerprint(self, repo: Path, cache: Path, use_uv: None) -> None:
        _, before = _ensure(repo, cache, FakeRunner())
        (repo / "requirements-dev.txt").write_text("changed\n", encoding="utf-8")

        _, after = _ensure(repo, cache, FakeRunner())

        assert before != after

    def test_incomplete_dir_without_ready_is_rebuilt(self, repo: Path, cache: Path, use_uv: None) -> None:
        _, fp = _ensure(repo, cache, FakeRunner())
        (cache / fp / ".ready").unlink()
        (cache / fp / "stale-marker").write_text("x", encoding="utf-8")
        runner = FakeRunner()

        _ensure(repo, cache, runner)

        assert len(runner.commands) == 3
        assert not (cache / fp / "stale-marker").exists()

    def test_mismatched_ready_is_rebuilt(self, repo: Path, cache: Path, use_uv: None) -> None:
        _, fp = _ensure(repo, cache, FakeRunner())
        (cache / fp / ".ready").write_text("other\n", encoding="utf-8")
        runner = FakeRunner()

        _ensure(repo, cache, runner)

        assert len(runner.commands) == 3

    def test_failure_leaves_no_ready_stamp(self, repo: Path, cache: Path, use_uv: None) -> None:
        class Failing(FakeRunner):
            def __call__(self, command: Sequence[str], cwd: Path) -> None:
                super().__call__(command, cwd)
                if command[1] == "pip":
                    raise fte.FastTierEnvError("install failed")

        with pytest.raises(fte.FastTierEnvError, match="install failed"):
            _ensure(repo, cache, Failing())

        assert not list(cache.glob("*/.ready"))

    def test_missing_requirements_file_is_a_fast_tier_error(self, tmp_path: Path, cache: Path) -> None:
        with pytest.raises(fte.FastTierEnvError, match="requirements-fast.txt"):
            _ensure(tmp_path, cache, FakeRunner())

    def test_default_runner_and_cache_root(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_uv: None
    ) -> None:
        runner = FakeRunner()
        monkeypatch.setattr(fte, "run_command", runner)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))

        python, _ = fte.ensure_env(repo)

        assert python.is_relative_to(tmp_path / "xdg")
        assert len(runner.commands) == 3

    def test_lock_contention_second_builder_blocks_then_reuses(self, repo: Path, cache: Path, use_uv: None) -> None:
        started, release = threading.Event(), threading.Event()

        class Slow(FakeRunner):
            def __call__(self, command: Sequence[str], cwd: Path) -> None:
                started.set()
                release.wait(10)
                super().__call__(command, cwd)

        second = FakeRunner()
        results: list[tuple[Path, str]] = []
        first_thread = threading.Thread(target=lambda: results.append(_ensure(repo, cache, Slow())))
        second_thread = threading.Thread(target=lambda: results.append(_ensure(repo, cache, second)))
        first_thread.start()
        assert started.wait(10)
        second_thread.start()
        second_thread.join(0.3)
        assert second_thread.is_alive()
        release.set()
        first_thread.join(10)
        second_thread.join(10)

        assert second.commands == []
        assert len(results) == 2
        assert results[0] == results[1]


class TestPrune:
    def _sibling(self, cache: Path, name: str, *, age: float, ready: bool = True) -> Path:
        directory = cache / name
        directory.mkdir(parents=True)
        if ready:
            stamp = directory / ".ready"
            stamp.write_text(name, encoding="utf-8")
            _age(stamp, age)
        (cache / f"{name}.lock").write_text("", encoding="utf-8")
        return directory

    def test_removes_only_old_unlocked_siblings(self, repo: Path, cache: Path, use_uv: None) -> None:
        old = self._sibling(cache, "old", age=fte.PRUNE_AGE_SECONDS + 60)
        fresh = self._sibling(cache, "fresh", age=60)
        unstamped = self._sibling(cache, "unstamped", age=0, ready=False)
        held = self._sibling(cache, "held", age=fte.PRUNE_AGE_SECONDS + 60)

        with fte._held(cache / "held.lock", blocking=True) as acquired:
            assert acquired
            _, fp = _ensure(repo, cache, FakeRunner())

        assert not old.exists()
        assert not (cache / "old.lock").exists()
        assert fresh.exists()
        assert unstamped.exists()
        assert held.exists()
        assert (cache / fp).exists()


class TestWindowsLock:
    @pytest.fixture
    def msvcrt(self, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
        fake = SimpleNamespace(LK_NBLCK=2, failures=0, calls=0)

        def locking(fd: int, mode: int, size: int) -> None:
            fake.calls += 1
            if fake.failures:
                fake.failures -= 1
                raise OSError("locked")

        fake.locking = locking
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        monkeypatch.setattr(fte, "os", _NtOs())
        monkeypatch.setattr(fte.time, "sleep", lambda seconds: None)
        return fake

    HANDLE = SimpleNamespace(fileno=lambda: 3)

    def test_acquires(self, msvcrt: SimpleNamespace) -> None:
        assert fte._acquire(self.HANDLE, blocking=True)
        assert msvcrt.calls == 1

    def test_blocking_retries_until_free(self, msvcrt: SimpleNamespace) -> None:
        msvcrt.failures = 2

        assert fte._acquire(self.HANDLE, blocking=True)
        assert msvcrt.calls == 3

    def test_non_blocking_reports_contention(self, msvcrt: SimpleNamespace) -> None:
        msvcrt.failures = 1

        assert not fte._acquire(self.HANDLE, blocking=False)

    def test_windows_env_python_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fte, "os", _NtOs())

        assert fte.env_python(Path("e")) == Path("e/Scripts/python.exe")


class TestConsoleScriptsRunFromFinalPath:
    def test_pip_console_script_runs_from_the_fingerprint_path(self, repo: Path, cache: Path, no_uv: None) -> None:
        try:
            import ensurepip  # noqa: F401, PLC0415
        except ImportError:
            pytest.skip("env: ensurepip unavailable, cannot seed pip offline")

        def runner(command: Sequence[str], cwd: Path) -> None:
            if command[1:3] == ["-m", "venv"]:
                fte.run_command(command, cwd)

        python, fp = fte.ensure_env(repo, cache_root=cache, runner=runner, today=DAY)

        assert python.parent.parent == cache / fp
        result = subprocess.run([str(python.parent / "pip"), "--version"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestInstallSequenceMatchesCi:
    def test_install_sequence_equals_pr_validate(self) -> None:
        workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["pr-validate"]["steps"]
        step = next(s for s in steps if s.get("name") == "Install fast-tier dependencies only")

        installed = [
            m.group(1) for line in step["run"].splitlines() if "pip install" in line for m in re.finditer(r"-r\s+(\S+)", line)
        ]

        assert installed == list(fte.INSTALL_REQUIREMENTS)


class TestCli:
    def test_ensure_prints_python_path(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr(fte, "ensure_env", lambda root: (Path("/e/bin/python"), "fp"))

        assert fte.main(["--ensure"]) == 0
        assert capsys.readouterr().out.strip() == "/e/bin/python"

    def test_ensure_failure_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        def boom(root: Path) -> tuple[Path, str]:
            raise fte.FastTierEnvError("no index")

        monkeypatch.setattr(fte, "ensure_env", boom)

        assert fte.main(["--ensure"]) == 1
        assert "no index" in capsys.readouterr().err

    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    def test_module_entry_requires_ensure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["fast_tier_env"])

        with pytest.raises(SystemExit) as exc:
            runpy.run_module("scripts.checks.deps.fast_tier_env", run_name="__main__")

        assert exc.value.code == 2
