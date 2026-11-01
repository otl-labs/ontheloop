"""CONCERN: scripts/telemetry_usage.py and the registration of the usage verbs (PLAN-telemetry-usage-reader-verbs).

The CLI runs over a fake reader (no duckdb, no network): chunking, exact sums, halving on 413/504, the fail-closed paths and
the call bounds. The registration nodes pin the three verbs as agent-facing seven-day window verbs declared by the serving
contract.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import telemetry_usage as cli
from src.common.ducklake_event_read_verbs import EVENT_READS, MAX_WINDOW_DAYS
from src.common.ducklake_reader_client import (
    VERB_FIELDS,
    ReadDeadlineExceededError,
    ReaderInvokeError,
    ResponseTooLargeError,
)

USAGE_VERBS = ("model_usage_rollup", "tool_use_rollup", "session_startup_baseline")
CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "ducklake_telemetry_reader.yaml"


def test_usage_verbs_are_registered_agent_facing_window_verbs() -> None:
    for verb in USAGE_VERBS:
        entry = EVENT_READS[verb]
        assert (entry.binding, entry.max_window_days, entry.agent_facing, entry.paginable) == ("window", 7, True, False)
        assert entry.params == ("day_from", "day_to") and MAX_WINDOW_DAYS == 7
        assert "excluded" not in entry.description and "one tree is the rows sharing" in entry.description
        assert VERB_FIELDS[verb][-3:] == ("generations", "conflicted_grain_keys", "derivation_conflicted")


def test_reader_contract_declares_the_usage_verbs() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    invariant = next(i for i in contract["audit_invariants"] if i.startswith("Usage verbs"))
    assert all(verb in invariant for verb in USAGE_VERBS)
    latest = contract["amendment_log"][-1]
    assert latest["change_class"] == "field_add" and latest["semantic_break"] is False
    assert all(verb in latest["summary"] for verb in USAGE_VERBS)


def _model_row(day: str, root: str, model: str, **over: Any) -> dict[str, Any]:
    row = {
        "root_session_id": root,
        "harness_session_key": f"session_{root}",
        "branch": "claude/project-thread-x",
        "workflow": "plan",
        "session_started_at": f"{day}T10:00:00+00:00",
        "last_event_timestamp": f"{day}T11:00:00+00:00",
        "roots_in_tree": 1,
        "model": model,
        "session_count": 2,
        "model_call_count": 3,
        "tokens_input_total": 10,
        "tokens_output_total": 20,
        "tokens_cache_read_total": 60,
        "tokens_cache_creation_total": 30,
        "cache_creation_1h_total": None,
        "cache_creation_5m_total": 5,
        "usage_missing_count": 0,
        "generations": [],
        "conflicted_grain_keys": [],
        "derivation_conflicted": False,
    }
    return {**row, **over}


class FakeReader:
    def __init__(self, rows: list[dict[str, Any]], errors: dict[tuple[str, str], Exception] | None = None) -> None:
        self.rows, self.errors, self.calls = rows, errors or {}, []

    def named(self, verb: str, day_from: str, day_to: str) -> list[dict[str, Any]]:
        self.calls.append((verb, day_from, day_to))
        error = self.errors.get((day_from, day_to))
        if error is not None:
            raise error
        return [r for r in self.rows if day_from <= r["session_started_at"][:10] <= day_to]


def _run(argv: list[str], reader: Any) -> tuple[int, dict[str, Any] | None, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv, reader=reader, out=out, err=err)
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def _days(first: int, last: int) -> list[str]:
    return [date(2026, 10, d).isoformat() for d in range(first, last + 1)]


def _rows() -> list[dict[str, Any]]:
    return [_model_row(day, f"root-{day}", "fable" if i % 2 else "opus") for i, day in enumerate(_days(1, 20))]


def test_twenty_days_make_three_exact_chunks() -> None:
    reader = FakeReader(_rows())
    code, result, err = _run(["models", "--from", "2026-10-01", "--to", "2026-10-20"], reader)
    assert code == 0 and result is not None
    assert [(c[1], c[2]) for c in reader.calls] == [
        ("2026-10-01", "2026-10-07"),
        ("2026-10-08", "2026-10-14"),
        ("2026-10-15", "2026-10-20"),
    ]
    assert result["calls"] == 3 and "windows=3" in err and "calls=3" in err
    assert cli.windows(date(2026, 10, 1), date(2026, 10, 7)) == [(date(2026, 10, 1), date(2026, 10, 7))]


def test_chunk_sums_equal_a_single_window_sum() -> None:
    rows = _rows()[:7]
    whole = _run(["models", "--from", "2026-10-01", "--to", "2026-10-07"], FakeReader(rows))[1]
    split = _run(["models", "--from", "2026-10-01", "--to", "2026-10-07"], FakeReader(rows, {}))[1]
    chunks = _run(["models", "--from", "2026-10-01", "--to", "2026-10-14"], FakeReader(rows))[1]
    assert whole is not None and split is not None and chunks is not None
    assert chunks["rows"] == whole["rows"]
    by_model = {r["model"]: r for r in chunks["rows"]}
    assert by_model["opus"]["model_call_count"] == 12 and by_model["opus"]["trees"] == 4
    assert by_model["fable"]["tokens_output_total"] == 60 and by_model["fable"]["cache_creation_1h_total"] is None
    assert by_model["fable"]["cache_creation_5m_total"] == 15


def test_a_failed_chunk_prints_no_total_and_exits_1() -> None:
    floor = ReadDeadlineExceededError("named_read", "slow", {"error_type": "read_deadline_exceeded"})
    gone = ReaderInvokeError("named_read", 503, "gone", {"error_type": "catalog_unavailable"})
    cases = (
        (["--from", "2026-10-08", "--to", "2026-10-08"], ("2026-10-08", "2026-10-08"), floor, "read_deadline_exceeded"),
        (["--from", "2026-10-01", "--to", "2026-10-14"], ("2026-10-08", "2026-10-14"), gone, "catalog_unavailable"),
    )
    for argv, window, error, error_type in cases:
        code, result, err = _run(["models", *argv], FakeReader(_rows(), {window: error}))
        assert code == 1 and result is None and "calls=" not in err
        assert f"telemetry_usage FAIL window={window[0]}..{window[1]} error_type={error_type}" in err


def test_413_and_504_halve_the_window() -> None:
    errors: dict[tuple[str, str], Exception] = {
        ("2026-10-01", "2026-10-07"): ResponseTooLargeError("named_read", "big", None),
        ("2026-10-01", "2026-10-04"): ReadDeadlineExceededError("named_read", "slow", None),
    }
    reader = FakeReader(_rows(), errors)
    code, result, _ = _run(["models", "--from", "2026-10-01", "--to", "2026-10-07"], reader)
    assert code == 0 and result is not None
    assert [(c[1], c[2]) for c in reader.calls] == [
        ("2026-10-01", "2026-10-07"),
        ("2026-10-01", "2026-10-04"),
        ("2026-10-01", "2026-10-02"),
        ("2026-10-03", "2026-10-04"),
        ("2026-10-05", "2026-10-07"),
    ]
    assert result["calls"] == 5
    assert sum(r["model_call_count"] for r in result["rows"]) == 21


def test_max_windows_refused_before_any_call() -> None:
    reader = FakeReader(_rows())
    code, result, err = _run(["models", "--from", "2026-09-01", "--to", "2026-11-30"], reader)
    assert (code, result, reader.calls) == (2, None, []) and "--max-windows 9" in err
    code, result, _ = _run(["models", "--from", "2026-10-01", "--to", "2026-10-20", "--max-windows", "2"], reader)
    assert (code, reader.calls) == (2, [])


def test_halving_past_the_call_cap_fails_closed() -> None:
    errors = {(f"2026-10-{d:02d}", f"2026-10-{d:02d}"): ReadDeadlineExceededError("named_read", "slow", None) for d in (1,)}
    always = {(w[0].isoformat(), w[1].isoformat()): ResponseTooLargeError("named_read", "big", None) for w in _all_splits()}
    reader = FakeReader(_rows(), {**errors, **always})
    code, result, err = _run(["models", "--from", "2026-10-01", "--to", "2026-10-07"], reader)
    assert code == 1 and result is None and len(reader.calls) <= cli.MAX_CALLS
    assert "telemetry_usage FAIL window=" in err


def _all_splits() -> list[tuple[date, date]]:
    out = []
    for size in range(1, 8):
        for start in range(1, 8):
            if start + size - 1 <= 31:
                out.append((date(2026, 10, start), date(2026, 10, start + size - 1)))
    return out


def test_cache_read_ratio_null_on_zero_denominator() -> None:
    assert cli.cache_read_ratio(60, 10, 30) == 0.6
    assert cli.cache_read_ratio(0, 0, 0) is None
    assert cli.cache_read_ratio(None, 1, 1) is None and cli.cache_read_ratio(1, 1, None) is None
    rows = [_model_row("2026-10-01", "a", "m", tokens_input_total=0, tokens_cache_read_total=0, tokens_cache_creation_total=0)]
    code, result, _ = _run(["models", "--from", "2026-10-01", "--to", "2026-10-01"], FakeReader(rows))
    assert code == 0 and result is not None and result["rows"][0]["cache_read_ratio"] is None


def test_branch_prefix_literal() -> None:
    assert cli.branch_prefix("claude/project-thread-abc123") == "claude/project-thread"
    assert cli.branch_prefix("claude/other-thing") == "claude"
    assert cli.branch_prefix("agent/run-1") == "agent"
    assert cli.branch_prefix("main") == "(none)" and cli.branch_prefix(None) == "(none)"


def test_by_root_and_by_agent_widen_the_key() -> None:
    rows = [_model_row("2026-10-01", "a", "m"), _model_row("2026-10-01", "b", "m")]
    plain = _run(["models", "--from", "2026-10-01", "--to", "2026-10-01"], FakeReader(rows))[1]
    wide = _run(["models", "--from", "2026-10-01", "--to", "2026-10-01", "--by-root"], FakeReader(rows))[1]
    assert plain is not None and wide is not None
    assert (len(plain["rows"]), plain["rows"][0]["trees"]) == (1, 2) and len(wide["rows"]) == 2
    assert {r["harness_session_key"] for r in wide["rows"]} == {"session_a", "session_b"}
    assert _run(["models", "--from", "2026-10-01", "--to", "2026-10-01", "--by-agent"], FakeReader(rows))[0] == 2
    tool = {
        "root_session_id": "a", "harness_session_key": "k", "session_started_at": "2026-10-01T10:00:00+00:00",
        "is_root": False, "agent_name": "Explore", "tool_name": "mcp__github__x", "mcp_server": "github",
        "call_count": 2, "error_count": 1, "blocked_count": 0, "interrupted_count": 0, "session_count": 1,
        "derivation_conflicted": True,
    }  # fmt: skip
    root_tool = {**tool, "is_root": True, "agent_name": None, "call_count": 3}
    narrow = _run(["tools", "--from", "2026-10-01", "--to", "2026-10-01"], FakeReader([tool, root_tool]))[1]
    split = _run(["tools", "--from", "2026-10-01", "--to", "2026-10-01", "--by-agent"], FakeReader([tool, root_tool]))[1]
    assert narrow is not None and split is not None
    assert (len(narrow["rows"]), narrow["rows"][0]["call_count"], narrow["rows"][0]["session_row_count"]) == (1, 5, 2)
    assert len(split["rows"]) == 2 and narrow["conflicted_rows"] == 2


def test_startup_groups_by_day_prefix_and_root_with_nearest_rank_percentiles() -> None:
    def row(n: int, tokens: int | None, branch: str = "claude/project-thread-z", root: bool = True) -> dict[str, Any]:
        return {
            "session_started_at": "2026-10-01T10:00:00+00:00", "branch": branch, "is_root": root, "model_call_count": n,
            "tokens_input": tokens, "tokens_cache_read": 0, "tokens_cache_creation": 0, "derivation_conflicted": False,
        }  # fmt: skip

    rows = [row(1, t) for t in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)] + [row(0, None), row(1, 5, "agent/x", False)]
    code, result, _ = _run(["startup", "--from", "2026-10-01", "--to", "2026-10-01"], FakeReader(rows))
    assert code == 0 and result is not None
    agent, thread = result["rows"]
    assert (thread["branch_prefix"], thread["is_root"], thread["count"], thread["sessions_without_call"]) == (
        "claude/project-thread",
        True,
        10,
        1,
    )
    assert (thread["first_call_input_median"], thread["first_call_input_p90"]) == (55, 90)
    assert (thread["first_call_input_min"], thread["first_call_input_max"]) == (10, 100)
    assert (agent["branch_prefix"], agent["is_root"], agent["count"]) == ("agent", False, 1)


def test_halving_beyond_the_call_cap_stops_at_the_cap() -> None:
    class Oversize(FakeReader):
        def named(self, verb: str, day_from: str, day_to: str) -> list[dict[str, Any]]:
            if day_from != day_to:
                self.calls.append((verb, day_from, day_to))
                raise ResponseTooLargeError("named_read", "big", None)
            return super().named(verb, day_from=day_from, day_to=day_to)

    reader = Oversize([])
    code, result, err = _run(["models", "--from", "2026-09-01", "--to", "2026-10-05"], reader)
    assert (code, result) == (1, None) and len(reader.calls) == cli.MAX_CALLS
    assert "error_type=call_cap_exceeded" in err


def test_the_default_reader_uses_the_platform_profile_never_the_environment_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.telemetry_capture import read_route  # noqa: PLC0415

    seen: list[str | None] = []

    class Recorder(FakeReader):
        def __init__(self, profile: str | None = None) -> None:
            super().__init__([])
            seen.append(profile)

    monkeypatch.setattr(read_route, "TelemetryReader", Recorder)
    monkeypatch.setenv("AWS_PROFILE", "agent_platform_admin")
    for name in ("AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(name, raising=False)
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(["tools", "--from", "2026-10-01", "--to", "2026-10-01"], out=out, err=err) == 0
    assert seen == ["agent_platform"]


def test_main_exits_with_the_run_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run", lambda argv: 3)
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 3
