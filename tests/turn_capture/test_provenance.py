"""Rules PV-1..PV-4 and DG-1's provenance counters: parse, select and count session provenance from the transcript only."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from src.turn_capture import provenance
from src.turn_capture.provenance import (
    SESSION_KEY_PATTERN,
    SHA_PATTERN,
    harness_session_key,
    open_provenance,
    pin_facts,
    resume_provenance,
)
from src.turn_capture.streams import ROOT
from src.turn_capture.usage import EFFORT_PATTERN
from tests.fixtures.turn_capture_corpus import (
    assistant,
    attachment,
    hook,
    parse_records,
    pin_attachment,
    pin_facts_text,
    prompt,
    remote_change,
    text_block,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
REF = "ontheloop"
SHA1, SHA2, SHA3 = "1" * 40, "2" * 40, "3" * 40


def _fields(name: str) -> dict:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))["fields"]


def _stream(records, final: bool = True):
    return parse_records(records, None, final).streams[ROOT]


def test_producer_patterns_equal_the_contract_rules() -> None:
    sessions, observations = _fields("telemetry_sessions.yaml"), _fields("telemetry_observations.yaml")
    assert (
        SHA_PATTERN
        == sessions["checkout_sha"]["dq_intent"]["pattern"]
        == sessions["checkout_base_sha"]["dq_intent"]["pattern"]
    )
    assert SESSION_KEY_PATTERN == sessions["harness_session_key"]["dq_intent"]["pattern"]
    assert EFFORT_PATTERN == observations["reasoning_effort"]["dq_intent"]["pattern"]


def test_pin_facts_parse_the_hook_line_and_reject_everything_else() -> None:
    line = "[session_start_log_turn] capture pin checkout={} base={} dirty={}\n"
    assert pin_facts({"stderr": line.format(SHA1, SHA2, "true")}) == (SHA1, SHA2, True)
    assert pin_facts({"stderr": line.format(SHA1, "unknown", "false")}) == (SHA1, None, False)
    assert pin_facts({"stderr": line.format("unknown", "unknown", "unknown")}) == (None, None, None)
    sha64 = "a" * 64
    assert pin_facts({"stderr": "noise\n" + line.format(sha64, sha64, "true")}) == (sha64, sha64, True)
    for bad in (
        "[session_start_log_turn] capture pin\n",
        line.format("1" * 39, SHA2, "true"),
        line.format("G" * 40, SHA2, "true"),
        line.format(SHA1, SHA2, "maybe"),
        line.format(("a" * 40).upper(), SHA2, "true"),
        line.format(SHA1, SHA2, "true").replace("capture pin", "capture   pin"),
        "x " + line.format(SHA1, SHA2, "true"),
        None,
        5,
    ):
        assert pin_facts({"stderr": bad}) == (None, None, None), bad
    assert pin_facts({}) == (None, None, None)


def _turns(*pre):
    return [*pre, prompt("u1", 1, "p1", "a"), assistant("a1", 2, "m1", [text_block("x")])]


def test_open_provenance_reads_the_last_pin_before_the_first_prompt_only() -> None:
    root = _turns(
        pin_attachment("h0", 0.1, REF, pin_facts_text("1", "2", "false")),
        pin_attachment("h1", 0.2, REF, pin_facts_text("3", "2", "true")),
        hook("other", 0.3, "SessionStart:startup", command="bash .claude/hooks/other.sh", stderr=pin_facts_text()),
        hook("late", 0.4, "PreToolUse:Bash", command="bash .claude/hooks/session_start_log_turn.sh", stderr="x"),
    )
    root.append(pin_attachment("after", 5, REF, pin_facts_text("4", "4", "false")))
    diag: Counter[str] = Counter()
    assert open_provenance(_stream(root), True, diag) == (SHA3, SHA2, True)
    assert not diag
    base_unknown = _turns(pin_attachment("h0", 0.1, REF, pin_facts_text("1", "unknown", "false")))
    diag = Counter()
    assert open_provenance(_stream(base_unknown), True, diag) == (SHA1, None, False)
    assert dict(diag) == {"checkout_base_unknown": 1}
    for none in (
        _turns(),
        _turns(pin_attachment("h0", 0.1, REF, None)),
        _turns(pin_attachment("h0", 0.1, REF, pin_facts_text("unknown", "2", "false"))),
    ):
        diag = Counter()
        assert open_provenance(_stream(none), True, diag)[0] is None and diag["pin_without_checkout"] == 1
    diag = Counter()
    assert open_provenance(_stream(_turns()), False, diag) == (None, None, None) and not diag


def test_resume_provenance_reads_the_last_pin_inside_its_hook_group() -> None:
    root = [
        *_turns(pin_attachment("h0", 0.1, REF, pin_facts_text("1", "2", "false"))),
        pin_attachment("rs", 10, REF, pin_facts_text("3", "2", "true"), "SessionStart:resume"),
        pin_attachment("rs2", 10.5, REF, pin_facts_text("4", "unknown", "false"), "SessionStart:resume"),
        prompt("u2", 11, "p2", "b"),
        pin_attachment("not_in_group", 11.5, REF, pin_facts_text("5", "5", "true")),
        assistant("a2", 12, "m2", [text_block("y")]),
    ]
    stream = _stream(root)
    assert len(stream.resumes) == 1
    diag: Counter[str] = Counter()
    assert resume_provenance(stream, stream.resumes[0], True, diag) == ("4" * 40, None, False)
    assert dict(diag) == {"checkout_base_unknown": 1}
    plain = [*_turns(), hook("rs", 10, "SessionStart:resume"), prompt("u2", 11, "p2", "b")]
    stream = _stream(plain)
    diag = Counter()
    assert resume_provenance(stream, stream.resumes[0], True, diag) == (None, None, None)
    assert dict(diag) == {"pin_without_checkout": 1}
    diag = Counter()
    resume_provenance(stream, stream.resumes[0], False, diag)
    assert not diag


def test_harness_session_key_is_the_first_remote_change_at_or_before_the_bound() -> None:
    diag: Counter[str] = Counter()
    root = _turns(remote_change("rc1", 0.5), remote_change("rc2", 0.6, "https://example.invalid/code/session_other"))
    assert harness_session_key(_stream(root), 10, diag) == "session_AAAA1111bbbb2222cccc3333" and not diag
    late = [*_turns(), remote_change("late", 5)]
    assert harness_session_key(_stream(late), 1, diag) is None and diag["harness_session_key_absent"] == 1
    for bad in ("https://example.invalid/", "http://[::1/session_x", "https://example.invalid/code/_bad", "", 7):
        diag = Counter()
        stream = _stream(_turns(attachment("rc", 0.5, "remote_session_change", url=bad)))
        assert harness_session_key(stream, 10, diag) is None and diag["harness_session_key_unparsed"] == 1, bad
    diag = Counter()
    assert harness_session_key(_stream(_turns()), None, diag) is None and diag["harness_session_key_absent"] == 1
    assert provenance.NO_FACTS == (None, None, None)


def test_late_pin_is_counted_never_read_by_the_open_row() -> None:
    root = _turns()
    root.append(pin_attachment("late", 5, REF, pin_facts_text("1", "2", "false")))
    diag: Counter[str] = Counter()
    assert open_provenance(_stream(root), True, diag) == (None, None, None)
    assert diag["pin_after_first_prompt"] == 1 and diag["pin_without_checkout"] == 1
    diag = Counter()
    open_provenance(_stream(root), False, diag)
    assert not diag


def test_late_resume_pin_is_not_counted_for_the_open_row() -> None:
    for name in ("SessionStart:resume", "SessionStart:compact"):
        root = [*_turns(), pin_attachment("late", 5, REF, pin_facts_text(), name)]
        diag: Counter[str] = Counter()
        open_provenance(_stream(root), True, diag)
        assert diag["pin_after_first_prompt"] == 0, name
    early = _turns(pin_attachment("h0", 0.1, REF, pin_facts_text()))
    early.append(pin_attachment("late", 5, REF, pin_facts_text()))
    diag = Counter()
    open_provenance(_stream(early), True, diag)
    assert diag["pin_after_first_prompt"] == 0
