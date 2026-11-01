"""gate_events: gate, precommit and status process_events from structured markers only (rules G-1..G-6, ST-2..ST-4)."""

from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter
from pathlib import Path

import yaml

from src.turn_capture.gate_events import GATE_COMMAND_TOKENS, GATE_TAIL_LINES
from src.turn_capture.markers import STATUS_SOURCE_MAX, format_status_marker
from src.turn_capture.observations import build_observations
from src.turn_capture.streams import parse_tree
from src.turn_capture.transcript import MemTree
from src.turn_capture.transcripts import Sidecars
from tests.fixtures.turn_capture_corpus import (
    SID,
    assistant,
    build_secret,
    make_ctx,
    make_files,
    prompt,
    rows_of,
    tool_result,
    tool_use_block,
)

_CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "telemetry_observations.yaml"
GATE = "GATE-FAILURE v1 source=gate check={}"
BLOCK = "ruff.....Failed\n- hook id: ruff\n- exit code: 1\n\nsrc/x.py:1:1: F401\n"
VALIDATE = "bin/venv-python -m scripts.validate --pre"
CLI = "bin/venv-python -m scripts.some_tool"


def events(output, command=VALIDATE, name="Bash", tur=None, sidecars=None, cap=None, is_error=None):
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", name, {"command": command})]),
        tool_result("r1", 3, "tu1", output, pid="p1", tur=tur, is_error=is_error),
        prompt("u2", 5, "p2", "next"),
    ]
    diag: Counter[str] = Counter()
    tree = MemTree(make_files(root, sidecars=sidecars))
    ctx = make_ctx() if cap is None else dataclasses.replace(make_ctx(), full_output_cap_bytes=cap)
    rows = rows_of(build_observations(parse_tree(tree, None, True), ctx, diag, Sidecars(tree, None)))
    return [r for r in rows if r["external_ref"].find("gate_event") >= 0], diag


def test_gate_and_precommit_rows_have_the_pinned_shape() -> None:
    rows, _ = events("x\n" + GATE.format("alpha") + "\n" + BLOCK)
    assert [(r["name"], r["external_ref"]) for r in rows] == [
        ("gate:alpha", "r1#0/gate_event.1"),
        ("precommit:ruff", "r1#0/gate_event.2"),
    ]
    first = rows[0]
    assert first["entity_ref"] == first["external_ref"] and first["parent_observation_ref"] == "tu1"
    assert (first["observation_type"], first["event_kind"], first["severity"], first["source_ordinal"]) == (
        "process_event",
        "point",
        "error",
        2,
    )
    assert json.loads(first["metadata"]) == {"marker_version": 1, "tool_use_id": "tu1"}
    assert first["session_ref"] == SID


def test_one_row_per_distinct_source_and_signature_in_first_seen_order() -> None:
    text = "\n".join([GATE.format("b"), GATE.format("a"), GATE.format("b"), BLOCK, BLOCK])
    rows, _ = events(text)
    assert [r["name"] for r in rows] == ["gate:b", "gate:a", "precommit:ruff"]
    assert [r["external_ref"] for r in rows] == [f"r1#0/gate_event.{n}" for n in (1, 2, 3)]


def test_gate_markers_count_only_in_the_last_200_lines_but_precommit_blocks_anywhere() -> None:
    filler = "\n".join(f"line {i}" for i in range(GATE_TAIL_LINES))
    far = GATE.format("far") + "\n" + BLOCK + filler + "\n"
    rows, _ = events(far)
    assert [r["name"] for r in rows] == ["precommit:ruff"]
    edge = "\n".join(f"line {i}" for i in range(GATE_TAIL_LINES - 1)) + "\n"
    assert [r["name"] for r in events(GATE.format("edge") + "\n" + edge)[0]] == ["gate:edge"]
    assert events(GATE.format("edge") + "\n" + filler + "\n")[0] == []


def test_only_gate_bash_commands_with_a_matched_result_are_scanned() -> None:
    text = GATE.format("alpha")
    for token in GATE_COMMAND_TOKENS:
        assert len(events(text, command=f"run {token} now")[0]) == 1, token
    assert events(text, command="ls -la")[0] == []
    assert events(text, name="Read")[0] == []
    no_result = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": VALIDATE})]),
        prompt("u2", 5, "p2", "next"),
    ]
    tree = MemTree(make_files(no_result))
    rows = build_observations(parse_tree(tree, None, True), make_ctx(), Counter(), Sidecars(tree, None))
    assert not [r for r in rows if "gate_event" in r["external_ref"]]
    assert events("GATE-FAILURE v1 source=gate check=")[0] == [] and events("free text: gate failed")[0] == []


def test_persisted_sidecar_text_is_scanned_instead_of_the_preview() -> None:
    body = "head\n" + GATE.format("from_sidecar") + "\n"
    tur = {"persistedOutputPath": "/e/tool-results/g.txt", "persistedOutputSize": len(body)}
    rows, diag = events(GATE.format("from_preview"), tur=tur, sidecars={"g.txt": body})
    assert [r["name"] for r in rows] == ["gate:from_sidecar"] and diag["gate_scan_unavailable"] == 0


