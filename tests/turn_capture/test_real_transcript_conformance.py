"""Conformance against REAL Claude Code transcripts on the implementer's machine (operator choice 2026-09-28).

pytest.mark.integration. Nothing from the transcripts is committed (the repository is public): failure messages and the
printed report carry counts, types, booleans and hashed session ids only, never content. The raw walk below is
independent of src.turn_capture.transcript / streams. Transcripts are read from TURN_CAPTURE_REAL_TRANSCRIPTS (an
os.pathsep-separated list of root .jsonl files or directories) else ~/.claude/projects/*/*.jsonl, and snapshotted to a
temp dir first so a live, growing transcript cannot change between passes. The test FAILS when none exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from src.telemetry.append import prepare_batch
from src.telemetry.identity import canonical_ref
from src.telemetry.timestamps import TimestampError, epoch_ms, parse_iso8601_utc
from src.turn_capture.record_turn import record_turn
from src.turn_capture.scrub import scrub_text
from src.turn_capture.transcript import FsTree
from tests.fixtures.telemetry_blob_fakes import as_written
from tests.fixtures.turn_capture_ducklake import create_tables, load_specs, open_local_lake
from tests.fixtures.turn_capture_real import PrefixTree, discover, raw_inherited, snapshot

pytestmark = pytest.mark.integration

PROJECT_REF = "real/conformance"
MAX_FULL_CUT_LINES = 3000
USAGE = (
    ("tokens_input", "input_tokens"),
    ("tokens_output", "output_tokens"),
    ("tokens_cache_read", "cache_read_input_tokens"),
    ("tokens_cache_creation", "cache_creation_input_tokens"),
)


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:10]


def raw_lines(path: Path, limit: int | None = None) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """Newline-terminated JSON-object lines (independent of the producer's reader) and the malformed count."""
    records, malformed = [], 0
    for index, raw in enumerate(path.read_bytes().split(b"\n")[:-1]):
        if limit is not None and index >= limit:
            break
        try:
            data = json.loads(raw)
        except ValueError:
            malformed += 1
            continue
        if isinstance(data, dict):
            records.append((index, data))
        else:
            malformed += 1
    return records, malformed


def own_records(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """The records a stream wrote itself: a fork's copied head is dropped (rule K-1, read independently)."""
    skip, _ = raw_inherited(path)
    return [(i, d) for i, d in raw_lines(path)[0] if i not in skip]


def _timed(d: dict[str, Any]) -> bool:
    try:
        return isinstance(d.get("uuid"), str) and d["uuid"] != "" and parse_iso8601_utc(d["timestamp"]) is not None
    except (KeyError, TypeError, TimestampError):
        return False


def _blocks(d: dict[str, Any]) -> list[Any]:
    content = (d.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


class _Walk:
    """Independent per-stream expectations from the raw records (spans by promptId and resume boundaries)."""

    def __init__(self) -> None:
        self.n: Counter[str] = Counter()
        self.cur: str | None = None
        self.turn = 0
        self.seen: set[str] = set()
        self.uses: dict[str, int] = {}
        self.answered: set[str] = set()
        self.responses: dict[tuple[int, str], dict[str, Any]] = {}
        self.in_group = False
        self.assistant_turns: set[int] = set()
        self.owner_final: dict[tuple[int, str], bool] = {}
        self.seg_assistant = False

    def attachment(self, d: dict[str, Any]) -> None:
        name = str((d.get("attachment") or {}).get("hookName") or "")
        if name.startswith("SessionStart:resume") and not self.in_group:
            self.in_group, self.cur = True, None
            self.n["segment_closes"] += self.seg_assistant
            self.seg_assistant = False

    def results(self, blocks: list[Any]) -> None:
        for b in blocks:
            tid = b.get("tool_use_id") if isinstance(b, dict) and b.get("type") == "tool_result" else None
            if not isinstance(tid, str) or not tid or tid in self.answered:
                continue
            self.answered.add(tid)
            if self.cur is not None and self.uses.get(tid, self.turn) == self.turn:
                self.n["closes"] += 1

    def prompt(self, d: dict[str, Any]) -> None:
        content, pid = (d.get("message") or {}).get("content"), d.get("promptId")
        if d.get("sourceToolUseID") or d.get("turnCompanion") or not (isinstance(pid, str) and pid):
            return
        if not isinstance(content, (str, list)) or (pid in self.seen and pid != self.cur):
            return
        if pid != self.cur:
            self.cur, self.turn = pid, self.turn + 1
            self.seen.add(pid)

    def assistant(self, d: dict[str, Any]) -> None:
        message = d.get("message") or {}
        if not (isinstance(message.get("id"), str) and message["id"] and isinstance(message.get("content"), list)):
            return
        if self.cur is None or (message.get("model") == "<synthetic>" and d.get("isApiErrorMessage") is not True):
            return
        self.assistant_turns.add(self.turn)
        self.seg_assistant = True
        if d.get("isApiErrorMessage") is True:
            return
        usage = message.get("usage")
        self.responses[(self.turn, message["id"])] = usage if isinstance(usage, dict) else {}
        self.owner_final[(self.turn, message["id"])] = isinstance(message.get("stop_reason"), str) or (
            d.get("isAbortedMidStream") is True
        )
        for b in _blocks(d):
            if (
                isinstance(b, dict)
                and b.get("type") == "tool_use"
                and isinstance(b.get("id"), str)
                and b["id"] not in self.uses
            ):
                self.uses[b["id"]] = self.turn
                self.n["opens"] += 1

    def corrected_output(self, spawn_usage: dict[str, Any] | None) -> int:
        """US-3 from the raw parent spawn result: the output-token delta for the first turn's last, non-final response."""
        keys = [k for k in self.responses if k[0] == 1]
        if spawn_usage is None or not keys or self.owner_final[keys[-1]]:
            return 0
        usage = self.responses[keys[-1]]
        inputs = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        if not all(type(spawn_usage.get(k)) is int and spawn_usage.get(k) == usage.get(k) for k in inputs):
            return 0
        new = spawn_usage.get("output_tokens")
        return (new if type(new) is int else 0) - (
            usage.get("output_tokens") if type(usage.get("output_tokens")) is int else 0
        )

    def finish(self, spawn_usage: dict[str, Any] | None = None) -> Counter[str]:
        if not self.assistant_turns:
            return Counter()
        self.n["due"] = 1
        self.n["turns"], self.n["model_calls"] = len(self.seen), len(self.responses)
        self.n["synthetic"] = sum(1 for t in self.uses if t not in self.answered)
        for col, key in USAGE:
            self.n[col] = sum(u.get(key, 0) for u in self.responses.values() if type(u.get(key, 0)) is int)
        self.n["tokens_output"] += self.corrected_output(spawn_usage)
        return self.n


def walk(records: list[tuple[int, dict[str, Any]]], spawn_usage: dict[str, Any] | None = None) -> Counter[str]:
    w = _Walk()
    for _, d in records:
        kind = d.get("type")
        if kind not in ("user", "assistant", "attachment") or not _timed(d):
            continue
        if kind == "attachment":
            w.attachment(d)
            continue
        w.in_group = False
        blocks = _blocks(d)
        if kind == "user" and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
            w.results(blocks)
        elif kind == "user":
            w.prompt(d)
        else:
            w.assistant(d)
    return w.finish(spawn_usage)


def _stream_files(root: Path) -> dict[str, Path]:
    files = {root.stem: root}
    for path in sorted((root.parent / root.stem / "subagents").glob("agent-*.jsonl")):
        files[f"{root.stem}:{path.stem[len('agent-') :]}"] = path
    return files


def spawn_usages(root: Path) -> dict[str, dict[str, Any]]:
    """agent id -> the usage dict of its sync spawn result, read from every stream file's raw records."""
    out: dict[str, dict[str, Any]] = {}
    for path in _stream_files(root).values():
        for _, d in own_records(path):
            info = d.get("toolUseResult") if d.get("type") == "user" else None
            if isinstance(info, dict) and isinstance(info.get("agentId"), str) and not info.get("isAsync"):
                if isinstance(info.get("usage"), dict):
                    out[info["agentId"]] = info["usage"]
    return out


def _keyed(result: Any) -> dict[tuple[str, str], dict[str, Any]]:
    return {(t, r["external_ref"]): r for t, rows in result.batches for r in rows}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    sources = discover()
    if not sources:
        pytest.fail(
            "no real Claude Code transcripts found: set TURN_CAPTURE_REAL_TRANSCRIPTS or run on a machine that has them"
        )
    out = []
    for root in snapshot(sources, tmp_path_factory.mktemp("real")):
        started = time.perf_counter()
        result = record_turn(FsTree(root), None, project_ref=PROJECT_REF, session_final=True)
        out.append({"root": root, "sid": root.stem, "result": result, "secs": time.perf_counter() - started})
    return out


def _live(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in runs if r["result"].next_cursor is not None]


def test_output_reconciles_with_an_independent_raw_walk(runs: list[dict[str, Any]]) -> None:
    live = _live(runs)
    assert live, "every real transcript deferred (no parseable timestamp)"
    exercised = 0
    for run in live:
        sid, result, tag = run["sid"], run["result"], _hash(run["sid"])
        malformed = 0
        for stream, path in _stream_files(run["root"]).items():
            records, bad = raw_lines(path)
            malformed += bad
            agent = stream.split(":", 1)[1] if ":" in stream else None
            want = walk(own_records(path), spawn_usages(run["root"]).get(agent) if agent else None)
            obs = [r for r in result.observations if r["session_ref"] == stream]
            opens = [r for r in obs if r["observation_type"] == "tool_call" and r["event_kind"] == "open"]
            closes = [r for r in obs if r["observation_type"] == "tool_call" and r["event_kind"] == "close"]
            calls = [r for r in obs if r["observation_type"] == "model_call"]
            got = {
                "turns": sum(1 for r in obs if r["observation_type"] == "turn" and r["event_kind"] == "open"),
                "model_calls": len(calls), "opens": len(opens),
                **{col: sum(r.get(col, 0) for r in calls) for col, _ in USAGE},
            }  # fmt: skip
            for key, value in got.items():
                assert value == want[key], f"{tag} stream {_hash(stream)}: {key} produced {value} != raw {want[key]}"
            if agent is None and want["due"]:
                session_closes = [r for r in result.sessions if r["event_kind"] == "close" and r["entity_ref"] == stream]
                assert len(session_closes) == want["segment_closes"] + 1, f"{tag}: segment closes != resume boundaries + eof"
            assert len({r["entity_ref"] for r in closes}) == len(closes), f"{tag}: a tool_call closed twice"
            assert len(closes) == want["closes"] + want["synthetic"], (
                f"{tag} stream {_hash(stream)}: closes != in-span + synthetic"
            )
            exercised += bool(opens) and ":" in "".join(_stream_files(run["root"]))
        assert result.diagnostics.get("malformed_lines", 0) == malformed, f"{tag}: malformed lines are counted and consumed"
        metas = list((run["root"].parent / sid / "subagents").glob("agent-*.meta.json"))
        wanted = {json.loads(m.read_text(encoding="utf-8")).get("toolUseId") for m in metas}
        opened = {r["entity_ref"] for r in result.agents if r["event_kind"] == "open"}
        assert opened <= wanted, f"{tag}: an agents open without a meta.json toolUseId"
    assert exercised, "no root transcript with a tool call and a sub-agent was exercised"


def test_rows_are_unique_valid_and_scrubbed(runs: list[dict[str, Any]]) -> None:
    generic = Counter()
    for run in _live(runs):
        result, tag = run["result"], _hash(run["sid"])
        starts = {r["session_started_at"] for _, rows in result.batches for r in rows}
        if starts:
            assert len(starts) == 1, f"{tag}: {len(starts)} session_started_at values in one tree"
        else:
            assert not walk(own_records(run["root"]))["due"], f"{tag}: a closed assistant turn emitted no row"
        seen = set()
        for table, rows in result.batches:
            for r in rows:
                key = (table, r["external_ref"])
                assert key not in seen, f"{tag}: duplicate (table, external_ref) in {table}"
                seen.add(key)
                for field in (
                    "external_ref",
                    "entity_ref",
                    "session_ref",
                    "parent_observation_ref",
                    "observation_ref",
                    "parent_session_ref",
                ):
                    if r.get(field) is not None:
                        canonical_ref(r[field])
                for value in r.values():
                    if isinstance(value, str):
                        assert scrub_text(value).counts == {}, (
                            f"{tag}: a scrub pattern still matches a stored value in {table}"
                        )
                        generic.update(["long_token"] * len([w for w in value.split() if len(w) >= 40 and w.isalnum()]))
        for r in result.transcripts:
            if "content" in r:
                assert r["content_bytes"] == len(r["content"].encode()), f"{tag}: content_bytes disagrees"
            else:
                assert r["content_omitted_reason"] == "oversize" and "content_uri" not in r, f"{tag}: a payload-less row"
    print(f"\nconformance: generic long alphanumeric tokens in stored text (count only): {sum(generic.values())}")


def _stream_closed(run: dict[str, Any], record: dict[str, Any]) -> bool:
    """A tool_result emits a row only once its turn is closed; the final pass closes every turn but an orphan's."""
    return run["result"].observations != [] and any(
        r["external_ref"].startswith(f"{record.get('uuid')}#") for r in run["result"].observations
    )


_SAVED = re.compile(r"Output has been saved to (\S+)")


def _persisted_name(info: Any) -> tuple[str, bool]:
    """(sidecar basename, via the MCP-overflow string) from an independent reading of the raw toolUseResult."""
    if isinstance(info, dict):
        return os.path.basename(str(info.get("persistedOutputPath") or "").replace("\\", "/")), False
    match = _SAVED.search(info) if isinstance(info, str) else None
    return (os.path.basename(match.group(1).replace("\\", "/")), True) if match else ("", False)


def test_persisted_outputs_have_one_full_row_and_results_store_the_visible_text(runs: list[dict[str, Any]]) -> None:
    checked = mcp = unmatched = 0
    for run in _live(runs):
        tag = _hash(run["sid"])
        rows = {r["external_ref"]: r for r in run["result"].transcripts}
        for path in _stream_files(run["root"]).values():
            for _, d in own_records(path):
                info = d.get("toolUseResult")
                name, via_string = _persisted_name(info)
                unmatched += isinstance(info, str) and not name
                readable = bool(name) and (run["root"].parent / run["sid"] / "tool-results" / name).is_file()
                for k, block in enumerate(_blocks(d)):
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    visible, full = rows.get(f"{d.get('uuid')}#{k}"), rows.get(f"{d.get('uuid')}#{k}/full")
                    if visible is not None:
                        assert visible["content_truncated"] is False, f"{tag}: a tool_result row marked truncated"
                    elif isinstance(block.get("content"), str) and block["content"] and _stream_closed(run, d):
                        raise AssertionError(f"{tag}: a tool_result with in-transcript text has no tool_result row")
                    if readable and (run["root"].parent / run["sid"] / "tool-results" / name).stat().st_size > 0:
                        checked += 1
                        mcp += via_string
                        assert full is not None, f"{tag}: a readable persisted output has no tool_output or omission row"
                        assert full["purpose"] == "tool_output" and full["origin"] == "tool"
                        if via_string and visible is not None:
                            assert visible["origin"] == "harness", f"{tag}: an MCP-overflow result is harness text"
                    else:
                        assert full is None, f"{tag}: a tool_output row without a readable persisted output"
    print(
        f"\nconformance: {checked} readable persisted outputs ({mcp} MCP overflow) each have one full row; "
        f"{unmatched} unmatched string toolUseResult"
    )


def test_pin_is_the_minimum_raw_timestamp_and_every_row_passes_the_kernel_rules(runs: list[dict[str, Any]]) -> None:
    from tests.fixtures.turn_capture_corpus import PROJECT, TENANT  # noqa: PLC0415

    specs = load_specs()
    for run in _live(runs):
        tag = _hash(run["sid"])
        stamps = [
            parse_iso8601_utc(d["timestamp"])
            for _, d in own_records(run["root"])
            if isinstance(d.get("timestamp"), str) and _stamp(d)
        ]
        assert run["result"].next_cursor.session_started_at_ms == epoch_ms(min(stamps)), (
            f"{tag}: the pin is not the earliest timestamp"
        )
        for (table, rows), _ in zip(run["result"].batches, range(4), strict=True):
            if rows:  # the core emits above-threshold content inline; spilling is the transport's job (as_written)
                written = as_written(rows, specs["telemetry_transcripts"].rules.content.threshold)
                prepare_batch(specs[table], written, tenant_id=TENANT, project_id=PROJECT)


def _stamp(d: dict[str, Any]) -> bool:
    try:
        parse_iso8601_utc(d["timestamp"])
        return True
    except (TimestampError, KeyError, TypeError):
        return False


def test_cross_tree_uniqueness(runs: list[dict[str, Any]]) -> None:
    trees = _live(runs)
    owner: dict[tuple[str, str], str] = {}
    for run in trees:
        for key in _keyed(run["result"]):
            other = owner.setdefault(key, run["sid"])
            assert other == run["sid"], (
                f"STOP: (table, external_ref) recurs across root trees {_hash(other)} and {_hash(run['sid'])} -- "
                "file a contract-risk rec against rec-4149's scope and return to /plan"
            )
    if len(trees) < 2:
        print(f"\nconformance: cross-tree uniqueness unverified ({len(trees)} root trees)")


def _cut_points(root: Path) -> list[int]:
    records, _ = raw_lines(root)
    total = root.read_bytes().count(b"\n")
    prompts = [i + 1 for i, d in records if d.get("type") == "user" and d.get("promptId") and not d.get("sourceToolUseID")]
    step = 50 if total <= MAX_FULL_CUT_LINES else max(50, total // 60)
    boundary = raw_inherited(root)[1]
    return sorted({c for c in (*prompts, *range(step, total, step), total, boundary) if c >= boundary and c > 0})


def test_incremental_equals_full_at_every_prompt_and_line_cut(runs: list[dict[str, Any]]) -> None:
    for run in _live(runs):
        tag, full = _hash(run["sid"]), _keyed(run["result"])
        cursor, seen, cuts = None, {}, _cut_points(run["root"])
        for index, limit in enumerate(cuts):
            step = record_turn(
                PrefixTree(run["root"], limit), cursor, project_ref=PROJECT_REF, session_final=index == len(cuts) - 1
            )
            cursor = step.next_cursor if step.next_cursor is not None else cursor
            for key, row in _keyed(step).items():
                assert key not in seen, f"{tag}: {key[0]} row emitted twice by incremental capture (cut {limit})"
                assert row == full[key], f"{tag}: {key[0]} row differs between incremental (cut {limit}) and full parse"
                seen[key] = row
        assert set(seen) == set(full), f"{tag}: incremental emitted {len(seen)} rows, full parse {len(full)}"
        forks = " (fork boundary leg proven)" if raw_inherited(run["root"])[1] else ""
        print(f"\nconformance: {tag}: {len(cuts)} incremental cuts (every prompt plus a line stride){forks}")


def test_finalized_prefix_at_true_session_ends_is_a_row_subset(runs: list[dict[str, Any]]) -> None:
    for run in _live(runs):
        tag, full = _hash(run["sid"]), _keyed(run["result"])
        records, _ = raw_lines(run["root"])
        ends, in_group = [], False
        for index, d in records:
            is_resume = d.get("type") == "attachment" and str((d.get("attachment") or {}).get("hookName") or "").startswith(
                "SessionStart:resume"
            )
            if is_resume and not in_group and index > 0 and index >= raw_inherited(run["root"])[1]:
                ends.append(index)
            in_group = is_resume if d.get("type") == "attachment" else False
        for limit in ends:
            prefix = _keyed(record_turn(PrefixTree(run["root"], limit), None, project_ref=PROJECT_REF, session_final=True))
            for key, row in prefix.items():
                if key[0] == "telemetry_sessions" and row["event_kind"] == "close":
                    continue
                assert key in full and full[key] == row, (
                    f"{tag}: a row of the finalized prefix at a resume boundary is absent or changed in the full parse"
                )


def test_loads_into_local_ducklake_with_every_fk_resolving(
    runs: list[dict[str, Any]], tmp_path: Path, _allow_network_for_integration: None
) -> None:
    from src.telemetry.append import append_events  # noqa: PLC0415
    from tests.fixtures.turn_capture_corpus import PROJECT, TENANT  # noqa: PLC0415

    live = _live(runs)
    specs = load_specs()
    con = open_local_lake(tmp_path)
    create_tables(con, specs)
    skipped = 0
    for table, position in (
        ("telemetry_observations", 0),
        ("telemetry_transcripts", 1),
        ("telemetry_agents", 2),
        ("telemetry_sessions", 3),
    ):
        rows = []
        for run in live:
            for r in run["result"].batches[position][1]:
                if table == "telemetry_transcripts" and "content" in r and len(r["content"].encode()) > 65536:
                    skipped += 1
                    continue
                rows.append(r)
        for start in range(0, len(rows), 500):
            append_events(con, specs[table], rows[start : start + 500], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
    checks = (
        ("telemetry_observations", "session_id", "telemetry_sessions", "session_id"),
        ("telemetry_transcripts", "session_id", "telemetry_sessions", "session_id"),
        ("telemetry_transcripts", "observation_id", "telemetry_observations", "observation_id"),
        ("telemetry_agents", "session_id", "telemetry_sessions", "session_id"),
        ("telemetry_observations", "parent_observation_id", "telemetry_observations", "observation_id"),
        ("telemetry_sessions", "parent_session_id", "telemetry_sessions", "session_id"),
    )
    for child, column, parent, key in checks:
        sql = (
            f"SELECT count(*) FROM lake.{child} c WHERE c.{column} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM lake.{parent} p WHERE p.{key} = c.{column})"
        )
        assert con.execute(sql).fetchone()[0] == 0, f"dangling {child}.{column}"
    print(f"\nconformance: loaded {len(live)} root trees; {skipped} transcript rows over 65,536 bytes excluded")
    con.close()


def test_drift_dashboard(runs: list[dict[str, Any]]) -> None:
    unknown: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    stops: Counter[str] = Counter()
    null_stops: Counter[str] = Counter()
    workflows: Counter[str] = Counter()
    for run in runs:
        result = run["result"]
        unknown.update({k: v for k, v in result.diagnostics.items() if k.startswith("unknown_")})
        outcomes.update(
            r["outcome"] for r in result.observations if r["observation_type"] == "tool_call" and r["event_kind"] == "close"
        )
        workflows.update(r["workflow"] for r in result.sessions if r["event_kind"] == "open")
        for stream, path in _stream_files(run["root"]).items():
            for _, d in raw_lines(path)[0]:
                is_assistant = d.get("type") == "assistant"
                reason = (d.get("message") or {}).get("stop_reason") if is_assistant else None
                stops[str(reason)] += is_assistant
                null_stops["subagent" if ":" in stream else "root"] += is_assistant and reason is None
        print(f"\nconformance: tree {_hash(run['sid'])}: parsed in {run['secs']:.2f}s, diagnostics {dict(result.diagnostics)}")
    print(
        f"\nconformance dashboard: unknown types {dict(unknown)}; outcomes {dict(outcomes)}; "
        f"stop_reasons {dict(stops)}; null stop_reason by stream kind {dict(null_stops)}; workflows {dict(workflows)}"
    )
    assert runs
