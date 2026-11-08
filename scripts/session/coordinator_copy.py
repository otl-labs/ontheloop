"""Write or check the generated shared-store copies of docs/contracts/coordinator.yaml and its harness profiles."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

CONTRACT = "docs/contracts/coordinator.yaml"
PROFILE_GLOB = "docs/contracts/coordinator-harness-*.yaml"
HEADER = "# GENERATED from {path} at {sha} -- DO NOT EDIT\n"


def _git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True)
    if result.returncode != 0:
        raise FileNotFoundError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _sources(ref: str) -> list[str]:
    names = _git("ls-tree", "--name-only", ref, "docs/contracts/").decode("utf-8", errors="replace").split("\n")
    profiles = sorted(n for n in names if n and fnmatch.fnmatchcase(n, PROFILE_GLOB))
    return [CONTRACT, *profiles]


def _copy_bytes(ref: str, path: str) -> tuple[str, bytes]:
    sha = _git("log", "-1", "--format=%H", ref, "--", path).decode("utf-8").strip()
    if not sha:
        raise FileNotFoundError(f"{path} not found at {ref}")
    header = HEADER.format(path=path, sha=sha).encode("utf-8")
    return sha, header + _git("show", f"{ref}:{path}")


def expected(ref: str) -> dict[str, tuple[str, bytes]]:
    """Return {copy basename: (sha, full copy bytes)} for the contract and every profile at ref."""
    return {Path(p).name: _copy_bytes(ref, p) for p in _sources(ref)}


def _orphans(directory: Path, names: set[str]) -> list[Path]:
    pattern = Path(PROFILE_GLOB).name
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(pattern) if p.name not in names)


def write(directory: Path, ref: str) -> int:
    try:
        copies = expected(ref)
    except FileNotFoundError as exc:
        print(f"missing: {exc}", file=sys.stderr)
        return 2
    directory.mkdir(parents=True, exist_ok=True)
    for name, (_, data) in copies.items():
        (directory / name).write_bytes(data)
    for orphan in _orphans(directory, set(copies)):
        orphan.unlink()
    return 0


def check(directory: Path, ref: str) -> int:
    try:
        copies = expected(ref)
    except FileNotFoundError as exc:
        print(f"missing: {exc}", file=sys.stderr)
        return 2
    missing = [directory / n for n in copies if not (directory / n).is_file()]
    for path in missing:
        print(f"missing: {path}", file=sys.stderr)
    if missing:
        return 2
    stale = False
    for name, (sha, data) in copies.items():
        actual = (directory / name).read_bytes()
        if actual == data:
            continue
        stale = True
        first = actual.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        copy_sha = first.split(" at ")[-1].split(" ")[0] if " at " in first else "unknown"
        print(f"stale: {name} copy sha {copy_sha}, current sha {sha}", file=sys.stderr)
    for orphan in _orphans(directory, set(copies)):
        stale = True
        print(f"stale: {orphan.name} has no source at {ref}", file=sys.stderr)
    return 1 if stale else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["write", "check"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--ref", default="origin/main")
    args = parser.parse_args(argv)
    return (write if args.mode == "write" else check)(args.directory, args.ref)


if __name__ == "__main__":
    raise SystemExit(main())
