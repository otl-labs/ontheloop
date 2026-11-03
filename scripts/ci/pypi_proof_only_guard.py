#!/usr/bin/env python3
"""Publisher-proof guard for publish-ontheloop.yml (rec-4537).

Runs in the pypi-environment job, so it is stdlib-only (validate_workflow_dependency_install). When
PROOF_ONLY is "true" it refuses unless every file in the dist directory is a wheel or sdist that PyPI
already lists, so a proof dispatch can never create a file. Every doubt fails closed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

SIMPLE_API = "https://pypi.org/simple/{project}/"
ACCEPT = "application/vnd.pypi.simple.v1+json"
_TIMEOUT_SECONDS = 20


def _fetch(url: str, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.load(response)


def check(project: str, dist: Path, flag: str | None, fetch: Callable[[str, dict[str, str]], Any] = _fetch) -> tuple[int, str]:
    if flag == "false":
        return 0, "PROOF_ONLY_OFF"
    if flag != "true":
        return 2, f"PROOF_ONLY_FLAG_INVALID: PROOF_ONLY must be 'true' or 'false', got {flag!r}"
    files = sorted(p for p in dist.iterdir() if p.is_file()) if dist.is_dir() else []
    if not files:
        return 1, f"PROOF_ONLY_NO_DIST: no files in {dist}"
    for path in files:
        if not path.name.endswith((".whl", ".tar.gz")):
            return 1, f"PROOF_ONLY_REFUSED: unexpected file {path.name}"
    try:
        published = {entry["filename"] for entry in fetch(SIMPLE_API.format(project=project), {"Accept": ACCEPT})["files"]}
    except Exception as exc:
        return 1, f"PROOF_ONLY_UNVERIFIED: {type(exc).__name__}"
    missing = [p.name for p in files if p.name not in published]
    if missing:
        return 1, f"PROOF_ONLY_REFUSED: not on PyPI: {', '.join(missing)}"
    return 0, f"PROOF_ONLY_OK files={len(files)}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: pypi_proof_only_guard PROJECT DIST_DIR", file=sys.stderr)
        return 2
    code, message = check(args[0], Path(args[1]), os.environ.get("PROOF_ONLY"))
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
