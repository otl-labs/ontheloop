"""record_turn: the cursor-driven capture pass (Decision 207 R7 producer obligations).

The emission set is a function of the tree and the cursor's recorded finalization cuts: the union, in order, of the
rows built from the tree truncated at each recorded cut (parsed as final) and from the tree at its end, keyed by
(table, external_ref) with the first build kept. A pass emits the rows of that set that the cursor's own set (as of
its last pass) did not hold, so a finalized tree that later grows (a resumed session) re-emits nothing already
emitted and incremental capture equals the set recomputed from the cuts. Data batches first, ONE telemetry_sessions
batch LAST. No file writes, no network, no writer call: the caller persists next_cursor only after every batch was
written.

The session pin is the EARLIEST timestamp among the session's own records. A record appended later with a timestamp
earlier than the persisted pin re-pins only while the persisted cursor's emission set holds no row (nothing was
emitted under the old pin); once a row was emitted it raises PinOrderError, the producer halt: never re-pinned, never
emitted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from src.telemetry.timestamps import epoch_ms
from src.turn_capture.cursor import CaptureCursor, CursorError
from src.turn_capture.observations import build_observations
from src.turn_capture.sessions import build_agents, build_sessions, stream_due
from src.turn_capture.streams import ParsedTree, parse_tree
from src.turn_capture.transcript import ROOT, TranscriptTree, read_lines, session_pin
from src.turn_capture.transcripts import Ctx, Sidecars, build_transcripts

PRODUCER = "claude_code"
PARSER_VERSION = 7
PRODUCER_VERSION = "turn-capture-7"
FULL_OUTPUT_CAP_BYTES = 8388608
BILLING_SHAPES = ("fixed_non_rollover_allowance",)
TABLES = ("telemetry_observations", "telemetry_transcripts", "telemetry_agents", "telemetry_sessions")


class PinOrderError(CursorError):
    """A record earlier than the persisted session pin appeared after a row was emitted under that pin: a producer halt."""


@dataclass(frozen=True)
class CaptureResult:
    observations: list[dict[str, Any]]
    transcripts: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    next_cursor: CaptureCursor | None
    diagnostics: dict[str, int]

    @property
    def batches(self) -> list[tuple[str, list[dict[str, Any]]]]:
        """Write order: data tables first, the single sessions batch last."""
        return list(zip(TABLES, (self.observations, self.transcripts, self.agents, self.sessions), strict=True))


def _build_all(parsed: ParsedTree, ctx: Ctx, sidecars: Sidecars, diag: Counter[str]) -> dict[str, list[dict[str, Any]]]:
    built = {
        "telemetry_observations": build_observations(parsed, ctx, diag, sidecars),
        "telemetry_transcripts": build_transcripts(parsed, ctx, sidecars, diag),
        "telemetry_agents": build_agents(parsed, ctx, diag),
        "telemetry_sessions": build_sessions(parsed, ctx, diag),
    }
    deferred = {s.session_ref for s in parsed.streams.values() if not stream_due(s)}
    for table, rows in built.items():
        ref_field = "entity_ref" if table == "telemetry_sessions" else "session_ref"
        seen: set[str] = set()
        unique = []
        for row in rows:
            if row.get(ref_field) in deferred:
                continue
            if row["external_ref"] in seen:
                diag["duplicate_refs"] += 1
                continue
            seen.add(row["external_ref"])
            unique.append(row)
        built[table] = unique
    return built


def _pin_moment(session_started_at_ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=session_started_at_ms)


Key = tuple[tuple[tuple[str, int], ...], bool]
Rows = dict[tuple[str, str], dict[str, Any]]


class _Emitter:
    """One pass's parses and builds, memoised per (limits, final) and sharing one Sidecars instance (rules R2-2)."""

    def __init__(self, tree: TranscriptTree, ctx: Ctx, sidecars: Sidecars, diag: Counter[str]) -> None:
        self._tree, self._ctx, self._sidecars, self.diag = tree, ctx, sidecars, diag
        self._built: dict[Key, dict[str, list[dict[str, Any]]]] = {}
        self.parses: dict[Key, ParsedTree] = {}

    def parse(self, limits: dict[str, int] | None, final: bool) -> tuple[Key, ParsedTree]:
        key = (tuple(sorted((limits or {}).items())), final)
        if limits is None:
            parsed = parse_tree(self._tree, None, final, frontier=not final)
            key = (tuple(sorted(parsed.lines_total.items())), final)
            if key not in self.parses:
                self.parses[key] = parsed
        elif key not in self.parses:
            self.parses[key] = parse_tree(self._tree, limits, final, frontier=not final)
        return key, self.parses[key]

    def built(self, key: Key, throwaway: bool = True) -> dict[str, list[dict[str, Any]]]:
        if key not in self._built:
            diag = Counter() if throwaway else self.diag
            self._built[key] = _build_all(self.parses[key], self._ctx, self._sidecars, diag)
        return self._built[key]

    def emit(self, cuts: list[dict[str, int]], limits: dict[str, int], final: bool, diag: Counter[str] | None) -> Rows:
        """The union, in order, of the cut parses (final) and the tree at *limits*; the first build of a key wins."""
        steps = [(cut, True) for cut in cuts]
        if not steps or steps[-1] != (limits, final):
            steps.append((limits, final))
        out: Rows = {}
        diverged: set[tuple[str, str]] = set()
        for lim, fin in steps:
            key, _ = self.parse(lim, fin)
            for table, rows in self.built(key).items():
                for row in rows:
                    ref = (table, row["external_ref"])
                    if ref not in out:
                        out[ref] = row
                    elif out[ref] != row and ref not in diverged:
                        diverged.add(ref)
                        if diag is not None:
                            diag["cut_content_divergence"] += 1
        return out


