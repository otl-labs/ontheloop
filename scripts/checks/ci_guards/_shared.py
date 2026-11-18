"""Constants and helpers shared by the ci_guards checks within this package only."""

from __future__ import annotations

import re
import sys

from scripts.checks import _common

_SHA_PINNED = re.compile(r"[^@\s]+@[0-9a-f]{40}")


def _ensure_root_on_path() -> bool:
    """Inject ROOT into sys.path if absent; return True if injection was performed."""
    root_str = str(_common.ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        return True
    return False


def is_sha_pinned(value: str) -> bool:
    """A comment-free `uses:` value pinned to a 40-hex commit (rec-5289); local and docker exemptions stay with callers."""
    return _SHA_PINNED.fullmatch(value) is not None
