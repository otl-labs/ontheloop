"""Backlog-health bounded probe runner (PLAN-backlog-health-detection).

THE SOLE EXECUTOR OF REC-AUTHORED COMMANDS in this package (VP step 14 / the AST invariant that
enforces it): every OTHER module in scripts/backlog_health/** must never import or call
`scripts.rec_relevance._run_acceptance_probe`, must never pass `run_acceptance_probe` as anything
but the literal `False`, and must never reach `subprocess`/`os.system`/`os.popen` with a non-
constant-foldable argv. This module is the one deliberate exception, and only for commands
census.py has already classified `probeable` -- never `unprobeable_unsafe`, `unprobeable_shape`,
`prose_only` or `expected_fail_missing_node`.

Each command runs under `unshare -rpmn --fork --kill-child --mount-proc` (new user/pid/mount/net
namespaces, current user mapped to root inside; killing unshare on timeout kills the namespace
init and the kernel kills every descendant, so none outlives the scratch dir) with a read-only
self-bind-mount of the checkout, a `ulimit -u` pid cap, and PYTHONPYCACHEPREFIX / PYTEST_ADDOPTS
redirected to a per-probe scratch dir so pytest's own cache writes never touch the read-only tree.
Network is unreachable inside the new net namespace by
construction (no interface beyond loopback). The command runs as the LAST member of an `&&` chain
(never `exec`'d), so a compound command's every segment executes and the verdict reflects the
AND/OR list's own exit status, not just its first simple command's.

Five verdicts, NEVER a silent pass: PASS/FAIL/TIMEOUT come from actually running the command;
BUDGET_EXHAUSTED means the global wall-clock budget ran out before this entry's turn; and
ISOLATION_UNAVAILABLE means the unshare/bind-mount primitive itself failed to engage on this
runner (Ubuntu 24.04 can restrict unprivileged user namespaces via AppArmor) -- when that
happens EVERY entry gets ISOLATION_UNAVAILABLE and NONE is attempted, so a missing sandbox can
never present as a real (and falsely reassuring) result.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

PASS = "pass"
FAIL = "fail"
TIMEOUT = "timeout"
BUDGET_EXHAUSTED = "budget_exhausted"
ISOLATION_UNAVAILABLE = "isolation_unavailable"

VERDICTS: frozenset[str] = frozenset({PASS, FAIL, TIMEOUT, BUDGET_EXHAUSTED, ISOLATION_UNAVAILABLE})

_DEFAULT_TIMEOUT_S = 10
_PYTEST_TIMEOUT_S = 30
_DEFAULT_GLOBAL_BUDGET_S = 25 * 60  # inside the workflow's 30-minute job timeout, leaving margin
_PID_CAP = 64
_RM_ATTEMPTS = 10
_RM_PAUSE_S = 0.5
_NS_PREFIX: list[str] = ["unshare", "-rpmn", "--fork", "--kill-child", "--mount-proc", "--"]

_PYTEST_RE = re.compile(r"\bpytest\b")


def is_pytest_command(cmd: str) -> bool:
    return bool(_PYTEST_RE.search(cmd))


def isolation_available(repo_root: Path, runner: Callable[..., Any] = subprocess.run) -> bool:
    """Probe the _NS_PREFIX namespaces plus a read-only self-bind-mount of repo_root -- the exact primitive
    AND the exact target run_one relies on (VP step 11 regression: binding `/` onto itself is
    NOT a safe proxy for binding a real subdirectory -- `/`'s own mount setup can reject a
    self-bind-mount for filesystem-specific reasons, e.g. `mount: /: wrong fs type, bad option,
    bad superblock`, even on a runner where binding repo_root works cleanly. Testing the wrong
    target produced a false ISOLATION_UNAVAILABLE on both the authoring container and the
    GitHub-hosted runner). Never raises: any exception, timeout, or non-zero exit means
    unavailable."""
    try:
        result = runner(
            [*_NS_PREFIX, "bash", "-c", f"mount --bind -o ro {repo_root} {repo_root} && true"],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _isolated_argv(cmd: str, repo_root: Path, pid_cap: int) -> list[str]:
    # No `exec` before {cmd}: `exec A && B` replaces the shell with A and B never runs, so an
    # `exec`'d compound command would silently execute only its first simple command and report
    # that segment's exit status as the whole AND/OR list's verdict.
    # The leading `trap : EXIT &&` stops bash exec-optimising the last member, so bash stays the
    # namespace's PID 1 and reaps orphans while {cmd} runs as an ordinary child.
    # KNOWN RESIDUAL (Decision 59 scope boundary, out of this fix's scope): a {cmd} carrying a
    # top-level `;` or `||` can let its tail run even when the mount or ulimit segment before it
    # failed -- `unshare -rpmn` still engages the user/pid/mount/net namespaces before any of this, so
    # the command stays networkless and outside the host mount namespace either way.
    inner = f"trap : EXIT && mount --bind -o ro {repo_root} {repo_root} && ulimit -u {pid_cap} && {cmd}"
    return [*_NS_PREFIX, "bash", "-c", inner]


def _remove_scratch(td: tempfile.TemporaryDirectory[str], *, sleep: Optional[Callable[[float], None]] = None) -> None:
    """Retry removal: namespace teardown is asynchronous to unshare's exit. Re-raises the last
    OSError after the bounded attempts so a real escape still fails loudly (Decision 55)."""
    pause = sleep if sleep is not None else time.sleep
    for attempt in range(_RM_ATTEMPTS):
        try:
            td.cleanup()
            return
        except OSError:
            if attempt == _RM_ATTEMPTS - 1:
                raise
            pause(_RM_PAUSE_S)


def _probe_env(scratch: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = str(scratch / "pyc")
    env["PYTEST_ADDOPTS"] = f"-o cache_dir={scratch / 'pytest_cache'}"
    return env


def run_one(
    cmd: str,
    *,
    repo_root: Path,
    timeout: Optional[int] = None,
    pid_cap: int = _PID_CAP,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    """Execute one already-classified-`probeable` command under isolation.

    Returns PASS/FAIL/TIMEOUT only -- BUDGET_EXHAUSTED and ISOLATION_UNAVAILABLE are decided by
    the caller (run_all) before any individual probe is attempted. Any launch error (OSError) or
    a non-zero exit is FAIL, never silently treated as PASS.
    """
    eff_timeout = timeout if timeout is not None else (_PYTEST_TIMEOUT_S if is_pytest_command(cmd) else _DEFAULT_TIMEOUT_S)
    td = tempfile.TemporaryDirectory(prefix="backlog_health_probe_", delete=False)
    try:
        scratch = Path(td.name)
        argv = _isolated_argv(cmd, repo_root, pid_cap)
        try:
            result = runner(
                argv,
                cwd=str(repo_root),
                capture_output=True,
                timeout=eff_timeout,
                encoding="utf-8",
                errors="replace",
                env=_probe_env(scratch),
            )
        except subprocess.TimeoutExpired:
            return TIMEOUT
        except OSError:
            return FAIL
    finally:
        _remove_scratch(td)
    return PASS if result.returncode == 0 else FAIL


def run_all(
    probe_payload: list[dict[str, Any]],
    *,
    repo_root: Path,
    main_sha: str,
    global_budget_s: int = _DEFAULT_GLOBAL_BUDGET_S,
    runner: Callable[..., Any] = subprocess.run,
    isolation_check: Callable[[Path], bool] = isolation_available,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run every `probe_payload` entry (as emitted by census.run_census), bounded by a global
    wall-clock budget. Returns {"main_sha": str, "isolation_available": bool, "verdicts":
    {rec_id: verdict}} -- `main_sha` is stamped unconditionally, since a vacuity verdict is
    meaningless without the tree it was measured against.

    If isolation is unavailable, no entry is ever attempted -- every entry gets
    ISOLATION_UNAVAILABLE. Once the global budget is exhausted, every REMAINING entry (not yet
    started) gets BUDGET_EXHAUSTED rather than being silently dropped.
    """
    if not isolation_check(repo_root):
        return {
            "main_sha": main_sha,
            "isolation_available": False,
            "verdicts": {entry["id"]: ISOLATION_UNAVAILABLE for entry in probe_payload},
        }

    verdicts: dict[str, str] = {}
    start = clock()
    for entry in probe_payload:
        if clock() - start >= global_budget_s:
            verdicts[entry["id"]] = BUDGET_EXHAUSTED
            continue
        verdicts[entry["id"]] = run_one(entry["acceptance"], repo_root=repo_root, runner=runner)
    return {"main_sha": main_sha, "isolation_available": True, "verdicts": verdicts}
