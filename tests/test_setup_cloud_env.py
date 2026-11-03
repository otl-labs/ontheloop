"""Contract tests for bin/setup-cloud-env.sh without executing networked installers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import botocore.session
import pytest

SETUP_SCRIPT = Path("bin/setup-cloud-env.sh")


def test_shared_session_bootstrap_steps_are_ordered_after_prerequisites() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    ordered_commands = (
        'bash "$REPO_ROOT/bin/sync-deps.sh"',
        "bash .claude/hooks/session_start_aws.sh",
        "bash .claude/hooks/session_start_precommit.sh",
        "bash .claude/hooks/session_start_sync_main.sh",
    )

    positions = [script.index(command) for command in ordered_commands]
    assert positions == sorted(positions)
    assert script.index("set -euo pipefail") < positions[0]
    assert script.index('REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"') < positions[0]


def test_setup_contract_uses_delegated_bootstrap_commands() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert script.count('bash "$REPO_ROOT/bin/sync-deps.sh"') == 1
    assert script.count("bash .claude/hooks/session_start_aws.sh") == 1
    assert script.count("bash .claude/hooks/session_start_precommit.sh") == 1
    assert script.count("bash .claude/hooks/session_start_sync_main.sh") == 1


def test_github_readiness_runs_before_precommit() -> None:
    script = Path("bin/setup-cloud-env.sh").read_text(encoding="utf-8")
    assert script.index("bash .claude/hooks/session_start_github_readiness.sh") < script.index(
        "bash .claude/hooks/session_start_precommit.sh"
    )


def test_github_mcp_server_is_neither_installed_nor_declared() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "ensure-github-mcp-server" not in script
    assert "gh-mcp" not in script
    assert "<PAT>" not in script
    assert "GITHUB_MCP_PAT" not in script
    servers = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "github-full" not in servers
    for name, server in servers.items():
        assert "github-mcp-server" not in " ".join([server.get("command", ""), *server.get("args", [])]), name


_PLACEHOLDER_NAME = "aws-proxy-placeholder-credentials"


def _header_block(script: str, start_marker: str, end_marker: str) -> list[str]:
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(start_marker))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith(end_marker))
    return [line[2:] if line.startswith("# ") else "" for line in lines[start + 1 : end] if line.startswith("#")]


def _template_block(script: str, admin: bool) -> str:
    dev = _header_block(script, "# DEV environment", "# boto3 then resolves")
    body = [line.removeprefix("    ") for line in dev if line.startswith("    ")]
    body = [line for line in body if not line.startswith("cd /home/user/ontheloop")]
    if admin:
        admin_lines = _header_block(script, "# ADMIN environment", "# and set this")
        start = next(i for i, line in enumerate(admin_lines) if line.startswith("    [profile"))
        profile = [line.removeprefix("    ") for line in admin_lines[start:] if line.startswith("    ")]
        eof = max(i for i, line in enumerate(body) if line == "EOF")
        body = body[:eof] + [""] + profile + body[eof:]
    text = "\n".join(body) + "\n"
    return text.replace("<ACCOUNT_ID>", "acct").replace("<PLACEHOLDER>", "extid")


def _install_block(script: str) -> str:
    start = script.index("# 2b. credential_process placeholder")
    end = script.index("# 3. AWS CLI v2")
    return script[start:end]


def _run_template(tmp_path: Path, admin: bool) -> tuple[Path, subprocess.CompletedProcess[str]]:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").write_text("[agent_static]\naws_access_key_id = stale\n", encoding="utf-8")
    harness = f"REPO_ROOT={Path.cwd()}\nlog() {{ :; }}\n" + _template_block(script, admin) + _install_block(script)
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    result = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30, check=False
    )
    return home, result


def _resolve(home: Path, profile: str, monkeypatch: pytest.MonkeyPatch) -> botocore.session.Session:
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(home / ".aws" / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(home / ".aws" / "credentials"))
    return botocore.session.Session(profile=profile)


def test_proxy_template_resolves_agent_static_through_credential_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for admin in (False, True):
        sub = tmp_path / ("admin" if admin else "dev")
        sub.mkdir()
        home, result = _run_template(sub, admin)
        assert result.returncode == 0, result.stderr
        assert not (home / ".aws" / "credentials").exists()
        config = (home / ".aws" / "config").read_text(encoding="utf-8")
        assert "[profile agent_platform]" in config
        assert ("[profile agent_platform_admin]" in config) is admin
        assert config.count("sts_regional_endpoints = regional") == (2 if admin else 1)
        creds = _resolve(home, "agent_static", monkeypatch).get_credentials()
        assert creds.access_key == "proxy-injected-key-id"


def test_placeholder_install_precedes_first_aws_call() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    install = script.index("install -m 0755")
    assert install < script.index("bash .claude/hooks/session_start_aws.sh")
    assert install < script.index('bash "$REPO_ROOT/bin/sync-deps.sh"')


def test_placeholder_install_is_idempotent(tmp_path: Path) -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    harness = f"REPO_ROOT={Path.cwd()}\nlog() {{ :; }}\nset -euo pipefail\n{_install_block(script)}\n{_install_block(script)}"
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, env=env, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    installed = home / ".local" / "bin" / _PLACEHOLDER_NAME
    assert installed.read_bytes() == (Path("bin") / _PLACEHOLDER_NAME).read_bytes()
    assert os.access(installed, os.X_OK)


def test_header_template_has_no_static_key_and_current_path() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "aws_secret_access_key" not in script
    assert "/home/user/agent-platform" not in script
    assert "cd /home/user/ontheloop && bash bin/setup-cloud-env.sh" in script
    allowed = re.search(r"allowed host (\S+) ONLY", script)
    assert allowed is not None
    assert allowed.group(1) == "sts.eu-west-2.amazonaws.com"
    assert "network secret" in script


def test_admin_template_sets_install_terraform_inline() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    admin = "\n".join(_header_block(script, "# ADMIN environment", "# Identity checks"))
    assert "cd /home/user/ontheloop && INSTALL_TERRAFORM=1 bash bin/setup-cloud-env.sh" in admin
    assert "the agent proxy never signs its requests" in admin
    dev = "\n".join(_header_block(script, "# DEV environment", "# boto3 then resolves"))
    assert "INSTALL_TERRAFORM" not in dev


def _role_max_session(resource: str) -> int:
    tf = Path("terraform/bootstrap/platform_roles.tf").read_text(encoding="utf-8")
    match = re.search(r'resource "aws_iam_role" "' + resource + r'" \{.*?max_session_duration\s*=\s*(\d+)', tf, re.DOTALL)
    assert match is not None
    return int(match.group(1))


def test_template_duration_seconds_fit_each_roles_max_session_duration() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    dev = "\n".join(_header_block(script, "# DEV environment", "# boto3 then resolves"))
    admin = "\n".join(_header_block(script, "# ADMIN environment", "# and set this"))
    dev_seconds = int(re.search(r"duration_seconds = (\d+)", dev).group(1))
    admin_seconds = int(re.search(r"duration_seconds = (\d+)", admin).group(1))
    assert dev_seconds <= _role_max_session("platform_dev")
    assert admin_seconds <= _role_max_session("platform_admin")


def _code_lines(script: str) -> str:
    return "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))


def test_setup_fetches_no_terraform_assets() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    code = _code_lines(script)
    assert not re.search(r"aws\s+(s3|secretsmanager|ssm|sts)\b", code)
    assert "materialise_tfvars" not in code
    assert "tf-provider-mirror" not in code
    assert 'bash "$REPO_ROOT/bin/sync-deps.sh"' in code
    admin = "\n".join(_header_block(script, "# ADMIN environment", "# Identity checks"))
    assert "the agent proxy never signs its requests" in admin