def test_gate_scan_unavailable_counts_unreadable_and_oversize_sidecars() -> None:
    tur = {"persistedOutputPath": "/e/tool-results/g.txt", "persistedOutputSize": 40}
    rows, diag = events(GATE.format("preview"), tur=tur, sidecars=None)
    assert rows == [] and diag["gate_scan_unavailable"] == 1
    body = GATE.format("big") + "\n" + "z" * 100
    rows, diag = events("p", tur=tur, sidecars={"g.txt": body}, cap=len(GATE.format("big")))
    assert rows == [] and diag["gate_scan_unavailable"] == 1


def test_scrubbed_text_never_mints_a_name_from_a_credential() -> None:
    secret = build_secret("anthropic")
    rows, _ = events(GATE.format(secret))
    assert rows == []
    assert [r["name"] for r in events(GATE.format("ok") + "\n" + f"token {secret}")[0]] == ["gate:ok"]


def test_status_rows_have_the_pinned_shape() -> None:
    text = "\n".join(
        [
            format_status_marker("tool_a", "done"),
            format_status_marker("tool_b", "skipped", "no_creds"),
            format_status_marker("tool_c", "failed"),
        ]
    )
    rows, _ = events(text, command=CLI)
    assert [(r["name"], r["severity"], r["external_ref"]) for r in rows] == [
        ("status:tool_b.skipped", "warning", "r1#0/gate_event.1"),
        ("status:tool_c.failed", "error", "r1#0/gate_event.2"),
    ]
    for row in rows:
        assert (row["observation_type"], row["event_kind"], row["parent_observation_ref"]) == ("process_event", "point", "tu1")
        assert json.loads(row["metadata"]) == {"marker_version": 1, "tool_use_id": "tu1"}
        assert "no_creds" not in str(row)


def test_skipped_and_failed_lines_of_one_source_give_two_rows() -> None:
    text = format_status_marker("tool_a", "skipped") + "\n" + format_status_marker("tool_a", "failed") + "\n"
    text += format_status_marker("tool_a", "skipped")
    rows, _ = events(text, command=CLI)
    assert [r["name"] for r in rows] == ["status:tool_a.skipped", "status:tool_a.failed"]


def test_status_markers_count_only_from_repo_cli_commands() -> None:
    line = format_status_marker("tool_a", "failed")
    assert events(line, command="echo replay")[0] == [] and events(line, command="cat notes.txt")[0] == []
    for command in ("bin/venv-python -m scripts.x", "bin/venv-python scripts/x.py", "run -m scripts.x now"):
        assert [r["name"] for r in events(line, command=command)[0]] == ["status:tool_a.failed"], command
    assert events(line, command=CLI, name="Read")[0] == []


def test_status_markers_count_only_in_the_last_200_lines() -> None:
    line = format_status_marker("tool_a", "failed")
    filler = "\n".join(f"line {i}" for i in range(GATE_TAIL_LINES))
    assert events(line + "\n" + filler + "\n", command=CLI)[0] == []
    edge = "\n".join(f"line {i}" for i in range(GATE_TAIL_LINES - 1)) + "\n"
    assert [r["name"] for r in events(line + "\n" + edge, command=CLI)[0]] == ["status:tool_a.failed"]


def test_gate_scanning_is_unchanged_for_status_commands() -> None:
    gate = GATE.format("alpha")
    assert events(gate, command=CLI)[0] == [] and events(BLOCK, command=CLI)[0] == []
    mixed = gate + "\n" + format_status_marker("tool_a", "failed")
    assert [r["name"] for r in events(mixed, command=VALIDATE)[0]] == ["gate:alpha", "status:tool_a.failed"]
    assert [r["name"] for r in events(mixed, command="git commit -m x")[0]] == ["gate:alpha"]


def test_overlong_status_source_is_dropped_and_counted() -> None:
    long = "STATUS v1 source=" + "a" * (STATUS_SOURCE_MAX + 1) + " result=failed"
    rows, diag = events(long, command=CLI)
    assert rows == [] and diag["status_source_too_long"] == 1
    assert events(long, command="echo x")[1]["status_source_too_long"] == 0
    filler = "\n".join(f"line {i}" for i in range(GATE_TAIL_LINES))
    assert events(long + "\n" + filler + "\n", command=CLI)[1]["status_source_too_long"] == 0
    exact = format_status_marker("a" * STATUS_SOURCE_MAX, "skipped")
    rows, diag = events(exact, command=CLI)
    assert len(rows) == 1 and diag["status_source_too_long"] == 0
    contract = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    pattern = contract["fields"]["name"]["dq_intent"]["pattern_when"][0]["pattern"]
    assert re.fullmatch(pattern, rows[0]["name"])
