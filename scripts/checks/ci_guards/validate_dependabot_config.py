"""Dependabot pip-group denylist exclusion gate (Decision 99, rec-3832).

The duckdb/DuckLake SSOT lockstep denylist lives as a literal in
`scripts/ci/dependabot_auto_merge.sh` (`_DENIED_DEPENDENCIES`) -- a bump of either name is never
auto-merged regardless of its semver class. Before this check existed, a denylisted name could
still ride inside a grouped Dependabot `minor-and-patch` update: the group as a whole would then
strand behind the denylist gate every week, blocking every OTHER dependency bumped alongside it.
`.github/dependabot.yml`'s `exclude-patterns` on each pip group is what keeps a denylisted name out
of the group (it still gets its own, permanently-quarantined standalone PR -- see the inline
comment on that group and Decision 99's reversal-conditions stanza for the accepted cost and its
retirement criteria).

DERIVED, never enumerated (Decision 187 derive-don't-enumerate): the denylist is parsed out of the
shell script at check time, never re-listed as a literal in this module, so a future addition to
`_DENIED_DEPENDENCIES` fails this gate until `.github/dependabot.yml` catches up -- that lag is the
point, not a bug. A denylist that cannot be parsed FAILS LOUDLY: silently treating an unparseable
denylist as "nothing to exclude" would convert this into a check that asserts nothing while
reporting green.

Scoped to `package-ecosystem: pip` entries only: duckdb is a PyPI distribution, so asserting its
absence from (for example) the `github-actions` ecosystem's groups would be vacuous and would force
noise config into an entry that could never carry it. The assertion is scoped to pip groups AS A
WHOLE, not to groups whose `patterns:` could plausibly match a denied name -- a future pip group
narrowly patterned on something duckdb could never match (say `patterns: ["boto3*"]`) is still
required to carry `exclude-patterns` for every denied name under this scoping. That is the same
noise-config trap the ecosystem-level scoping exists to avoid; if such a group appears, revisit the
scoping rather than special-casing around it. Note `ducklake` is not a pip requirement anywhere in
this repo (no requirements file declares it -- it is a DuckDB extension, not a package), so its
exclusion from the pip group is inert defensive config Dependabot could never act on. It is carried
anyway because the derived assertion reads the denylist verbatim; that is the honest price of
deriving rather than enumerating, not evidence a ducklake pip bump is reachable.

Second leg (rec-5020): the github-actions entry must carry cooldown.default-days >= 7, so auto-merged action
bumps wait out a compromised-release window. It lives here because this is the one Dependabot-config gate; it
runs only when `updates` is a list and cannot pass vacuously (a missing github-actions entry fails).

Filesystem-only: no subprocess, no network, matching every sibling ci_guards module.

Decision 170 (MANDATORY declaration obligation): calls registry.examined() on every reachable exit
path -- this is a NEW check, so it cannot be grandfathered into
config/check_accounting_baseline.yaml (frozen _BASELINE_SEED; validate_check_accounting rejects an
undeclared new check outright).
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from scripts.checks import _common, registry

_PREFIX = "dependabot-config"

_AUTO_MERGE_SCRIPT_REL_PATH = "scripts/ci/dependabot_auto_merge.sh"
_DEPENDABOT_CONFIG_REL_PATH = ".github/dependabot.yml"
_PIP_ECOSYSTEM = "pip"

# Matches `_DENIED_DEPENDENCIES="duckdb ducklake"` in the shell delegate -- the literal this module
# derives from rather than re-enumerates. See module docstring, Decision 187.
_DENIED_DEPENDENCIES_RE = re.compile(r'_DENIED_DEPENDENCIES="([^"]*)"')


def _report(failed: list[str], label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS: {label}")
        return
    print(f"  FAIL: {label}{f' ({detail})' if detail else ''}")
    failed.append(f"{_PREFIX}: {label}")


def _parse_denied_dependencies(failed: list[str], script_text: str) -> tuple[str, ...]:
    """Derive the denylist from the shell script's own literal. Empty/unparseable fails loudly."""
    match = _DENIED_DEPENDENCIES_RE.search(script_text)
    if match is None:
        print(f"  FAIL: could not parse _DENIED_DEPENDENCIES out of {_AUTO_MERGE_SCRIPT_REL_PATH}")
        failed.append(f"{_PREFIX}: could not parse _DENIED_DEPENDENCIES from the denylist script")
        return ()
    names = tuple(match.group(1).split())
    if not names:
        print(f"  FAIL: _DENIED_DEPENDENCIES parsed empty out of {_AUTO_MERGE_SCRIPT_REL_PATH}")
        failed.append(f"{_PREFIX}: _DENIED_DEPENDENCIES parsed empty")
        return ()
    print(f"  PASS: parsed denylist {names!r} from {_AUTO_MERGE_SCRIPT_REL_PATH}")
    return names


