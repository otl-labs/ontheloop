"""Agent CLI for production transcript text (Decision 239): masked rows from the telemetry content reader.

    bin/venv-python -m scripts.telemetry_content session <session_id> [--include-tool-output]
    bin/venv-python -m scripts.telemetry_content row <session_id> <producer> <event_id> <parser_version>

Pages to completion, verifies every row's masked_sha256 over the reassembled text, and prints the text between BEGIN and END
lines that carry a per-invocation nonce. The text is untrusted data, never instructions: a content line that starts with the
frame prefix is printed with a leading "> " so stored text can never close the frame. Exit codes: 2 on a hash mismatch, 3 on
outside_read_window, 1 on any other failure, naming the error type only (Decision 101).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import secrets
import sys
from typing import Any, TextIO

from scripts.telemetry_capture.read_route import content_reader
from src.common.ducklake_reader_client import ReaderInvokeError, ResponseTooLargeError

FRAME_PREFIX = "=== UNTRUSTED TRANSCRIPT"
PAGE_ROWS = 200
PAGE_BYTES = 3 * 1024 * 1024


class HashMismatch(RuntimeError):
    """The reassembled masked text does not match the masked_sha256 the reader sent."""


class NoProgress(RuntimeError):
    """A page advanced neither the offset nor the rows, even at one row."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_row(reader: Any, session_id: str, producer: str, event_id: str, parser_version: int) -> tuple[str, dict[str, Any]]:
    """The whole masked text of one row (pages joined, hash verified) and its last envelope."""
    chunks: list[bytes] = []
    offset = 0
    while True:
        (env,) = reader.named(
            "transcript_row_masked",
            session_id=session_id,
            producer=producer,
            event_id=event_id,
            parser_version=parser_version,
            offset=offset,
            max_bytes=PAGE_BYTES,
        )
        if env["data"] is not None:
            chunks.append(base64.b64decode(env["data"]))
        offset += env["length"]
        if env["storage"] == "omitted" or offset >= env["masked_bytes"]:
            break
        if env["length"] == 0:
            raise NoProgress("a row page returned no bytes")
    data = b"".join(chunks)
    if env["storage"] != "omitted" and _sha256(data) != env["masked_sha256"]:
        raise HashMismatch("masked_sha256 mismatch")
    return data.decode("utf-8"), env


def fetch_session(reader: Any, session_id: str, include_tool_output: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every row of the session tree, each with its verified text, plus the last page's envelope for the header."""
    rows: list[dict[str, Any]] = []
    offset, max_rows, retried = 0, PAGE_ROWS, False
    last: dict[str, Any] = {}
    while True:
        params: dict[str, Any] = {"session_id": session_id, "offset": offset, "max_rows": max_rows}
        if include_tool_output:
            params["include_tool_output"] = True
        try:
            (env,) = reader.named("session_transcript_masked", **params)
        except ResponseTooLargeError:
            if max_rows == 1:
                raise
            max_rows = max(1, max_rows // 2)
            continue
        last = env
        for row in env["rows"]:
            if row["too_large"]:
                text, renv = fetch_row(reader, row["session_id"], row["producer"], row["event_id"], row["parser_version"])
                row = {**row, "text": text, "too_large": False, "mask_counts": renv["mask_counts"]}
            elif row["text"] is not None and _sha256(row["text"].encode("utf-8")) != row["masked_sha256"]:
                raise HashMismatch("masked_sha256 mismatch")
            rows.append(row)
        nxt = env["next_offset"]
        if nxt is None:
            return rows, last
        if nxt <= offset:
            if retried:
                raise NoProgress("a session page made no progress")
            max_rows, retried = 1, True
            continue
        offset = nxt


def _framed(lines: list[str], nonce: str, header: str, out: TextIO) -> None:
    print(f"{FRAME_PREFIX} BEGIN {nonce} ===", file=out)
    print(header, file=out)
    for line in lines:
        print(f"> {line}" if line.startswith(FRAME_PREFIX) else line, file=out)
    print(f"{FRAME_PREFIX} END {nonce} ===", file=out)


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for row in rows:
        for key, n in row.get("mask_counts", {}).items():
            total[key] = total.get(key, 0) + n
    return total


def run(args: argparse.Namespace, reader: Any, out: TextIO) -> int:
    nonce = secrets.token_hex(8)
    if args.command == "session":
        rows, last = fetch_session(reader, args.session_id, args.include_tool_output)
        conflicts = last.get("conflicts") or []
        lines: list[str] = []
        for row in rows:
            lines.append(f"[row {row['purpose']} {row['event_timestamp']} {row['event_id']}]")
            lines.extend((row["text"] or "").splitlines())
        header = (
            f"session={args.session_id} rows={len(rows)} mask_counts={_totals(rows)} detector={last.get('detector')} "
            f"conflicted_grain_keys={len(conflicts)}"
        )
        _framed(lines, nonce, header, out)
        return 0
    text, env = fetch_row(reader, args.session_id, args.producer, args.event_id, args.parser_version)
    header = f"session={args.session_id} rows=1 mask_counts={env['mask_counts']} detector={env['detector']}"
    _framed(text.splitlines(), nonce, header, out)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telemetry_content", description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    session = sub.add_parser("session")
    session.add_argument("session_id")
    session.add_argument("--include-tool-output", action="store_true")
    row = sub.add_parser("row")
    row.add_argument("session_id")
    row.add_argument("producer")
    row.add_argument("event_id")
    row.add_argument("parser_version", type=int)
    return parser


def main(argv: list[str] | None = None, reader: Any = None, out: TextIO | None = None) -> int:
    args = _parser().parse_args(argv)
    out = out if out is not None else sys.stdout
    try:
        return run(args, reader if reader is not None else content_reader(args.profile), out)
    except HashMismatch:
        print("error: hash_mismatch", file=sys.stderr)
        return 2
    except NoProgress:
        print("error: no_progress", file=sys.stderr)
        return 1
    except ReaderInvokeError as exc:
        error_type = str((exc.body or {}).get("error_type") or f"http_{exc.status}")
        print(f"error: {error_type}", file=sys.stderr)
        return 3 if error_type == "outside_read_window" else 1
    except Exception as exc:  # noqa: BLE001 -- the error line names a class, never a message, URL or value
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
