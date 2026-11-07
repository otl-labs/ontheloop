"""rec-4939: capture identity under Claude Code's hook labels (rules PW-1, P-2, PV-1, WT-2, H-1, DG-1)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from src.turn_capture.observations import hook_name
from src.turn_capture.provenance import open_provenance
from src.turn_capture.record_turn import record_turn
from src.turn_capture.streams import ROOT
from src.turn_capture.transcript import MemTree, project_ref_pin
from src.turn_capture.witness import is_witness_record
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    SID,
    WITNESS_STDERR,
    assistant,
    hook,
    load_case,
    make_files,
    normalise,
    parse_records,
    prompt,
    text_block,
)
from tests.fixtures.turn_capture_v9_cases import FACTS_LINE, REF, REF_LINE, labelled_pin, labelled_witness


def _tree(*records: dict[str, Any]) -> MemTree:
    return MemTree(make_files([*records, prompt("u1", 20, "p1", "hi")]))


def _pin(
    command: str | None, stderr: str, name: str = "SessionStart:startup", uuid: str = "h0", t: float = 1
) -> dict[str, Any]:
    return hook(uuid, t, name, command=command, stderr=stderr)


def test_labelled_pin_attachment_pins_from_witness() -> None:
    assert project_ref_pin(_tree(_pin("command hook 2", FACTS_LINE + REF_LINE))) == REF


def test_labelled_stop_attachment_is_a_witness() -> None:
    assert is_witness_record(labelled_witness("w", 1))


def test_label_grammar_is_never_parsed() -> None:
    for label in ("command hook", "command hook 1", "command hook 11", "http hook 2"):
        assert project_ref_pin(_tree(_pin(label, FACTS_LINE + REF_LINE))) == REF, label
        assert project_ref_pin(_tree(_pin(label, FACTS_LINE))) is None, label


def test_witness_line_ref_wins_over_the_command_form_and_is_counted() -> None:
    command = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=other'
    att = _pin(command, FACTS_LINE + REF_LINE)
    assert project_ref_pin(_tree(att)) == REF
    stream = parse_records(
        [att, prompt("u1", 2, "p1", "go"), assistant("a1", 3, "m1", [text_block("x")])], None, True
    ).streams[ROOT]
    diag: Counter[str] = Counter()
    open_provenance(stream, True, diag)
    assert diag["pin_ref_disagreement"] == 1
    sub: Counter[str] = Counter()
    open_provenance(stream, False, sub)
    assert not sub


def test_malformed_or_repeated_ref_lines_fall_back_to_the_command_form() -> None:
    bad = "[session_start_log_turn] capture pin project_ref=has space\n"
    twice = REF_LINE + "[session_start_log_turn] capture pin project_ref=again\n"
    assert project_ref_pin(_tree(_pin("command hook", FACTS_LINE + bad))) is None
    assert project_ref_pin(_tree(_pin("command hook", FACTS_LINE + twice))) is None
    assert project_ref_pin(_tree(_pin("command hook", FACTS_LINE + bad + REF_LINE))) == REF
    command = 'bash "/w/.claude/hooks/session_start_log_turn.sh" --project-ref=legacy'
    assert project_ref_pin(_tree(_pin(command, FACTS_LINE + twice))) == "legacy"


def test_labelled_pin_without_a_ref_line_does_not_pin() -> None:
    assert project_ref_pin(_tree(_pin("command hook 9", FACTS_LINE))) is None


def test_inherited_labelled_pin_is_skipped() -> None:
    copied = {**_pin("command hook 2", FACTS_LINE + REF_LINE), "forkedFrom": {"sessionId": "x"}}
    assert project_ref_pin(_tree(copied)) is None


def test_gated_plugin_copy_never_pins_or_supplies_facts() -> None:
    records = [
        labelled_pin("g1", 0.1, "command hook 1", ""),
        labelled_pin("g2", 0.2, "command hook 2", FACTS_LINE + REF_LINE),
        labelled_pin("g3", 0.3, "command hook 1", ""),
        prompt("u1", 1, "p1", "go"),
    ]
    assert project_ref_pin(_tree(*records[:1])) is None
    assert project_ref_pin(_tree(*records)) == REF
    gated = parse_records([records[0], records[3]], None, True).streams[ROOT]
    diag: Counter[str] = Counter()
    assert open_provenance(gated, True, diag) == (None, None, None)


def test_labelled_pin_supplies_checkout_facts() -> None:
    stream = parse_records(
        [labelled_pin("g2", 0.2, "command hook 2", FACTS_LINE + REF_LINE), prompt("u1", 1, "p1", "go")], None, True
    )
    facts = open_provenance(stream.streams[ROOT], True, Counter())
    assert facts == ("1" * 40, "2" * 40, False)


def test_capture_records_keep_their_names_under_labels() -> None:
    assert hook_name(_pin("command hook 2", FACTS_LINE + REF_LINE)["attachment"]) == "hook:session_start_log_turn"
    assert hook_name(labelled_witness("w", 1)["attachment"]) == "hook:stop_log_turn"
    prompt_hook = hook("p", 1, "UserPromptSubmit", command="command hook", stderr=WITNESS_STDERR)
    assert hook_name(prompt_hook["attachment"]) == "hook:stop_log_turn"


def test_other_labelled_hooks_fall_back_to_hook_name() -> None:
    assert (
        hook_name(hook("a", 1, "PreToolUse:Bash", command="command hook 3", stderr="")["attachment"]) == "hook:PreToolUse.Bash"
    )
    assert hook_name(hook("b", 1, "SessionEnd", command="command hook", stderr="")["attachment"]) == "hook:SessionEnd"
    assert hook_name(hook("c", 1, "Stop", command="command hook", stderr="[WARN] x\n")["attachment"]) == "hook:Stop"


def test_statusmessage_free_text_does_not_hide_the_witness() -> None:
    assert is_witness_record(labelled_witness("w", 1, "some free statusMessage text"))
    assert is_witness_record(hook("w", 1, "Stop", stderr=WITNESS_STDERR, stdout=""))


def test_command_shape_parses_as_before() -> None:
    command = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=ontheloop'
    assert project_ref_pin(_tree(_pin(command, FACTS_LINE))) == "ontheloop"


def _rows(name: str) -> list[dict[str, Any]]:
    files, _ = load_case(name)
    return normalise(record_turn(MemTree(files), None, project_ref=PROJECT_REF, session_final=True).batches)


def test_status_message_shape_matches_the_command_shape() -> None:
    from tests.fixtures.turn_capture_v9_cases import hook_status_message_shape_files  # noqa: PLC0415

    command = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=' + PROJECT_REF
    stop = '"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/stop_log_turn.py"'
    text = hook_status_message_shape_files()[f"{SID}.jsonl"]
    registered = text.replace("session_start_log_turn.sh --project-ref=" + PROJECT_REF, command.replace('"', '\\"'))
    registered = registered.replace('"stop_log_turn.py"', json.dumps(stop))
    assert registered != text
    other = normalise(
        record_turn(MemTree({f"{SID}.jsonl": registered}), None, project_ref=PROJECT_REF, session_final=True).batches
    )
    shape = _rows("hook_status_message_shape.json")

    def strip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "metadata"} for r in rows]

    assert strip(shape) == strip(other)


def test_labelled_twins_still_collapse() -> None:
    names = [r["external_ref"] for r in _rows("hook_label_shape.json") if r.get("name", "").startswith("hook:SessionStart")]
    assert names == ["g1#0/process_event", "g3#0/process_event", "g4#0/process_event"]
