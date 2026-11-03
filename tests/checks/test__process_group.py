"""Mirror for scripts/checks/_process_group.py (PLAN-coverage-timeout-process-group).

Real processes throughout: the helper's whole job is which group a SIGKILL reaches, so a mock
would prove nothing. Pid helpers come from tests/fixtures/process_probes.py, never another test
module. The TestKillAndReapBoundedWait nodes moved here from test__vp_replay_budget.py with the
helper they exercise."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from scripts.checks import _process_group as pg
from tests.fixtures.process_probes import pid_is_dead, pid_state, read_published_pid


class TestKillAndReapSignalTargeting:
    def test_kills_the_detached_childs_whole_group(self, tmp_path: Path) -> None:
        pidfile = tmp_path / "grandchild.pid"
        popen = subprocess.Popen(
            f"sleep 30 & echo $! > {pidfile}; wait",
            shell=True,
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        grandchild_pid = read_published_pid(pidfile)

        pg.kill_and_reap(popen)

        deadline = time.monotonic() + 5.0
        while not pid_is_dead(grandchild_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pid_is_dead(grandchild_pid), f"grandchild pid {grandchild_pid} survived the process-group kill"

    def test_never_signals_the_callers_own_group(self) -> None:
        popen = subprocess.Popen(["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        seen: list[int] = []
        real_killpg = os.killpg

        def _spy(pgid: int, sig: int) -> None:
            seen.append(pgid)
            if pgid == os.getpgrp():
                raise PermissionError("the caller's own group must never be signalled")
            real_killpg(pgid, sig)

        try:
            with patch.object(os, "killpg", _spy):
                pg.kill_and_reap(popen)
        finally:
            if popen.returncode is None:
                popen.kill()
                popen.wait()
        assert os.getpgrp() not in seen
        assert popen.returncode == -signal.SIGKILL

    def test_an_already_reaped_child_is_not_an_error(self, tmp_path: Path) -> None:
        popen = subprocess.Popen(
            "true", shell=True, cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        popen.wait(timeout=5.0)
        pg.kill_and_reap(popen)  # must not raise

    def test_a_leader_gone_before_lookup_falls_back_to_its_pid(self, tmp_path: Path) -> None:
        popen = subprocess.Popen(
            ["sleep", "30"], cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        sent: list[int] = []
        real_killpg = os.killpg

        def _record(pgid: int, sig: int) -> None:
            sent.append(pgid)
            real_killpg(pgid, sig)

        try:
            with patch.object(os, "getpgid", side_effect=ProcessLookupError), patch.object(os, "killpg", _record):
                pg.kill_and_reap(popen)
        finally:
            if popen.returncode is None:
                popen.kill()
                popen.wait()
        assert sent == [popen.pid]
        assert popen.returncode == -signal.SIGKILL


class TestKillAndReapBoundedWait:
    def test_reap_timeout_closes_pipes_and_reaps_the_shell(self, tmp_path: Path) -> None:
        """When the bounded reap itself times out, both pipes are closed and the already-
        SIGKILLed SHELL (never the escaped descendant) is reaped with a plain wait -- no zombie,
        no open file descriptor survives the call. Teardown kills the shell's whole group
        whenever it is still running, including when the pidfile read below fails (rec-4185)."""
        pidfile = tmp_path / "grandchild.pid"
        popen = subprocess.Popen(
            f"sleep 30 & echo $! > {pidfile}; wait",
            shell=True,
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        shell_pid = popen.pid
        try:
            read_published_pid(pidfile)
            with patch.object(pg, "REAP_TIMEOUT_SECONDS", 0.05):
                stdout, stderr = pg.kill_and_reap(popen)
            assert stdout == ""
            assert stderr == ""
            assert popen.stdout is None or popen.stdout.closed
            assert popen.stderr is None or popen.stderr.closed

            deadline = time.monotonic() + 5.0
            state = pid_state(shell_pid)
            while state not in ("gone", "Z") and time.monotonic() < deadline:
                time.sleep(0.05)
                state = pid_state(shell_pid)
            assert state in ("gone", "Z"), f"shell pid {shell_pid} was neither reaped nor a zombie (state={state!r})"
        finally:
            if popen.returncode is None:
                try:
                    os.killpg(popen.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                popen.wait()

    def test_reap_success_returns_the_captured_output(self, tmp_path: Path) -> None:
        popen = subprocess.Popen(
            "echo reaped",
            shell=True,
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        popen.wait(timeout=5.0)
        stdout, _stderr = pg.kill_and_reap(popen)
        assert "reaped" in stdout

    def test_reap_itself_timing_out_returns_empty_strings(self, tmp_path: Path) -> None:
        """A descendant that escaped the killed group via setsid, still holding the pipe open,
        would otherwise make an unbounded communicate() hang forever -- mocked here (rather than
        spawned for real) so the test is deterministic and leaves nothing to clean up."""
        popen = subprocess.Popen(
            "sleep 5",
            shell=True,
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            with patch.object(
                popen, "communicate", side_effect=subprocess.TimeoutExpired(cmd="sleep 5", timeout=pg.REAP_TIMEOUT_SECONDS)
            ):
                stdout, stderr = pg.kill_and_reap(popen)
            assert stdout == ""
            assert stderr == ""
        finally:
            # kill_and_reap's own killpg call above already sent the real SIGKILL and its plain
            # wait reaped the shell; this only guards a failure before that point.
            if popen.returncode is None:
                popen.kill()
                popen.wait(timeout=5.0)