def _close_refs(rows: Rows) -> dict[tuple[str, str], set[str]]:
    """The distinct close external_refs per (observation_type, entity_ref) of a turn or tool_call."""
    out: dict[tuple[str, str], set[str]] = {}
    for (table, ref), row in rows.items():
        if (
            table == "telemetry_observations"
            and row["event_kind"] == "close"
            and row["observation_type"] in ("turn", "tool_call")
        ):
            out.setdefault((row["observation_type"], row["entity_ref"]), set()).add(ref)
    return out


def _count_closes(prior: Rows, current: Rows, end_tree: Rows, diag: Counter[str]) -> None:
    """Rule DG-5: reanchored_close counts entities whose distinct close rows across the prior set and this pass's set
    outnumber their close rows in the pass's own end-of-tree parse; multi_close_turns counts turns with several there."""
    ended = _close_refs(end_tree)
    for (otype, entity), refs in _close_refs({**prior, **current}).items():
        if len(refs) > len(ended.get((otype, entity), ())):
            diag[f"reanchored_close:{otype}"] += 1
    diag["multi_close_turns"] += sum(1 for (otype, _), refs in ended.items() if otype == "turn" and len(refs) > 1)


def _repin(tree: TranscriptTree, cursor: CaptureCursor, started_ms: int, cap: int) -> CaptureCursor:
    """Re-pin *cursor* to an earlier record only if its emission set, as persisted, holds no row."""
    ctx = Ctx(
        cursor.producer,
        PRODUCER_VERSION,
        cursor.parser_version,
        _pin_moment(cursor.session_started_at_ms),
        cursor.billing_shape,
        cap,
    )
    emitter = _Emitter(tree, ctx, Sidecars(tree, cursor), Counter())
    if emitter.emit(cursor.finalize_cuts, cursor.lines_consumed, cursor.finalized, None):
        raise PinOrderError("a record earlier than the persisted session pin appeared after a row was emitted under it")
    return replace(cursor, session_started_at_ms=started_ms)


def record_turn(
    tree: TranscriptTree,
    cursor: CaptureCursor | None,
    *,
    project_ref: str,
    billing_shape: str = "fixed_non_rollover_allowance",
    session_final: bool = False,
    resend_tree: bool = False,
) -> CaptureResult:
    """*resend_tree* re-emits every row of the tree, keeping the cursor's pins and finalization cuts (a catalog reset)."""
    if billing_shape not in BILLING_SHAPES:
        raise ValueError(f"billing_shape must be one of {BILLING_SHAPES}")
    if not project_ref:
        raise ValueError("project_ref is required")
    root = tree.read(ROOT)
    pin = session_pin(read_lines(root or b""))
    if pin is None:
        return CaptureResult([], [], [], [], None, {})
    started_ms = epoch_ms(pin)
    cap = FULL_OUTPUT_CAP_BYTES
    base = None
    matches = False
    if cursor is not None:
        if started_ms < cursor.session_started_at_ms:
            cursor = _repin(tree, cursor, started_ms, cap)
        cursor.check_pins(
            root_session_ref=tree.session_id,
            project_ref=project_ref,
            billing_shape=billing_shape,
            session_started_at_ms=started_ms,
        )
        matches = not resend_tree and (cursor.producer, cursor.parser_version) == (PRODUCER, PARSER_VERSION)
        base = cursor if matches else cursor.reset(PARSER_VERSION)
    ctx = Ctx(PRODUCER, PRODUCER_VERSION, PARSER_VERSION, pin, billing_shape, cap)
    diag: Counter[str] = Counter()
    sidecars = Sidecars(tree, base)
    emitter = _Emitter(tree, ctx, sidecars, diag)
    key, parsed = emitter.parse(None, session_final)
    end = dict(parsed.lines_total)
    for held in (base.lines_consumed, *base.finalize_cuts) if base is not None else ():
        for stream_key, count in held.items():
            end.setdefault(stream_key, count)
    emitter.built(key, throwaway=False)
    cuts = [dict(cut) for cut in base.finalize_cuts] if base else []
    if session_final and (not cuts or cuts[-1] != end):
        cuts.append(end)
    current = emitter.emit(cuts, end, session_final, diag)
    prior: Rows = {}
    if matches and base is not None:
        prior = emitter.emit(base.finalize_cuts, base.lines_consumed, base.finalized, None)
    fresh: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    for (table, ref), row in current.items():
        if (table, ref) not in prior:
            fresh[table].append(row)
        elif prior[(table, ref)] != row:
            diag["prior_content_divergence"] += 1
    end_tree = {("telemetry_observations", row["external_ref"]): row for row in emitter.built(key)["telemetry_observations"]}
    _count_closes(prior, current, end_tree, diag)
    diag.update(parsed.diag)
    diag["parse_passes"] = len(emitter.parses)
    diag["sidecar_reads"] = sidecars.reads
    nxt = CaptureCursor(
        PRODUCER,
        PARSER_VERSION,
        tree.session_id,
        project_ref,
        billing_shape,
        started_ms,
        lines_consumed=end,
        sidecars={**(base.sidecars if base else {}), **sidecars.used},
        finalized=session_final,
        finalize_cuts=cuts,
    )
    return CaptureResult(
        fresh[TABLES[0]], fresh[TABLES[1]], fresh[TABLES[2]], fresh[TABLES[3]], nxt, dict(sorted(diag.items()))
    )
