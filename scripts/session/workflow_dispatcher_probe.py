"""Approval-bypass probe for the workflow dispatcher App (PLAN-scheduled-workflow-dispatch VP 21, operator step M3).

Run by the operator in AWS CloudShell in the production account (Decision 248 cl.8 precedent): the session's GitHub
proxy refuses the App and installation-token paths. It mints an installation token with the register's two
permissions, runs scripts.session.deployment_approval_probe against a pending run with the token in that child's
environment only, revokes the token, and prints the child's JSON line and exit code. It never prints the PEM, the JWT
or the token.

Exit 0 when the child's verdict is TOKEN_LACKS_PERMISSION or NOT_REVIEWER (the token cannot approve), 1 on APPROVED,
2 on anything else (which proves nothing).

    python3.12 -m scripts.session.workflow_dispatcher_probe --run-id N \
        --key-parameter /secret/agent-platform-workflow-dispatcher-app-key
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from src.data.handlers import github_app_token
from src.data.handlers.workflow_dispatcher_handler import TOKEN_PERMISSIONS, load_register

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REFUSALS = {"TOKEN_LACKS_PERMISSION", "NOT_REVIEWER"}
_DNF_LINE = "sudo dnf install -y python3.12 python3.12-pip"


def _region() -> str:
    return (_REPO_ROOT / "config" / "aws-region").read_text(encoding="utf-8").strip()


def _read_pem(parameter: str, profile: str | None) -> str:
    cmd = ["aws", "ssm", "get-parameter", "--name", parameter, "--with-decryption", "--region", _region()]
    cmd += ["--query", "Parameter.Value", "--output", "text"]
    if profile:
        cmd += ["--profile", profile]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        raise RuntimeError(f"aws ssm get-parameter exited {done.returncode}")
    return done.stdout.strip()


def _run_child(repository: str, run_id: int, token: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    cmd = [sys.executable, "-m", "scripts.session.deployment_approval_probe", "--leg", "env:GH_TOKEN"]
    cmd += ["--repo", repository, "--run-id", str(run_id), "--wait-pending", "180"]
    done = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=_REPO_ROOT, check=False
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    return done.returncode, (lines[-1] if lines else "")


def _verdict(line: str) -> str:
    try:
        parsed: Any = json.loads(line)
    except ValueError:
        return ""
    return str(parsed.get("verdict", "")) if isinstance(parsed, dict) else ""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="workflow_dispatcher_probe", description=__doc__)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--key-parameter", required=True)
    p.add_argument("--profile")
    return p


def main(argv: list[str] | None = None, version: tuple[int, int] | None = None) -> int:
    version = version or (sys.version_info.major, sys.version_info.minor)
    if version < (3, 12):
        print(f"Python {version[0]}.{version[1]} is too old; run under python3.12 ({_DNF_LINE})", file=sys.stderr)
        return 2
    args = _parser().parse_args(argv)
    register = load_register()
    if args.key_parameter != register["key_parameter"]:
        print("--key-parameter must equal the register's key_parameter", file=sys.stderr)
        return 2
    pem = _read_pem(args.key_parameter, args.profile)
    api = github_app_token.GitHubApi()
    app_token = github_app_token.app_jwt(register["app"]["client_id"], pem, int(time.time()))
    token = github_app_token.installation_token(api, app_token, register["repository"], dict(TOKEN_PERMISSIONS))
    try:
        code, line = _run_child(register["repository"], args.run_id, token)
    except BaseException:
        try:
            github_app_token.revoke(api, token)
        except Exception as exc:  # noqa: BLE001
            print(f"token revoke also failed: {type(exc).__name__}", file=sys.stderr)
        raise
    github_app_token.revoke(api, token)
    print(line.replace(token, "[redacted]"))
    print(f"child_exit={code}")
    verdict = _verdict(line)
    if verdict in _REFUSALS:
        return 0
    return 1 if verdict == "APPROVED" else 2


if __name__ == "__main__":
    sys.exit(main())
