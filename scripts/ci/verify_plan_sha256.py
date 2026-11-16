#!/usr/bin/env python3
"""Download-side sha256 verify of a fresh plan.bin (relief delegate for reconcile.yml and the sandbox workflow).

Same verdicts and text as the inline bodies it replaces: the expected digest comes from env FRESH_PLAN_SHA256.
Stdlib only (the apply jobs run plain python3).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Marker, e.g. RECONCILE_FRESH_PLAN_ARTIFACT.")
    parser.add_argument("--plan", default="plan.bin")
    args = parser.parse_args(argv)
    expected = os.environ.get("FRESH_PLAN_SHA256", "")
    try:
        actual = hashlib.sha256(Path(args.plan).read_bytes()).hexdigest()
    except OSError:
        actual = ""
    if not expected or actual != expected:
        print(
            f"::error::{args.label} sha256 mismatch: expected {expected}, got {actual}. "
            "Refusing to apply a possibly-corrupted plan.",
            file=sys.stderr,
        )
        return 1
    print(f"{args.label} sha256 verified: {actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
