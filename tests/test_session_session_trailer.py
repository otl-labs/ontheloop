"""Tests for scripts/session/session_trailer.py: the Claude-Session stamp, commit check and merge-time set equality."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from scripts.session import merge_step as ms
from scripts.session import session_trailer as st
from scripts.telemetry_verify import completeness
from scripts.telemetry_verify import session_presence as sp
from tests.fixtures import merge_step_repos as fx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://claude.ai/code/session_"
VAR = "CLAUDE_CODE_REMOTE_SESSION_ID"


def _msg(*keys: str, body: str = "subject\n\nbody\n") -> str:
    return body + "".join(f"\nClaude-Session: {URL}{k}" for k in keys) + ("\n" if keys else "")


def _commit(body: str) -> sp.Commit:
    return sp.Commit("0" * 40, datetime(2026, 11, 1, tzinfo=timezone.utc), "s (#1)", body)


def test_a_foreign_key_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(VAR, "cse_OWN")
    assert st.foreign_to_session(_msg("OTHER"), os.environ) == ["OTHER"]
    path = tmp_path / "m"
    path.write_text(_msg("OTHER"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert st.main(["commit-msg", str(path)]) == 1
    assert st.trailer_line("OWN") in capsys.readouterr().err


def test_the_own_key_passes(monkeypatch, tmp_path):
    path = tmp_path / "m"
    path.write_text(_msg("OWN"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for value in ("cse_OWN", "session_OWN"):
        monkeypatch.setenv(VAR, value)
        assert st.foreign_to_session(_msg("OWN"), os.environ) == []
        assert st.main(["commit-msg", str(path)]) == 0


@pytest.mark.parametrize("value", [None, ""])
def test_an_unset_or_empty_variable_skips(monkeypatch, tmp_path, value):
    if value is None:
        monkeypatch.delenv(VAR, raising=False)
    else:
        monkeypatch.setenv(VAR, value)
    path = tmp_path / "m"
    path.write_text(_msg("OTHER"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert st.foreign_to_session(_msg("OTHER"), os.environ) is None
    assert st.main(["commit-msg", str(path)]) == 0
    assert st.main(["prepare-commit-msg", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == _msg("OTHER")


def test_an_untrailered_message_passes(monkeypatch):
    monkeypatch.setenv(VAR, "cse_OWN")
    assert st.foreign_to_session("subject\n", os.environ) == []


def test_usage_and_unreadable_file(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert st.main([]) == 2 and st.main(["bogus", "x"]) == 2
    monkeypatch.setenv(VAR, "cse_OWN")
    assert st.main(["commit-msg", str(tmp_path / "missing")]) == 1
    assert "session_trailer commit-msg" in capsys.readouterr().err


BODIES = [
    "plan\n",
    _msg("A"),
    _msg("A", "B"),
    _msg("A", "A"),
    "x\n\nClaude-Session: https://claude.ai/code/session_A/\n",
    "x\n\nClaude-Session: https://claude.ai/code/cse_A\n",
    "x\n\nClaude-Session: A\n",
    "x\nClaude-Session: https://claude.ai/code/session_A mid\nmore\n",
    "x\n\n  Claude-Session: https://claude.ai/code/session_A\n",
    "x\n\nClaude-Session: https://claude.ai/code/session_A   \n",
    "x\n\nClaude-Session: https://claude.ai/code/session_\n",
]


def test_parity_with_the_arrival_oracle():
    for body in BODIES:
        assert st.trailer_keys(body) == sp._session_keys(_commit(body)), body
    for value in ("", "session_", "cse_", "cse_X", "session_X", "X", None, 7):
        assert st.normalise(value) == completeness.normalise_key(value), value
    assert st.TRAILER.pattern == sp._TRAILER.pattern and st.TRAILER.flags == sp._TRAILER.flags


def test_parity_over_main_history():
    probe = subprocess.run(
        ["git", "log", "--first-parent", "--format=%B%x1e", "-n", "200"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        pytest.skip("env: git unavailable")
    bodies = [b for b in probe.stdout.split("\x1e") if b.strip()]
    assert len(bodies) >= 20
    for body in bodies:
        assert st.trailer_keys(body) == sp._session_keys(_commit(body))


def _repo(tmp_path: Path) -> Path:
    repo = fx.make_repo(tmp_path)
    fx.git(repo, "config", "user.email", "t@example.com")
    return repo


def test_branch_keys_reads_only_the_range(tmp_path):
    repo = _repo(tmp_path)
    fx.commit_file(repo, "a", "1", _msg("A"))
    base = fx.git(repo, "rev-parse", "HEAD")
    fx.commit_file(repo, "b", "2", _msg("B"))
    fx.commit_file(repo, "c", "3", "none\n")
    assert st.branch_keys(repo, base, "HEAD") == {"B"}
    with pytest.raises(st.TrailerReadError):
        st.branch_keys(repo, "nope", "HEAD")


def test_mismatch_is_set_equality():
    assert st.mismatch(_msg("A"), {"B"}) == (["A"], ["B"])
    dup = _msg("A") + "Claude-Session: https://claude.ai/code/cse_A\n"
    assert st.mismatch(dup, {"A"}) == ([], [])
    assert st.mismatch("plain\n", set()) == ([], [])
    assert st.mismatch(_msg("A"), set()) == (["A"], [])


class TestStamp:
    def _stamp(self, tmp_path: Path, text: str, env: dict[str, str]) -> str:
        repo = _repo(tmp_path)
        path = tmp_path / "msg"
        path.write_text(text, encoding="utf-8")
        st.stamp(path, env, repo)
        return path.read_text(encoding="utf-8")

    def test_a_typed_foreign_trailer_is_replaced(self, tmp_path):
        out = self._stamp(tmp_path, _msg("OTHER"), {VAR: "cse_OWN"})
        assert st.trailer_keys(out) == ["OWN"]

    def test_no_trailer_adds_the_own_line(self, tmp_path):
        out = self._stamp(tmp_path, "subject\n\nbody\n", {VAR: "cse_OWN"})
        assert out.rstrip("\n").endswith(st.trailer_line("OWN")) and st.trailer_keys(out) == ["OWN"]

    def test_two_typed_keys_and_a_mid_body_line_leave_only_the_own(self, tmp_path):
        text = (
            "subject\n\nClaude-Session: https://claude.ai/code/session_X\nbody\n\n"
            f"Claude-Session: {URL}A\nClaude-Session: {URL}B\nCo-Authored-By: Someone <s@example.com>\n"
        )
        out = self._stamp(tmp_path, text, {VAR: "cse_OWN"})
        assert st.trailer_keys(out) == ["OWN"] and "Co-Authored-By: Someone" in out

    def test_unset_variable_leaves_the_file_byte_identical(self, tmp_path):
        assert self._stamp(tmp_path, _msg("OTHER"), {}) == _msg("OTHER")

    @pytest.mark.parametrize("name", ["rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD"])
    def test_a_sequencer_operation_leaves_the_file_byte_identical(self, tmp_path, name):
        repo = _repo(tmp_path)
        marker = repo / ".git" / name
        marker.mkdir() if name.startswith("rebase") else marker.write_text("x\n")
        path = tmp_path / "msg"
        path.write_text(_msg("OTHER"), encoding="utf-8")
        st.stamp(path, {VAR: "cse_OWN"}, repo)
        assert path.read_text(encoding="utf-8") == _msg("OTHER")


def _install_hooks(repo: Path) -> None:
    for verb in ("prepare-commit-msg", "commit-msg"):
        hook = repo / ".git" / "hooks" / verb
        hook.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -m scripts.session.session_trailer {verb} "$@"\n', encoding="utf-8"
        )
        hook.chmod(0o755)


def _env(var: str | None, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != VAR}
    env |= {"PYTHONPATH": str(ROOT), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_EDITOR": "true", **extra}
    if var:
        env[VAR] = var
    return env


def _g(repo: Path, var: str | None, *args: str, check: bool = True, **extra: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=_env(var, **extra),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args}: {result.stdout}{result.stderr}")
    return result


def _head_keys(repo: Path) -> list[str]:
    return st.trailer_keys(fx.git(repo, "log", "-1", "--format=%B"))


def _hooked_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    _install_hooks(repo)
    return repo


def test_stamp_then_check_on_a_real_commit(tmp_path):
    repo = _hooked_repo(tmp_path)
    _g(repo, "cse_OWN", "commit", "--allow-empty", "-m", "x", "-m", f"Claude-Session: {URL}OTHER")
    assert _head_keys(repo) == ["OWN"]
    _g(
        repo,
        "cse_OWN",
        "commit",
        "--allow-empty",
        "-m",
        "y",
        "-m",
        f"mid Claude-Session: {URL}Z",
        "-m",
        f"Claude-Session: {URL}Q",
    )
    assert _head_keys(repo) == ["OWN"]
    before = fx.git(repo, "rev-parse", "HEAD")
    (repo / ".git" / "hooks" / "prepare-commit-msg").unlink()
    refused = _g(repo, "cse_OWN", "commit", "--allow-empty", "-m", "z", "-m", f"Claude-Session: {URL}OTHER", check=False)
    assert refused.returncode != 0 and fx.git(repo, "rev-parse", "HEAD") == before
    _install_hooks(repo)
    _g(
        repo,
        "cse_OWN",
        "commit",
        "--allow-empty",
        "--no-verify",
        "-m",
        "w",
        "-m",
        f"Claude-Session: {URL}A",
        "-m",
        f"Claude-Session: {URL}B",
    )
    assert _head_keys(repo) == ["OWN"]


def test_rebase_keeps_another_sessions_trailer(tmp_path):
    repo = _hooked_repo(tmp_path)
    fx.push_main(repo)
    _g(repo, "cse_A", "checkout", "-q", "-b", "feat")
    (repo / "f.txt").write_text("f\n")
    _g(repo, "cse_A", "add", "-A")
    _g(repo, "cse_A", "commit", "-m", "feat")
    assert _head_keys(repo) == ["A"]
    _g(repo, "cse_B", "checkout", "-q", "main")
    (repo / "m.txt").write_text("m\n")
    _g(repo, "cse_B", "add", "-A")
    _g(repo, "cse_B", "commit", "-m", "main change")
    _g(repo, "cse_B", "checkout", "-q", "feat")
    _g(repo, "cse_B", "rebase", "main")
    assert _head_keys(repo) == ["A"]


def _diverged(repo: Path, var: str) -> None:
    """main and `feat` both edit c.txt; feat's commit was made under cse_A."""
    (repo / "c.txt").write_text("base\n")
    _g(repo, var, "add", "-A")
    _g(repo, var, "commit", "-m", "base")
    _g(repo, "cse_A", "checkout", "-q", "-b", "feat")
    (repo / "c.txt").write_text("feat\n")
    _g(repo, "cse_A", "commit", "-am", "feat")
    _g(repo, "cse_B", "checkout", "-q", "main")
    (repo / "c.txt").write_text("main\n")
    _g(repo, "cse_B", "commit", "-am", "main")


