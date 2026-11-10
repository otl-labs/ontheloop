"""The plugin's checkout locator (.claude/hooks/locate_checkout.bash) and the notices rendered from it."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCATOR = ROOT / ".claude" / "hooks" / "locate_checkout.bash"
HOOKS_FILE = ROOT / ".claude" / "hooks" / "hooks.json"
SETTINGS = ROOT / ".claude" / "settings.json"
BROKEN_PHRASE = "repo hooks not loaded"


def _env(project: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "ONTHELOOP_PROJECT_SETTINGS", "PWD")}
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    return env


def locate(
    project: str | Path | None, *args: str, cwd: Path | None = None, timeout: float = 20
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LOCATOR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(None if project is None else str(project)),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def checkout(path: Path, name: str = "ontheloop", git: str | None = None) -> Path:
    manifest = path / ".claude" / ".claude-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(f'{{\n  "name": "{name}"\n}}\n', encoding="utf-8")
    if git == "dir":
        (path / ".git").mkdir()
    elif git == "file":
        (path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    return path


def found(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0 and result.stderr == "", (result.returncode, result.stderr)
    assert result.stdout.endswith("\n") and result.stdout.count("\n") == 1
    return result.stdout[:-1]


def none(result: subprocess.CompletedProcess[str]) -> None:
    assert (result.returncode, result.stdout, result.stderr) == (1, "", "")


class TestLocate:
    def test_the_project_dir_wins_when_it_is_a_candidate_even_with_candidate_children(self, tmp_path: Path) -> None:
        top = checkout(tmp_path / "top")
        checkout(top / "child")
        assert found(locate(top)) == str(top.resolve())

    def test_a_single_child_is_found_also_with_spaces(self, tmp_path: Path) -> None:
        child = checkout(tmp_path / "work" / "my on the loop")
        assert found(locate(tmp_path / "work")) == str(child.resolve())

    def test_a_symlink_twin_counts_once_and_the_printed_path_is_physical(self, tmp_path: Path) -> None:
        real = checkout(tmp_path / "work" / "ontheloop")
        (tmp_path / "work" / "twin").symlink_to(real)
        assert found(locate(tmp_path / "work")) == str(real.resolve())
        link = tmp_path / "link"
        link.symlink_to(real)
        assert found(locate(link)) == str(real.resolve())

    def test_a_hidden_child_and_a_checkout_two_levels_down_are_not_found(self, tmp_path: Path) -> None:
        checkout(tmp_path / "work" / ".hidden")
        checkout(tmp_path / "work" / "mid" / "deep")
        none(locate(tmp_path / "work"))

    def test_an_empty_or_relative_project_dir_fails(self, tmp_path: Path) -> None:
        checkout(tmp_path / "ontheloop")
        none(locate(""))
        none(locate(None, cwd=tmp_path))
        none(locate("ontheloop", cwd=tmp_path))
        none(locate(".", cwd=tmp_path / "ontheloop"))

    def test_a_manifest_naming_another_plugin_is_not_a_candidate(self, tmp_path: Path) -> None:
        checkout(tmp_path / "work" / "other", name="someone-else")
        none(locate(tmp_path / "work"))

    def test_two_clones_fail(self, tmp_path: Path) -> None:
        checkout(tmp_path / "work" / "a", git="dir")
        checkout(tmp_path / "work" / "b", git="dir")
        none(locate(tmp_path / "work"))

    def test_a_clone_with_a_linked_worktree_gives_the_clone_from_any_cwd(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        clone = work / "clone"
        clone.mkdir(parents=True)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
        checkout(clone)
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"], ["worktree", "add", "-q", str(work / "wt")]):
            subprocess.run([*git, *cmd], cwd=clone, check=True, capture_output=True)
        assert (work / "wt" / ".git").is_file() and (clone / ".git").is_dir()
        for cwd in (work, clone, work / "wt", tmp_path):
            assert found(locate(work, cwd=cwd)) == str(clone.resolve())

    def test_a_clone_beside_a_copy_with_no_git_gives_the_clone(self, tmp_path: Path) -> None:
        clone = checkout(tmp_path / "work" / "clone", git="dir")
        checkout(tmp_path / "work" / "copy")
        assert found(locate(tmp_path / "work")) == str(clone.resolve())

    def test_two_linked_worktrees_with_no_clone_fail(self, tmp_path: Path) -> None:
        checkout(tmp_path / "work" / "a", git="file")
        checkout(tmp_path / "work" / "b", git="file")
        none(locate(tmp_path / "work"))

    def test_a_fifo_named_plugin_json_returns_under_a_timeout(self, tmp_path: Path) -> None:
        manifest = tmp_path / "work" / "fifo" / ".claude" / ".claude-plugin"
        manifest.mkdir(parents=True)
        os.mkfifo(manifest / "plugin.json")
        none(locate(tmp_path / "work", timeout=5))
        os.mkfifo(tmp_path / "fifo-plugin.json")
        top = tmp_path / "top" / ".claude" / ".claude-plugin"
        top.mkdir(parents=True)
        os.mkfifo(top / "plugin.json")
        none(locate(tmp_path / "top", timeout=5))

    def test_one_checkout_among_three_thousand_children_is_found_under_a_timeout(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        for i in range(3000):
            (work / f"d{i:04d}").mkdir()
        wanted = checkout(work / "ontheloop")
        assert found(locate(work, timeout=5)) == str(wanted.resolve())

    def test_a_name_with_shell_syntax_is_printed_verbatim_and_creates_nothing(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        name = "a$(touch pwned)`touch pwned2`;touch pwned3*"
        wanted = checkout(work / name)
        assert found(locate(work)) == str(wanted.resolve())
        assert sorted(p.name for p in work.iterdir()) == [name]
        assert not any(p.name.startswith("pwned") for p in tmp_path.rglob("pwned*"))

    def test_the_output_is_one_line_stderr_is_empty_and_stdin_is_left_unread(self, tmp_path: Path) -> None:
        wanted = checkout(tmp_path / "work" / "ontheloop")
        read, write = os.pipe()
        try:
            result = subprocess.run(
                ["bash", str(LOCATOR)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=_env(str(tmp_path / "work")),
                stdin=read,
                timeout=5,
            )
        finally:
            os.close(read)
            os.close(write)
        assert found(result) == str(wanted.resolve())
        none(locate(tmp_path / "nothing-here"))


def _rendered(event: str, needle: str) -> str:
    hooks = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))["hooks"]
    return next(h["command"] for g in hooks[event] for h in g["hooks"] if needle in h["command"])


class TestNotice:
    def test_session_start_says_the_candidate_count(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        zero = locate(work, "--notice", "SessionStart")
        assert (zero.returncode, zero.stdout, zero.stderr) == (0, "", "[plugin_hooks] no ontheloop checkout (candidates=0)\n")
        checkout(work / "a", git="dir")
        checkout(work / "b", git="dir")
        two = locate(work, "--notice", "SessionStart")
        assert (two.returncode, two.stdout, two.stderr) == (0, "", "[plugin_hooks] no ontheloop checkout (candidates=2)\n")
        for project in ("", "relative"):
            bad = locate(project, "--notice", "SessionStart", cwd=tmp_path)
            assert (bad.returncode, bad.stdout, bad.stderr) == (0, "", "[plugin_hooks] no ontheloop checkout (candidates=0)\n")

    def test_nothing_is_said_when_a_checkout_is_found(self, tmp_path: Path) -> None:
        checkout(tmp_path / "work" / "ontheloop")
        for event in ("SessionStart", "UserPromptSubmit"):
            ok = locate(tmp_path / "work", "--notice", event)
            assert (ok.returncode, ok.stdout, ok.stderr) == (0, "", "")

    def test_the_prompt_notice_is_a_json_object_only_for_two_clones(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        for project in (work, "", "relative"):
            quiet = locate(project, "--notice", "UserPromptSubmit", cwd=tmp_path)
            assert (quiet.returncode, quiet.stdout, quiet.stderr) == (0, "", "")
        checkout(work / "a", git="dir")
        checkout(work / "b", git="dir")
        loud = locate(work, "--notice", "UserPromptSubmit")
        assert (loud.returncode, loud.stderr) == (0, "")
        payload = json.loads(loud.stdout)
        assert set(payload) == {"hookSpecificOutput"}
        out = payload["hookSpecificOutput"]
        assert out["hookEventName"] == "UserPromptSubmit"
        assert BROKEN_PHRASE in out["additionalContext"] and "2 ontheloop checkouts" in out["additionalContext"]
        assert "[hook_canary]" not in out["additionalContext"]

    def test_the_rendered_entries_are_plugin_only_and_carry_no_status_message(self) -> None:
        settings = SETTINGS.read_text(encoding="utf-8")
        for event, mode in (("SessionStart", "--notice SessionStart"), ("UserPromptSubmit", "--notice UserPromptSubmit")):
            command = _rendered(event, mode)
            assert mode not in settings
            assert command.startswith('[ -n "${ONTHELOOP_PROJECT_SETTINGS:-}" ] || ')
            hooks = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))["hooks"][event]
            entry = next(h for g in hooks for h in g["hooks"] if h["command"] == command)
            assert "statusMessage" not in entry
        assert _rendered("SessionStart", "--notice").endswith("</dev/null || :")
        assert _rendered("UserPromptSubmit", "--notice").split("</dev/null || ")[1].startswith("printf '%s\\n' '{")

    def _plugin_root(self, tmp_path: Path, locator_text: str | None) -> Path:
        root = tmp_path / "plugin"
        (root / "hooks").mkdir(parents=True)
        if locator_text is not None:
            (root / "hooks" / "locate_checkout.bash").write_text(locator_text, encoding="utf-8")
        return root

    def _run(self, command: str, root: Path, project: Path) -> subprocess.CompletedProcess[str]:
        env = _env(str(project))
        env["CLAUDE_PLUGIN_ROOT"] = str(root)
        return subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=20,
        )

    @pytest.mark.parametrize("variant", ["absent", "syntax-error"])
    def test_a_missing_or_unparseable_locator_still_exits_0_and_the_prompt_says_so(self, tmp_path: Path, variant: str) -> None:
        root = self._plugin_root(tmp_path, None if variant == "absent" else "if true; then\necho unterminated\n")
        project = tmp_path / "work"
        project.mkdir()
        start = self._run(_rendered("SessionStart", "--notice"), root, project)
        assert (start.returncode, start.stdout) == (0, "")
        prompt = self._run(_rendered("UserPromptSubmit", "--notice"), root, project)
        assert prompt.returncode == 0
        context = json.loads(prompt.stdout)["hookSpecificOutput"]["additionalContext"]
        assert BROKEN_PHRASE in context and "locator" in context and "[hook_canary]" not in context

    def test_a_healthy_locator_with_one_checkout_prints_nothing(self, tmp_path: Path) -> None:
        root = self._plugin_root(tmp_path, LOCATOR.read_text(encoding="utf-8"))
        project = tmp_path / "work"
        checkout(project / "ontheloop")
        for event in ("SessionStart", "UserPromptSubmit"):
            ok = self._run(_rendered(event, "--notice"), root, project)
            assert (ok.returncode, ok.stdout, ok.stderr) == (0, "", "")
