"""record_turn: golden corpus equality, R7 write order, incremental equals full, finalize, reset, loud pins."""

from __future__ import annotations

import dataclasses
import json

import pytest

from src.telemetry.append import prepare_batch
from src.turn_capture import record_turn as record_turn_module
from src.turn_capture.cursor import CaptureCursor, CursorError, SourceMutated
from src.turn_capture.record_turn import (
    BILLING_SHAPES,
    FULL_OUTPUT_CAP_BYTES,
    PARSER_VERSION,
    PRODUCER,
    PRODUCER_VERSION,
    TABLES,
    PinOrderError,
    record_turn,
)
from src.turn_capture.render import render_rows_json
from src.turn_capture.scrub import scrub_text
from src.turn_capture.streams import ROOT
from src.turn_capture.transcript import MemTree
from tests.fixtures import turn_capture_line_cuts as line_cuts
from tests.fixtures.telemetry_blob_fakes import as_written
from tests.fixtures.turn_capture_corpus import (
    PROJECT,
    PROJECT_REF,
    SID,
    TENANT,
    assert_content_hashes,
    assert_output_identities,
    assistant,
    build_secret,
    case_cap,
    case_full_output_cap,
    case_names,
    child,
    fork_boundary,
    full_output_omitted_over_cap_files,
    hook,
    hooks_before_first_record_files,
    jsonl,
    large_tree,
    load_case,
    make_files,
    mcp_overflow_files,
    normalise,
    prompt,
    reasoning_forms_files,
    resume_tail,
    text_block,
    tool_result,
    tool_use_block,
)
from tests.fixtures.turn_capture_ducklake import load_specs
from tests.fixtures.turn_capture_v3_cases import V3_BUILDERS, check_schedule, keyed
from tests.fixtures.turn_capture_v4_cases import V4_BUILDERS
from tests.fixtures.turn_capture_v5_cases import V5_BUILDERS
from tests.fixtures.turn_capture_v6_cases import V6_BUILDERS
from tests.fixtures.turn_capture_v7_cases import V7_BUILDERS

SECRET_KINDS = ("aws_access", "aws_secret", "aws_session", "anthropic", "github", "github_pat", "bearer")


def capture(files, cursor=None, final=False, **kw):
    return record_turn(MemTree(files), cursor, project_ref=PROJECT_REF, session_final=final, **kw)


def test_golden_cases_match_expected_rows() -> None:
    names = case_names()
    assert len(names) == len(set(names))
    for name in names:
        files, expected = load_case(name)
        with case_cap(name):
            result = capture(files, final=True)
        assert_content_hashes(result.batches)
        assert_output_identities(files, result.batches)
        assert normalise(result.batches) == expected, name


def test_no_secret_shaped_value_emitted() -> None:
    for name in case_names():
        files, _ = load_case(name)
        with case_cap(name):
            wire = render_rows_json([row for _, rows in capture(files, final=True).batches for row in rows])
        assert scrub_text(wire).counts == {}, name
        for kind in SECRET_KINDS:
            assert build_secret(kind) not in wire, (name, kind)


def test_rows_use_only_caller_known_columns() -> None:
    specs = load_specs()
    for name in case_names():
        files, _ = load_case(name)
        with case_cap(name):
            batches = capture(files, final=True).batches
        for table, rows in batches:
            spec = specs[table]
            caller_not_null = {c for c in spec.not_null if c in spec.caller_known_columns}
            for row in rows:
                assert set(row) <= spec.caller_known_columns, (name, table, set(row) - spec.caller_known_columns)
                assert all(row.get(col) is not None for col in caller_not_null), (name, table, row["external_ref"])
                for column, plan in spec.key_plans.items():
                    if plan.required:
                        assert row.get(plan.ref_field), (name, table, column)


def test_sessions_batch_written_last() -> None:
    files, _ = load_case("subagent_sync.json")
    result = capture(files, final=True)
    assert [table for table, _ in result.batches] == list(TABLES) and TABLES[-1] == "telemetry_sessions"
    assert result.sessions and all(rows for _, rows in result.batches)
    assert {r["entity_ref"] for r in result.sessions} == {SID, f"{SID}:agent1"}
    assert result.next_cursor is not None and result.next_cursor.finalized and result.next_cursor.producer == PRODUCER


