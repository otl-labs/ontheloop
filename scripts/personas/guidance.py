"""Persona guidance provider seam (Decision 280, rec-5569).

A persona contract declares guidance slots (executor-personas.yaml guidance_slots); this module fills them. A slot's
provider is looked up by slot role, else file_default reads HOME at the base root. base_root is always the base-ref
checkout the caller passes, never the head snapshot. Only the pytest equality node binds HOME to
executor-personas.yaml guidance_slot.home. Stdlib only: it runs beside materialise under python3 -I.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path
from typing import Callable

HOME = "ontheloop/guidance/<persona>/<slot>.md"
GuidanceProvider = Callable[[Path, str, str], str]
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_REGISTRY: dict[str, GuidanceProvider] = {}


def register(slot: str, provider: GuidanceProvider) -> None:
    """Register provider(base_root, persona, slot) for a slot role; a second registration for the slot raises."""
    if slot in _REGISTRY:
        raise ValueError(f"a guidance provider is already registered for slot {slot}")
    _REGISTRY[slot] = provider


def file_default(base_root: Path, persona: str, slot: str) -> str:
    """Read HOME under base_root; "" when absent, ValueError on a bad name, symlink, non-file or non-UTF-8 file."""
    for kind, name in (("persona", persona), ("slot", slot)):
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"guidance {kind} name {name!r} is off ^[a-z][a-z0-9_]*$")
    if not base_root.is_dir():
        raise ValueError(f"guidance base root {base_root} is not a directory")
    path = base_root / HOME.replace("<persona>", persona).replace("<slot>", slot)
    if not path.parent.resolve().is_relative_to(base_root.resolve()):
        raise ValueError(f"guidance path {path} resolves outside the base root")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return ""
    except NotADirectoryError as exc:
        raise ValueError(f"guidance path {path} has a non-directory parent") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"guidance file {path} is a symlink or not a regular file")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"guidance file {path} is not UTF-8") from exc


def fill(base_root: Path, persona: str, slots: list[str]) -> dict[str, str]:
    """Slot values from each slot's registered provider, else file_default; the contract validator gates slot names."""
    return {slot: _REGISTRY.get(slot, file_default)(base_root, persona, slot) for slot in slots}
