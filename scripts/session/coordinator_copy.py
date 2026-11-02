"""Write or check the generated shared-store copy of docs/contracts/coordinator.yaml."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CONTRACT = "docs/contracts/coordinator.yaml"
HEADER = "# GENERATED from docs/contracts/coordinator.yaml at {sha} -- DO NOT EDIT\n"


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise FileNotFoundError(result.stderr.strip())
    return result.stdout


def expected(ref: str) -> tuple[str, str]:
    """Return (sha, full copy text) for the contract at ref."""
    sha = _git("log", "-1", "--format=%H", ref, "--", CONTRACT).strip()
    if not sha:
        raise FileNotFoundError(f"{CONTRACT} not found at {ref}")
    return sha, HEADER.format(sha=sha) + _git("show", f"{ref}:{CONTRACT}")


def write(directory: Path, ref: str) -> int:
    try:
        _, text = expected(ref)
    except FileNotFoundError as exc:
        print(f"missing: {exc}", file=sys.stderr)
        return 2
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "coordinator.yaml").write_text(text, encoding="utf-8", newline="")
    return 0


def check(directory: Path, ref: str) -> int:
    copy = directory / "coordinator.yaml"
    try:
        sha, text = expected(ref)
    except FileNotFoundError as exc:
        print(f"missing: {exc}", file=sys.stderr)
        return 2
    if not copy.is_file():
        print(f"missing: {copy}", file=sys.stderr)
        return 2
    actual = copy.read_text(encoding="utf-8")
    if actual == text:
        return 0
    first = actual.splitlines()[0] if actual else ""
    copy_sha = first.split(" at ")[-1].split(" ")[0] if " at " in first else "unknown"
    print(f"stale: copy sha {copy_sha}, current sha {sha}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["write", "check"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--ref", default="origin/main")
    args = parser.parse_args(argv)
    return (write if args.mode == "write" else check)(args.directory, args.ref)


if __name__ == "__main__":
    raise SystemExit(main())
