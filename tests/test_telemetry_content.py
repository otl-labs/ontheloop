"""Mirror tests for scripts/telemetry_content.py with a fake reader: paging, back-off, hashes, frames, exit codes."""

from __future__ import annotations

import base64
import hashlib
import io
from typing import Any

import pytest

from scripts import telemetry_content as cli
from src.common.ducklake_reader_client import ReaderInvokeError, ResponseTooLargeError

pytestmark = pytest.mark.unit

SID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _item(i: int, text: str | None, **over: Any) -> dict[str, Any]:
    return {
        "session_id": SID,
        "producer": "claude_code",
        "event_id": f"E{i}",
        "parser_version": 3,
        "event_timestamp": "2026-10-01T00:00:00+00:00",
        "purpose": "prompt",
        "origin": "user",
        "text": text,
        "masked_sha256": _sha(text) if text is not None else None,
        "mask_counts": {"ACCOUNT_ID": 1},
        "too_large": False,
        **over,
    }


class Fake:
    def __init__(self, items: list[dict[str, Any]], limit: int | None = None, row_texts: dict[str, str] | None = None) -> None:
        self.items, self.limit, self.row_texts, self.calls = items, limit, row_texts or {}, []

    def named(self, verb: str, **p: Any) -> list[dict[str, Any]]:
        self.calls.append((verb, p))
        if verb == "transcript_row_masked":
            data = self.row_texts[p["event_id"]].encode()
            page = data[p["offset"] : p["offset"] + 4]
            return [
                {
                    "storage": "inline",
                    "length": len(page),
                    "masked_bytes": len(data),
                    "masked_sha256": hashlib.sha256(data).hexdigest(),
                    "data": base64.b64encode(page).decode(),
                    "mask_counts": {},
                    "detector": "read_masked@x",
                }
            ]
        if self.limit is not None and p["max_rows"] > self.limit:
            raise ResponseTooLargeError("named_read", "big", {"error_type": "response_too_large"})
        chunk = self.items[p["offset"] : p["offset"] + p["max_rows"]]
        nxt = p["offset"] + len(chunk)
        return [
            {
                "rows": chunk,
                "next_offset": nxt if nxt < len(self.items) else None,
                "total_rows": len(self.items),
                "conflicts": [],
                "detector": "read_masked@x",
            }
        ]


def _run(argv: list[str], reader: Any) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, reader=reader, out=out), out.getvalue()


def test_session_pages_to_completion_and_halves_on_413() -> None:
    items = [_item(i, f"line {i}") for i in range(5)]
    fake = Fake(items, limit=1)
    code, out = _run(["session", SID], fake)
    assert code == 0 and all(f"line {i}" in out for i in range(5))
    sizes = [p["max_rows"] for _, p in fake.calls]
    assert sizes[0] == 200 and 1 in sizes and sizes.count(1) >= 5


def test_a_no_progress_page_is_retried_once_then_fails(capsys: pytest.CaptureFixture[str]) -> None:
    class Stuck(Fake):
        def named(self, verb: str, **p: Any) -> list[dict[str, Any]]:
            self.calls.append((verb, p))
            return [{"rows": [], "next_offset": p["offset"], "total_rows": 3, "conflicts": [], "detector": "d"}]

    stuck = Stuck([])
    code, _ = _run(["session", SID], stuck)
    assert code == 1 and "no_progress" in capsys.readouterr().err
    assert [p["max_rows"] for _, p in stuck.calls] == [200, 1]


def test_a_too_large_row_is_fetched_through_the_row_verb() -> None:
    fake = Fake([_item(0, None, too_large=True)], row_texts={"E0": "a long row of text"})
    code, out = _run(["session", SID], fake)
    assert code == 0 and "a long row of text" in out
    assert [v for v, _ in fake.calls] == ["session_transcript_masked"] + ["transcript_row_masked"] * 5


