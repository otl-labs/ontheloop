"""The one entry point the verdict workflow calls, as `python3 -I <main>/scripts/personas/run.py <verb>`.

The main checkout's root is derived from this file's own path, never the cwd, and goes first on sys.path; any sys.path
entry that resolves inside $PERSONA_SNAPSHOT (the untrusted head tree) refuses the run before any other import.
Stdlib only at import.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERBS = {
    "resolve": "resolve_pr",
    "route": "route",
    "fetch": "snapshot",
    "materialise": "materialise",
    "execute": "execute",
    "post": "post_verdict",
    "override-render": "override",
}
EXIT_SNAPSHOT_ON_PATH = 3


def snapshot_on_path(entries: Iterable[str], snapshot: str | None) -> str | None:
    if not snapshot:
        return None
    root = Path(snapshot).resolve()
    for entry in entries:
        resolved = Path(entry or ".").resolve()
        if resolved == root or root in resolved.parents:
            return entry
    return None


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1 or argv[0] not in VERBS:
        print(f"usage: run.py {{{'|'.join(VERBS)}}}", file=sys.stderr)
        return 2
    root = str(ROOT)
    sys.path[:] = [root] + [entry for entry in sys.path if entry != root]
    offender = snapshot_on_path(sys.path, os.environ.get("PERSONA_SNAPSHOT"))
    if offender is not None:
        print(f"PERSONA_SNAPSHOT_ON_PATH {offender}", file=sys.stderr)
        return EXIT_SNAPSHOT_ON_PATH
    module = importlib.import_module(f"scripts.personas.{VERBS[argv[0]]}")
    return int(module.main([]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
