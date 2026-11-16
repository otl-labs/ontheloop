#!/usr/bin/env python3
"""Resolve the originating PR head sha of RED_SHA (relief delegate for reconcile.yml's artifact_sha steps).

Same output and fail-closed marker as the inline bodies it replaces: writes value=<sha> to GITHUB_OUTPUT, and on an
empty or null answer, or a gh failure, prints the Decision 77 no-TOCTOU error and exits 1. Env: GH_TOKEN, REPO, RED_SHA.
Stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    repo, red_sha = os.environ.get("REPO", ""), os.environ.get("RED_SHA", "")
    try:
        res = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{red_sha}/pulls", "--jq", ".[0].head.sha"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        value = res.stdout.strip() if res.returncode == 0 else ""
    except OSError:
        value = ""
    if not value or value == "null":
        print(
            f"::error::No originating PR resolved for {red_sha}; cannot resolve the content-addressed artifact sha "
            "(Decision 77 no-TOCTOU).",
            file=sys.stderr,
        )
        return 1
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"value={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
