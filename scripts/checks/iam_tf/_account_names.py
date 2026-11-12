"""Read Terraform text as rendered for one environment (rec-5025, SIT Decision 252).

terraform/bootstrap and terraform/personal each hold an account_names.tf that derives every globally unique bucket
name from var.environment. Checks and tests that match literal ARNs read the .tf files through render(), which
substitutes each account-name local with its value for the chosen environment, so they see today's literal names for
sandbox. Values are read from _common.ROOT at call time, never at import. With no account_names.tf in reach, render
is the identity.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks import _common

_ASSIGN_RE = re.compile(r'^\s*([a-z_]+)\s*=\s*"([^"]*)"\s*$', re.MULTILINE)
_SUFFIX_RE = re.compile(r"name_suffix\s*=\s*\{(?P<body>[^}]*)\}\[var\.environment\]")
_MAP_ENTRY_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_VALIDATION_RE = re.compile(r'can\(regex\("\^\((?P<envs>[^)]*)\)\$", var\.environment\)\)')
_INTERP_RE = re.compile(r"\$\{local\.([a-z_]+)\}")


def account_names_files(root: Path | None = None) -> list[Path]:
    """Candidate account_names.tf files, bootstrap first. An explicit root dir is tried before the repo's roots."""
    found = [root / "account_names.tf"] if root is not None else []
    for rel in ("bootstrap", "personal"):
        found.append(_common.ROOT / "terraform" / rel / "account_names.tf")
    return [p for p in found if p.is_file()]


def suffix_map(text: str) -> dict[str, str]:
    """The environment -> suffix map written in an account_names.tf, or {} when absent."""
    m = _SUFFIX_RE.search(text)
    return dict(_MAP_ENTRY_RE.findall(m.group("body"))) if m else {}


def admitted_environments(variables_text: str) -> list[str]:
    """The environments a variables.tf environment validation admits, in written order."""
    m = _VALIDATION_RE.search(variables_text)
    return m.group("envs").split("|") if m else []


def name_locals(text: str, env: str = "sandbox") -> dict[str, str]:
    """Every account-name local of account_names.tf (except name_suffix) resolved for env."""
    suffix = suffix_map(text).get(env, "")
    raw = dict(_ASSIGN_RE.findall(text))
    resolved: dict[str, str] = {}

    def resolve(name: str, depth: int = 0) -> str:
        value = raw[name]
        if depth > 8:
            return value
        return _INTERP_RE.sub(lambda m: suffix if m.group(1) == "name_suffix" else resolve(m.group(1), depth + 1), value)

    for name in raw:
        resolved[name] = resolve(name)
    return resolved


def render(text: str, env: str = "sandbox", root: Path | None = None) -> str:
    """Substitute every account-name local reference in text with its value for env.

    `${local.<name>}` becomes the value inline; a bare `local.<name>` (optionally parenthesised, as a map key) becomes
    the quoted value. Every other token is left alone. Identity when no account_names.tf is found.
    """
    files = account_names_files(root)
    if not files:
        return text
    values = name_locals(files[0].read_text(encoding="utf-8"), env)
    if not values:
        return text
    names = "|".join(sorted(values, key=len, reverse=True))
    text = re.sub(rf"\$\{{local\.({names})\}}", lambda m: values[m.group(1)], text)
    text = re.sub(rf"\(local\.({names})\)", lambda m: f'"{values[m.group(1)]}"', text)
    return re.sub(rf"(?<![\w.])local\.({names})(?!\w)", lambda m: f'"{values[m.group(1)]}"', text)
