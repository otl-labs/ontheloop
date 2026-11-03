"""rec-4025's acceptance module: the derived layer proved end to end on known inputs (PLAN-telemetry-golden-harness).

Every producer golden tree and the slice 4 verification trees run through the real producer (record_turn), the real append
kernel (append_events on an in-memory DuckDB telemetry catalog) and every derivation reader verb; the results must equal the
hand-authored values in derived_golden_{state,rollups}.yaml AND the independent oracle in scripts.telemetry_verify run over the
rows read back from the catalog. Module-level importorskip in the assignment form scripts/verification_graduation.py
recognises, so the venv-less graduation differential skips rather than fails.
"""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")

from typing import Any  # noqa: E402

from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402


def _entries() -> list[Any]:
    return [pytest.param(e, id=f"{e['tree']}-{e['pass']}-{e['file'].split('_')[-1][:-5]}") for e in chain.expectation_trees()]


def _reconcile_entry(entry: dict, kind: str = "memory", tmp_path=None) -> list[str]:
    with chain.loaded(entry["tree"], chain.pass_is_final(entry), kind, tmp_path) as (con, rows, ids):
        return chain.reconcile(con, rows, entry["expect"], ids)


def test_golden_fixtures_reconcile() -> None:
    from src.turn_capture.record_turn import PARSER_VERSION  # noqa: PLC0415

    problems: list[str] = []
    for entry in chain.expectation_trees():
        assert entry["parser_version"] == PARSER_VERSION, entry["file"]
        problems += [f"{entry['tree']} {entry['pass']}: {p}" for p in _reconcile_entry(entry)]
    assert problems == []


from scripts.telemetry_verify import dedupe  # noqa: E402
from src.turn_capture.record_turn import PARSER_VERSION  # noqa: E402
from tests.fixtures.turn_capture_corpus import case_names  # noqa: E402

_SESSION_VERBS = (
    "agent_run_token_rollup",
    "session_process_event_rollup",
    "session_friction_rollup",
    "model_call_cost_estimate",
)
_STATE_VERBS = ("session_state_and_duration", "agent_run_state_and_duration")
_WINDOW_VERBS = ("sessions_window", "sessions_window_all_purposes")
_ALL_TREES = [f"corpus:{n.removesuffix('.json')}" for n in case_names()] + [
    f"verification:{n}" for n in chain.verification.TREES
]


def _passes(tree: str) -> list[bool]:
    """Pass kinds to run: the final pass always, the Stop pass too where the tree finalizes (the final pass holds a close)."""
    closes = any(r["event_kind"] == "close" for r in chain.emit(tree, True).sessions)
    return [True, False] if closes else [True]


def _agreement(con: Any, rows: dict, ids: dict) -> list[str]:
    problems: list[str] = []

    def same(verb: str, **params: Any) -> None:
        got, oracle = chain.run_verb(con, verb, **params), chain.run_oracle(rows, verb, **params)
        if chain.canonical(got) != chain.canonical(oracle):
            problems.append(f"{verb} {params}: verb {got} != oracle {oracle}")

    roots = [sid for selector, sid in ids.items() if selector == "root"]
    for sid in ids.values():
        for as_of in chain.as_of_rule(chain.last_event(rows, sid)):
            for verb in _STATE_VERBS:
                same(verb, session_id=sid, as_of=as_of)
            if sid in roots:
                for verb in _WINDOW_VERBS:
                    same(verb, as_of=as_of, **chain.DAY)
        for verb in _SESSION_VERBS:
            same(verb, session_id=sid)
    return problems