def test_cherry_pick_keeps_another_sessions_trailer(tmp_path):
    repo = _hooked_repo(tmp_path)
    _g(repo, "cse_A", "checkout", "-q", "-b", "feat")
    (repo / "p.txt").write_text("p\n")
    _g(repo, "cse_A", "add", "-A")
    _g(repo, "cse_A", "commit", "-m", "pick me")
    _g(repo, "cse_B", "checkout", "-q", "main")
    _g(repo, "cse_B", "cherry-pick", "feat")
    assert _head_keys(repo) == ["A"]


def test_conflicted_cherry_pick_continue_and_stopped_rebase_commit(tmp_path):
    repo = _hooked_repo(tmp_path)
    _diverged(repo, "cse_A")
    assert _g(repo, "cse_B", "cherry-pick", "feat", check=False).returncode != 0
    (repo / "c.txt").write_text("resolved\n")
    _g(repo, "cse_B", "add", "-A")
    _g(repo, "cse_B", "cherry-pick", "--continue")
    assert _head_keys(repo) == ["A"]
    _g(repo, "cse_B", "checkout", "-q", "feat")
    assert _g(repo, "cse_B", "rebase", "main", check=False).returncode != 0
    (repo / "c.txt").write_text("resolved again\n")
    _g(repo, "cse_B", "add", "-A")
    _g(repo, "cse_B", "commit", "-m", "manual", "-m", f"Claude-Session: {URL}A")
    assert _head_keys(repo) == ["A"]