def test_incremental_equals_full_at_every_line_cut() -> None:
    boundaries = {name: fork_boundary(load_case(name)[0]) for name in case_names()}
    assert boundaries["fork_session_copied_head.json"] > 0 and boundaries["fork_session_nested.json"] > 0
    assert boundaries["fork_branch_copied_head.json"] == 0 and boundaries["secrets.json"] == 0
    for name in case_names():
        files, _ = load_case(name)
        with case_cap(name):
            full_rows = keyed(capture(files, final=True))
            for path in line_cuts.paths(files):
                cursor, seen = None, {}
                for index, cut in enumerate(path):
                    last = index == len(path) - 1
                    result = capture(line_cuts.cut(files, cut), cursor, final=last)
                    cursor = result.next_cursor
                    for key, row in keyed(result).items():
                        assert key not in seen, (name, cut, key)
                        assert row == full_rows[key], (name, cut, key)
                        seen[key] = row
                assert set(seen) == set(full_rows), (name, set(full_rows) ^ set(seen))


def test_finalize_equals_virtual_prompt_at_eof() -> None:
    skipped = {"subagent_background_without_completion.json"}
    for name in case_names():
        if name in skipped:
            continue
        files, _ = load_case(name)
        with case_cap(name):
            final_rows = keyed(capture(files, final=True))
        root_closes = [
            k
            for k, r in final_rows.items()
            if k[0] == "telemetry_sessions" and r["event_kind"] == "close" and r["entity_ref"] == SID
        ]
        closes = sorted(root_closes, key=lambda k: final_rows[k]["source_ordinal"])[-1:]
        child_closes = [
            k
            for k, r in final_rows.items()
            if k[0] == "telemetry_sessions" and r["event_kind"] == "close" and k not in root_closes
        ]
        assert not child_closes, name
        opens = [
            k
            for k, r in final_rows.items()
            if k[0] == "telemetry_sessions" and r["event_kind"] == "open" and r["entity_ref"] == SID
        ]
        assert len(closes) == len(opens), name
        virtual = dict(files)
        for rel in files:
            if rel.endswith(".jsonl"):
                agent = rel.split("agent-")[1][: -len(".jsonl")] if "/subagents/" in rel else None
                rec = prompt(f"zz-{agent or 'root'}", 999, "p-virtual", "virtual")
                virtual[rel] = (
                    files[rel] + ("\n" if not files[rel].endswith("\n") else "") + jsonl([child(rec, agent) if agent else rec])
                )
        with case_cap(name):
            live_rows = {k: v for k, v in keyed(capture(virtual, final=False)).items() if not k[1].startswith("zz-")}
        ended_by_virtual = {k for k, v in live_rows.items() if v.get("event_kind") == "resume" and k not in final_rows}
        assert {k: v for k, v in live_rows.items() if k not in ended_by_virtual} == {
            k: v for k, v in final_rows.items() if k not in closes
        }, name


def _grown_files():
    base = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuX", "Bash", {})]),
    ]
    grown = [
        hook("rs1", 10, "SessionStart:resume"),
        prompt("u2", 11, "p2", "back"),
        assistant("a2", 12, "m2", [text_block("hello again")]),
        tool_result("late", 13, "tuX", "very late", pid="p2"),
    ]
    return make_files(base), make_files(base + grown)


def test_finalize_then_grow_through_resume_re_emits_nothing() -> None:
    before, after = _grown_files()
    first = capture(before, final=True)
    assert "a1#0/tool_call_close" in {r["external_ref"] for r in first.observations}
    second = capture(after, first.next_cursor, final=True)
    old_keys, new_keys = set(keyed(first)), set(keyed(second))
    assert not old_keys & new_keys
    assert ("telemetry_sessions", "rs1#0/resume") in new_keys and ("telemetry_observations", "late#0/turn_close") in new_keys
    assert not any(k[1] == "late#0/tool_call_close" for k in new_keys)
    first_close = ("telemetry_sessions", "a1#0/close")
    assert first_close in old_keys and keyed(first)[first_close]["event_kind"] == "close"
    assert set(keyed(capture(after, None, final=True))) == old_keys | new_keys
    assert capture(after, second.next_cursor, final=True).observations == []