def _invariants(con: Any, rows: dict, result: Any, final: bool) -> list[str]:
    problems = []
    emitted = chain.hold_back(result)
    for table, batch in emitted.batches:
        if len(rows[table]) != len(batch):
            problems.append(f"{table}: stored {len(rows[table])} rows, emitted {len(batch)}")
    generations = dedupe.compute_generations(rows["telemetry_sessions"])
    sessions = {r["session_id"] for r in rows["telemetry_sessions"]}
    if generations != {("claude_code", sid): PARSER_VERSION for sid in sessions}:
        problems.append(f"generations {generations}")
    for table in chain.TABLES:
        collapse = (lambda r: r.get("observation_type") == "model_call") if table.endswith("observations") else False
        survivors, conflicted = dedupe.resolve_table(table, rows[table], generations, entity_collapse=collapse)
        if {r["event_id"] for r in survivors} != {r["event_id"] for r in rows[table]}:
            problems.append(f"{table}: survivors differ from the emitted rows")
        if conflicted:
            problems.append(f"{table}: conflicted {conflicted}")
    if final and any(r["event_kind"] == "close" for r in emitted.sessions):
        problems += [f"join {j} does not resolve" for j in chain.unresolved_joins(con)]
    return problems


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_every_tree_agrees_with_the_oracle_and_holds_invariants(tree: str) -> None:
    problems: list[str] = []
    for final in _passes(tree):
        with chain.loaded(tree, final) as (con, rows, ids):
            problems += _agreement(con, rows, ids)
            problems += _invariants(con, rows, chain.emit(tree, final), final)
    assert problems == [], tree


import dataclasses  # noqa: E402
from datetime import timedelta  # noqa: E402

from src.common import ducklake_event_read_sql as read_sql  # noqa: E402
from src.common import ducklake_event_read_verbs as read_verbs  # noqa: E402
from tests.fixtures import telemetry_read_db as read_db  # noqa: E402


def _entries_where(keep: Any) -> list[tuple[str, bool, list[dict[str, Any]]]]:
    """(tree, final, expectations kept) per authored (tree, pass) that has at least one kept expectation."""
    out = []
    for entry in chain.expectation_trees():
        kept = [e for e in entry["expect"] if keep(e)]
        if kept:
            out.append((entry["tree"], chain.pass_is_final(entry), kept))
    return out


def _has_close_tie(tree: str, final: bool) -> bool:
    """A session whose latest rows by (event_timestamp, source_ordinal) include a close tying another lifecycle kind."""
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in chain.emit(tree, final).sessions:
        if row["event_kind"] != "annotate":
            by_session.setdefault(row["entity_ref"], []).append(row)
    for rows in by_session.values():
        top = max((r["event_timestamp"], r.get("source_ordinal") or 0) for r in rows)
        kinds = {r["event_kind"] == "close" for r in rows if (r["event_timestamp"], r.get("source_ordinal") or 0) == top}
        if kinds == {True, False}:
            return True
    return False


def _reconcile_inputs(inputs: list[tuple[str, bool, list[dict[str, Any]]]]) -> list[str]:
    problems: list[str] = []
    for tree, final, expectations in inputs:
        with chain.loaded(tree, final) as (con, rows, ids):
            problems += chain.reconcile(con, rows, expectations, ids)
    return problems


def _shared_observation_pair() -> list[str]:
    """A claude_code plus litellm model_call pair sharing one observation_id, inserted directly (the R5c input)."""
    problems: list[str] = []
    with chain.loaded("corpus:resume_after_interrupt") as (con, rows, ids):
        base = next(r for r in rows["telemetry_observations"] if r["observation_type"] == "model_call")
        stamp = base["event_timestamp"] + timedelta(seconds=30)
        pair = [
            dict(
                base,
                event_id="r5c-claude",
                observation_id="r5c-shared",
                producer="claude_code",
                tokens_input=111,
                event_timestamp=stamp,
            ),
            dict(
                base,
                event_id="r5c-litellm",
                observation_id="r5c-shared",
                producer="litellm",
                tokens_input=222,
                event_timestamp=stamp + timedelta(seconds=1),
            ),
        ]
        read_db.insert_rows(con, "telemetry_observations", pair)
        stored = chain.stored_rows(con)
        sid = ids["root"]
        before = [r for r in rows["telemetry_observations"] if r["observation_type"] == "model_call"]
        (got,) = chain.run_verb(con, "agent_run_token_rollup", session_id=sid)
        (oracle,) = chain.run_oracle(stored, "agent_run_token_rollup", session_id=sid)
        if chain.canonical(got) != chain.canonical(oracle):
            problems.append(f"R5c pair: verb {got} != oracle {oracle}")
        if got["model_call_count"] != len(before) + 1:
            problems.append(f"R5c pair counted {got['model_call_count']} calls, expected {len(before) + 1}")
        if got["tokens_input_total"] != sum(r["tokens_input"] for r in before) + 111:
            problems.append("R5c pair: the claude_code row did not win")
    return problems


