"""Secret-safe, non-mutating GitHub capability readiness probe."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from scripts import repo_identity

ROOT = Path(__file__).resolve().parents[2]


def _run(command: list[str], timeout: int = 15) -> tuple[str, str]:
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", type(exc).__name__
    return ("pass", "") if result.returncode == 0 else ("fail", f"exit {result.returncode}")


def _authenticated_read(timeout: int = 15) -> tuple[str, str]:
    token_path = Path.home() / ".config" / "gh-mcp" / "token"
    try:
        token = token_path.read_text(encoding="utf-8").strip()
        if not token:
            return "unknown", "token unavailable"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo_identity.full_name()}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return ("pass", "") if response.status == 200 else ("fail", f"http {response.status}")
    except FileNotFoundError:
        return "unknown", "token unavailable"
    except (OSError, urllib.error.URLError) as exc:
        return "unknown", type(exc).__name__


def probe() -> dict[str, dict[str, str]]:
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    branch = branch_result.stdout.strip()
    fetch = _run(["git", "fetch", "--dry-run", "origin", "main"])
    api_read = _authenticated_read()
    push = ("unknown", "requires a non-main feature branch")
    if branch and branch != "main":
        push = _run(["git", "push", "--dry-run", "origin", f"HEAD:refs/heads/{branch}"])
    binary = ROOT / "bin" / "github-mcp-server"
    if not binary.exists():
        binary = Path.home() / ".local" / "bin" / "github-mcp-server"
    config = ROOT / ".mcp.json"
    token = Path.home() / ".config" / "gh-mcp" / "token"
    mcp_state = "pass" if binary.exists() and config.exists() and token.exists() else "unknown"
    return {
        "anonymous_fetch": {"status": fetch[0], "detail": fetch[1]},
        "authenticated_repository_read": {"status": api_read[0], "detail": api_read[1]},
        "feature_branch_push_dry_run": {"status": push[0], "detail": push[1]},
        "github_mcp_runtime": {"status": mcp_state, "detail": "configured" if mcp_state == "pass" else "incomplete"},
        "pr_write": {"status": "unknown", "detail": "not safely provable without mutation"},
    }


def main() -> int:
    print(json.dumps(probe(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
