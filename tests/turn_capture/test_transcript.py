"""Source rules: line rules, the pin and deferral, sidecar resolution, sub-agent file lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import (
    ROOT,
    SIDECAR_CHUNK_BYTES,
    FsTree,
    MemTree,
    inherited_lines,
    project_ref_pin,
    read_lines,
    record_time,
    session_pin,
)
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    SID,
    assistant,
    child,
    hook,
    jsonl,
    make_files,
    prompt,
    queue_op,
    text_block,
    tool_result,
    tool_use_block,
    ts,
)
from tests.fixtures.turn_capture_v3_cases import (
    fork_branch_copied_head_files,
    fork_session_copied_head_files,
    fork_session_nested_files,
)

PIN = "bash .claude/hooks/session_start_log_turn.sh --project-ref={}"


def test_pin_is_earliest_parseable_timestamp() -> None:
    body = jsonl([{"type": "last-prompt"}, {"type": "x", "timestamp": "not a time"}, queue_op(5), prompt("u1", 1, "p1", "hi")])
    pin = session_pin(read_lines(body.encode()))
    assert pin is not None and pin.isoformat().startswith("2026-01-01T00:00:01")
    hooks_after = jsonl([queue_op(5), prompt("h1", 1.5, "p1", "hook"), prompt("h2", 2, "p1", "hook")])
    pin = session_pin(read_lines(hooks_after.encode()))
    assert pin is not None and pin.isoformat().startswith("2026-01-01T00:00:01.500")
    assert session_pin(read_lines(b'[1]\n{"timestamp": "2026-01-01T00:00:09Z"}\n')) is not None


def test_sidecar_size_and_chunks(tmp_path: Path) -> None:
    body = "a" * (SIDECAR_CHUNK_BYTES * 2 + 5)
    files = make_files([prompt("u1", 1, "p1", "hi")], sidecars={"big.txt": body, "empty.txt": ""})
    mem = MemTree(files)
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    fs = FsTree(tmp_path / f"{SID}.jsonl")
    for tree in (mem, fs):
        assert tree.sidecar_size("big.txt") == len(body) and tree.sidecar_size("empty.txt") == 0
        assert tree.sidecar_size("nope.txt") is None and list(tree.sidecar_chunks("nope.txt")) == []
        chunks = list(tree.sidecar_chunks("big.txt"))
        assert [len(c) for c in chunks] == [SIDECAR_CHUNK_BYTES, SIDECAR_CHUNK_BYTES, 5]
        assert b"".join(chunks) == body.encode() and list(tree.sidecar_chunks("empty.txt")) == []


def test_no_timestamp_defers() -> None:
    files = make_files([{"type": "last-prompt", "sessionId": SID}, {"type": "mode"}])
    result = record_turn(MemTree(files), None, project_ref=PROJECT_REF)
    assert result.next_cursor is None
    assert result.batches[-1][1] == [] and all(rows == [] for _, rows in result.batches)
    assert session_pin(read_lines(b"")) is None


def test_trailing_partial_line_not_consumed() -> None:
    data = jsonl([prompt("u1", 1, "p1", "hi")]).encode() + b'{"type": "user", "uu'
    lines = read_lines(data)
    assert [item.line for item in lines] == [0]
    assert (
        read_lines(b'{"a": 1}\n{"b": 2}\n', limit=1)[0].data == {"a": 1}
        and len(read_lines(b'{"a": 1}\n{"b": 2}\n', limit=1)) == 1
    )


def test_malformed_and_non_object_lines_are_consumed_but_never_records() -> None:
    lines = read_lines(b'garbage\n[1, 2]\n{"ok": true}\n\xff\xfe\n')
    assert [item.data for item in lines] == [None, None, {"ok": True}, None]


def test_record_time_rules() -> None:
    assert record_time({"timestamp": ts(1)}) is not None
    assert record_time({"timestamp": 12}) is None
    assert record_time({"timestamp": "yesterday"}) is None
    assert record_time({}) is None


def test_sidecar_resolved_by_basename_under_session_dir(tmp_path: Path) -> None:
    files = make_files([prompt("u1", 1, "p1", "hi")], sidecars={"out.txt": "FULL"})
    mem = MemTree(files)
    assert mem.sidecar("out.txt") == b"FULL" and mem.sidecar("nope.txt") is None
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    fs = FsTree(tmp_path / f"{SID}.jsonl")
    assert fs.session_id == SID
    assert fs.sidecar("out.txt") == b"FULL" and fs.sidecar("nope.txt") is None
    assert fs.sidecar("../out.txt") is None


def test_subagent_file_lookup_by_agent_id(tmp_path: Path) -> None:
    files = make_files(
        [prompt("u1", 1, "p1", "hi")],
        {"b2": [child(assistant("c1", 2, "m", [text_block("x")]), "b2")], "a1": [child(prompt("c0", 2, "p1", "t"), "a1")]},
    )
    mem = MemTree(files)
    assert mem.agent_ids() == ["a1", "b2"]
    assert mem.read("a1") is not None and mem.read("zz") is None and mem.read(ROOT) is not None
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    fs = FsTree(tmp_path / f"{SID}.jsonl")
    assert fs.agent_ids() == ["a1", "b2"]
    assert fs.read("a1") == mem.read("a1") and fs.read("zz") is None and fs.read(ROOT) == mem.read(ROOT)
    assert FsTree(tmp_path / "missing.jsonl").read(ROOT) is None


def test_memtree_needs_exactly_one_root_file() -> None:
    with pytest.raises(ValueError):
        MemTree({"a.jsonl": "", "b.jsonl": ""})
    with pytest.raises(ValueError):
        MemTree({"x/y.txt": ""})


def test_tool_use_result_helpers_are_reachable_from_builders() -> None:
    assert tool_result("r", 1, "t", "c")["message"]["content"][0]["tool_use_id"] == "t"
    assert tool_use_block("t", "Bash", {})["name"] == "Bash"


def _lines(files, upto=None):
    return read_lines(files[f"{SID}.jsonl"].encode(), upto)


def test_inherited_lines_and_pin_over_a_branch_fork() -> None:
    lines = _lines(fork_branch_copied_head_files())
    assert inherited_lines(lines) == {0, 1, 2, 3}
    pin = session_pin(lines)
    assert pin is not None and pin.isoformat().startswith("2026-01-01T00:00:19")


def test_inherited_lines_are_a_function_of_the_prefix_for_a_session_fork() -> None:
    files = fork_session_copied_head_files()
    assert inherited_lines(_lines(files, 4)) == set() and inherited_lines(_lines(files, 5)) == {0, 1, 2, 3}
    early = session_pin(_lines(files, 4))
    late = session_pin(_lines(files))
    assert early is not None and late is not None and early.isoformat().startswith("2026-01-01T00:00:01")
    assert late.isoformat().startswith("2026-01-01T00:00:19")


def test_the_last_fork_group_is_the_boundary_of_a_nested_fork() -> None:
    files = fork_session_nested_files()
    assert inherited_lines(_lines(files, 7)) == {0, 1, 2, 3}
    assert inherited_lines(_lines(files)) == set(range(7))
    assert inherited_lines(_lines(files, 3)) == set()


def test_a_fork_group_ends_at_the_next_prompt_or_reply() -> None:
    body = jsonl(
        [
            hook("f1", 1, "SessionStart:fork"),
            hook("f2", 2, "SessionStart:fork"),
            prompt("u1", 3, "p1", "x"),
            hook("f3", 4, "SessionStart:fork"),
        ]
    )
    assert inherited_lines(read_lines(body.encode())) == {0, 1, 2}


def _pin_tree(*commands, root_extra=()):
    records = [hook(f"h{i}", 1 + i, "SessionStart:startup", command=c) for i, c in enumerate(commands)]
    return MemTree(make_files([*records, *root_extra, prompt("u1", 20, "p1", "hi")]))


def test_project_ref_pin_reads_the_earliest_capture_hook_token() -> None:
    assert project_ref_pin(_pin_tree(PIN.format("acme/app"))) == "acme/app"
    assert (
        project_ref_pin(_pin_tree('bash "/w/.claude/hooks/session_start_log_turn.sh" "--project-ref=a.b:c/d-e"'))
        == "a.b:c/d-e"
    )
    assert project_ref_pin(_pin_tree(PIN.format("first/one"), PIN.format("second/two"))) == "first/one"
    assert project_ref_pin(_pin_tree("echo hi", PIN.format("later/one"))) == "later/one"


def test_project_ref_pin_quoted_flag_value() -> None:
    assert project_ref_pin(_pin_tree('bash session_start_log_turn.sh --project-ref="a/b"')) == "a/b"
    assert project_ref_pin(_pin_tree("bash session_start_log_turn.sh --project-ref='a/b'")) == "a/b"
    assert project_ref_pin(_pin_tree("bash session_start_log_turn.sh '--project-ref=a/b'")) == "a/b"
    assert project_ref_pin(_pin_tree('bash session_start_log_turn.sh --project-ref=""')) is None
    assert project_ref_pin(_pin_tree("bash session_start_log_turn.sh --project-ref=''a/b")) is None


def test_project_ref_pin_requires_exactly_one_valid_token_and_the_pinned_script() -> None:
    assert project_ref_pin(_pin_tree("bash session_start_log_turn.sh")) is None
    assert project_ref_pin(_pin_tree(PIN.format("a/b") + " --project-ref=c/d")) is None
    assert project_ref_pin(_pin_tree(PIN.format("-bad"))) is None
    assert project_ref_pin(_pin_tree(PIN.format("a!b/c"))) is None
    assert project_ref_pin(_pin_tree(PIN.format("x" * 201))) is None and project_ref_pin(_pin_tree(PIN.format("x" * 200)))
    assert project_ref_pin(_pin_tree("bash other_hook.sh --project-ref=a/b")) is None
    assert project_ref_pin(_pin_tree("bash a.sh " + PIN.format("a/b"))) is None
    assert project_ref_pin(_pin_tree(None)) is None
    assert project_ref_pin(MemTree(make_files([prompt("u1", 1, "p1", "hi")]))) is None


def test_project_ref_pin_ignores_non_startup_non_hook_and_inherited_records() -> None:
    stop = hook("s1", 1, "Stop", command=PIN.format("a/b"))
    assert project_ref_pin(MemTree(make_files([stop, prompt("u1", 5, "p1", "hi")]))) is None
    plain = {
        "type": "attachment",
        "attachment": {"type": "environment", "command": PIN.format("a/b")},
        "uuid": "e",
        "timestamp": ts(1),
    }
    assert project_ref_pin(MemTree(make_files([plain, prompt("u1", 5, "p1", "hi")]))) is None
    copied = hook("c1", 1, "SessionStart:startup", command=PIN.format("copied/ref"))
    own = hook("o1", 3, "SessionStart:startup", command=PIN.format("own/ref"))
    forked = [{**copied, "forkedFrom": {"sessionId": "x", "messageUuid": "c1"}}, prompt("u1", 2, "p1", "hi"), own]
    assert project_ref_pin(MemTree(make_files(forked))) == "own/ref"
    assert project_ref_pin(MemTree({"x/y.jsonl": ""} | {"s.jsonl": ""})) is None