def test_pre_commit_runs_the_hooks(tmp_path):
    repo = tmp_path / "pc"
    repo.mkdir()
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    config["repos"] = [r for r in config["repos"] if r["repo"] == "local"]
    config["repos"][0]["hooks"] = [h for h in config["repos"][0]["hooks"] if h["id"].startswith("claude-session")]
    (repo / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (repo / "bin").mkdir()
    shutil.copy2(ROOT / "bin" / "venv-python", repo / "bin" / "venv-python")
    (repo / ".venv").symlink_to(ROOT / ".venv")
    env = _env("cse_OWN", PRE_COMMIT_HOME=str(tmp_path / "pch"), GIT_CONFIG_GLOBAL=os.devnull, NO_COLOR="1")
    run = lambda *a: subprocess.run(a, cwd=repo, env=env, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "add", "-A")
    run("git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "cfg")
    msg = tmp_path / "msg"
    pc = [sys.executable, "-m", "pre_commit", "run", "--hook-stage"]
    msg.write_text(_msg("OTHER"), encoding="utf-8")
    assert run(*pc, "commit-msg", "--commit-msg-filename", str(msg)).returncode != 0
    assert (
        run(
            *pc, "prepare-commit-msg", "--commit-msg-filename", str(msg), "--prepare-commit-message-source", "message"
        ).returncode
        == 0
    )
    assert st.trailer_keys(msg.read_text(encoding="utf-8")) == ["OWN"]
    assert run(*pc, "commit-msg", "--commit-msg-filename", str(msg)).returncode == 0


def test_the_commit_msg_hook_is_declared():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    assert set(config["default_install_hook_types"]) == {"pre-commit", "prepare-commit-msg", "commit-msg"}
    assert config["default_stages"] == ["pre-commit"]
    hooks = {h["id"]: h for r in config["repos"] for h in r["hooks"]}
    stamp, check = hooks["claude-session-stamp"], hooks["claude-session-trailer"]
    assert stamp["stages"] == ["prepare-commit-msg"] and check["stages"] == ["commit-msg"]
    assert stamp["entry"] == "bin/venv-python -m scripts.session.session_trailer prepare-commit-msg"
    assert check["entry"] == "bin/venv-python -m scripts.session.session_trailer commit-msg"
    assert stamp["language"] == check["language"] == "system"


class TestPremergeAttribution:
    SLUG = "demo"

    def _branch(self, tmp_path, monkeypatch, message="feat\n\nClaude-Session: " + URL + "BUILD\n\nResolves: rec-1\n") -> Path:
        return fx.make_pr_branch(tmp_path, monkeypatch, self.SLUG, ["rec-1"], message=message)

    def _premerge(self, repo: Path, tmp_path: Path, squash: str) -> int:
        msg = fx.message_file(tmp_path, squash)
        return ms.main(["premerge", "--slug", self.SLUG, "--root", str(repo), "--message-file", str(msg)])

    @pytest.mark.parametrize("var", [None, "cse_COORD"])
    def test_verdicts_ignore_the_merging_session(self, tmp_path, monkeypatch, capsys, var):
        if var:
            monkeypatch.setenv(VAR, var)
        else:
            monkeypatch.delenv(VAR, raising=False)
        repo = self._branch(tmp_path, monkeypatch)
        bad = f"feat\n\nResolves: rec-1\n\nClaude-Session: {URL}COORD\n"
        assert self._premerge(repo, tmp_path, bad) == 1
        out = capsys.readouterr().out
        assert "PREMERGE ATTRIBUTION MISMATCH extra=['COORD'] missing=['BUILD']" in out
        assert f"  {st.trailer_line('BUILD')}" in out
        good = f"feat\n\nResolves: rec-1\n\nClaude-Session: {URL}BUILD\n"
        assert self._premerge(repo, tmp_path, good) == 0
        assert "PREMERGE ATTRIBUTION OK" in capsys.readouterr().out
        assert self._premerge(repo, tmp_path, good.replace("session_", "cse_")) == 0
        assert self._premerge(repo, tmp_path, "feat\n\nResolves: rec-1\n") == 1
        assert "missing=['BUILD']" in capsys.readouterr().out

    def test_no_trailers_anywhere_prints_nothing(self, tmp_path, monkeypatch, capsys):
        repo = self._branch(tmp_path, monkeypatch, message="feat\n\nResolves: rec-1\n")
        assert self._premerge(repo, tmp_path, "feat\n\nResolves: rec-1\n") == 0
        assert "ATTRIBUTION" not in capsys.readouterr().out

    def test_an_extra_key_on_an_untrailered_branch_prints_none(self, tmp_path, monkeypatch, capsys):
        repo = self._branch(tmp_path, monkeypatch, message="feat\n\nResolves: rec-1\n")
        assert self._premerge(repo, tmp_path, f"feat\n\nResolves: rec-1\n\nClaude-Session: {URL}X\n") == 1
        assert "(none)" in capsys.readouterr().out

    def test_an_unreadable_range_prints_unavailable(self, tmp_path, capsys):
        assert st.premerge_attribution(root=_repo(tmp_path), base="no-such-ref", message="x\n") is False
        assert "PREMERGE ATTRIBUTION UNAVAILABLE" in capsys.readouterr().out


def test_gate_denial(tmp_path):
    repo = _repo(tmp_path)
    base = fx.git(repo, "rev-parse", "HEAD")
    fx.git(repo, "checkout", "-q", "-b", "pr")
    fx.commit_file(repo, "p", "1", _msg("BUILD"))
    kw = {"root": repo, "base_ref": base, "head_ref": "pr"}
    assert st.gate_denial(**kw, message=_msg("BUILD")) is None
    assert "extra ['COORD'], missing ['BUILD']" in (st.gate_denial(**kw, message=_msg("COORD")) or "")
    assert "missing ['BUILD']" in (st.gate_denial(**kw, message="plain\n") or "")
    assert (st.gate_denial(**{**kw, "head_ref": "nope"}, message="x") or "").startswith("attribution UNAVAILABLE")
    fx.git(repo, "checkout", "-q", "main")
    assert st.gate_denial(root=repo, base_ref="main", head_ref="main", message="plain\n") is None
