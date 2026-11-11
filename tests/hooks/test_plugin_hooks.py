"""The ontheloop plugin (.claude as its root): marketplace wiring, the gated hooks.json and its drift check."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from src.turn_capture.observations import hook_name
from src.turn_capture.transcript import MemTree, project_ref_pin

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
HOOKS_FILE = ROOT / ".claude" / "hooks" / "hooks.json"
MANIFEST = ROOT / ".claude" / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
SID = "aaaaaaaa-0000-4000-8000-000000000001"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_plugin_hooks_under_test", ROOT / ".claude" / "hooks" / "plugin_hooks.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings() -> dict[str, Any]:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def _commands(hooks: dict[str, Any]) -> list[tuple[str, str]]:
    return [(event, h["command"]) for event, groups in hooks.items() for g in groups for h in g["hooks"]]


def test_the_committed_hooks_file_is_the_render_of_the_settings() -> None:
    expected = load().render(_settings())
    assert json.loads(HOOKS_FILE.read_text(encoding="utf-8")) == expected, (
        "regenerate with: bin/venv-python .claude/hooks/plugin_hooks.py render"
    )


def test_every_settings_hook_is_gated_in_place_and_the_check_leads_session_start() -> None:
    mod = load()
    settings, plugin = _settings()["hooks"], mod.render(_settings())["hooks"]
    assert list(plugin) == list(settings)
    check_group, *rest = plugin["SessionStart"]
    assert check_group == {
        "hooks": [
            {"type": "command", "command": mod.GATE + mod.CHECK},
            {"type": "command", "command": mod.NOTICE_SESSION_START},
        ]
    }
    notice_group, *prompt_rest = plugin["UserPromptSubmit"]
    assert notice_group == {"hooks": [{"type": "command", "command": mod.NOTICE_PROMPT}]}
    for event, groups in settings.items():
        plugin_groups = {"SessionStart": rest, "UserPromptSubmit": prompt_rest}.get(event, plugin[event])
        assert len(plugin_groups) == len(groups)
        for group, plugin_group in zip(groups, plugin_groups, strict=True):
            assert {k: v for k, v in plugin_group.items() if k != "hooks"} == {k: v for k, v in group.items() if k != "hooks"}
            for hook, plugin_hook in zip(group["hooks"], plugin_group["hooks"], strict=True):
                assert plugin_hook == {**hook, "command": mod.GATE + hook["command"]}


def test_gating_keeps_what_the_capture_parser_reads() -> None:
    mod = load()
    for text in (mod.GATE, mod.NOTICE_SESSION_START, mod.NOTICE_PROMPT):
        for token in text.split():
            assert not token.strip("\"'").endswith((".py", ".sh")), token
    for _, command in _commands(_settings()["hooks"]):
        assert hook_name({"command": mod.gated(command)}) == hook_name({"command": command})
    pin = next(c for e, c in _commands(_settings()["hooks"]) if e == "SessionStart" and "--project-ref=" in c)
    assert '"$CLAUDE_PROJECT_DIR/.claude/hooks/' in pin
    attachment = {"type": "hook_success", "hookName": "SessionStart:startup", "hookEvent": "SessionStart"}
    record: dict[str, Any] = {"type": "attachment", "uuid": "h1", "timestamp": "2026-01-01T00:00:00.000Z"}
    record["attachment"] = {**attachment, "command": mod.gated(pin)}
    prompt = {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:01.000Z", "message": {"content": "hi"}}
    body = "".join(json.dumps(r) + "\n" for r in (record, prompt))
    assert project_ref_pin(MemTree({f"{SID}.jsonl": body})) == "ontheloop"


PLUGIN_ROOT = ROOT / ".claude"


def _run_gated(
    command: str, project: Path, marker: str | None, cwd: Path | None = None, plugin_root: Path | None = PLUGIN_ROOT
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("ONTHELOOP_PROJECT_SETTINGS", "CLAUDE_PLUGIN_ROOT")}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    if marker is not None:
        env["ONTHELOOP_PROJECT_SETTINGS"] = marker
    gated = load().gated(command)
    return subprocess.run(
        ["/bin/sh", "-c", gated], capture_output=True, text=True, encoding="utf-8", env=env, cwd=cwd or project
    )


def test_the_gate_runs_a_hook_only_without_the_settings_marker_and_inside_a_plugin_checkout(tmp_path: Path) -> None:
    checkout, other, foreign = tmp_path / "checkout", tmp_path / "other", tmp_path / "foreign"
    for project, name in ((checkout, "ontheloop"), (foreign, "someone-else")):
        (project / ".claude" / ".claude-plugin").mkdir(parents=True)
        (project / ".claude" / ".claude-plugin" / "plugin.json").write_text(f'{{\n  "name": "{name}"\n}}\n', encoding="utf-8")
    other.mkdir()
    probe = tmp_path / "probe.sh"
    probe.write_text("echo ran >&2\nexit 2\n", encoding="utf-8")
    command = f"sh {probe}"
    ran = _run_gated(command, checkout, None)
    assert (ran.returncode, ran.stderr.strip()) == (2, "ran")
    for project, marker in ((checkout, "1"), (other, None), (other, "1"), (foreign, None)):
        skipped = _run_gated(command, project, marker)
        assert (skipped.returncode, skipped.stdout, skipped.stderr) == (0, "", "")
    assert _run_gated(command, checkout, "").returncode == 2


@pytest.mark.parametrize("command", ["a.sh; b.sh", "a.sh && b.sh", "a.sh || b.sh", "a.sh | b.sh", "a.sh\nb.sh", "x $(y)"])
def test_a_command_the_gate_would_split_is_refused(command: str) -> None:
    with pytest.raises(ValueError, match="plain simple command"):
        load().gated(command)


def test_a_non_command_hook_is_refused() -> None:
    with pytest.raises(ValueError, match="only command hooks"):
        load().render({"hooks": {"Stop": [{"hooks": [{"type": "http", "url": "https://example.invalid"}]}]}})


def test_the_settings_env_sets_the_marker_the_gate_reads() -> None:
    mod = load()
    assert _settings()["env"][mod.MARKER_ENV] == "1"
    assert f"${{{mod.MARKER_ENV}:-}}" in mod.GATE


def test_the_marketplace_lists_the_claude_dir_as_the_ontheloop_plugin() -> None:
    marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert [(p["name"], p["source"]) for p in marketplace["plugins"]] == [(manifest["name"], "./.claude")]
    assert manifest["name"] == "ontheloop" and '"${CLAUDE_PLUGIN_ROOT}/hooks/locate_checkout.bash"' in load().GATE
    locator = ROOT / ".claude" / "hooks" / "locate_checkout.bash"
    assert locator.is_file(), "the locator ships under .claude/hooks, inside the plugin root"
    literal = f'"name": "{manifest["name"]}"'
    assert literal in MANIFEST.read_text(encoding="utf-8"), "the locator greps the manifest for this literal"
    text = locator.read_text(encoding="utf-8")
    assert literal in text and ".claude/.claude-plugin/plugin.json" in text
    assert not (ROOT / ".claude" / "bin").exists(), "claude.ai refuses a plugin with a top-level bin/ directory"


def _plugin_root(tmp_path: Path, hooks: dict[str, Any]) -> Path:
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    return tmp_path


def test_check_is_quiet_outside_the_plugin_and_when_in_sync(tmp_path: Path) -> None:
    mod = load()
    assert mod.check(None) == ("", "[plugin_hooks] not running from the plugin")
    root = _plugin_root(tmp_path, mod.render(_settings()))
    assert mod.check(str(root)) == ("", "[plugin_hooks] plugin in sync")
    reordered = mod.render(_settings())
    reordered["hooks"] = dict(reversed(list(reordered["hooks"].items())))
    assert mod.check(str(_plugin_root(tmp_path / "r", reordered)))[1] == "[plugin_hooks] plugin in sync"


def test_check_counts_hooks_missing_from_the_plugin_and_ones_the_checkout_dropped(tmp_path: Path) -> None:
    mod = load()
    stale = copy.deepcopy(mod.render(_settings()))
    stale["hooks"]["Stop"] = []
    stale["hooks"]["PreToolUse"][0]["matcher"] = "Bash"
    stale["hooks"]["Notification"] = [{"hooks": [{"type": "command", "command": mod.gated("bash gone.sh")}]}]
    group_size = len(mod.render(_settings())["hooks"]["PreToolUse"][0]["hooks"])
    out, err = mod.check(str(_plugin_root(tmp_path, stale)))
    assert err == f"[plugin_hooks] drift missing={1 + group_size} stale={1 + group_size}"
    assert out.startswith("WARNING: the ontheloop plugin's hooks differ") and "Customize > Plugins" in out


def test_check_warns_on_an_unreadable_plugin_and_the_cli_always_exits_0(tmp_path: Path) -> None:
    mod = load()
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text("{not json", encoding="utf-8")
    out, err = mod.check(str(tmp_path))
    assert out.startswith("[plugin_hooks] WARNING: could not compare") and err == "[plugin_hooks] unreadable"
    for root in (str(tmp_path), str(tmp_path / "absent"), ""):
        env = {**os.environ, "CLAUDE_PLUGIN_ROOT": root}
        script = str(ROOT / ".claude" / "hooks" / "plugin_hooks.py")
        result = subprocess.run([sys.executable, script, "check"], capture_output=True, text=True, encoding="utf-8", env=env)
        assert result.returncode == 0 and result.stderr.startswith("[plugin_hooks] ")


def test_render_rewrites_the_hooks_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = load()
    target = tmp_path / "hooks.json"
    monkeypatch.setattr(mod, "HOOKS_FILE", target)
    assert mod.main(["render"]) == 0
    assert json.loads(target.read_text(encoding="utf-8")) == mod.render(_settings())
    assert mod.main(["bogus"]) == 1


def _checkout(path: Path, name: str = "ontheloop", git: str | None = None) -> Path:
    (path / ".claude" / ".claude-plugin").mkdir(parents=True)
    (path / ".claude" / ".claude-plugin" / "plugin.json").write_text(f'{{\n  "name": "{name}"\n}}\n', encoding="utf-8")
    if git == "dir":
        (path / ".git").mkdir()
    elif git == "file":
        (path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    return path


_PROBE = "pwd -P >&2; printf '%s\\n' \"$CLAUDE_PROJECT_DIR\" >&2; cat >&2; exit 2"


class TestLocatedGate:
    def _gated_probe(self, tmp_path: Path) -> str:
        probe = tmp_path / "probe.sh"
        probe.write_text(f"{_PROBE}\n", encoding="utf-8")
        return f"sh {probe}"

    def _run(self, tmp_path: Path, project: Path, cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in ("ONTHELOOP_PROJECT_SETTINGS", "CLAUDE_PLUGIN_ROOT")}
        env.update(CLAUDE_PROJECT_DIR=str(project), CLAUDE_PLUGIN_ROOT=str(PLUGIN_ROOT))
        gated = load().gated(self._gated_probe(tmp_path))
        return subprocess.run(
            ["/bin/sh", "-c", gated], capture_output=True, text=True, encoding="utf-8", env=env, cwd=cwd, input=stdin
        )

    def test_a_project_dir_that_is_the_parent_runs_the_hook_from_the_checkout(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path / "work" / "ontheloop", git="dir").resolve()
        payload = '{"tool_name": "Bash", "tool_input": {"command": "ls"}}'
        ran = self._run(tmp_path, checkout.parent, checkout.parent, payload)
        assert ran.returncode == 2
        assert ran.stderr.splitlines()[:2] == [str(checkout), str(checkout)]
        assert ran.stderr.splitlines()[2] == payload

    def test_a_project_dir_that_is_the_checkout_runs_the_hook_there(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path / "ontheloop", git="dir").resolve()
        ran = self._run(tmp_path, checkout, checkout, "payload")
        assert ran.returncode == 2
        assert ran.stderr.splitlines() == [str(checkout), str(checkout), "payload"]

    def test_a_settings_command_naming_the_project_dir_runs_the_checkouts_copy(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path / "work" / "ontheloop").resolve()
        (checkout / ".claude" / "hooks").mkdir()
        (checkout / ".claude" / "hooks" / "x.sh").write_text("echo checkout copy >&2\nexit 2\n", encoding="utf-8")
        command = 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/x.sh"'
        env = {k: v for k, v in os.environ.items() if k not in ("ONTHELOOP_PROJECT_SETTINGS", "CLAUDE_PLUGIN_ROOT")}
        env.update(CLAUDE_PROJECT_DIR=str(checkout.parent), CLAUDE_PLUGIN_ROOT=str(PLUGIN_ROOT))
        ran = subprocess.run(
            ["/bin/sh", "-c", load().gated(command)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=checkout.parent,
        )
        assert (ran.returncode, ran.stderr.strip()) == (2, "checkout copy")

    def test_the_settings_marker_skips_the_hook(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path / "ontheloop")
        skipped = _run_gated(self._gated_probe(tmp_path), checkout, "1")
        assert (skipped.returncode, skipped.stdout, skipped.stderr) == (0, "", "")

    def test_zero_candidates_and_two_clones_skip_silently(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        two = tmp_path / "two"
        _checkout(two / "a", git="dir")
        _checkout(two / "b", git="dir")
        for project in (empty, two):
            skipped = self._run(tmp_path, project, project)
            assert (skipped.returncode, skipped.stdout, skipped.stderr) == (0, "", "")

    def test_a_missing_locator_or_plugin_root_skips_with_exit_0(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path / "ontheloop")
        bare = tmp_path / "bare-root"
        bare.mkdir()
        for root in (bare, None):
            skipped = _run_gated(self._gated_probe(tmp_path), checkout, None, plugin_root=root)
            assert (skipped.returncode, skipped.stdout) == (0, "")

    def test_the_shell_directory_never_changes_the_answer(self, tmp_path: Path) -> None:
        base = tmp_path / "work"
        checkout = _checkout(base / "ontheloop", git="dir").resolve()
        outside = _checkout(tmp_path / "elsewhere" / "second", git="dir")
        sibling = _checkout(base / "wt", git="file")
        for cwd in (base, checkout, outside, sibling):
            ran = self._run(tmp_path, base, cwd)
            assert ran.returncode == 2
            assert ran.stderr.splitlines()[:2] == [str(checkout), str(checkout)]
