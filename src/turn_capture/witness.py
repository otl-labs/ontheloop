"""Stop witness recognition, closing events, the frontier and close-row windows (rules WT-2..WT-7).

Pure: stdlib plus src.turn_capture.transcript. streams.py imports this module, so the streams types it annotates come in
under TYPE_CHECKING only and the two helpers that need transcripts.py import it inside the function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.turn_capture.transcript import ROOT, hook_script

if TYPE_CHECKING:
    from src.turn_capture.streams import Rec, Turn

WITNESS_LINE = "[stop_log_turn] turn witness"
WITNESS_STEM = "stop_log_turn"
API_ERROR_FLAG_KEYS = ("isApiErrorMessage",)


def is_api_error(data: dict[str, Any]) -> bool:
    return data.get("type") == "assistant" and any(data.get(key) is True for key in API_ERROR_FLAG_KEYS)


def is_witness_record(data: dict[str, Any]) -> bool:
    """The raw-record form of rule WT-2: a Stop hook_success of the capture shim whose stderr opens with the line."""
    att = data.get("attachment")
    if data.get("type") != "attachment" or not isinstance(att, dict):
        return False
    if att.get("type") != "hook_success" or att.get("hookEvent") != "Stop":
        return False
    if hook_script(att.get("command"))[0] != WITNESS_STEM:
        return False
    stderr = att.get("stderr")
    lines = stderr.splitlines() if isinstance(stderr, str) else []
    return bool(lines) and lines[0] == WITNESS_LINE


def is_witness(rec: Rec) -> bool:
    """Rule WT-2: a ROOT-stream hook record in the witness shape (a sub-agent file's SubagentStop attachment never is one)."""
    return rec.file == ROOT and rec.kind == "hook" and is_witness_record(rec.data)


def at_rest(turn: Turn, line: int) -> bool:
    """Rule WT-3's usable test at *line*: an assistant record that ended its response (or an API error) and no pending tool."""
    last = None
    for rec in turn.recs:
        if rec.line >= line:
            break
        if rec.kind == "assistant":
            last = rec
    if last is None or not (is_api_error(last.data) or isinstance(last.data["message"].get("stop_reason"), str)):
        return False
    return all(use.result_any is not None and use.result_any.rec.line < line for use in turn.tool_uses if use.rec.line < line)


def closing_events(turn: Turn) -> list[Rec]:
    """Rules WT-3 and WT-4: the turn's usable witnesses and its API-error records, in file order."""
    return [
        rec
        for rec in turn.recs
        if (rec.kind == "assistant" and is_api_error(rec.data)) or (is_witness(rec) and at_rest(turn, rec.line))
    ]


def frontier(turn: Turn) -> int:
    """Rule WT-7: the line of the turn's last closing event, else of its first prompt record."""
    return turn.closes[-1].line if turn.closes else turn.first.line


def close_window(turn: Turn, bound: int | None) -> list[Rec]:
    """The turn's records at or before *bound* (a closing event's line); None is the whole turn."""
    return turn.recs if bound is None else [rec for rec in turn.recs if rec.line <= bound]


def counts(turn: Turn, bound: int | None) -> tuple[int, int]:
    """(response_count, tool_call_count) over the records at or before *bound* (None = the whole turn), rule WT-6."""
    if bound is None:
        return len(turn.responses), len(turn.tool_uses)
    return (
        sum(1 for resp in turn.responses if resp.recs[0].line <= bound),
        sum(1 for use in turn.tool_uses if use.rec.line <= bound),
    )


def interrupted(turn: Turn, bound: int | None = None) -> bool:
    """Rule C-3's interruption tests over the records at or before *bound* (None = the whole turn)."""
    from src.turn_capture.transcripts import is_sentinel, text_of  # noqa: PLC0415

    recs = close_window(turn, bound)
    prompts = [r for r in recs if r.kind == "prompt"]
    results = [r for r in recs if r.kind == "tool_result"]
    assistants = [r for r in recs if r.kind == "assistant"]
    info = results[-1].data.get("toolUseResult") if results else None
    pending = any(
        use.result is None or (bound is not None and use.result.rec.line > bound)
        for use in turn.tool_uses
        if bound is None or use.rec.line <= bound
    )
    return (
        (bool(prompts) and is_sentinel(text_of(prompts[-1].data["message"]["content"])))
        or (bool(results) and results[-1].data.get("toolDenialKind") == "user-rejected")
        or (isinstance(info, dict) and info.get("interrupted") is True)
        or pending
        or (bool(assistants) and assistants[-1].data.get("isAbortedMidStream") is True)
    )


def turn_outcome(turn: Turn, bound: int | None = None) -> str:
    """Rule WT-6: interrupted when C-3 reads interrupted, error when the window's last assistant record is an API error."""
    if interrupted(turn, bound):
        return "interrupted"
    assistants = [r for r in close_window(turn, bound) if r.kind == "assistant"]
    return "error" if assistants and is_api_error(assistants[-1].data) else "success"
