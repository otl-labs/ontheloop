#!/usr/bin/env python3
"""The hooks of the `ontheloop` plugin, whose root is this .claude directory (marketplace: .claude-plugin/).

Project threads never read .claude/settings.json, so the repository's hooks reach them through the plugin, which claude.ai
loads in every cloud session of the account. Each plugin hook is the settings hook behind GATE, which skips it silently
(no transcript record) when the project settings loaded, since their `env` sets ONTHELOOP_PROJECT_SETTINGS, or when the
session's project is not a checkout whose plugin manifest names this plugin. A hook therefore runs exactly once wherever the
settings load, in project threads, and nowhere else. The gate keeps the command's own tokens, so the capture parser
reads the same script stem and pin (rules H-1, P-1).

`render` rewrites .claude/hooks/hooks.json from .claude/settings.json. `check` is a plugin-only SessionStart hook that
warns when the plugin claude.ai installed differs from this checkout's render; it exits 0 on every path.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path(__file__).resolve().parents[1]
SETTINGS = CLAUDE_DIR / "settings.json"
HOOKS_FILE = CLAUDE_DIR / "hooks" / "hooks.json"
MARKER_ENV = "ONTHELOOP_PROJECT_SETTINGS"
MANIFEST_NAME = '"name": "ontheloop"'
MANIFEST = "$CLAUDE_PROJECT_DIR/.claude/.claude-plugin/plugin.json"
GATE = f'[ -n "${{{MARKER_ENV}:-}}" ] || ! grep -qs \'{MANIFEST_NAME}\' "{MANIFEST}" || '
CHECK = "bin/venv-python .claude/hooks/plugin_hooks.py check"
TAG = "[plugin_hooks]"
_CONTROL = (";", "&", "|", "\n", "`", "$(")


def gated(command: str) -> str:
    """*command* behind GATE; refuses a shell control operator, which the gate's `||` chain would split."""
    if any(op in command for op in _CONTROL):
        raise ValueError(f"hook command needs a plain simple command to be gated: {command!r}")
    return GATE + command


def render(settings: dict[str, Any]) -> dict[str, Any]:
    """The plugin's hooks.json for *settings*: every command hook gated, plus the plugin-only drift check."""
    hooks: dict[str, list[dict[str, Any]]] = {}
    for event, groups in settings["hooks"].items():
        hooks[event] = []
        for group in groups:
            entries = []
            for hook in group["hooks"]:
                if hook.get("type") != "command":
                    raise ValueError(f"only command hooks can be gated: {event} {hook!r}")
                entries.append({**hook, "command": gated(hook["command"])})
            hooks[event].append({**group, "hooks": entries})
    hooks.setdefault("SessionStart", []).insert(0, {"hooks": [{"type": "command", "command": gated(CHECK)}]})
    return {"hooks": hooks}


def _identities(hooks: object) -> set[str]:
    found: set[str] = set()
    for event, groups in hooks.items() if isinstance(hooks, dict) else ():
        for group in groups if isinstance(groups, list) else ():
            for hook in group.get("hooks") or () if isinstance(group, dict) else ():
                found.add(json.dumps([event, group.get("matcher", ""), hook], sort_keys=True))
    return found


def check(plugin_root: str | None) -> tuple[str, str]:
    """(stdout, stderr) comparing the installed plugin's hooks with this checkout's render."""
    if not plugin_root:
        return "", f"{TAG} not running from the plugin"
    try:
        installed = json.loads((Path(plugin_root) / "hooks" / "hooks.json").read_text(encoding="utf-8")).get("hooks")
        expected = render(json.loads(SETTINGS.read_text(encoding="utf-8")))["hooks"]
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
        return f"{TAG} WARNING: could not compare the plugin's hooks: {type(exc).__name__}: {exc}", f"{TAG} unreadable"
    missing, stale = len(_identities(expected) - _identities(installed)), len(_identities(installed) - _identities(expected))
    if not missing and not stale:
        return "", f"{TAG} plugin in sync"
    warning = (
        f"WARNING: the ontheloop plugin's hooks differ from this checkout's .claude/settings.json: {missing} hook(s) "
        f"missing from the plugin, {stale} the checkout no longer has. Project threads run the plugin's list. Update the "
        "plugin on claude.ai (Customize > Plugins), or regenerate .claude/hooks/hooks.json if this branch changed hooks."
    )
    return warning, f"{TAG} drift missing={missing} stale={stale}"


def main(argv: list[str]) -> int:
    if argv[:1] == ["render"]:
        rendered = render(json.loads(SETTINGS.read_text(encoding="utf-8")))
        HOOKS_FILE.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
        return 0
    if argv[:1] != ["check"]:
        sys.stderr.write(f"{TAG} usage: plugin_hooks.py render|check\n")
        return 1
    try:
        out, err = check(os.environ.get("CLAUDE_PLUGIN_ROOT"))
        if out:
            print(out)
        sys.stderr.write(err + "\n")
    except Exception as exc:  # noqa: BLE001 -- advisory on every path; never wedges session start
        print(f"{TAG} WARNING: failed: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