def _mutate(sql: str, old: str, new: str) -> str:
    assert old in sql, f"mutation target {old!r} is absent from the verb SQL"
    return sql.replace(old, new)


_MUTATIONS = {
    "token_sum_becomes_max": ("agent_run_token_rollup", "sum(tokens_output)", "max(tokens_output)"),
    "close_ranks_first_on_a_tie": ("session_state_and_duration", "(event_kind = 'close') DESC", "(event_kind = 'close') ASC"),
    "model_call_collapse_removed": ("agent_run_token_rollup", read_sql._MODEL_CALL_COLLAPSE, "o AS (SELECT * FROM o_ev)"),
    "idle_comparison_becomes_inclusive": ("session_state_and_duration", " > {idle_seconds}", " >= {idle_seconds}"),
}


def _inputs_for(name: str) -> Any:
    """The discriminating inputs for a mutation, selected by property (never by tree name), with named preconditions."""
    if name == "token_sum_becomes_max":
        inputs = _entries_where(lambda e: e["verb"] == "agent_run_token_rollup")
        assert inputs, "precondition: an authored agent_run_token_rollup expectation"
        return lambda: _reconcile_inputs(inputs)
    if name == "close_ranks_first_on_a_tie":
        ties = {(t, f) for t, f, _ in _entries_where(lambda e: True) if _has_close_tie(t, f)}
        inputs = [
            (t, f, [e for e in x if e["verb"] == "session_state_and_duration"])
            for t, f, x in _entries_where(lambda e: e["verb"] == "session_state_and_duration")
            if (t, f) in ties
        ]
        assert inputs, "precondition: an authored tree whose root's latest rows include a close tying another event kind"
        return lambda: _reconcile_inputs(inputs)
    if name == "model_call_collapse_removed":
        return _shared_observation_pair
    inputs = _entries_where(lambda e: e.get("discriminates") == "idle_threshold")
    assert sum(len(x) for _, _, x in inputs) >= 2, "precondition: at least two idle_threshold expectations"
    return lambda: _reconcile_inputs(inputs)


