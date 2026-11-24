"""Run one persona image locally and write the result, the events file and the host-run sidecar side by side.

Usage: python -m src.persona_hosts.local_container.cli --image REF --persona ID --input PATH --out DIR
       --project-ref REF --agent KEY [--key-variable V] [--base-url-variable V] [--network N] [--timeout S]
Exit: the container's own exit code once DIR is written. 2: refused before the run (non-empty DIR, unreadable input,
bad sidecar or variable arguments), nothing written. 3: the run could not complete (docker unreachable, timeout),
nothing written. The CLI's exit 2 and 3 are told apart from a container's by whether DIR was written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.persona_hosts.local_container.adapter import run_image


def _read_input(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local_container.cli")
    parser.add_argument("--image", required=True)
    parser.add_argument("--persona", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--key-variable", default=None)
    parser.add_argument("--base-url-variable", default=None)
    parser.add_argument("--network", default="bridge")
    parser.add_argument("--timeout", type=int, default=3700)
    args = parser.parse_args(argv)
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        print(f"refused: {args.out} is not an empty directory", file=sys.stderr)
        return 2
    payload = _read_input(args.input)
    if payload is None:
        print(f"refused: unreadable input {args.input}", file=sys.stderr)
        return 2
    try:
        run = run_image(
            args.image,
            args.persona,
            payload,
            key_variable=args.key_variable,
            base_url_variable=args.base_url_variable,
            network=args.network,
            timeout_s=args.timeout,
            project_ref=args.project_ref,
            agent=args.agent,
        )
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 3
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "result.json").write_text(json.dumps(run.result, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "events.json").write_text(json.dumps(run.events, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "host_run.json").write_bytes(run.host_run or b"")
    if run.stderr:
        print(run.stderr, file=sys.stderr, end="")
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())
