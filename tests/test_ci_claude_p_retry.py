"""Unit tests for scripts/ci/claude_p_retry.sh and validate.py enforcement check.

Tests:
  (a) transient-5xx on attempt 1 then PROCEED on attempt 2 -> exit 0, PROCEED in output, 2 attempts
  (b) substantive REVISE output -> exactly 1 attempt (anti-masking, Decision 55)
  (c) persistent 5xx -> exactly 3 attempts, non-zero exit, error text in output
  (d) enforcement check flags fixture workflow with raw `claude -p`; passes the wrapped form
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

WRAPPER = ROOT / "scripts" / "ci" / "claude_p_retry.sh"

_STUB_CLAUDE = """\
#!/usr/bin/env bash
if [ -n "${CLAUDE_STUB_ARGV_FILE:-}" ]; then
    printf '%s\\n' "$*" >> "$CLAUDE_STUB_ARGV_FILE"
fi
COUNT=0
if [ -f "$CLAUDE_STUB_COUNTER_FILE" ]; then
    COUNT=$(cat "$CLAUDE_STUB_COUNTER_FILE")
fi
NEW_COUNT=$((COUNT + 1))
echo "$NEW_COUNT" > "$CLAUDE_STUB_COUNTER_FILE"
case "$CLAUDE_STUB_BEHAVIOR" in
    transient_then_proceed)
        if [ "$NEW_COUNT" -eq 1 ]; then
            echo "API Error: 500 Internal server error"
            exit 1
        else
            echo "PROCEED"
            exit 0
        fi
        ;;
    always_revise)
        echo "REVISE because the plan contains unexpected changes"
        exit 1
        ;;
    always_transient)
        echo "API Error: 500 Internal server error"
        exit 1
        ;;
    *)
        echo "Unknown behavior: $CLAUDE_STUB_BEHAVIOR" >&2
        exit 1
        ;;
esac
"""


@pytest.fixture()
def stub_env(tmp_path: Path):
    """Create a stub `claude` on PATH with zero backoff and a shared attempt counter."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    counter_file = tmp_path / "attempt_count"

    stub_claude = stub_dir / "claude"
    stub_claude.write_text(_STUB_CLAUDE, encoding="utf-8")
    stub_claude.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    env["CLAUDE_P_RETRY_BACKOFF_BASE"] = "0"
    env["CLAUDE_STUB_COUNTER_FILE"] = str(counter_file)
    return env, counter_file


def _run_wrapper(env: dict, output_file: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    args = ["bash", str(WRAPPER), str(output_file), "--", "--output-format", "text"]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, env=env, capture_output=True, text=True)


class TestTransientThenProceed:
    """Test (a): transient 5xx on attempt 1, PROCEED on attempt 2."""

    def test_exit_zero(self, stub_env, tmp_path):
        env, _ = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
        result = _run_wrapper(env, tmp_path / "out.txt")
        assert result.returncode == 0

    def test_output_contains_proceed(self, stub_env, tmp_path):
        env, _ = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
        out = tmp_path / "out.txt"
        _run_wrapper(env, out)
        assert "PROCEED" in out.read_text(encoding="utf-8")

    def test_exactly_two_attempts(self, stub_env, tmp_path):
        env, counter_file = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
        _run_wrapper(env, tmp_path / "out.txt")
        assert int(counter_file.read_text().strip()) == 2


class TestReviseNotRetried:
    """Test (b): substantive REVISE -- exactly 1 attempt, never retried (Decision 55)."""

    def test_exit_nonzero(self, stub_env, tmp_path):
        env, _ = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "always_revise"
        result = _run_wrapper(env, tmp_path / "out.txt")
        assert result.returncode != 0

    def test_output_contains_revise(self, stub_env, tmp_path):
        env, _ = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "always_revise"
        out = tmp_path / "out.txt"
        _run_wrapper(env, out)
        assert "REVISE" in out.read_text(encoding="utf-8")

    def test_exactly_one_attempt(self, stub_env, tmp_path):
        env, counter_file = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "always_revise"
        _run_wrapper(env, tmp_path / "out.txt")
        assert int(counter_file.read_text().strip()) == 1


class TestPersistentTransientExhausts:
    """Test (c): persistent 5xx -- exactly 3 attempts, non-zero exit, error text in output."""

    def test_exit_nonzero(self, stub_env, tmp_path):
        env, _ = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "always_transient"
        result = _run_wrapper(env, tmp_path / "out.txt")
        assert result.returncode != 0

    def test_error_text_in_output(self, stub_env, tmp_path):
        env, _ = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "always_transient"
        out = tmp_path / "out.txt"
        _run_wrapper(env, out)
        content = out.read_text(encoding="utf-8")
        assert "500" in content or "Internal server error" in content

    def test_exactly_three_attempts(self, stub_env, tmp_path):
        env, counter_file = stub_env
        env["CLAUDE_STUB_BEHAVIOR"] = "always_transient"
        _run_wrapper(env, tmp_path / "out.txt")
        assert int(counter_file.read_text().strip()) == 3


