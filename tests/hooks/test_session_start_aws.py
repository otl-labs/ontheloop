"""Tests for .claude/hooks/session_start_aws.sh using a stub bin/venv-python and a stub aws."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.ci.redaction_check import ACCOUNT_ID_SHAPE, UNIQUE_ID_SHAPE

HOOK = Path(".claude/hooks/session_start_aws.sh").resolve()


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(
    tmp_path: Path, helper_body: str, with_config: bool = True, transport: str | None = "execute_api"
) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path / "repo"
    (root / "bin").mkdir(parents=True)
    _stub(root / "bin" / "venv-python", helper_body)
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    if with_config:
        (home / ".aws" / "config").write_text("[profile agent_platform]\n", encoding="utf-8")
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    argv_log = tmp_path / "aws-argv.log"
    _stub(fake_bin / "aws", f'echo "$@" >> "{argv_log}"\nexit 0\n')
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(home),
        "CLAUDE_PROJECT_DIR": str(root),
    }
    if transport is not None:
        env["OTL_OPS_VERBS_TRANSPORT"] = transport
    result = subprocess.run(
        ["bash", str(HOOK)], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30, check=False
    )
    return result, argv_log


_SESSION_ARN = "arn:aws:iam::[ACCOUNT_ID]:user/agent-platform-session"


def test_reports_session_user(tmp_path: Path) -> None:
    helper = f'if [ "$1" = "-c" ]; then exit 0; fi\necho "$@" > "$(dirname "$0")/helper-argv"\necho "{_SESSION_ARN}"\n'
    result, argv_log = _run(tmp_path, helper)
    argv = (tmp_path / "repo" / "bin" / "helper-argv").read_text(encoding="utf-8").strip()
    assert argv == "-m scripts.aws_identity caller-arn --profile agent_platform"
    assert result.returncode == 0
    assert "OK: user/agent-platform-session" in result.stdout
    assert "WARNING" not in result.stdout
    assert "arn:" not in result.stdout and not ACCOUNT_ID_SHAPE.search(result.stdout)
    assert not argv_log.exists() or "sts" not in argv_log.read_text(encoding="utf-8").split()


def test_warns_on_assumed_role(tmp_path: Path) -> None:
    helper = 'if [ "$1" = "-c" ]; then exit 0; fi\necho "arn:aws:sts::[ACCOUNT_ID]:assumed-role/PlatformDev/s"\n'
    result, _ = _run(tmp_path, helper)
    assert result.returncode == 0
    assert "WARNING" in result.stdout and "bin/setup-cloud-env.sh" in result.stdout
    assert "OK:" not in result.stdout


def test_warns_on_near_miss_user(tmp_path: Path) -> None:
    helper = 'if [ "$1" = "-c" ]; then exit 0; fi\necho "arn:aws:iam::[ACCOUNT_ID]:user/agent-platform-session-x"\n'
    result, _ = _run(tmp_path, helper)
    assert "WARNING" in result.stdout and "OK:" not in result.stdout


@pytest.mark.parametrize("transport", [None, "function_url"])
def test_warns_when_transport_is_not_execute_api(tmp_path: Path, transport: str | None) -> None:
    helper = f'if [ "$1" = "-c" ]; then exit 0; fi\necho "{_SESSION_ARN}"\n'
    result, _ = _run(tmp_path, helper, transport=transport)
    assert "OK: user/agent-platform-session" in result.stdout
    assert "OTL_OPS_VERBS_TRANSPORT" in result.stdout and "agent_session_identity" in result.stdout


_ACCT = str(10**11 + 7)
_UNIQUE = "AROA" + "B" * 17


@pytest.mark.parametrize(
    ("helper_body", "expected"),
    [
        (f'echo "arn:aws:iam::{_ACCT}:user/agent-platform-session"\n', "OK: user/agent-platform-session"),
        ('echo "arn:aws:iam::[ACCOUNT_ID]:user/agent-platform-session"\n', "OK: user/agent-platform-session"),
        (
            f'echo "arn:aws:sts::{_ACCT}:assumed-role/PlatformDev/botocore-session-1"\n',
            "did not resolve to user/agent-platform-session",
        ),
        (f'echo "arn:aws:iam::{_ACCT}:user/agent-static {_UNIQUE}"\n', "did not resolve to user/agent-platform-session"),
        (
            (
                f'echo "boom arn:aws:iam::{_ACCT}:user/x {_UNIQUE}" >&2\n'
                'echo "caller-arn failed: ClientError AccessDenied" >&2\nexit 1\n'
            ),
            "caller-arn failed: ClientError AccessDenied",
        ),
        (f'echo "arn:aws:iam::{_ACCT}:user/x" >&2\nexit 1\n', "helper exited with no diagnostic line"),
    ],
    ids=[
        "unmasked-success",
        "masked-success",
        "assumed-role",
        "iam-user",
        "failure-with-diagnostic",
        "failure-without-diagnostic",
    ],
)
def test_output_never_contains_account_id_or_arn(tmp_path: Path, helper_body: str, expected: str) -> None:
    result, _ = _run(tmp_path, 'if [ "$1" = "-c" ]; then exit 0; fi\n' + helper_body)
    combined = result.stdout + result.stderr
    assert result.returncode == 0
    assert not ACCOUNT_ID_SHAPE.search(combined)
    assert not UNIQUE_ID_SHAPE.search(combined)
    assert "arn:" not in combined
    assert expected in combined


def test_warns_and_exits_zero_when_helper_fails(tmp_path: Path) -> None:
    helper = 'if [ "$1" = "-c" ]; then exit 0; fi\necho "caller-arn failed: ClientError InvalidClientTokenId" >&2\nexit 1\n'
    result, _ = _run(tmp_path, helper)
    assert result.returncode == 0
    assert "WARNING: could not read the identity" in result.stdout
    assert "InvalidClientTokenId" in result.stdout
    assert "network secret" in result.stdout
    assert "external_id" not in result.stdout and "assume" not in result.stdout.lower()


def test_skips_without_aws_config(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, "exit 0\n", with_config=False)
    assert result.returncode == 0
    assert "not materialised" in result.stdout


def test_skips_when_venv_not_ready(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, 'if [ "$1" = "-c" ]; then exit 1; fi\necho should-not-run\n')
    assert result.returncode == 0
    assert "skipping credential check" in result.stdout
    assert "should-not-run" not in result.stdout


def test_hook_never_calls_aws_sts() -> None:
    code = [line for line in HOOK.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    assert "aws sts" not in "\n".join(code)
