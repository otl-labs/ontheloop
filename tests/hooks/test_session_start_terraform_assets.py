"""Tests for .claude/hooks/session_start_terraform_assets.sh using a stub bin/sync-terraform-assets.sh."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

REAL_ROOT = Path(__file__).resolve().parents[2]
HOOK = REAL_ROOT / ".claude" / "hooks" / "session_start_terraform_assets.sh"
COMMAND = "bash .claude/hooks/session_start_terraform_assets.sh"


def _stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(
    tmp_path: Path, sync_body: str, admin: bool = True, terraform: bool = True
) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path / "repo"
    hook = root / ".claude" / "hooks" / HOOK.name
    hook.parent.mkdir(parents=True)
    shutil.copy(HOOK, hook)
    record = tmp_path / "sync.argv"
    _stub(root / "bin" / "sync-terraform-assets.sh", f'echo "calls: $#:$*" >> "{record}"\n{sync_body}')
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    profiles = "[profile agent_platform]\n" + ("[profile agent_platform_admin]\n" if admin else "")
    (home / ".aws" / "config").write_text(profiles, encoding="utf-8")
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _stub(fake_bin / "aws", f'echo aws-called >> "{tmp_path / "aws.log"}"\n')
    if terraform:
        _stub(fake_bin / "terraform", "exit 0\n")
    keep = [d for d in os.environ["PATH"].split(":") if not (Path(d) / "terraform").exists()]
    env = {"PATH": f"{fake_bin}:{':'.join(keep)}", "HOME": str(home)}
    result = subprocess.run(
        ["bash", str(hook)], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30, check=False
    )
    return result, record


def test_dev_session_skips_without_an_aws_call(tmp_path: Path) -> None:
    result, record = _run(tmp_path, "exit 0\n", admin=False)
    assert result.returncode == 0
    assert "skipped" in result.stdout
    assert not record.exists()
    assert not (tmp_path / "aws.log").exists()


def test_admin_session_runs_the_mirror_sync_only(tmp_path: Path) -> None:
    result, record = _run(tmp_path, 'echo "mirror-sync: OK (1s)"\n')
    assert result.returncode == 0
    assert record.read_text(encoding="utf-8").splitlines() == ["calls: 0:"]
    assert "[session_start_terraform_assets] mirror-sync: OK (1s)" in result.stdout
    code = [ln for ln in HOOK.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    assert not any("--tfvars" in ln for ln in code)


def test_failed_sync_still_exits_zero_with_a_warning(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, 'echo "mirror-sync: WARNING NETWORK (3s)"\nexit 1\n')
    assert result.returncode == 0
    assert "[session_start_terraform_assets] mirror-sync: WARNING NETWORK (3s)" in result.stdout


def test_missing_terraform_warns_and_still_syncs(tmp_path: Path) -> None:
    result, record = _run(tmp_path, 'echo "mirror-sync: OK (1s)"\n', terraform=False)
    assert result.returncode == 0
    assert "WARNING: no terraform binary" in result.stdout
    assert record.exists()


def test_hook_is_registered_with_a_timeout() -> None:
    settings = json.loads((REAL_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    first_group = settings["hooks"]["SessionStart"][0]["hooks"]
    assert first_group[-1] == {"type": "command", "command": COMMAND, "timeout": 60}
    assert first_group[0]["command"] == "bash .claude/hooks/session_start_aws.sh"
