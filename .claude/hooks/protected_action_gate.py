#!/usr/bin/env python3
"""PreToolUse hook entry for the protected-action gate -- PLUMBING ONLY (rec-4518, Decision 239 cl.5).

Reads the hook JSON from stdin, the environment, the contract at origin/main, the session transcript and git state, and
calls the pure modules in scripts/session/ (protected_action_match, _shell, _approvals), which hold every decision.
Exit 0 with no output allows; exit 2 with the reason on stderr denies. It never prints to stdout and never returns a
permission decision, so it cannot bypass the auto-mode classifier. Malformed hook input exits 0 (nothing to classify).
The registered command is protected_action_gate.sh, which maps a failure of this file to a deny for coarse payloads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[2]))

CONTRACT_PATH = "docs/contracts/protected-actions.yaml"
_BASH = ("Bash", "Monitor")


def _err(message: str) -> None:
    sys.stderr.write(message.rstrip("\n") + "\n")


def _git(repo: str, *args: str, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def _load_contract(repo: str) -> tuple[dict[str, Any] | None, str]:
    import yaml

    try:
        shown = _git(repo, "show", f"origin/main:{CONTRACT_PATH}")
        if shown.returncode != 0:
            if _git(repo, "cat-file", "-e", "origin/main").returncode == 0:
                return None, (
                    "the gate is not merged yet (origin/main has no protected-actions contract); "
                    "finish the build in its starting session"
                )
            return None, "the protected-actions contract could not be read from origin/main; run git fetch origin main"
        data = yaml.safe_load(shown.stdout)
    except (OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        return None, f"the protected-actions contract could not be read ({type(exc).__name__}); run git fetch origin main"
    if not (isinstance(data, dict) and isinstance(data.get("gate"), dict) and isinstance(data.get("approval"), dict)):
        return None, "the protected-actions contract at origin/main is invalid; run git fetch origin main"
    if not isinstance(data.get("classes"), list) or not data["classes"]:
        return None, "the protected-actions contract at origin/main has no classes; run git fetch origin main"
    return data, ""


def _read_transcript(path: str) -> list[dict[str, Any]] | None:
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.split("\n")
    if lines and lines[-1] != "":
        lines = lines[:-1]
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return None
        if not isinstance(rec, dict):
            return None
        records.append(rec)
    return records


def _git_state(root_dir: str) -> tuple[str, str]:
    shown = _git(root_dir, "rev-parse", "--show-toplevel", "HEAD")
    if shown.returncode != 0:
        raise RuntimeError("rev-parse failed")
    top, head = shown.stdout.split("\n")[:2]
    status = _git(top, "status", "--porcelain", "--untracked-files=normal")
    if status.returncode != 0:
        raise RuntimeError("status failed")
    return head.strip(), status.stdout


def _bash_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ""


def _decide(payload: dict[str, Any]) -> int:
    from scripts.session import protected_action_approvals as approvals
    from scripts.session import protected_action_match as match

    tool = payload.get("tool_name")
    raw_input = payload.get("tool_input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    raw_path = payload.get("transcript_path")
    transcript: str = raw_path if isinstance(raw_path, str) else ""
    raw_cwd = payload.get("cwd")
    input_cwd: str = raw_cwd if isinstance(raw_cwd, str) and raw_cwd else os.getcwd()
    home = os.environ.get("HOME", "")
    reason = match.tamper_message(str(tool), tool_input, transcript, home, input_cwd)
    if reason:
        _err(reason)
        return 2
    command = _bash_command(payload)
    if tool not in _BASH or not command:
        return 0
    ambient = os.environ.get("AWS_PROFILE", "")
    if match.fast_path_ok(command, ambient):
        return 0
    repo: str = os.environ.get("CLAUDE_PROJECT_DIR") or input_cwd
    contract, failure = _load_contract(repo)
    if contract is None:
        _err(f"protected-action-gate: {failure}.")
        return 2
    try:
        hits = match.classify(command, contract, ambient, input_cwd)
    except ValueError:
        _err("protected-action-gate: the command could not be parsed and names a protected keyword; rewrite it more simply.")
        return 2
    if not hits:
        return 0
    records = _read_transcript(transcript)
    if records is None:
        _err("protected-action-gate: the session transcript is unreadable, so no approval can be verified; command refused.")
        return 2

    def classify_prev(prev: str, cwd: str | None) -> list[tuple[str, str]]:
        try:
            return [(h.cls, h.target) for h in match.classify(prev, contract, ambient, cwd or input_cwd)]
        except ValueError:
            return []

    denials = approvals.evaluate(
        hits,
        records,
        agent_id=bool(payload.get("agent_id")),
        tool_use_id=payload.get("tool_use_id") if isinstance(payload.get("tool_use_id"), str) else None,
        input_cwd=input_cwd,
        classify_prev=classify_prev,
        git_state=_git_state,
    )
    if not denials:
        return 0
    _err(approvals.deny_message(denials))
    return 2


def _coarse(payload: dict[str, Any]) -> bool:
    from scripts.session import protected_action_match as match

    command = _bash_command(payload)
    ambient = os.environ.get("AWS_PROFILE", "")
    return payload.get("tool_name") in _BASH and bool(command) and not match.fast_path_ok(command, ambient)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        return _decide(payload)
    except Exception as exc:  # noqa: BLE001 - an unexpected failure denies exactly what the fast path would not have passed
        try:
            coarse = _coarse(payload)
        except Exception:  # noqa: BLE001
            coarse = False
        if coarse:
            _err(f"protected-action-gate: internal error ({type(exc).__name__}); protected-looking command refused.")
            return 2
        return 0


if __name__ == "__main__":
    sys.exit(main())
