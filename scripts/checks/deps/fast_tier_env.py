"""CI-shaped fast-tier environment: build, cache, and re-exec `validate --pre` inside it.

Stdlib-only: scripts/validate.py imports this on every --pre, including CI jobs that carry only pyyaml and pydantic.
PR CI runs the fast tier on requirements-fast.txt + requirements-dev.txt (no duckdb, psycopg2, pytz or radon); the agent
container's full .venv has them, so duckdb-dependent failures pass locally and go red in CI. Re-executing --pre in an
environment built the way CI builds it closes that gap.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from scripts.checks.deps.fast_tier_harness_support import HarnessError, environment_fingerprint

INSTALL_REQUIREMENTS = ("requirements-fast.txt", "requirements-dev.txt")
FULL_ENV_BANNER = "CI-parity: OFF (--full-env) -- this --pre result is not CI-predictive"
REPO_ROOT = Path(__file__).resolve().parents[3]
READY_NAME = ".ready"
PRUNE_AGE_SECONDS = 24 * 3600
_BYPASS_FLAGS = ("--full-env", "--ignore-budget")
_KEEP_AWS = ("AWS_REGION", "AWS_DEFAULT_REGION")
_SCRUBBED = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")

Runner = Callable[[Sequence[str], Path], None]


class FastTierEnvError(RuntimeError):
    """The CI-shaped environment could not be built or reused."""


def default_cache_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = Path(env["XDG_CACHE_HOME"]) if env.get("XDG_CACHE_HOME") else Path.home() / ".cache"
    return base / "ontheloop" / "fast-tier-env"


def env_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run_command(command: Sequence[str], cwd: Path) -> None:
    try:
        result = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FastTierEnvError(f"fast-tier environment setup failed: {' '.join(command)}: {exc}") from exc
    if result.returncode != 0:
        raise FastTierEnvError(
            f"fast-tier environment setup failed: {' '.join(command)}: {(result.stdout + result.stderr).strip()}"
        )


def _acquire(handle: object, blocking: bool) -> bool:
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                return True
            except OSError:
                if not blocking:
                    return False
                time.sleep(0.5)
    fcntl = importlib.import_module("fcntl")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except OSError:
        return False
    return True


@contextlib.contextmanager
def _held(lock_path: Path, *, blocking: bool) -> Iterator[bool]:
    """Yield whether the exclusive lock on lock_path was acquired; the lock drops when the handle closes."""
    with open(lock_path, "a+b") as handle:
        yield _acquire(handle, blocking)


def _fingerprint(repo_root: Path, today: str) -> str:
    try:
        base = environment_fingerprint(repo_root)
    except HarnessError as exc:
        raise FastTierEnvError(str(exc)) from exc
    return hashlib.sha256(f"{base}\n{today}".encode()).hexdigest()


def _is_ready(env_dir: Path, fingerprint: str) -> bool:
    ready = env_dir / READY_NAME
    try:
        stamped = ready.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stamped == fingerprint and env_python(env_dir).is_file()


def _build(env_dir: Path, repo_root: Path, run: Runner) -> None:
    python = env_python(env_dir)
    uv = shutil.which("uv")
    if uv:
        run([uv, "venv", "--python", sys.executable, str(env_dir)], repo_root)
        installs = [
            [uv, "pip", "install", "--python", str(python), "-r", str(repo_root / name)] for name in INSTALL_REQUIREMENTS
        ]
    else:
        run([sys.executable, "-m", "venv", str(env_dir)], repo_root)
        installs = [[str(python), "-m", "pip", "install", "-r", str(repo_root / name)] for name in INSTALL_REQUIREMENTS]
    for install in installs:
        run(install, repo_root)


def _prune(cache_root: Path, keep: str) -> None:
    now = time.time()
    for sibling in cache_root.iterdir():
        ready = sibling / READY_NAME
        if sibling.name == keep or not ready.is_file() or now - ready.stat().st_mtime <= PRUNE_AGE_SECONDS:
            continue
        lock_path = cache_root / f"{sibling.name}.lock"
        with _held(lock_path, blocking=False) as acquired:
            if acquired:
                shutil.rmtree(sibling, ignore_errors=True)
        if not sibling.exists():
            lock_path.unlink(missing_ok=True)


def ensure_env(
    repo_root: Path, *, cache_root: Path | None = None, runner: Runner | None = None, today: str | None = None
) -> tuple[Path, str]:
    """Build or reuse the CI-shaped environment; returns (its python, fingerprint)."""
    run = runner or run_command
    root = cache_root or default_cache_root()
    fingerprint = _fingerprint(repo_root, today or datetime.now(UTC).strftime("%Y-%m-%d"))
    env_dir = root / fingerprint
    root.mkdir(parents=True, exist_ok=True)
    with _held(root / f"{fingerprint}.lock", blocking=True):
        if not _is_ready(env_dir, fingerprint):
            shutil.rmtree(env_dir, ignore_errors=True)
            _build(env_dir, repo_root, run)
            (env_dir / READY_NAME).write_text(f"{fingerprint}\n", encoding="utf-8")
    _prune(root, fingerprint)
    return env_python(env_dir), fingerprint


def child_env(environ: Mapping[str, str], python: Path, fingerprint: str) -> dict[str, str]:
    """The re-exec environment: CI-like (no AWS credentials, no leaked interpreter variables), env python first."""
    env = {
        key: value
        for key, value in environ.items()
        if key not in _SCRUBBED and (not key.startswith("AWS_") or key in _KEEP_AWS)
    }
    existing = environ.get("PATH")
    env["PATH"] = f"{python.parent}{os.pathsep}{existing}" if existing else str(python.parent)
    env["OTL_VENV_PYTHON"] = str(python)
    env["OTL_FAST_TIER_ENV"] = fingerprint
    env["AWS_CONFIG_FILE"] = os.devnull
    env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    env["AWS_EC2_METADATA_DISABLED"] = "true"
    return env


def _should_reexec(argv: Sequence[str], environ: Mapping[str, str]) -> bool:
    if "--pre" not in argv or any(flag in argv for flag in _BYPASS_FLAGS):
        return False
    if environ.get("CI") == "true" or environ.get("PYTEST_CURRENT_TEST"):
        return False
    return int(environ.get("_VALIDATE_DEPTH") or 0) < 1 and "OTL_FAST_TIER_ENV" not in environ


def flush_and_exec(command: Sequence[str], env: Mapping[str, str], cwd: Path) -> None:
    """Replace this process with command (POSIX) or run it and exit with its status (Windows)."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        sys.exit(subprocess.run(list(command), env=dict(env), cwd=cwd).returncode)
    os.chdir(cwd)
    os.execve(command[0], list(command), dict(env))


def reexec_pre_if_needed(argv: Sequence[str], environ: Mapping[str, str]) -> None:
    """Re-run `validate --pre` in the CI-shaped environment unless a no-op condition holds."""
    if not _should_reexec(argv, environ):
        return
    try:
        python, fingerprint = ensure_env(REPO_ROOT)
    except FastTierEnvError as exc:
        print(f"[ERROR] CI-parity environment unavailable: {exc}", file=sys.stderr)
        print(
            "Remedy: fix the cause above and re-run (check package-index access); "
            "--full-env runs in the current interpreter (not CI-predictive).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"CI-parity: running --pre in the CI-shaped fast-tier environment {python}; "
        "it predicts PR CI's fast tier only, not the full tier"
    )
    flush_and_exec([str(python), "-m", "scripts.validate", *argv[1:]], child_env(environ, python, fingerprint), REPO_ROOT)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or reuse the CI-shaped fast-tier environment.")
    parser.add_argument("--ensure", action="store_true", required=True, help="print the environment's python path")
    parser.parse_args(argv)
    try:
        python, _ = ensure_env(REPO_ROOT)
    except FastTierEnvError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(python)
    return 0


if __name__ == "__main__":
    sys.exit(main())