@pytest.mark.parametrize("name", list(_MUTATIONS))
def test_harness_detects_planted_verb_defects(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    verb, old, new = _MUTATIONS[name]
    run = _inputs_for(name)
    assert run() == [], f"control: the unmutated verbs reconcile on the {name} inputs"
    entry = read_verbs.EVENT_READS[verb]
    monkeypatch.setitem(read_verbs.EVENT_READS, verb, dataclasses.replace(entry, sql=_mutate(entry.sql, old, new)))
    assert run() != [], f"the harness did not detect the planted defect {name}"


from tests.fixtures.turn_capture_corpus import fork_boundary, root_lines, with_root  # noqa: E402


def _snapshot(con: Any, ids: dict[str, str], as_ofs: dict[str, list[str]]) -> dict[str, Any]:
    """Every session-bound and window verb output of a catalog, keyed by call (stamps included)."""
    out: dict[str, Any] = {}
    for selector, sid in ids.items():
        for as_of in as_ofs[selector]:
            for verb in _STATE_VERBS:
                out[f"{verb} {selector} {as_of}"] = chain.canonical(chain.run_verb(con, verb, session_id=sid, as_of=as_of))
            if selector == "root":
                for verb in _WINDOW_VERBS:
                    out[f"{verb} {as_of}"] = chain.canonical(chain.run_verb(con, verb, as_of=as_of, **chain.DAY))
        for verb in _SESSION_VERBS:
            out[f"{verb} {selector}"] = chain.canonical(chain.run_verb(con, verb, session_id=sid))
    return out


def _incremental(tree: str) -> Any:
    """A catalog fed by a non-final pass at every root line count from the fork boundary to the end, then the final pass."""
    files, _ = chain.tree_files(tree)
    lines = root_lines(files)
    con = chain.open_catalog()
    cursor = None
    for count in range(fork_boundary(files), len(lines) + 1):
        result = chain.record(tree, with_root(files, "".join(lines[:count])), cursor, False)
        chain.append_batches(con, chain.hold_back(result))
        cursor = result.next_cursor
    result = chain.record(tree, files, cursor, True)
    chain.append_batches(con, chain.hold_back(result))
    return con


@pytest.mark.parametrize("tree", _ALL_TREES)
def test_replay_order_and_late_delivery_leave_derived_values_unchanged(tree: str) -> None:
    result = chain.hold_back(chain.emit(tree, True))
    with chain.loaded(tree, True) as (con, rows, ids):
        as_ofs = {selector: chain.as_of_rule(chain.last_event(rows, sid)) for selector, sid in ids.items()}
        baseline = _snapshot(con, ids, as_ofs)
        assert not any(chain.append_batches(con, result).values()), "an identical re-send must insert 0 rows"
        assert _snapshot(con, ids, as_ofs) == baseline

    deliveries = {
        "reversed batch and row order": lambda c: chain.append_batches(c, result, reverse=True),
        "three days late": lambda c: chain.append_batches(c, result, now=chain.latest_event(result) + timedelta(days=3)),
    }
    for label, deliver in deliveries.items():
        late = chain.open_catalog()
        deliver(late)
        assert _snapshot(late, ids, as_ofs) == baseline, label
        late.close()

    if result.sessions:
        stepped = _incremental(tree)
        assert _snapshot(stepped, ids, as_ofs) == baseline, "incremental Stop-pass delivery"
        stepped.close()


from src.telemetry.gate import GrainConflictError  # noqa: E402

_STAMPS = ("generations", "conflicted_grain_keys", "derivation_conflicted")
_TREE = "corpus:nested_subagent_depth2"


def _stamps_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: chain.canonical(r[k]) for k in _STAMPS} for r in rows]


def _bound_calls(ids: dict[str, str], as_of: str) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for sid in ids.values():
        calls += [(v, {"session_id": sid, "as_of": as_of}) for v in _STATE_VERBS]
        calls += [(v, {"session_id": sid}) for v in _SESSION_VERBS]
    calls += [(v, {"as_of": as_of, **chain.DAY}) for v in _WINDOW_VERBS]
    return calls


def _conflict_report(table: str) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """Insert a conflicting twin of one stored row of *table* (distinct created_timestamp, one compared column changed); return
    the twin's key and, per verb, the sessions whose three stamps verb and oracle both report identically."""
    changed = {"telemetry_sessions": ("branch", "somewhere-else"), "telemetry_transcripts": ("purpose", "system")}
    column, value = changed[table]
    reported: dict[str, set[str]] = {}
    with chain.loaded(_TREE) as (con, rows, ids):
        victim = next(r for r in rows[table] if r["session_id"] == ids["root"] and r.get(column) != value)
        twin = dict(victim, **{column: value}, created_timestamp=victim["created_timestamp"] + timedelta(seconds=5))
        read_db.insert_rows(con, table, [twin])
        stored = chain.stored_rows(con)
        as_of = chain.as_of_rule(chain.last_event(stored, ids["root"]))[0]
        key = {
            "table_name": table,
            "producer": victim["producer"],
            "event_id": victim["event_id"],
            "parser_version": victim["parser_version"],
        }
        for verb, params in _bound_calls(ids, as_of):
            got, oracle = chain.run_verb(con, verb, **params), chain.run_oracle(stored, verb, **params)
            assert _stamps_only(got) == _stamps_only(oracle), (verb, params)
            for g, o in zip(got, oracle):
                if not g["derivation_conflicted"]:
                    assert chain.canonical(g) == chain.canonical(o), (verb, params)
                elif g["session_id"] == ids["root"]:
                    reported.setdefault(verb, set()).add(g["session_id"])
                    assert key in g["conflicted_grain_keys"]
    return key, reported


