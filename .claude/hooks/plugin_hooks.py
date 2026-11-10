#!/usr/bin/env python3
"""The hooks of the `ontheloop` plugin, whose root is this .claude directory (marketplace: .claude-plugin/).

Project threads never read .claude/settings.json, so the repository's hooks reach them through the plugin, which claude.ai
loads in every cloud session of the account. Each plugin hook is the settings hook behind GATE, which skips it silently
(no transcript record) when the project settings loaded, since their `env` sets ONTHELOOP_PROJECT_SETTINGS, or when the
shipped locator (hooks/locate_checkout.bash) finds no single ontheloop checkout at the session start directory or one level
below it. Otherwise GATE runs the hook from that checkout with CLAUDE_PROJECT_DIR set to it. A hook therefore runs exactly
once wherever the settings load, in project threads, and nowhere else. The gate keeps the command's own tokens, so the
capture parser reads the same script stem and pin (rules H-1, P-1). Two plugin-only notice commands leave a trace when the
locator finds no usable layout, or does not run at all (see NOTICE_*).

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
LOCATOR = '"${CLAUDE_PLUGIN_ROOT}/hooks/locate_checkout.bash"'
_SKIP = f'[ -n "${{{MARKER_ENV}:-}}" ] || '
GATE = (
    _SKIP
    + f'! CLAUDE_PROJECT_DIR="$(bash {LOCATOR} </dev/null)" || ! cd "$CLAUDE_PROJECT_DIR" || ! export CLAUDE_PROJECT_DIR || '
)
_BROKEN = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": (
            "[plugin_hooks] repo hooks not loaded: the synced ontheloop locator is missing or does not run, so the "
            "ontheloop plugin ran no hook (no guards, no capture). Follow the AGENTS.md hook canary rule."
        ),
    }
}
NOTICE_SESSION_START = _SKIP + f"bash {LOCATOR} --notice SessionStart </dev/null || :"
NOTICE_PROMPT = _SKIP + f"bash {LOCATOR} --notice UserPromptSubmit </dev/null || printf '%s\\n' '{json.dumps(_BROKEN)}'"
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
    start = [{"type": "command", "command": gated(CHECK)}, {"type": "command", "command": NOTICE_SESSION_START}]
    hooks.setdefault("SessionStart", []).insert(0, {"hooks": start})
    hooks.setdefault("UserPromptSubmit", []).insert(0, {"hooks": [{"type": "command", "command": NOTICE_PROMPT}]})
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