def test_cursor_reset_on_version_mismatch_reuses_pins() -> None:
    files, _ = load_case("subagent_sync.json")
    first = capture(files, final=True)
    stale = dataclasses.replace(first.next_cursor, parser_version=PARSER_VERSION - 1)
    again = capture(files, stale, final=True)
    assert keyed(again) == keyed(first)
    kept, fresh = again.next_cursor, first.next_cursor
    assert (kept.project_ref, kept.session_started_at_ms, kept.root_session_ref, kept.billing_shape) == (
        fresh.project_ref, fresh.session_started_at_ms, fresh.root_session_ref, fresh.billing_shape,
    )  # fmt: skip
    assert kept.parser_version == PARSER_VERSION
    other_producer = dataclasses.replace(first.next_cursor, producer="somebody_else")
    assert keyed(capture(files, other_producer, final=True)) == keyed(first)
    reset = dataclasses.replace(stale, session_started_at_ms=stale.session_started_at_ms + 1)
    with pytest.raises(CursorError):
        capture(files, reset, final=True)


def test_pin_mismatch_and_sidecar_mutation_are_loud() -> None:
    files, _ = load_case("sidecar_present.json")
    cursor = capture(files, final=False).next_cursor
    assert cursor is not None
    with pytest.raises(CursorError, match="project_ref"):
        record_turn(MemTree(files), cursor, project_ref="another/project")
    with pytest.raises(ValueError, match="billing_shape"):
        record_turn(MemTree(files), cursor, project_ref=PROJECT_REF, billing_shape="metered_marginal")
    metered = dataclasses.replace(cursor, billing_shape="metered_marginal")
    with pytest.raises(CursorError, match="billing_shape"):
        record_turn(MemTree(files), metered, project_ref=PROJECT_REF)
    other = {k.replace(SID, "bbbbbbbb-0000-4000-8000-000000000002"): v for k, v in files.items()}
    with pytest.raises(CursorError, match="root_session_ref"):
        record_turn(MemTree(other), cursor, project_ref=PROJECT_REF)
    pinned = capture(files, final=True).next_cursor
    assert isinstance(pinned.sidecars["big1.txt"], str)
    mutated = dict(files)
    mutated[f"{SID}/tool-results/big1.txt"] = "TAMPERED"
    with pytest.raises(SourceMutated):
        capture(mutated, pinned, final=True)
    vanished = {k: v for k, v in files.items() if "tool-results" not in k}
    with pytest.raises(SourceMutated):
        capture(vanished, pinned, final=True)


def test_finalize_then_grow_keeps_the_synthetic_close_anchored() -> None:
    before, after = _grown_files()
    close_before = keyed(capture(before, final=True))[("telemetry_observations", "a1#0/tool_call_close")]
    close_after = keyed(capture(after, final=True))[("telemetry_observations", "a1#0/tool_call_close")]
    assert close_before == close_after and close_after["outcome"] == "interrupted"


def test_inputs_are_validated_and_diagnostics_are_counts() -> None:
    files, _ = load_case("unknown_record_and_attachment_types.json")
    with pytest.raises(ValueError, match="billing_shape"):
        capture(files, billing_shape="free")
    with pytest.raises(ValueError, match="project_ref"):
        record_turn(MemTree(files), None, project_ref="")
    assert BILLING_SHAPES == ("fixed_non_rollover_allowance",)
    result = capture(files, final=True)
    assert (
        all(isinstance(v, int) for v in result.diagnostics.values())
        and result.diagnostics["unknown_record_type:weird-new"] == 1
    )
    assert {r["billing_shape"] for r in result.observations if r["observation_type"] == "model_call"} == set(BILLING_SHAPES)
    assert CaptureCursor.from_json(result.next_cursor.to_json()) == result.next_cursor
    assert json.loads(result.next_cursor.to_json())["finalized"] is True


def test_duplicate_source_refs_are_counted_and_first_wins() -> None:
    files = make_files(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [text_block("first")]),
            prompt("u2", 3, "p2", "next"),
            assistant("a1", 4, "m2", [text_block("same uuid again")]),
        ]
    )
    result = capture(files, final=True)
    assert result.diagnostics["duplicate_refs"] >= 1
    assert [r["content"] for r in result.transcripts if r["external_ref"] == "a1#0"] == ["first"]