class TestEnforcementCheck:
    """Test (d): validate.py enforcement flags raw `claude -p`; passes the wrapped form."""

    def test_raw_claude_p_is_flagged(self, tmp_path):
        from scripts.checks.ci_guards.validate_claude_p_retry_wrapper import _check_claude_p_raw_invocations

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        raw_wf = workflows_dir / "test-raw.yml"
        raw_wf.write_text(
            "name: test\njobs:\n  j:\n    steps:\n      - run: claude -p --output-format text 'do it'\n",
            encoding="utf-8",
        )
        violations = _check_claude_p_raw_invocations(workflows_dir)
        assert len(violations) == 1
        assert "test-raw.yml" in violations[0]
        assert "claude -p" in violations[0]

    def test_wrapped_form_passes(self, tmp_path):
        from scripts.checks.ci_guards.validate_claude_p_retry_wrapper import _check_claude_p_raw_invocations

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        wrapped_wf = workflows_dir / "test-wrapped.yml"
        wrapped_wf.write_text(
            "name: test\njobs:\n  j:\n    steps:\n"
            "      - run: scripts/ci/claude_p_retry.sh out.txt -- --output-format text 'prompt'\n",
            encoding="utf-8",
        )
        violations = _check_claude_p_raw_invocations(workflows_dir)
        assert violations == []

    def test_command_v_check_not_flagged(self, tmp_path):
        from scripts.checks.ci_guards.validate_claude_p_retry_wrapper import _check_claude_p_raw_invocations

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        wf = workflows_dir / "test-presence.yml"
        wf.write_text(
            "name: test\njobs:\n  j:\n    steps:\n"
            "      - run: if ! command -v claude &> /dev/null; then npm install -g ...; fi\n",
            encoding="utf-8",
        )
        violations = _check_claude_p_raw_invocations(workflows_dir)
        assert violations == []

    def test_comment_lines_not_flagged(self, tmp_path):
        from scripts.checks.ci_guards.validate_claude_p_retry_wrapper import _check_claude_p_raw_invocations

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        wf = workflows_dir / "test-comment.yml"
        wf.write_text(
            "name: test\njobs:\n  j:\n    steps:\n"
            "      # wraps claude -p with retry\n"
            "      - run: scripts/ci/claude_p_retry.sh out.txt -- --output-format text 'p'\n",
            encoding="utf-8",
        )
        violations = _check_claude_p_raw_invocations(workflows_dir)
        assert violations == []

    def test_real_workflows_pass(self):
        """Real .github/workflows/*.yml must pass after the wrapper migration."""
        from scripts.checks.ci_guards.validate_claude_p_retry_wrapper import _check_claude_p_raw_invocations

        violations = _check_claude_p_raw_invocations(ROOT / ".github" / "workflows")
        assert violations == [], f"Real workflows have unwrapped claude -p: {violations}"


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _manifest(out: Path) -> list[dict]:
    path = Path(f"{out}.sessions.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_each_attempt_runs_under_its_own_session_id(stub_env, tmp_path):
    env, _ = stub_env
    env["CLAUDE_STUB_BEHAVIOR"] = "always_transient"
    argv = tmp_path / "argv"
    env["CLAUDE_STUB_ARGV_FILE"] = str(argv)
    _run_wrapper(env, tmp_path / "out.txt")
    ids = [re.search(r"--session-id (\S+)", line).group(1) for line in argv.read_text().splitlines()]
    assert len(ids) == 3 and len(set(ids)) == 3 and all(UUID.match(i) for i in ids)
    assert all(line.startswith("-p --session-id ") for line in argv.read_text().splitlines())
    starts = [m for m in _manifest(tmp_path / "out.txt") if "attempt" in m]
    assert [m["session_id"] for m in starts] == ids
    assert [m["attempt"] for m in starts] == [1, 2, 3]
    assert all(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", m["started_at"]) for m in starts)


def test_manifest_appends_start_and_result_lines_across_invocations(stub_env, tmp_path):
    env, counter = stub_env
    out = tmp_path / "out.txt"
    env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
    assert _run_wrapper(env, out).returncode == 0
    counter.unlink()
    env["CLAUDE_STUB_BEHAVIOR"] = "always_revise"
    assert _run_wrapper(env, out).returncode != 0
    lines = _manifest(out)
    assert len(lines) == 6
    results = [m for m in lines if "exit" in m]
    assert [(m["exit"], m["transient"]) for m in results] == [(1, True), (0, False), (1, False)]
    starts = {m["session_id"] for m in lines if "attempt" in m}
    assert {m["session_id"] for m in results} == starts and len(starts) == 3
    assert [m["attempt"] for m in lines if "attempt" in m] == [1, 2, 1]


@pytest.mark.parametrize(
    "flag",
    ["--session-id", "--session-id=abc", "--resume", "-r", "--continue", "-c", "--fork-session", "--no-session-persistence"],
)
def test_session_identity_flags_are_refused(stub_env, tmp_path, flag):
    env, counter = stub_env
    env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
    result = _run_wrapper(env, tmp_path / "out.txt", [flag])
    assert result.returncode == 1 and not counter.exists()
    assert not Path(f"{tmp_path / 'out.txt'}.sessions.jsonl").exists()


def test_manifest_write_failure_is_not_fatal(stub_env, tmp_path):
    env, _ = stub_env
    env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
    out = tmp_path / "out.txt"
    Path(f"{out}.sessions.jsonl").mkdir()
    assert _run_wrapper(env, out).returncode == 0


def test_wrapper_runs_under_env_i(stub_env, tmp_path):
    env, _ = stub_env
    env["CLAUDE_STUB_BEHAVIOR"] = "transient_then_proceed"
    out = tmp_path / "out.txt"
    keep = {k: env[k] for k in ("PATH", "CLAUDE_STUB_BEHAVIOR", "CLAUDE_STUB_COUNTER_FILE", "CLAUDE_P_RETRY_BACKOFF_BASE")}
    cmd = [
        "env",
        "-i",
        *[f"{k}={v}" for k, v in keep.items()],
        "bash",
        str(WRAPPER),
        str(out),
        "--",
        "--output-format",
        "text",
    ]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    assert len(_manifest(out)) == 4
