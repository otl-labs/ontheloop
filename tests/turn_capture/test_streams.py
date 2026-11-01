"""Classification, companion exclusion, stream membership, turn spans and closure, response grouping, completeness."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from src.turn_capture import streams
from src.turn_capture.streams import (
    API_ERROR_FLAG_KEYS,
    HARNESS_DENIAL_PREFIXES,
    HARNESS_INTERRUPT_SENTINELS,
    ROOT,
    TOOL_DENIAL_OUTCOMES,
    classify,
    emitting_streams,
    is_api_error,
    is_background_completion,
    parse_tree,
)
from src.turn_capture.transcript import FsTree, MemTree
from tests.fixtures.turn_capture_corpus import (
    SID,
    assistant,
    attachment,
    child,
    hook,
    make_files,
    prompt,
    stop_summary,
    task_notice,
    text_block,
    tool_result,
    tool_use_block,
    ts,
    witness,
)
from tests.fixtures.turn_capture_v3_cases import (
    fork_branch_copied_head_files,
    fork_session_copied_head_files,
    fork_session_nested_files,
)


def parsed(root, children=None, final=False):
    return parse_tree(MemTree(make_files(root, children)), None, final)


def test_companion_records_never_open_a_turn() -> None:
    root = [
        prompt("c0", 0.5, "p0", "injected", sourceToolUseID="tuX"),
        {**prompt("c1", 0.6, "p0", [text_block("x")]), "turnCompanion": True},
        prompt("u1", 1, "p1", "real"),
        prompt("c2", 1.5, "p9", "companion after", sourceToolUseID="tuY"),
        assistant("a1", 2, "m1", [text_block("ok")]),
    ]
    tree = parsed(root, final=True)
    turns = tree.streams[ROOT].turns
    assert [t.prompt_id for t in turns] == ["p1"]
    assert [r.uuid for r in turns[0].companions] == ["c2"]


def test_classification_branches() -> None:
    diag: Counter[str] = Counter()
    ok = {"uuid": "u", "timestamp": "2026-01-01T00:00:00.000Z"}
    cases = {
        "other": [{"type": "mode"}, {"type": "weird", "uuid": "u"}, {"type": "user"}, {"type": "user", "uuid": "u"}],
        "prompt": [{**ok, "type": "user", "promptId": "p", "message": {"content": "x"}}],
        "companion": [{**ok, "type": "user", "promptId": "p", "sourceToolUseID": "s", "message": {"content": "x"}}],
        "tool_result": [{**ok, "type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t"}]}}],
        "assistant": [{**ok, "type": "assistant", "message": {"id": "m", "content": []}}],
        "hook": [{**ok, "type": "attachment", "attachment": {"type": "hook_success"}}],
        "attach": [
            {**ok, "type": "attachment", "attachment": {"type": "date"}},
            {**ok, "type": "attachment", "attachment": {"type": "new_kind"}},
        ],
    }
    for kind, records in cases.items():
        assert {classify(r, diag) for r in records} == {kind}, kind
    assert classify({**ok, "type": "user", "message": {"content": 5}, "promptId": "p"}, diag) == "other"
    assert classify({**ok, "type": "user", "message": {"content": "x"}}, diag) == "other"
    assert classify({**ok, "type": "assistant", "message": {"id": "", "content": []}}, diag) == "other"
    assert classify({**ok, "type": "attachment"}, diag) == "attach"
    assert diag["records_without_uuid"] == 1 and diag["records_without_timestamp"] == 1
    assert diag["unknown_record_type:weird"] == 1 and diag["unknown_attachment_type:new_kind"] == 1
    assert diag["unclassified_user_records"] == 2 and diag["unclassified_assistant_records"] == 1


def test_turn_spans_boundaries_and_closure() -> None:
    root = [
        prompt("u1", 1, "p1", "a"),
        prompt("u1b", 1.5, "p1", "same turn again"),
        assistant("a1", 2, "m1", [text_block("x")]),
        prompt("u2", 3, "p2", "b"),
        assistant("a2", 4, "m2", [text_block("y")]),
    ]
    open_tree = parsed(root)
    turns = open_tree.streams[ROOT].turns
    assert [(t.prompt_id, t.closed, len(t.prompts)) for t in turns] == [("p1", True, 2), ("p2", False, 1)]
    assert turns[0].last_assistant.uuid == "a1" and turns[0].ordinal == 1 and turns[1].ordinal == 2
    assert parsed(root, final=True).streams[ROOT].turns[1].closed is True


def test_resume_boundary_ends_a_turn_and_reused_prompt_id_is_a_companion() -> None:
    root = [
        hook("rs0", 0.5, "SessionStart:resume"),
        prompt("u1", 1, "p1", "a"),
        assistant("a1", 2, "m1", [text_block("x")]),
        hook("rs1", 3, "SessionStart:resume"),
        hook("rs2", 3.1, "SessionStart:resume"),
        assistant("a-pre", 3.5, "mpre", [text_block("pre-turn")]),
        prompt("u2", 4, "p1", "reuses the old id"),
        prompt("u3", 5, "p2", "new"),
        prompt("u4", 6, "p1", "old id again inside p2"),
    ]
    tree = parsed(root)
    stream = tree.streams[ROOT]
    assert [r.uuid for r in stream.resumes] == ["rs0", "rs1"]
    assert [(t.prompt_id, t.closed) for t in stream.turns] == [("p1", True), ("p2", False)]
    assert tree.diag["reused_prompt_ids"] == 2 and tree.diag["pre_turn_assistant_records"] == 1
    assert [r.uuid for r in stream.turns[1].companions] == ["u4"]


def test_compact_group_is_one_record() -> None:
    root = [
        prompt("u1", 1, "p1", "a"),
        assistant("a1", 2, "m1", [text_block("x")]),
        hook("cp1", 3, "PreCompact"),
        hook("cp2", 3.1, "SessionStart:compact"),
        hook("cp3", 3.2, "PostCompact"),
        prompt("u2", 4, "p2", "b"),
        assistant("a2", 5, "m2", [text_block("y")]),
        hook("cp4", 6, "PreCompact"),
        hook("plain", 6.1, "PostToolUse:Bash"),
    ]
    stream = parsed(root).streams[ROOT]
    assert [r.uuid for r in stream.compacts] == ["cp1", "cp4"] and len(stream.hooks) == 5


def test_tools_results_and_responses() -> None:
    root = [
        assistant("pre", 0.5, "mpre", [tool_use_block("tuPre", "Bash", {})]),
        tool_result("rpre", 0.6, "tuPre", "outside"),
        prompt("u1", 1, "p1", "a"),
        assistant(
            "a1", 2, "m1", [tool_use_block("tu1", "Bash", {}), {"type": "text"}, "junk", tool_use_block("tu1", "Bash", {})]
        ),
        tool_result("r1", 3, "tu1", "one", pid="p1"),
        tool_result("r1b", 3.1, "tu1", "dup", pid="p1"),
        {
            **tool_result("rx", 3.2, "tuX", "orphan"),
            "message": {"content": [{"type": "tool_result", "tool_use_id": "tuX"}, {"type": "tool_result"}, 5]},
        },
        hook("h1", 3.3, "PreToolUse:Bash", tool_use_id="tu2", exit_code=2),
        hook("h2", 3.4, "PreToolUse:Bash", tool_use_id="tu2", exit_code=0),
        assistant("a2", 4, "m1", [tool_use_block("tu2", "Bash", {})]),
        prompt("u2", 5, "p2", "b"),
        assistant("a3", 6, "m1", [{"type": "text", "text": "same id, later turn"}]),
        tool_result("rl", 7, "tu2", "late", pid="p2"),
    ]
    tree = parsed(root, final=True)
    stream = tree.streams[ROOT]
    turn1, turn2 = stream.turns
    assert [u.tool_id for u in turn1.tool_uses] == ["tu1", "tu2"] and [u.ordinal for u in turn1.tool_uses] == [1, 2]
    assert turn1.tool_uses[0].result.rec.uuid == "r1" and turn1.tool_uses[1].result is None
    assert [r.tool_use_id for r in turn1.orphan_results] == ["tuX"]
    assert [(r.message_id, len(r.recs), r.owner.uuid) for r in turn1.responses] == [("m1", 2, "a2")]
    assert [(r.message_id, r.owner.uuid) for r in turn2.responses] == [("m1", "a3")]
    assert stream.result_by_id["tuPre"].disposition == "outside"
    assert stream.result_by_id["tu2"].disposition == "late"
    assert stream.pre_tool_blocks == {"tu2": [stream.recs[7].line]}
    for key in (
        "duplicate_tool_uses",
        "duplicate_tool_results",
        "results_outside_turn",
        "late_tool_results",
        "orphan_tool_results",
    ):
        assert tree.diag[key] >= 1, key


def _spawn_root(agent: str, *, bg: bool, notify: bool, name: str = "Agent") -> list[dict]:
    tur = {"agentId": agent, "isAsync": bg, "status": "async_launched" if bg else "completed"}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", name, {"subagent_type": "Explore"})]),
        tool_result("r1", 3, "tuA", "x", pid="p1", tur=tur),
    ]
    if notify:
        root.append(attachment("n0", 3.5, "queued_command", prompt="unrelated"))
        root.append(
            attachment(
                "n1", 4, "queued_command", prompt=f"<task-notification>\n<task-id>{agent}</task-id>\n</task-notification>"
            )
        )
    return root


def _kid(agent: str) -> list[dict]:
    return [child(prompt("c1", 2.5, "p1", "t"), agent), child(assistant("c2", 3.5, "mc", [text_block("w")]), agent)]


def test_subagent_completeness_sync_and_background() -> None:
    sync = parsed(_spawn_root("s1", bg=False, notify=False), {"s1": _kid("s1")})
    assert sync.spawns[0].complete and sync.spawns[0].close_result is not None
    assert sync.streams["s1"].session_ref == f"{SID}:s1" and sync.streams["s1"].turns[0].closed
    assert [s.key for s in emitting_streams(sync)] == [ROOT, "s1"]
    waiting = parsed(_spawn_root("b1", bg=True, notify=False), {"b1": _kid("b1")})
    assert not waiting.spawns[0].complete and waiting.spawns[0].close_rec is None
    assert waiting.streams["b1"].turns[0].closed is False
    assert [s.key for s in emitting_streams(waiting)] == [ROOT]
    finished = parsed(_spawn_root("b1", bg=True, notify=False), {"b1": _kid("b1")}, final=True)
    assert [s.key for s in emitting_streams(finished)] == [ROOT, "b1"] and finished.streams["b1"].turns[0].closed
    done = parsed(_spawn_root("b1", bg=True, notify=True), {"b1": _kid("b1")})
    assert done.spawns[0].complete and done.spawns[0].close_rec.uuid == "n1" and done.streams["b1"].turns[0].closed


def test_spawn_edge_cases() -> None:
    task = parsed(_spawn_root("t1", bg=False, notify=False, name="Task"), {"t1": _kid("t1")})
    assert len(task.spawns) == 1
    other_tool = parsed(_spawn_root("t1", bg=False, notify=False, name="Bash"), {"t1": _kid("t1")})
    assert other_tool.spawns == [] and other_tool.diag["orphan_subagents"] == 1
    missing = parsed(_spawn_root("gone", bg=False, notify=False))
    assert missing.diag["missing_child_files"] == 1 and missing.spawns == []
    root = _spawn_root("d1", bg=False, notify=False)
    root[1] = assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {}), tool_use_block("tuB", "Agent", {})])
    root.append(tool_result("r2", 3.5, "tuB", "x", pid="p1", tur={"agentId": "d1", "isAsync": False}))
    dup = parsed(root, {"d1": _kid("d1")})
    assert len(dup.spawns) == 1 and dup.diag["duplicate_spawns"] == 1
    no_id = _spawn_root("z", bg=False, notify=False)
    no_id[2] = tool_result("r1", 3, "tuA", "x", pid="p1", tur={"status": "completed"})
    assert parsed(no_id).spawns == []


def test_nested_spawns_are_found_in_the_child_stream() -> None:
    nested = _kid("n1") + [
        child(assistant("c3", 3.6, "mc2", [tool_use_block("tuB", "Task", {})]), "n1"),
        child(tool_result("c4", 3.7, "tuB", "x", pid="p1", tur={"agentId": "n2", "isAsync": False}), "n1"),
    ]
    grand = [child(prompt("g1", 3.65, "p1", "t"), "n2")]
    tree = parsed(_spawn_root("n1", bg=False, notify=False), {"n1": nested, "n2": grand})
    assert [s.agent_id for s in tree.spawns] == ["n1", "n2"] and tree.spawns[1].parent == "n1"
    assert tree.streams["n2"].session_ref == f"{SID}:n2"


def test_background_completion_predicate() -> None:
    def rec_of(record: dict) -> streams.Rec:
        tree = parsed([prompt("u0", 0.1, "p0", "x"), record])
        return tree.streams[ROOT].recs[-1]

    text = "<task-notification>\n<task-id>ag</task-id>\n</task-notification>"
    assert is_background_completion(rec_of(attachment("q", 1, "queued_command", prompt=text)), "ag")
    assert not is_background_completion(rec_of(attachment("q", 1, "queued_command", prompt=text)), "other")
    assert is_background_completion(rec_of(prompt("q", 1, "p0", text)), "ag")
    assert not is_background_completion(rec_of(prompt("q", 1, "p0", "hello")), "ag")
    assert not is_background_completion(rec_of(attachment("q", 1, "queued_command", prompt=5)), "ag")
    assert not is_background_completion(rec_of(attachment("q", 1, "date")), "ag")
    assert not is_background_completion(rec_of(assistant("q", 1, "m", [text_block("x")])), "ag")


def test_missing_files_and_malformed_lines(tmp_path: Path) -> None:
    assert parse_tree(FsTree(tmp_path / "nope.jsonl"), None, True).streams == {}
    files = make_files([prompt("u1", 1, "p1", "a")])
    files[f"{SID}.jsonl"] += "not json\n"
    assert parse_tree(MemTree(files), None, False).diag["malformed_lines"] == 1

    class Tree:
        session_id = SID

        def agent_ids(self):
            return ["ghost"]

        def read(self, key):
            return MemTree(_spawn_files).read(key)

        def sidecar(self, name):
            return None

    _spawn_files = make_files(_spawn_root("ghost", bg=False, notify=False))
    tree = parse_tree(Tree(), None, True)
    assert len(tree.spawns) == 1 and "ghost" not in tree.streams


def test_limits_truncate_each_file() -> None:
    tree = MemTree(make_files(_spawn_root("s1", bg=False, notify=False), {"s1": _kid("s1")}))
    cut = parse_tree(tree, {ROOT: 2}, False)
    assert len(cut.streams[ROOT].recs) == 2 and cut.spawns == [] and cut.lines_total == {ROOT: 2}
    assert parse_tree(tree, {}, False).streams[ROOT].recs == []


def test_spike_pinned_constants() -> None:
    assert HARNESS_INTERRUPT_SENTINELS == ("[Request interrupted by user]", "[Request interrupted by user for tool use]")
    assert HARNESS_DENIAL_PREFIXES == () and API_ERROR_FLAG_KEYS == ("isApiErrorMessage",)
    assert TOOL_DENIAL_OUTCOMES == {"permission-rule": "blocked", "user-rejected": "interrupted"}


def _parse_files(files, limits=None):
    return parse_tree(MemTree(files), limits, True)


def test_inherited_records_are_classified_and_left_out_of_the_stream() -> None:
    diag: Counter[str] = Counter()
    ok = {"uuid": "u", "timestamp": "2026-01-01T00:00:00.000Z", "type": "user", "promptId": "p", "message": {"content": "x"}}
    assert classify(ok, diag, inherited=True) == "inherited" and diag["inherited_records"] == 1
    assert classify(ok, diag) == "prompt"
    branch = _parse_files(fork_branch_copied_head_files())
    assert branch.diag["inherited_records"] == 4
    assert [r.uuid for r in branch.streams[ROOT].recs if r.data.get("uuid")] == ["sys1", "o1", "o2", "o3"]
    assert [t.prompt_id for t in branch.streams[ROOT].turns] == ["op1", "op2"]
    session = _parse_files(fork_session_copied_head_files())
    assert session.diag["inherited_records"] == 4
    assert [t.prompt_id for t in session.streams[ROOT].turns] == ["op1", "op2"]
    nested = _parse_files(fork_session_nested_files())
    assert nested.diag["inherited_records"] == 7 and [t.prompt_id for t in nested.streams[ROOT].turns] == ["op1", "op2"]


def test_inheritance_is_a_function_of_the_prefix() -> None:
    files = fork_session_copied_head_files()
    before = _parse_files(files, {ROOT: 4})
    assert before.diag["inherited_records"] == 0 and len(before.streams[ROOT].recs) == 4
    after = _parse_files(files, {ROOT: 5})
    assert after.diag["inherited_records"] == 4
    nested = fork_session_nested_files()
    assert _parse_files(nested, {ROOT: 7}).diag["inherited_records"] == 4
    assert _parse_files(nested, {ROOT: 8}).diag["inherited_records"] == 7


def test_inheritance_never_applies_to_a_subagent_file() -> None:
    kid = [
        child(prompt("c1", 2, "p1", "t"), "ag1"),
        child({**assistant("c2", 3, "mc", [text_block("w")]), "forkedFrom": {}}, "ag1"),
    ]
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 1.5, "m1", [tool_use_block("tuA", "Agent", {})]),
        tool_result("r1", 1.6, "tuA", "done", pid="p1", tur={"agentId": "ag1", "isAsync": False}),
    ]
    tree = parsed(root, {"ag1": kid}, final=True)
    assert tree.diag["inherited_records"] == 0 and len(tree.streams["ag1"].recs) == 2


def test_synthetic_and_api_error_assistant_records() -> None:
    diag: Counter[str] = Counter()
    ok = {
        "uuid": "u",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "type": "assistant",
        "message": {"id": "m", "content": [], "model": "<synthetic>"},
    }
    assert classify(ok, diag) == "other" and diag["synthetic_assistant_records"] == 1
    flagged = {**ok, "isApiErrorMessage": True}
    assert (
        classify(flagged, diag) == "assistant"
        and is_api_error(flagged)
        and not is_api_error({**ok, "isApiErrorMessage": False})
    )
    assert not is_api_error({**flagged, "type": "user"}) and diag["synthetic_assistant_records"] == 1
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "e1",
            2,
            "00000000-0000-4000-8000-0000000000e1",
            [text_block("API Error")],
            model="<synthetic>",
            isApiErrorMessage=True,
        ),
        prompt("u2", 3, "p2", "again"),
        assistant(
            "s1",
            4,
            "00000000-0000-4000-8000-0000000000e2",
            [text_block("No response requested.")],
            model="<synthetic>",
            isApiErrorMessage=False,
        ),
        assistant("a2", 5, "m2", [text_block("fine")]),
    ]
    tree = parsed(root, final=True)
    first, second = tree.streams[ROOT].turns
    assert first.last_assistant is not None and first.last_assistant.uuid == "e1" and first.responses == []
    assert [r.message_id for r in second.responses] == ["m2"] and tree.diag["synthetic_assistant_records"] == 1


def _background_root(agent: str, *tail: dict) -> list[dict]:
    return [*_spawn_root(agent, bg=True, notify=False), *tail]


def test_background_spawn_collects_every_notice_in_order() -> None:
    tail = (
        task_notice("n1", 4, "b1", "completed"),
        task_notice("n9", 4.5, "other", "failed"),
        task_notice("n2", 5, "b1", "failed"),
        task_notice("n3", 6, "b1", None),
        task_notice("n4", 7, "b1", raw_status="<status>bad value</status>\n"),
        task_notice("n5", 8, "b1", raw_status="<status>unclosed\n"),
    )
    tree = parsed(_background_root("b1", *tail), {"b1": _kid("b1")})
    spawn = tree.spawns[0]
    assert [n.uuid for n in spawn.notices] == ["n1", "n2", "n3", "n4", "n5"] and spawn.close_rec.uuid == "n1"
    assert [streams.notice_status(n) for n in spawn.notices] == ["completed", "failed", None, "malformed", "malformed"]
    assert spawn.complete and not spawn.completed_at_boundary
    nested = _kid("b1") + [
        child(assistant("c3", 3.6, "mc2", [tool_use_block("tuB", "Agent", {"run_in_background": True})]), "b1"),
        child(tool_result("c4", 3.7, "tuB", "x", pid="p1", tur={"agentId": "g1", "isAsync": True}), "b1"),
        child(task_notice("cn1", 3.8, "g1", "killed"), "b1"),
        child(task_notice("cn2", 3.9, "g1", "completed"), "b1"),
    ]
    deep = parsed(_spawn_root("b1", bg=False, notify=False), {"b1": nested, "g1": _kid("g1")})
    inner = next(sp for sp in deep.spawns if sp.agent_id == "g1")
    assert [n.uuid for n in inner.notices] == ["cn1", "cn2"]


def test_background_spawn_completes_at_a_later_resume_boundary() -> None:
    resume = hook("rs", 10, "SessionStart:resume")
    root = _background_root("b1", resume, prompt("u2", 11, "p2", "again"))
    tree = parsed(root, {"b1": _kid("b1")})
    spawn = tree.spawns[0]
    assert spawn.complete and spawn.completed_at_boundary and spawn.close_rec is None and spawn.notices == []
    assert tree.streams["b1"].turns[0].closed and tree.diag["background_completed_at_resume"] == 1
    late = parsed(_background_root("b1", resume, task_notice("n1", 12, "b1", "completed")), {"b1": _kid("b1")})
    assert late.spawns[0].completed_at_boundary and late.spawns[0].close_rec.uuid == "n1"
    assert late.diag["notice_after_boundary"] == 1
    before = parsed(_background_root("b1", task_notice("n1", 5, "b1"), resume), {"b1": _kid("b1")})
    assert not before.spawns[0].completed_at_boundary and before.diag["background_completed_at_resume"] == 0
    nested = _kid("b1") + [
        child(assistant("c3", 3.6, "mc2", [tool_use_block("tuB", "Agent", {"run_in_background": True})]), "b1"),
        child(tool_result("c4", 3.7, "tuB", "x", pid="p1", tur={"agentId": "g1", "isAsync": True}), "b1"),
        child(hook("crs", 3.8, "SessionStart:resume"), "b1"),
    ]
    deep = parsed(_spawn_root("b1", bg=False, notify=False), {"b1": nested, "g1": _kid("g1")})
    inner = next(sp for sp in deep.spawns if sp.agent_id == "g1")
    assert not inner.complete and not inner.completed_at_boundary
    assert tree.streams[ROOT].resume_ends == {tree.streams[ROOT].resumes[0].line: tree.streams[ROOT].recs[-1].line}


def framed(root, final=False, frontier=True):
    return parse_tree(MemTree(make_files(root)), None, final, frontier=frontier)


def test_the_frontier_withholds_an_open_turns_tail_only_when_asked() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("k")]),
        witness("w1", 3),
        stop_summary("s1", 3.1),
        hook("late", 3.2, "Notification", command="bash ./x.sh"),
    ]
    kept = framed(root)
    turn = kept.streams[ROOT].turns[0]
    assert [r.uuid for r in turn.closes] == ["w1"] and turn.closed and not turn.bounded
    assert [r.uuid for r in kept.streams[ROOT].recs][-1] == "w1" and kept.diag["frontier_withheld"] == 2
    assert [h.uuid for h in kept.streams[ROOT].hooks] == ["w1"]
    whole = framed(root, frontier=False)
    assert len(whole.streams[ROOT].recs) == 5 and "frontier_withheld" not in whole.diag
    assert framed(root, final=True).streams[ROOT].turns[0].bounded


def test_nothing_is_withheld_without_an_open_last_turn_or_a_tail() -> None:
    assert framed([hook("h0", 0.5, "SessionStart:startup")]).streams[ROOT].turns == []
    resumed = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("k")]),
        hook("rs", 3, "SessionStart:resume", command="bash ./r.sh"),
        hook("rs2", 3.5, "SessionStart:resume", command="bash ./r.sh"),
    ]
    bounded = framed(resumed)
    assert bounded.streams[ROOT].turns[0].bounded and "frontier_withheld" not in bounded.diag
    tail = framed([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness("w1", 3)])
    assert "frontier_withheld" not in tail.diag and tail.streams[ROOT].turns[0].closes


def test_witness_counters_survive_the_frontier_cut() -> None:
    pending = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "ls"})]),
        witness("w1", 3),
        tool_result("r1", 4, "t1", "out", pid="p1"),
    ]
    cut = framed([witness("w0", 0.5), *pending])
    assert cut.diag["witness_not_at_rest"] == 1 and cut.diag["witness_outside_turn"] == 1
    assert cut.diag["frontier_withheld"] == 3 and cut.streams[ROOT].turns[0].closes == []
    assert not cut.streams[ROOT].turns[0].closed


def _two_turns(*tail: dict) -> list[dict]:
    return [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("x")]),
        prompt("u2", 4, "p2", "again"),
        assistant("a2", 5, "m2", [text_block("y")]),
        *tail,
    ]


def _late_resume(uuid: str = "lr", t: float = 9) -> dict:
    return hook(uuid, t, "SessionStart:resume", durationMs=8000)


def test_late_startup_records_attach_to_no_turn() -> None:
    names = ("SessionStart:startup", "SessionStart:clear", "SessionStart:compact")
    root = _two_turns(*(hook(f"s{n}", 6 + n, name, toolUseID="ss") for n, name in enumerate(names)))
    stream = parsed(root).streams[ROOT]
    verdict = {r.uuid: r.turn for r in stream.hooks}
    assert verdict["s0"] is None and verdict["s1"] is None and verdict["s2"] is stream.turns[1]
    assert parsed(root).diag["late_session_start_records"] == 2


def test_late_resume_group_closes_no_turn_and_is_still_a_resume() -> None:
    tree = parsed(_two_turns(_late_resume(), _late_resume("lr2", 9.1), assistant("a3", 10, "m3", [text_block("z")])))
    stream = tree.streams[ROOT]
    first, second = stream.turns
    assert [r.uuid for r in stream.resumes] == ["lr"] and tree.diag["late_resume_group"] == 1
    assert stream.resume_at == {stream.resumes[0].line: second.first.line} and not second.bounded
    assert all(r.turn is None for r in stream.hooks) and second.last_assistant.uuid == "a3"
    assert stream.resume_ends == {stream.resumes[0].line: stream.recs[-1].line}


def test_on_time_resume_group_is_unchanged() -> None:
    tree = parsed(_two_turns(hook("rs", 6, "SessionStart:resume", durationMs=500), prompt("u3", 7, "p3", "more")))
    stream = tree.streams[ROOT]
    assert stream.turns[1].bounded and stream.resume_at == {stream.resumes[0].line: stream.resumes[0].line}
    assert tree.diag["late_resume_group"] == 0


def test_background_spawn_completes_at_the_effective_boundary() -> None:
    tur = {"agentId": "b1", "isAsync": True, "status": "async_launched"}
    use = assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {"subagent_type": "Explore"})])
    launched = [prompt("u1", 1, "p1", "go"), use, tool_result("r1", 3, "tuA", "x", pid="p1", tur=tur)]
    tail = [prompt("u2", 4, "p2", "again"), assistant("a2", 5, "m2", [text_block("y")]), _late_resume()]
    outside = parsed([*launched, *tail], {"b1": _kid("b1")}).spawns[0]
    assert outside.complete and outside.completed_at_boundary
    inside = parsed(
        [
            *launched[:1],
            *tail[:2],
            {**use, "uuid": "a3", "timestamp": ts(6)},
            tool_result("r2", 7, "tuA", "x", pid="p2", tur=tur),
            tail[2],
        ],
        {"b1": _kid("b1")},
    ).spawns[0]
    assert not inside.complete and not inside.completed_at_boundary


def test_twins_stay_turnless_across_the_frontier_reassembly() -> None:
    twin = {"toolUseID": "tw", "command": "c", "durationMs": 7}
    root = [
        prompt("u1", 1, "p1", "go"),
        hook("t1", 1.2, "UserPromptSubmit", **twin),
        hook("t2", 1.3, "UserPromptSubmit", **twin),
        assistant("a1", 2, "m1", [text_block("x")]),
        witness("w1", 3),
        hook("t3", 3.5, "Notification", **twin),
        hook("t4", 3.6, "Notification", **twin),
    ]

    def shape(tree):
        stream = tree.streams[ROOT]
        recs = [(r.uuid, r.kind, r.turn.prompt_id if r.turn else None) for r in stream.recs]
        return recs, [[r.uuid for r in t.recs] for t in stream.turns], [r.uuid for r in stream.hooks]

    cut = framed(root)
    assert cut.diag["frontier_withheld"] == 2 and shape(cut) == shape(parsed(root[:5]))
    assert shape(cut)[1] == [["u1", "t1", "a1", "w1"]] and ("t2", "hook_twin", None) in shape(cut)[0]
    assert cut.diag["hook_twin_records"] == 1