def test_every_emitted_row_passes_kernel_rules() -> None:
    specs = load_specs()
    threshold = specs["telemetry_transcripts"].rules.content.threshold
    for name in case_names():
        files, _ = load_case(name)
        with case_cap(name):
            batches = capture(files, final=True).batches
        for table, rows in batches:
            prepare_batch(specs[table], as_written(rows, threshold), tenant_id=TENANT, project_id=PROJECT)


def test_full_output_cap_constant_and_case_override() -> None:
    assert FULL_OUTPUT_CAP_BYTES == 8388608 and case_full_output_cap("sidecar_present.json") is None
    cap = case_full_output_cap("full_output_omitted_over_cap.json")
    assert cap is not None and cap < 43
    with case_cap("full_output_omitted_over_cap.json"):
        assert record_turn_module.FULL_OUTPUT_CAP_BYTES == cap
    assert record_turn_module.FULL_OUTPUT_CAP_BYTES == 8388608


def test_new_cases_are_built_from_their_builders() -> None:
    builders = {
        "hooks_before_first_record.json": hooks_before_first_record_files,
        "mcp_overflow_saved_output.json": mcp_overflow_files,
        "full_output_omitted_over_cap.json": full_output_omitted_over_cap_files,
        "reasoning_visibility_forms.json": reasoning_forms_files,
        **V3_BUILDERS,
        **V4_BUILDERS,
        **V5_BUILDERS,
        **V6_BUILDERS,
        **V7_BUILDERS,
    }
    for name, builder in builders.items():
        assert load_case(name)[0] == builder(), name


def test_repin_while_nothing_emitted() -> None:
    files = hooks_before_first_record_files()
    only_queue_op = line_cuts.cut(files, {ROOT: 1})
    first = capture(only_queue_op)
    assert first.next_cursor is not None and not any(rows for _, rows in first.batches)
    held = first.next_cursor.session_started_at_ms
    second = capture(files, first.next_cursor, final=True)
    assert second.next_cursor.session_started_at_ms < held
    full = capture(files, None, final=True)
    assert keyed(second) == keyed(full) and second.next_cursor.session_started_at_ms == full.next_cursor.session_started_at_ms
    assert {r["session_started_at"] for _, rows in second.batches for r in rows} == {full.sessions[0]["session_started_at"]}


def test_earlier_record_after_pin_halts() -> None:
    root = [prompt("u1", 5, "p1", "go"), assistant("a1", 6, "m1", [text_block("hi")]), prompt("u2", 8, "p2", "next")]
    first = capture(make_files(root))
    assert first.observations and first.next_cursor is not None
    grown = make_files(root + [hook("hk", 1, "SessionStart:startup")])
    with pytest.raises(PinOrderError):
        capture(grown, first.next_cursor)
    assert issubclass(PinOrderError, CursorError)
    later = make_files(root[1:])
    with pytest.raises(CursorError) as raised:
        capture(later, first.next_cursor)
    assert not isinstance(raised.value, PinOrderError)


def test_finalize_resume_schedules_emit_each_row_once() -> None:
    for name in case_names():
        files, _ = load_case(name)
        for kind in ("resume", "continued"):
            for cut in range(fork_boundary(files), files[f"{SID}.jsonl"].count("\n") + 1):
                check_schedule(files, cut, kind, name)


def _resumable():
    base = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [tool_use_block("tuX", "Bash", {})])]
    return base, [tool_result("r1", 3, "tuX", "late result", pid="p1"), assistant("a2", 4, "m2", [text_block("after")])]


def test_root_result_after_a_finalize_counts_reanchor() -> None:
    base, tail = _resumable()
    first = capture(make_files(base), final=True)
    second = capture(make_files(base + tail), first.next_cursor, final=True)
    assert second.diagnostics["reanchored_close:tool_call"] == 1 and second.diagnostics["reanchored_close:turn"] == 1
    interposed = [hook("rs", 2.5, "SessionStart:resume")]
    split = capture(make_files(base + interposed + tail), first.next_cursor, final=True)
    assert split.diagnostics["results_outside_turn"] == 1
    assert not split.diagnostics.get("reanchored_close:tool_call") and not split.diagnostics.get("reanchored_close:turn")


