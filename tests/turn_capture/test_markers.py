"""Mirror test for src/turn_capture/markers.py (rec-4172)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.turn_capture import markers
from src.turn_capture.markers import (
    STATUS_SOURCE_MAX,
    Marker,
    count_overlong_status_lines,
    format_gate_marker,
    format_status_marker,
    parse_markers,
)

_CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "telemetry_observations.yaml"


def _contract_process_event_pattern() -> str:
    return yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))["fields"]["name"]["dq_intent"]["pattern_when"][0]["pattern"]


def test_gate_marker_round_trips() -> None:
    line = format_gate_marker("validate_scope_boundary")
    assert line == "GATE-FAILURE v1 source=gate check=validate_scope_boundary"
    assert parse_markers(f"noise\n{line}\n") == [Marker("gate", "validate_scope_boundary", 1)]


@pytest.mark.parametrize("bad", ["", "-lead", "a b", "a:b", "a" * 201, "café"])
def test_format_rejects_non_identifiers(bad: str) -> None:
    with pytest.raises(ValueError):
        format_gate_marker(bad)


@pytest.mark.parametrize(
    "line",
    [
        "x GATE-FAILURE v1 source=gate check=a",
        "GATE-FAILURE v1 source=gate check=a extra",
        "GATE-FAILURE v2 source=gate check=a",
        "GATE-FAILURE v1 source=hook check=a",
        "GATE-FAILURE v1 source=gate check=",
        "GATE-FAILURE v1 source=gate check=a b",
    ],
)
def test_partial_and_foreign_lines_are_not_markers(line: str) -> None:
    assert parse_markers(line) == []


def test_ansi_and_crlf_tolerated_and_lines_split_on_newline_only() -> None:
    text = "a\x0bb\r\n\x1b[31mGATE-FAILURE v1 source=gate check=x\x1b[0m\r\nlast"
    assert parse_markers(text) == [Marker("gate", "x", 1)]


_PRECOMMIT_BLOCKS = (
    "ruff....................................................................Failed\n"
    "- hook id: ruff\n"
    "- exit code: 1\n"
    "\n"
    "trailing-whitespace.....................................................Failed\n"
    "- hook id: trailing-whitespace\n"
    "- exit code: 1\n"
    "- files were modified by this hook\n"
    "\n"
    "fixer...................................................................Failed\n"
    "- hook id: fixer\n"
    "- files were modified by this hook\n"
    "\n"
    "verbose.................................................................Passed\n"
    "- hook id: verbose\n"
    "- duration: 0.01s\n"
    "- exit code: 0\n"
)


def test_precommit_block_rule_requires_nonzero_exit_or_modified_files() -> None:
    assert [m.signature for m in parse_markers(_PRECOMMIT_BLOCKS)] == ["ruff", "trailing-whitespace", "fixer"]
    assert {m.source for m in parse_markers(_PRECOMMIT_BLOCKS)} == {"precommit"}


def test_coloured_precommit_block_parses() -> None:
    text = "\x1b[1m- hook id: ruff\x1b[0m\r\n\x1b[2m- exit code: 1\x1b[0m\r\n"
    assert parse_markers(text) == [Marker("precommit", "ruff", 0)]


def test_precommit_hook_id_must_fit_the_signature_class() -> None:
    assert parse_markers("- hook id: bad id\n- exit code: 1\n") == []


def test_signature_pattern_equals_contract_class() -> None:
    contract = _contract_process_event_pattern()
    assert contract == f"^(hook|gate|precommit|status):{markers.SIGNATURE_PATTERN}$"
    assert re.fullmatch(markers.SIGNATURE_PATTERN, "validate_scope_boundary")


def test_status_marker_grammar_round_trips() -> None:
    plain = format_status_marker("tool_a", "skipped")
    reasoned = format_status_marker("tool_a", "failed", "no_creds")
    assert parse_markers(f"noise\n{plain}\n{reasoned}\n") == [
        Marker("status", "tool_a.skipped", 1, "skipped"),
        Marker("status", "tool_a.failed", 2, "failed"),
    ]
    assert parse_markers(format_status_marker("tool_a", "done")) == [Marker("status", "tool_a.done", 0, "done")]
    assert reasoned.endswith(" reason=no_creds")


def test_status_marker_refuses_bad_tokens() -> None:
    for source in ("", "-lead", "a b", "a:b", "a" * (STATUS_SOURCE_MAX + 1)):
        with pytest.raises(ValueError):
            format_status_marker(source, "done")
    with pytest.raises(ValueError):
        format_status_marker("a" * STATUS_SOURCE_MAX + "x", "done")
    for result in ("", "ok", "SKIPPED"):
        with pytest.raises(ValueError):
            format_status_marker("tool_a", result)
    with pytest.raises(ValueError):
        format_status_marker("tool_a", "failed", "bad reason")
    assert format_status_marker("a" * STATUS_SOURCE_MAX, "done")


def test_overlong_status_source_is_not_a_marker() -> None:
    long = "a" * (STATUS_SOURCE_MAX + 1)
    line = f"STATUS v1 source={long} result=failed"
    assert parse_markers(line) == [] and count_overlong_status_lines(f"x\n{line}\n") == 1
    assert count_overlong_status_lines(format_status_marker("a" * STATUS_SOURCE_MAX, "failed")) == 0
    assert count_overlong_status_lines(f" {line}") == 0


def test_status_lines_parse_only_as_whole_lines() -> None:
    line = format_status_marker("tool_a", "failed")
    for bad in (
        f" {line}",
        f'"{line}"',
        f"> {line}",
        f"{line} extra",
        f"x {line}",
        line.replace(" v1 ", " v2 "),
        line.replace("result=failed", "result=bogus"),
        line + " reason=",
        line + " reason=a b",
    ):
        assert parse_markers(bad) == [], bad
    assert parse_markers(f"\x1b[31m{line}\x1b[0m\r\n") == [Marker("status", "tool_a.failed", 0, "failed")]