def _pip_updates(config: Any) -> list[dict[str, Any]]:
    updates = config.get("updates") if isinstance(config, dict) else None
    if not isinstance(updates, list):
        return []
    return [update for update in updates if isinstance(update, dict) and update.get("package-ecosystem") == _PIP_ECOSYSTEM]


def _assert_group_excludes_denylist(failed: list[str], group_name: str, group: Any, denied: tuple[str, ...]) -> None:
    exclude_patterns = group.get("exclude-patterns") if isinstance(group, dict) else None
    if not isinstance(exclude_patterns, list):
        exclude_patterns = []
    missing = [name for name in denied if name not in exclude_patterns]
    _report(
        failed,
        f"pip group '{group_name}' excludes every denylisted dependency",
        not missing,
        f"missing from exclude-patterns: {missing}" if missing else "",
    )


_ACTIONS_ECOSYSTEM = "github-actions"
_MIN_COOLDOWN_DAYS = 7


def _assert_actions_cooldown(failed: list[str], config: Any) -> None:
    """rec-5020: auto-merged action bumps wait at least 7 days, so a compromised release can be caught first."""
    updates = config.get("updates") if isinstance(config, dict) else None
    if not isinstance(updates, list):
        return
    entries = [u for u in updates if isinstance(u, dict) and u.get("package-ecosystem") == _ACTIONS_ECOSYSTEM]
    if not entries:
        _report(failed, "a github-actions entry carries a cooldown", False, "no github-actions entry in updates")
        return
    for entry in entries:
        cooldown = entry.get("cooldown")
        days = cooldown.get("default-days") if isinstance(cooldown, dict) else None
        ok = isinstance(days, int) and not isinstance(days, bool) and days >= _MIN_COOLDOWN_DAYS
        _report(
            failed,
            f"github-actions cooldown.default-days is at least {_MIN_COOLDOWN_DAYS}",
            ok,
            f"got {days!r}; set cooldown: default-days: {_MIN_COOLDOWN_DAYS}" if not ok else "",
        )


@registry.register("validate_dependabot_config", owner="platform")
def validate_dependabot_config(failed: list[str]) -> None:
    """Every denylisted dependency stays excluded from every pip group. See module docstring."""
    print("\n=== dependabot config denylist exclusion guard ===")

    script_path = _common.ROOT / _AUTO_MERGE_SCRIPT_REL_PATH
    try:
        script_text = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"  FAIL: could not read {_AUTO_MERGE_SCRIPT_REL_PATH}: {exc}")
        failed.append(f"{_PREFIX}: denylist script unreadable")
        registry.examined(0, unit="dependabot_pip_groups")
        return

    denied = _parse_denied_dependencies(failed, script_text)
    if not denied:
        registry.examined(0, unit="dependabot_pip_groups")
        return

    config_path = _common.ROOT / _DEPENDABOT_CONFIG_REL_PATH
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"  FAIL: could not load {_DEPENDABOT_CONFIG_REL_PATH}: {exc}")
        failed.append(f"{_PREFIX}: dependabot config unreadable")
        registry.examined(0, unit="dependabot_pip_groups")
        return

    examined_count = 0
    for update in _pip_updates(config):
        groups = update.get("groups") if isinstance(update, dict) else None
        if not isinstance(groups, dict):
            continue
        for group_name, group in groups.items():
            examined_count += 1
            _assert_group_excludes_denylist(failed, str(group_name), group, denied)

    _assert_actions_cooldown(failed, config)
    registry.examined(examined_count, unit="dependabot_pip_groups")