def _background_files(grown: bool):
    use = tool_use_block("tuA", "Agent", {"subagent_type": "Explore", "run_in_background": True})
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [use]),
        tool_result("r1", 3, "tuA", "started", pid="p1", tur={"agentId": "ag1", "isAsync": True}),
    ]
    partial = {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    kid = [
        child(prompt("c1", 4, "cp1", "task"), "ag1"),
        child(assistant("c2", 5, "mc", [tool_use_block("tuC", "Bash", {})], usage=partial, api_block=0), "ag1"),
    ]
    if grown:
        kid += [
            child(assistant("c3", 6, "mc", [text_block("more")], api_block=1), "ag1"),
            child(tool_result("c4", 7, "tuC", "child result", pid="cp1"), "ag1"),
        ]
    return make_files(root, {"ag1": kid})


def test_child_growth_after_finalize_counts_divergence_and_resends_nothing() -> None:
    cut, full = _background_files(False), _background_files(True)
    first = capture(cut, final=True)
    stop = capture(full, first.next_cursor, final=False)
    last = capture(full, stop.next_cursor, final=True)
    assert first.next_cursor is not None and last.diagnostics.get("prior_content_divergence", 0) == 0
    assert last.diagnostics["cut_content_divergence"] == 1 and last.diagnostics["reanchored_close:turn"] == 1
    assert last.diagnostics["reanchored_close:tool_call"] == 1
    sent = [keyed(r) for r in (first, stop, last)]
    assert not (set(sent[0]) & set(sent[2])) and ("telemetry_observations", "mc/model_call") in sent[0]
    assert ("telemetry_observations", "mc/model_call") not in sent[2]
    cut_close = sent[0][("telemetry_observations", "c2#0/tool_call_close")]
    assert cut_close["outcome"] == "interrupted" and cut_close["output_capture"] == "no_result"
    assert sent[2][("telemetry_observations", "c4#0/tool_call_close")]["outcome"] == "success"
    assert ("telemetry_agents", "a1#0/agent_open") in sent[0] and not any(k[1].endswith("agent_close") for k in sent[0])
    same = capture(cut, last.next_cursor, final=True)
    assert not same.diagnostics.get("reanchored_close:turn") and not same.diagnostics.get("reanchored_close:tool_call")
    twice = capture(cut, first.next_cursor, final=True)
    assert keyed(twice) == {} and not twice.diagnostics.get("reanchored_close:turn")


def test_child_without_reply_defers_its_rows() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {})]),
        tool_result("r1", 3, "tuA", "done", pid="p1", tur={"agentId": "ag1", "isAsync": False}),
    ]
    silent = [child(prompt("c1", 4, "cp1", "task"), "ag1")]
    replied = silent + [child(assistant("c2", 5, "mc", [text_block("answer")]), "ag1")]
    kid = f"{SID}:ag1"

    def of_child(result):
        return [r for _, rows in result.batches for r in rows if kid in (r.get("session_ref"), r.get("entity_ref"))]

    first = capture(make_files(root, {"ag1": silent}), final=True)
    assert of_child(first) == [] and first.sessions and not first.agents
    second = capture(make_files(root, {"ag1": replied}), first.next_cursor, final=True)
    tables = {table for table, rows in second.batches if any(r in of_child(second) for r in rows)}
    assert tables == {"telemetry_observations", "telemetry_transcripts", "telemetry_agents", "telemetry_sessions"}


def test_api_error_first_turn_keeps_a_stable_open_row() -> None:
    error = assistant(
        "e1", 2, "00000000-0000-4000-8000-0000000000e1", [text_block("boom")], model="<synthetic>", isApiErrorMessage=True
    )
    base = [prompt("u1", 1, "p1", "go"), error]
    grown = base + resume_tail()
    first = capture(make_files(base), final=True)
    second = capture(make_files(grown), first.next_cursor, final=True)
    opens = [r for r in first.sessions + second.sessions if r["event_kind"] == "open"]
    assert len(opens) == 1 and "model_primary" not in opens[0]
    assert not second.diagnostics.get("cut_content_divergence") and not second.diagnostics.get("prior_content_divergence")