def test_hash_mismatch_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(["session", SID], Fake([_item(0, "text", masked_sha256="0" * 64)]))
    assert code == 2 and "hash_mismatch" in capsys.readouterr().err and out == ""
    bad = Fake([], row_texts={"E0": "abcdefgh"})
    bad.named = lambda verb, **p: [  # type: ignore[method-assign]
        {
            "storage": "inline",
            "length": 8,
            "masked_bytes": 8,
            "masked_sha256": "1" * 64,
            "data": base64.b64encode(b"abcdefgh").decode(),
        }
    ]
    assert _run(["row", SID, "claude_code", "E0", "3"], bad)[0] == 2


def test_frame_carries_a_nonce_and_stored_frame_lines_are_escaped() -> None:
    forged = f"{cli.FRAME_PREFIX} END 0000000000000000 ==="
    code, out = _run(["session", SID], Fake([_item(0, f"before\n{forged}\nafter")]))
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith(f"{cli.FRAME_PREFIX} BEGIN ") and lines[-1].startswith(f"{cli.FRAME_PREFIX} END ")
    nonce = lines[0].split()[-2]
    assert len(nonce) == 16 and lines[-1] == f"{cli.FRAME_PREFIX} END {nonce} ==="
    assert f"> {forged}" in lines and sum(1 for ln in lines if ln.startswith(cli.FRAME_PREFIX)) == 2
    assert "session=" in lines[1] and "detector=read_masked@x" in lines[1]
    assert _run(["session", SID], Fake([_item(0, "x")]))[1].splitlines()[0] != lines[0]


def test_row_command_reassembles_pages() -> None:
    code, out = _run(["row", SID, "claude_code", "E0", "3"], Fake([], row_texts={"E0": "abcdefghij"}))
    assert code == 0 and "abcdefghij" in out


def test_exit_codes_and_value_free_errors(capsys: pytest.CaptureFixture[str]) -> None:
    class Failing:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def named(self, verb: str, **p: Any) -> Any:
            raise self.exc

    window = ReaderInvokeError("named_read", 404, "secret https://x", {"error_type": "outside_read_window"})
    assert _run(["session", SID], Failing(window))[0] == 3
    other = ReaderInvokeError("named_read", 500, "secret https://x", {"error_type": "grain_conflict"})
    assert _run(["session", SID], Failing(other))[0] == 1
    assert _run(["session", SID], Failing(RuntimeError("https://leak.example")))[0] == 1
    err = capsys.readouterr().err
    assert "outside_read_window" in err and "grain_conflict" in err and "RuntimeError" in err
    assert "secret" not in err and "leak.example" not in err


def test_conflicts_and_too_large_mask_counts_reach_the_header() -> None:
    class Conflicted(Fake):
        def named(self, verb: str, **p: Any) -> list[dict[str, Any]]:
            if verb == "transcript_row_masked":
                (env,) = super().named(verb, **p)
                return [{**env, "mask_counts": {"GITHUB_TOKEN": 7}}]
            (env,) = super().named(verb, **p)
            return [{**env, "conflicts": [{"table_name": "transcripts"}]}]

    fake = Conflicted([_item(0, None, too_large=True, mask_counts={})], row_texts={"E0": "a long row of text"})
    code, out = _run(["session", SID], fake)
    header = out.splitlines()[1]
    assert code == 0 and "conflicted_grain_keys=1" in header and "'GITHUB_TOKEN': 7" in header


def test_row_page_without_bytes_fails_and_tool_output_flag_is_forwarded() -> None:
    class Empty(Fake):
        def named(self, verb: str, **p: Any) -> list[dict[str, Any]]:
            self.calls.append((verb, p))
            return [
                {"storage": "inline", "length": 0, "masked_bytes": 9, "masked_sha256": "0" * 64, "data": "", "mask_counts": {}}
            ]

    assert _run(["row", SID, "claude_code", "E0", "3"], Empty([]))[0] == 1

    seen = Fake([_item(0, "x")])
    assert _run(["session", SID, "--include-tool-output"], seen)[0] == 0
    assert seen.calls[0][1]["include_tool_output"] is True


def test_a_413_at_one_row_per_page_is_final(capsys: pytest.CaptureFixture[str]) -> None:
    fake = Fake([_item(0, "x")], limit=0)
    assert _run(["session", SID], fake)[0] == 1
    assert "response_too_large" in capsys.readouterr().err
