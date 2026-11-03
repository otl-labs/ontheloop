"""Adapter and shim tests for the handoff evidence gate.

Anti-vacuity: every deny assertion checks the expected rule code in stderr AND the absence of
"handoff gate unavailable" and "handoff gate error", so a broken interpreter cannot satisfy it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_HOOK_PATH = _REPO_ROOT / ".claude" / "hooks" / "handoff_evidence_gate.py"
_SHIM_PATH = _REPO_ROOT / ".claude" / "hooks" / "handoff_evidence_gate.sh"
_SETTINGS_PATH = _REPO_ROOT / ".claude" / "settings.json"

_spec = importlib.util.spec_from_file_location("handoff_evidence_gate", _HOOK_PATH)
_hook_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_hook_mod)  # type: ignore[union-attr]

_GIT_ENV = ["-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


_PYTHON3 = subprocess.run(["which", "python3"], capture_output=True, text=True).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(["init", "--bare", "-b", "main"], cwd=origin)
    work = tmp_path / "work"
    work.mkdir()
    _git(["init", "-b", "main"], cwd=work)
    _git(["remote", "add", "origin", str(origin)], cwd=work)
    (work / "docs" / "contracts").mkdir(parents=True)
    (work / "docs" / "contracts" / "git-ops.yaml").write_text(
        "branching_topology:\n  agent_branch_prefixes: [claude/, agent/]\n"
        "handoff_evidence_gate:\n  exempt_path_globs: []\n  never_exempt_paths: []\n",
        encoding="utf-8",
    )
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=work)
    _git([*_GIT_ENV, "commit", "-m", "initial"], cwd=work)
    _git(["push", "-u", "origin", "main"], cwd=work)
    return work


def _new_branch_worktree(work: Path, branch: str) -> Path:
    parent = work.parent / f"wt-{branch.replace('/', '-')}"
    _git(["worktree", "add", "-b", branch, str(parent), "main"], cwd=work)
    return parent


def _commit_file(worktree: Path, rel_path: str, content: str) -> str:
    target = worktree / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(["add", "-A"], cwd=worktree)
    _git([*_GIT_ENV, "commit", "-m", "change"], cwd=worktree)
    return _git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def _write_real_evidence(worktree: Path) -> None:
    from scripts.checks import validation_result

    path = validation_result.evidence_path_for(worktree)
    orig_root = validation_result._common.ROOT
    try:
        validation_result._common.ROOT = worktree
        validation_result.clear(path)
        validation_result.write_completed_visible(
            started_at=validation_result.utc_now(), exit_code=0, failed_checks=[], path=path
        )
    finally:
        validation_result._common.ROOT = orig_root


def _run_adapter(payload: dict) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(json.dumps(payload))),
        patch("sys.stdout", stdout),
        patch("sys.stderr", stderr),
    ):
        rc = _hook_mod.main()
    return rc, stdout.getvalue(), stderr.getvalue()


def test_git_push_denied_without_matching_evidence(tmp_path: Path) -> None:
    """rec-4083's acceptance node (VERBATIM, module-level): a canonical push with no matching
    evidence is denied naming its rule code; the SAME push with attested evidence is the
    positive control and exits 0."""
    work = _make_repo(tmp_path)
    wt = _new_branch_worktree(work, "claude/feature")
    _commit_file(wt, "src/thing.py", "print(1)\n")

    payload = {"tool_name": "Bash", "tool_input": {"command": f"git -C {wt} push -u origin HEAD"}}
    rc, _out, err = _run_adapter(payload)
    assert rc == 2
    assert "handoff-evidence-gate: missing:" in err
    assert "handoff gate unavailable" not in err
    assert "handoff gate error" not in err

    _write_real_evidence(wt)
    rc2, _out2, err2 = _run_adapter(payload)
    assert rc2 == 0
    assert err2 == ""


class TestAdapterRouting:
    def test_bash_non_push_command_exits_zero_without_denial(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 0
        assert err == ""

    def test_monitor_routes_through_same_classifier(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        payload = {"tool_name": "Monitor", "tool_input": {"command": f"git -C {wt} push -u origin HEAD"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 2
        assert "handoff-evidence-gate:" in err

    def test_monitor_ws_payload_with_no_command_exits_zero(self) -> None:
        payload = {"tool_name": "Monitor", "tool_input": {"ws": "some-session-id"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 0
        assert err == ""

    def test_non_canonical_push_shaped_denied(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 2
        assert "non_canonical" in err

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp__github__create_pull_request",
            "mcp__github__push_files",
            "mcp__github__create_or_update_file",
            "mcp__github__delete_file",
            "mcp__github-full__create_pull_request",
            "mcp__github-full__push_files",
            "mcp__github-full__create_or_update_file",
            "mcp__github-full__delete_file",
        ],
    )
    def test_every_mcp_tool_name_is_routed(self, tool_name: str) -> None:
        payload = {"tool_name": tool_name, "tool_input": {"branch": "main", "path": "scripts/x.py"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 2
        assert "handoff-evidence-gate: destination:" in err

    def test_create_pull_request_routes_to_decide_pr(self) -> None:
        payload = {"tool_name": "mcp__github__create_pull_request", "tool_input": {"head": "owner:branch"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 2
        assert "cross_repo_head" in err

    def test_push_files_multiple_paths_routes_correctly(self) -> None:
        payload = {
            "tool_name": "mcp__github__push_files",
            "tool_input": {"branch": "main", "files": [{"path": "a.py"}, {"path": "b.py"}]},
        }
        rc, _out, err = _run_adapter(payload)
        assert rc == 2
        assert "destination" in err

    def test_warn_emits_additional_context_and_system_message_never_permission_decision(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        from scripts.checks import validation_result

        path = validation_result.evidence_path_for(wt)
        orig_root = validation_result._common.ROOT
        try:
            validation_result._common.ROOT = wt
            validation_result.clear(path)
            validation_result.write_completed_visible(
                started_at=validation_result.utc_now(), exit_code=1, failed_checks=["x"], path=path
            )
        finally:
            validation_result._common.ROOT = orig_root

        payload = {"tool_name": "Bash", "tool_input": {"command": f"git -C {wt} push -u origin HEAD"}}
        rc, out, err = _run_adapter(payload)
        assert rc == 0
        assert err == ""
        data = json.loads(out)
        assert "permissionDecision" not in data
        assert "permissionDecision" not in data.get("hookSpecificOutput", {})
        assert data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "additionalContext" in data["hookSpecificOutput"]
        assert "systemMessage" in data

    def test_unrouted_tool_name_exits_zero(self) -> None:
        payload = {"tool_name": "Read", "tool_input": {"file_path": "x.py"}}
        rc, _out, err = _run_adapter(payload)
        assert rc == 0
        assert err == ""

    def test_main_wraps_any_exception_and_fails_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO("not json")),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            rc = _hook_mod.main()
        assert rc == 2
        assert "handoff gate error" in stderr.getvalue()


def _real_env(**overrides: str) -> dict[str, str]:
    """Inherit the REAL environment (so bin/venv-python's venv-less PATH fallback keeps
    working on a venv-less CI runner) rather than a hardcoded PATH that only happens to work
    locally where .venv exists."""
    env = dict(os.environ)
    env.pop("GITHUB_ACTIONS", None)
    env["CLAUDE_PROJECT_DIR"] = str(_REPO_ROOT)
    env.update(overrides)
    return env


def _run_shim(payload: dict, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = _real_env()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(_SHIM_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_shim_fails_closed_when_adapter_crashes(tmp_path: Path) -> None:
    """Graduated node (module-level, kept fast for the VP replay budget -- Decision 55: the
    real-timeout scenario is split into test_shim_kills_adapter_that_sleeps_past_bound below,
    which exercises the same shim unmodified): an adapter forced to raise, an unstartable
    interpreter, an unset CLAUDE_PROJECT_DIR, an unrelated hook cwd and a pretty-printed MCP
    payload each give exit 2 for a push-shaped payload; a non-push payload still exits 0."""
    push_payload = {"tool_name": "Bash", "tool_input": {"command": "git push -u origin HEAD"}}
    non_push_payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}

    # Adapter crashes: point CLAUDE_PROJECT_DIR at a repo whose adapter is a syntax-broken stub.
    broken_root = tmp_path / "broken_root"
    (broken_root / ".claude" / "hooks").mkdir(parents=True)
    (broken_root / ".claude" / "hooks" / "handoff_evidence_gate.sh").write_text(
        _SHIM_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (broken_root / ".claude" / "hooks" / "handoff_evidence_gate.py").write_text(
        "raise RuntimeError('boom')\n", encoding="utf-8"
    )
    (broken_root / "bin").mkdir()
    (broken_root / "bin" / "venv-python").write_text(
        f'#!/usr/bin/env bash\nexec {_PYTHON3} "$@"\n',
        encoding="utf-8",
    )
    (broken_root / "bin" / "venv-python").chmod(0o755)
    result = subprocess.run(
        ["bash", str(broken_root / ".claude" / "hooks" / "handoff_evidence_gate.sh")],
        input=json.dumps(push_payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(broken_root)},
        timeout=60,
    )
    assert result.returncode == 2

    # Unstartable interpreter.
    no_python_root = tmp_path / "no_python_root"
    (no_python_root / ".claude" / "hooks").mkdir(parents=True)
    (no_python_root / ".claude" / "hooks" / "handoff_evidence_gate.sh").write_text(
        _SHIM_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (no_python_root / ".claude" / "hooks" / "handoff_evidence_gate.py").write_text("", encoding="utf-8")
    (no_python_root / "bin").mkdir()
    (no_python_root / "bin" / "venv-python").write_text("#!/usr/bin/env bash\nexit 127\n", encoding="utf-8")
    (no_python_root / "bin" / "venv-python").chmod(0o755)
    result = subprocess.run(
        ["bash", str(no_python_root / ".claude" / "hooks" / "handoff_evidence_gate.sh")],
        input=json.dumps(push_payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(no_python_root)},
        timeout=60,
    )
    assert result.returncode == 2

    # CLAUDE_PROJECT_DIR unset: shim falls back to its own on-disk location (the real repo).
    result = subprocess.run(
        ["bash", str(_SHIM_PATH)],
        input=json.dumps(non_push_payload),
        capture_output=True,
        text=True,
        env={k: v for k, v in _real_env().items() if k != "CLAUDE_PROJECT_DIR"},
        timeout=60,
    )
    assert result.returncode == 0

    # An unrelated hook cwd (still resolves via CLAUDE_PROJECT_DIR / its own dirname).
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    result = subprocess.run(
        ["bash", str(_SHIM_PATH)],
        input=json.dumps(non_push_payload),
        capture_output=True,
        text=True,
        cwd=unrelated,
        env=_real_env(),
        timeout=60,
    )
    assert result.returncode == 0

    # A pretty-printed MCP payload still matches the pre-filter.
    pretty_payload = json.dumps({"tool_name": "mcp__github__create_pull_request", "tool_input": {"head": "x"}}, indent=2)
    result = subprocess.run(
        ["bash", str(_SHIM_PATH)],
        input=pretty_payload,
        capture_output=True,
        text=True,
        env=_real_env(),
        timeout=60,
    )
    assert result.returncode in (0, 2)  # denies unless head happens to resolve; never "unavailable"
    assert "handoff gate unavailable" not in result.stderr

    # A non-push payload still exits 0 through the real shim.
    result = _run_shim(non_push_payload)
    assert result.returncode == 0


def test_shim_pins_the_production_timeout_bound() -> None:
    """Static pin (fast, no subprocess wait): the shim's real bound stays 45s with a 5s kill
    grace, below the registered settings.json timeout of 60s."""
    text = _SHIM_PATH.read_text(encoding="utf-8")
    assert "timeout -k 5 45" in text


def test_shim_kills_adapter_that_sleeps_past_bound(tmp_path: Path) -> None:
    """Dynamic proof of the SAME kill mechanism the production 45s bound uses, scaled down to
    keep this fast-tier-selected test cheap: a copy of the real shim with its timeout/kill-grace
    substituted to 2s/1s (never the production file) kills an adapter that outlives it, and the
    shim still exits 2 for a push-shaped payload."""
    push_payload = {"tool_name": "Bash", "tool_input": {"command": "git push -u origin HEAD"}}
    slow_root = tmp_path / "slow_root"
    (slow_root / ".claude" / "hooks").mkdir(parents=True)
    scaled_down_shim = _SHIM_PATH.read_text(encoding="utf-8").replace("timeout -k 5 45", "timeout -k 1 2")
    assert "timeout -k 1 2" in scaled_down_shim
    (slow_root / ".claude" / "hooks" / "handoff_evidence_gate.sh").write_text(scaled_down_shim, encoding="utf-8")
    (slow_root / ".claude" / "hooks" / "handoff_evidence_gate.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    (slow_root / "bin").mkdir()
    (slow_root / "bin" / "venv-python").write_text(
        f'#!/usr/bin/env bash\nexec {_PYTHON3} "$@"\n',
        encoding="utf-8",
    )
    (slow_root / "bin" / "venv-python").chmod(0o755)
    result = subprocess.run(
        ["bash", str(slow_root / ".claude" / "hooks" / "handoff_evidence_gate.sh")],
        input=json.dumps(push_payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(slow_root)},
        timeout=15,
    )
    assert result.returncode == 2


class TestShimFailClosed:
    def test_github_actions_carve_out_exits_zero(self, tmp_path: Path) -> None:
        push_payload = {"tool_name": "Bash", "tool_input": {"command": "git push -u origin HEAD"}}
        result = _run_shim(push_payload, {"GITHUB_ACTIONS": "true"})
        assert result.returncode == 0
        assert "carve-out" in result.stderr

    def test_prefilter_skips_python_for_non_push_non_mcp_calls(self) -> None:
        result = _run_shim({"tool_name": "Bash", "tool_input": {"command": "echo hello world"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_real_shim_denies_non_canonical_push(self) -> None:
        result = _run_shim({"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
        assert result.returncode == 2
        assert "non_canonical" in result.stderr


def test_settings_registers_gate_on_push_and_remote_write_tools() -> None:
    """Graduated node: the new entry, its anchored matcher accepting all eleven names and
    rejecting a superstring, the explicit timeout, the shim command, and the pre-existing
    entry unchanged."""
    data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    pre_tool_use = data["hooks"]["PreToolUse"]

    original_entry = pre_tool_use[0]
    assert original_entry["matcher"] == "Edit|Write|MultiEdit|NotebookEdit|Bash"
    assert len(original_entry["hooks"]) == 4

    gate_entries = [e for e in pre_tool_use if any("handoff_evidence_gate.sh" in h["command"] for h in e["hooks"])]
    assert len(gate_entries) == 1
    gate_entry = gate_entries[0]
    matcher = gate_entry["matcher"]
    assert matcher.startswith("^") and matcher.endswith("$")

    import re

    pattern = re.compile(matcher)
    expected_names = [
        "Bash",
        "Monitor",
        "mcp__github__create_pull_request",
        "mcp__github__push_files",
        "mcp__github__create_or_update_file",
        "mcp__github__delete_file",
        "mcp__github-full__create_pull_request",
        "mcp__github-full__push_files",
        "mcp__github-full__create_or_update_file",
        "mcp__github-full__delete_file",
    ]
    assert len(expected_names) == 10
    for name in expected_names:
        assert pattern.fullmatch(name), f"{name} should match {matcher}"
    assert not pattern.fullmatch("XBash")
    assert not pattern.fullmatch("BashX")

    hook = gate_entry["hooks"][0]
    assert hook["type"] == "command"
    assert "handoff_evidence_gate.sh" in hook["command"]
    assert hook["timeout"] == 60