def test_fork_boundary_arriving_after_a_pass_halts() -> None:
    for name in ("fork_session_copied_head.json", "fork_session_nested.json"):
        files, _ = load_case(name)
        boundary = fork_boundary(files)
        early = capture(line_cuts.cut(files, {ROOT: boundary - 1}), final=True)
        assert early.next_cursor is not None and early.observations
        copied = {r["external_ref"] for r in early.observations}
        assert "c1#0/turn_open" in copied or "pp1#0/turn_open" in copied
        with pytest.raises(CursorError, match="session_started_at_ms") as raised:
            capture(files, early.next_cursor, final=True)
        assert not isinstance(raised.value, PinOrderError)
        fresh = capture(files, None, final=True)
        shared = set(keyed(early)) & set(keyed(fresh))
        assert shared == {("telemetry_sessions", f"{SID}#0/open")}, name
        assert early.sessions[0]["event_timestamp"] < fresh.sessions[0]["event_timestamp"]
        assert fresh.next_cursor.session_started_at_ms > early.next_cursor.session_started_at_ms


def test_large_transcript_pass_within_budget() -> None:
    files = large_tree(2000)
    cold = record_turn(MemTree(files), None, project_ref=PROJECT_REF, session_final=True)
    cursor = cold.next_cursor
    distinct = len(cursor.sidecars)
    assert distinct == 4 and cold.diagnostics["sidecar_reads"] == distinct and cold.diagnostics["parse_passes"] == 1
    warm = record_turn(MemTree(files), cursor, project_ref=PROJECT_REF, session_final=True)
    assert warm.diagnostics["sidecar_reads"] == distinct
    assert warm.diagnostics["parse_passes"] <= 2 + len(cursor.finalize_cuts) and keyed(warm) == {}


def test_finalization_cuts_are_recorded_once_and_survive_a_reset() -> None:
    files, _ = load_case("subagent_sync.json")
    first = capture(files, final=True)
    cuts = first.next_cursor.finalize_cuts
    assert len(cuts) == 1 and cuts[0] == first.next_cursor.lines_consumed
    again = capture(files, first.next_cursor, final=True)
    assert again.next_cursor.finalize_cuts == cuts and keyed(again) == {}
    live = capture(files, first.next_cursor, final=False)
    assert live.next_cursor.finalize_cuts == cuts and not live.next_cursor.finalized
    stale = dataclasses.replace(first.next_cursor, parser_version=PARSER_VERSION - 1)
    re_emitted = capture(files, stale, final=True)
    assert [r["event_kind"] for r in re_emitted.sessions].count("close") == 1 and re_emitted.next_cursor.finalize_cuts == cuts
    assert PRODUCER_VERSION == "turn-capture-7" and PARSER_VERSION == 7


def test_resend_tree_re_emits_every_row_keeping_pins_and_cuts() -> None:
    base = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("hi")])]
    grown = [prompt("u2", 11, "p2", "back"), assistant("a2", 12, "m2", [text_block("hello again")])]
    before, after = make_files(base), make_files(base + grown)
    first = capture(before, final=True)
    second = capture(after, first.next_cursor)
    held = second.next_cursor
    normal = capture(after, held)
    resent = capture(after, held, resend_tree=True)
    assert keyed(normal) == {} and keyed(resent) == {**keyed(first), **keyed(second)}
    full = keyed(capture(after))
    assert all(keyed(resent)[key] == row for key, row in full.items())
    assert [key for key in keyed(resent) if key not in full] == [("telemetry_sessions", "a1#0/close")]
    assert resent.next_cursor == normal.next_cursor and resent.next_cursor.finalize_cuts == held.finalize_cuts == [{ROOT: 2}]


def test_a_stream_with_no_assistant_reply_emits_no_row_in_any_table() -> None:
    files, expected = load_case("no_reply_session.json")
    result = capture(files, final=True)
    assert expected == [] and all(rows == [] for _, rows in result.batches) and result.next_cursor is not None


def test_prior_content_divergence_is_counted_and_not_resent(monkeypatch) -> None:
    original = record_turn_module._build_all

    def tagged(parsed, ctx, sidecars, diag):
        built = original(parsed, ctx, sidecars, diag)
        for rows in built.values():
            for row in rows:
                row["_lines"] = sum(parsed.lines_total.values())
        return built

    monkeypatch.setattr(record_turn_module, "_build_all", tagged)
    base = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("x")]), prompt("u2", 3, "p2", "next")]
    first = capture(make_files(base))
    grown = base + [assistant("a2", 4, "m2", [text_block("y")]), prompt("u3", 5, "p3", "more")]
    second = capture(make_files(grown), first.next_cursor)
    assert second.diagnostics["prior_content_divergence"] > 0
    assert not set(keyed(first)) & set(keyed(second))
