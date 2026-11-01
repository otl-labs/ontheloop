"""Tests for scripts/ci/neon_api_key.sh and the terraform-init-retry delegate.

The stub `aws` records each call; the stub key must never appear in output except on the add-mask line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "neon_api_key.sh"
DELEGATE = ROOT / ".github" / "actions" / "terraform-init-retry" / "neon_api_key_check.sh"
STUB_KEY = "stub-neon-key-0123456789"  # pragma: allowlist secret

_STUB_AWS = """\
#!/usr/bin/env bash
echo "$*" >> "$AWS_STUB_LOG"
case "$AWS_STUB_BEHAVIOR" in
    ok) printf '%s\\n' "$AWS_STUB_VALUE" ;;
    empty) printf '\\n' ;;
    none) printf 'None\\n' ;;
    fail) echo "AccessDeniedException containing $AWS_STUB_VALUE" >&2; exit 254 ;;
esac
"""

_CHILD = 'printf "child:%s\\n" "${TF_VAR_neon_api_key:-unset}" > "$CHILD_OUT"; exit "${CHILD_RC:-0}"'


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    aws = stub_dir / "aws"
    aws.write_text(_STUB_AWS, encoding="utf-8")
    aws.chmod(0o755)
    home = tmp_path / "home"
    runner_temp = tmp_path / "runner"
    home.mkdir()
    runner_temp.mkdir()
    e = os.environ.copy()
    for k in ("GITHUB_EVENT_NAME", "TF_VAR_neon_api_key"):
        e.pop(k, None)
    e.update(
        PATH=f"{stub_dir}:{e['PATH']}",
        HOME=str(home),
        RUNNER_TEMP=str(runner_temp),
        AWS_STUB_LOG=str(tmp_path / "aws.log"),
        AWS_STUB_BEHAVIOR="ok",
        AWS_STUB_VALUE=STUB_KEY,
        CHILD_OUT=str(tmp_path / "child.out"),
        GITHUB_EVENT_NAME="push",
    )
    return e


def _run(env: dict[str, str], *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, cwd=cwd)


def _calls(env: dict[str, str]) -> list[str]:
    log = Path(env["AWS_STUB_LOG"])
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_check_skips_on_pull_request_without_calling_aws(env):
    env["GITHUB_EVENT_NAME"] = "pull_request"
    r = _run(env, "check")
    assert r.returncode == 0
    assert "skipped (no personal-root plan runs on pull_request)" in r.stdout
    assert _calls(env) == []


def test_check_on_pull_request_target_fetches_the_key(env):
    env["GITHUB_EVENT_NAME"] = "pull_request_target"
    r = _run(env, "check")
    assert r.returncode == 0
    assert "fetch ok" in r.stdout
    assert len(_calls(env)) == 1
    assert STUB_KEY not in r.stdout + r.stderr


def test_check_on_push_calls_aws_once_and_prints_no_value(env):
    r = _run(env, "check")
    assert r.returncode == 0
    assert len(_calls(env)) == 1
    assert "get-secret-value" in _calls(env)[0] and "neon-api-key" in _calls(env)[0]
    assert STUB_KEY not in r.stdout + r.stderr


@pytest.mark.parametrize("behavior", ["empty", "none", "fail"])
@pytest.mark.parametrize("sub", ["check", "run"])
def test_fetch_failure_or_empty_fails_closed_without_value(env, behavior, sub):
    env["AWS_STUB_BEHAVIOR"] = behavior
    args = ["check"] if sub == "check" else ["run", "--", "bash", "-c", _CHILD]
    r = _run(env, *args)
    assert r.returncode != 0
    assert STUB_KEY not in r.stdout + r.stderr
    assert not Path(env["CHILD_OUT"]).exists()


def test_run_masks_once_exports_to_child_and_propagates_exit(env):
    r = _run(env, "run", "--", "bash", "-c", _CHILD)
    assert r.returncode == 0
    assert r.stdout.count("::add-mask::") == 1
    assert Path(env["CHILD_OUT"]).read_text(encoding="utf-8").strip() == f"child:{STUB_KEY}"
    others = [ln for ln in (r.stdout + r.stderr).splitlines() if not ln.startswith("::add-mask::")]
    assert all(STUB_KEY not in ln for ln in others)
    env["CHILD_RC"] = "7"
    assert _run(env, "run", "--", "bash", "-c", _CHILD).returncode == 7


def test_run_twice_in_one_job_both_succeed(env):
    assert _run(env, "run", "--", "bash", "-c", _CHILD).returncode == 0
    assert _run(env, "run", "--", "bash", "-c", _CHILD).returncode == 0
    assert len(_calls(env)) == 2


def test_run_does_not_leak_into_parent_env_or_files(env, tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    _run(env, "run", "--", "bash", "-c", _CHILD, cwd=cwd)
    for root in (cwd, Path(env["RUNNER_TEMP"]), Path(env["HOME"])):
        assert not any(root.rglob("*"))


def test_usage_errors(env):
    assert _run(env).returncode != 0
    assert _run(env, "run").returncode != 0
    assert _run(env, "run", "--").returncode != 0
    assert _run(env, "bogus").returncode != 0


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_init_retry_delegate_matches_check(env, tmp_path, event):
    ws = tmp_path / "ws"
    (ws / "scripts" / "ci").mkdir(parents=True)
    shutil.copy(SCRIPT, ws / "scripts" / "ci" / "neon_api_key.sh")
    env["GITHUB_WORKSPACE"] = str(ws)
    env["GITHUB_EVENT_NAME"] = event
    via_delegate = subprocess.run(["bash", str(DELEGATE)], env=env, capture_output=True, text=True)
    n_calls = len(_calls(env))
    direct = _run(env, "check")
    assert (via_delegate.returncode, via_delegate.stdout) == (direct.returncode, direct.stdout)
    assert n_calls == (0 if event == "pull_request" else 1)
