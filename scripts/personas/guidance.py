"""Persona guidance provider seam (Decision 280, rec-5569).

A persona contract declares guidance slots (executor-personas.yaml guidance_slots); this module fills them. A slot's
provider is looked up by slot role, else file_default reads HOME at the base root. base_root is always the base-ref
checkout the caller passes, never the head snapshot. Only the pytest equality node binds HOME to
executor-personas.yaml guidance_slot.home. A slot may instead have a case provider (register_case): fill then takes the
name of a case and reads CASE_HOME at the same base root, so a head can name a case but never supply a value. Stdlib only:
it runs beside materialise under python3 -I.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path
from typing import Callable

HOME = "ontheloop/guidance/<persona>/<slot>.md"
CASE_HOME = "tests/fixtures/persona_verdict/guidance_cases/<persona>/<case>.md"
FIXTURE_CASE_SLOT = "fixture_guidance"
GuidanceProvider = Callable[[Path, str, str], str]
CaseProvider = Callable[[Path, str, str, str], str]
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_REGISTRY: dict[str, GuidanceProvider] = {}
_CASES: dict[str, CaseProvider] = {}


def register(slot: str, provider: GuidanceProvider) -> None:
    """Register provider(base_root, persona, slot) for a slot role; a second registration for the slot raises."""
    if slot in _REGISTRY or slot in _CASES:
        raise ValueError(f"a guidance provider is already registered for slot {slot}")
    _REGISTRY[slot] = provider


def register_case(slot: str, provider: CaseProvider) -> None:
    """Register provider(base_root, persona, slot, case) for a slot role; a slot held by either registry raises."""
    if slot in _REGISTRY or slot in _CASES:
        raise ValueError(f"a guidance provider is already registered for slot {slot}")
    _CASES[slot] = provider


def has_case_provider(slot: str) -> bool:
    return slot in _CASES


def _read_private(base_root: Path, template: str, persona: str, key: str, key_kind: str, *, absent_ok: bool) -> str:
    for kind, name in (("persona", persona), (key_kind, key)):
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"guidance {kind} name {name!r} is off ^[a-z][a-z0-9_]*$")
    if not base_root.is_dir():
        raise ValueError(f"guidance base root {base_root} is not a directory")
    path = base_root / template.replace("<persona>", persona).replace(f"<{key_kind}>", key)
    if not path.parent.resolve().is_relative_to(base_root.resolve()):
        raise ValueError(f"guidance path {path} resolves outside the base root")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if absent_ok:
            return ""
        raise ValueError(f"guidance file {path} is absent") from None
    except NotADirectoryError as exc:
        raise ValueError(f"guidance path {path} has a non-directory parent") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"guidance file {path} is a symlink or not a regular file")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"guidance file {path} is not UTF-8") from exc


def file_default(base_root: Path, persona: str, slot: str) -> str:
    """Read HOME under base_root; "" when absent, ValueError on a bad name, symlink, non-file or non-UTF-8 file."""
    return _read_private(base_root, HOME, persona, slot, "slot", absent_ok=True)


def case_file(base_root: Path, persona: str, slot: str, case: str) -> str:
    """Read CASE_HOME under base_root for the named case; the same refusals as file_default, and an absent file raises."""
    return _read_private(base_root, CASE_HOME, persona, case, "case", absent_ok=False)


register_case(FIXTURE_CASE_SLOT, case_file)


def fill(base_root: Path, persona: str, slots: list[str], case: str | None = None) -> dict[str, str]:
    """Slot values from each slot's registered provider, else file_default; the contract validator gates slot names.

    A slot with a case provider gives "" when case is None, else the provider's value for that case.
    """
    out: dict[str, str] = {}
    for slot in slots:
        if slot in _CASES:
            out[slot] = "" if case is None else _CASES[slot](base_root, persona, slot, case)
        else:
            out[slot] = _REGISTRY.get(slot, file_default)(base_root, persona, slot)
    return out
