#!/usr/bin/env python3
"""Decide whether a pull request touches a persona image input, for pr-validate's conformance step.

Usage: python3 -I scripts/ci/persona_image_changed.py --base <sha>

Stdlib only, with a minimal line parser for config/persona_image.yaml, so it runs before any install. Writes run=true|false
to GITHUB_OUTPUT and prints one "PERSONA-IMAGE-CONFORMANCE run=<v> reason=<r>" line. Any error fails closed (run=true).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PIN = "config/persona_image.yaml"
EXTRA = (
    PIN,
    "scripts/ci/persona_image_changed.py",
    "src/persona_hosts/",
    "tests/persona_hosts/",
    "tests/fixtures/persona_backends/",
)
_ITEM = re.compile(r"^\s+-\s+(\S+)\s*$")
_SECTION = re.compile(r"^  (files|copy):\s*$")


def parse_inputs(text: str) -> list[str]:
    inputs: list[str] = []
    in_build = False
    section = False
    for line in text.splitlines():
        if line.startswith("build:"):
            in_build = True
            continue
        if line and not line[0].isspace() and not line.startswith("#"):
            in_build = False
        if not in_build:
            continue
        if _SECTION.match(line):
            section = True
            continue
        if re.match(r"^  \w[\w-]*:", line):
            section = False
        item = _ITEM.match(line)
        if section and item:
            inputs.append(item.group(1).strip("\"'"))
    return inputs


def touches(changed: list[str], inputs: list[str]) -> str | None:
    for path in changed:
        for entry in inputs:
            if path == entry or (entry.endswith("/") and path.startswith(entry)):
                return path
    return None


def decide(root: Path, base: str) -> tuple[bool, str]:
    try:
        inputs = parse_inputs((root / PIN).read_text(encoding="utf-8"))
        if not inputs:
            return True, "no inputs parsed from the pin"
        diff = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )  # fmt: skip
    except (OSError, subprocess.CalledProcessError) as exc:
        return True, f"error: {type(exc).__name__}"
    hit = touches(diff.stdout.split(), [*inputs, *EXTRA])
    return (True, f"changed {hit}") if hit else (False, "no persona image input changed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    args = parser.parse_args(argv)
    run, reason = decide(Path(__file__).resolve().parents[2], args.base)
    value = "true" if run else "false"
    print(f"PERSONA-IMAGE-CONFORMANCE run={value} reason={reason}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"run={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