def test_conflicts_are_reported_and_generations_retire() -> None:
    result = chain.hold_back(chain.emit(_TREE, True))
    with chain.loaded(_TREE) as (con, rows, ids):
        before = {t: len(r) for t, r in rows.items()}
        differing = dict(result.sessions[0], branch="a different branch")
        with pytest.raises(GrainConflictError, match="TELEMETRY_GRAIN_CONFLICT"):
            chain.append_events(
                con,
                chain.specs()["telemetry_sessions"],
                [differing],
                tenant_id=chain.TENANT,
                project_id=chain.PROJECT,
                catalog=read_db.ALIAS,
                now=chain.latest_event(result) + timedelta(seconds=1),
            )
        assert {t: len(r) for t, r in chain.stored_rows(con).items()} == before, "a refused re-send writes nothing"

    _, sessions_reported = _conflict_report("telemetry_sessions")
    composing = set(_SESSION_VERBS) | {"session_state_and_duration", *_WINDOW_VERBS}
    assert composing <= set(sessions_reported), "every verb composing telemetry_sessions reports its conflict"
    _, transcripts_reported = _conflict_report("telemetry_transcripts")
    assert "session_process_event_rollup" not in transcripts_reported and "agent_run_token_rollup" not in transcripts_reported
    assert "session_state_and_duration" in transcripts_reported, "the state verbs compose transcripts"

    assert _shared_observation_pair() == []

    bump = PARSER_VERSION + 1
    with chain.loaded(_TREE) as (con, rows, ids):
        as_of = chain.as_of_rule(chain.last_event(rows, ids["root"]))[0]
        baseline = _snapshot(con, ids, {s: [as_of] for s in ids})
        for table, batch in result.batches:
            bumped = [dict(r, parser_version=bump) for r in batch]
            if bumped:
                done = chain.append_events(
                    con,
                    chain.specs()[table],
                    bumped,
                    tenant_id=chain.TENANT,
                    project_id=chain.PROJECT,
                    catalog=read_db.ALIAS,
                    now=chain.latest_event(result) + timedelta(seconds=1),
                )
                assert done.inserted == len(bumped), table
        bumped_rows = chain.stored_rows(con)
        for verb, params in _bound_calls(ids, as_of):
            got = chain.run_verb(con, verb, **params)
            assert chain.canonical(got) == chain.canonical(chain.run_oracle(bumped_rows, verb, **params)), (verb, params)
        after = _snapshot(con, ids, {s: [as_of] for s in ids})
        for key, rows_after in after.items():
            for row in rows_after:
                assert row["generations"] == [{"producer": "claude_code", "parser_version": bump}], key
            stripped = [{k: v for k, v in r.items() if k != "generations"} for r in rows_after]
            assert stripped == [{k: v for k, v in r.items() if k != "generations"} for r in baseline[key]], key

    with chain.loaded(_TREE) as (con, rows, ids):
        sessions = [dict(r, parser_version=bump) for r in result.sessions]
        chain.append_events(
            con,
            chain.specs()["telemetry_sessions"],
            sessions,
            tenant_id=chain.TENANT,
            project_id=chain.PROJECT,
            catalog=read_db.ALIAS,
            now=chain.latest_event(result) + timedelta(seconds=1),
        )
        lone = chain.stored_rows(con)
        for sid in ids.values():
            for verb in (
                "agent_run_token_rollup",
                "session_process_event_rollup",
                "session_friction_rollup",
                "model_call_cost_estimate",
            ):
                got = chain.run_verb(con, verb, session_id=sid)
                assert chain.canonical(got) == chain.canonical(chain.run_oracle(lone, verb, session_id=sid)), verb
            (tokens,) = chain.run_verb(con, "agent_run_token_rollup", session_id=sid)
            assert tokens["model_call_count"] == 0 and tokens["tokens_input_total"] is None, (
                "the other tables' rows are retired"
            )


@pytest.mark.integration
@pytest.mark.parametrize("entry", _entries())
def test_golden_fixtures_reconcile_on_local_ducklake(entry: dict, tmp_path: Any) -> None:
    """The step 4 expectations on a real local DuckLake catalog (partitioned, inlining off, UTC), one tree entry per case."""
    assert _reconcile_entry(entry, "lake", tmp_path) == []
