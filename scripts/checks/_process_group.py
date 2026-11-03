"""Shared process-group kill-and-reap for checks that run a detached child under a deadline.

A top-level ``scripts/checks/`` sibling (like ``_marker_guard.py``), not a domain ``_shared.py``
(Decision 104): both ``coverage_baseline`` and ``_vp_replay_budget`` reach it as
``_process_group.kill_and_reap`` so a test patches one place. It takes the ``Popen`` object and
never constructs a process, so each caller keeps its own injected subprocess seam.

POSIX-only by design (``os.killpg``): every surface that runs these checks is Linux (CC-web, CI).
"""

from __future__ import annotations

import os
import signal
import subprocess

# Bounded reap window after a SIGKILL -- never an unbounded communicate(): a descendant that
# escaped the killed group via setsid, still holding the pipe open, must not be able to hang the
# caller indefinitely.
REAP_TIMEOUT_SECONDS = 5.0


def kill_and_reap(popen: subprocess.Popen) -> tuple[str, str]:
    """SIGKILL ``popen``'s whole process group and reap it with a BOUNDED wait.

    The group is the child's own (callers start it with ``start_new_session=True``). A leader that
    is already reaped uses ``popen.pid`` as the pgid directly -- ``os.getpgid`` on a possibly
    recycled pid is never called -- and a leader that vanishes between the poll and the lookup
    falls back to ``popen.pid`` too, so surviving group members are still killed. If the looked-up
    pgid is the caller's own group (a call site that forgot ``start_new_session``), only the child
    pid is signalled, never the group the caller belongs to.

    A descendant that escaped the group before the kill (e.g. a double-forked daemonizer still
    holding the inherited stdout/stderr pipe open) would make an unbounded ``communicate()`` hang
    forever waiting for EOF -- the bounded reap trades a guaranteed return for a possibly
    incomplete capture of that descendant's own output, a fine trade since the child is already
    being reported as a deadline kill.

    When the bounded reap itself times out, the pipes are closed and the already-SIGKILLed SHELL
    (never the escaped descendant) is reaped with a plain, unbounded ``popen.wait()`` -- the
    kernel reaps a SIGKILLed process promptly and the descendant holding the pipe cannot block a
    wait on a different process. No zombie and no open file descriptor survive the call.
    """
    try:
        if popen.returncode is not None:
            pgid = popen.pid
        else:
            try:
                pgid = os.getpgid(popen.pid)
            except ProcessLookupError:
                pgid = popen.pid
        if pgid == os.getpgrp():
            os.kill(popen.pid, signal.SIGKILL)
        else:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        return popen.communicate(timeout=REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if popen.stdout is not None:
            popen.stdout.close()
        if popen.stderr is not None:
            popen.stderr.close()
        popen.wait()
        return "", ""
