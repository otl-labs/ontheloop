"""Entry point: map one run_persona events file and its host-run sidecar to telemetry rows in a rows file (sends nothing).

Usage: bin/venv-python -m scripts.personas.telemetry_rows --events PATH --host-run PATH --out PATH
Exit 0 rows file written; 2 refused; 3 deferred (no file); 4 producer HALT (no file).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from src.turn_capture.litellm_producer import (
    PARSER_VERSION,
    PRODUCER,
    PRODUCER_VERSION,
    Deferred,
    Mapped,
    ProducerHalt,
    Refused,
    map_run,
)
from src.turn_capture.render import render_rows_json


def rows_document(mapped: Mapped) -> dict[str, Any]:
    batches = [{"table": table, "rows": json.loads(render_rows_json(rows))} for table, rows in mapped.batches]
    return {
        "producer": PRODUCER,
        "parser_version": PARSER_VERSION,
        "producer_version": PRODUCER_VERSION,
        "batches": batches,
        "diagnostics": dict(mapped.diagnostics),
    }


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="telemetry_rows")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--host-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        events = json.loads(args.events.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"refused: unreadable events file: {exc}", file=sys.stderr)
        return 2
    try:
        host_run: bytes | None = args.host_run.read_bytes()
    except OSError:
        host_run = None
    try:
        result = map_run(events, host_run)
    except ProducerHalt as exc:
        print(f"halt: {exc}", file=sys.stderr)
        return 4
    if isinstance(result, Refused):
        print(f"refused: {result.reason}", file=sys.stderr)
        return 2
    if isinstance(result, Deferred):
        print(f"deferred: {result.reason}", file=sys.stderr)
        return 3
    _write_atomic(args.out, rows_document(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
