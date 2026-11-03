"""The in-session source check: what the warehouse stored for a captured tree against a fresh re-parse of its transcript.

One check runs in the already-detached Stop runner (or on demand from the `verify` command) and never changes a capture
pass's outcome: it is an alarm, never a gate (Decision 210 cl.2). Order, per PLAN-telemetry-in-session-source-check
execution_steps[2]: (1) under the tree lock, load the verify state and the cursor and confirm the scheduling pass is still the
latest; (2) unlocked, build the expected rows over the cursor view, run the transcript ledger and shape legs, then read the
stored tree through the named verbs; (3) re-confirm under the lock that no pass ran; (4) unlocked, compare, run the
stored-row ledger leg and hand the alarm classes to the shared helper; (5) write the diagnostics record and the check's own
verify-state fields under the lock.

Counts only (Decision 101): the diagnostics record, the VERIFY line and the rec text carry counts, class names, column names
and refs, never a row value or transcript content. All logging is disabled for the whole check, and nothing is printed.
"""

from __future__ import annotations

import contextlib
import io
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.aws_profile import resolve_aws_profile
from scripts.telemetry_capture import cursor_tree, state, verify_due
from scripts.telemetry_capture.state import CHECK, OPERATOR, CaptureState, state_dir
from scripts.telemetry_capture.transport import Budget
from scripts.telemetry_verify import compare, ledger, rows, shape, stored_ledger
from scripts.telemetry_verify.alarms import AlarmOutcome, raise_alarms
from src.turn_capture.record_turn import PARSER_VERSION, PRODUCER
from src.turn_capture.refs import model_call_ref
from src.turn_capture.transcript import ROOT, FsTree, inherited_lines, read_lines

VERIFY_BUDGET_S = 600.0
MAX_TREE_PAGES = 10
PAGE_ROWS = 2000
MIN_CALL_BUDGET_S = 180.0
VERIFY_INCOMPLETE_STREAK = 3
LOCK_WAIT_S = 30.0
FILE = "scripts/telemetry_capture/verify.py"
OBSERVATIONS = "telemetry_observations"
PER_TREE_SKIPS = frozenset(
    {"no_cursor", "cursor_tail_mismatch", "halted", "parser_version_skip", "catalog_skip", "non_real_project"}
)
BENIGN = PER_TREE_SKIPS | {"busy", "tree_moved", "superseded_by_pass", "no_credentials"}
ALARM_CLASSES = frozenset(
    {*compare.ALARM_CLASSES, "root_absent", *ledger.ALARMS, *shape.ALARMS, *stored_ledger.ALARMS, "verify_incomplete"}
)
DECLARED_CLASSES = frozenset(
    {*ledger.DECLARED, *shape.DECLARED, *stored_ledger.DECLARED, "race_duplicate_rows", "ledger_fork_unmeasured"}
    | {"r7_boundary_divergence"}
)
SHAPE_SPAWNS = ("spawn_equal", "spawn_corrects_nonfinal", "spawn_usage_mismatch")
R7_MASKED = ("column_mismatch", "missing_in_store", "unexpected_in_store")


class TreeCallCap(Exception):
    """The stored tree needs more reader calls than MAX_TREE_PAGES allows."""


class BudgetExhausted(Exception):
    pass


class _Stop(Exception):
    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.token = token


@dataclass
class VerifyResult:
    status: str
    trigger: str
    compare_skipped: str = "none"
    counts: dict[str, int] = field(default_factory=dict)
    columns: dict[str, int] = field(default_factory=dict)
    masked: dict[str, int] = field(default_factory=dict)
    stored_models: list[tuple[int, str, str]] = field(default_factory=list)
    alarms: list[str] = field(default_factory=list)
    reader_calls: int = 0
    response_bytes: int = 0
    elapsed_s: int = 0
    outcome: AlarmOutcome | None = None

    @property
    def completed(self) -> bool:
        return self.status == "completed"


class CountingReader:
    """The reader behind a call counter, a response-byte counter, the tree page cap and the per-call budget floor."""

    def __init__(self, reader: Any, budget: Budget) -> None:
        self._reader, self._budget = reader, budget
        self.calls = self.bytes = self.tree_calls = 0
        self.pages: list[tuple[int, int]] = []

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if self._budget.remaining() < MIN_CALL_BUDGET_S:
            raise BudgetExhausted
        if verb == rows.VERB:
            if self.tree_calls >= MAX_TREE_PAGES:
                raise TreeCallCap
            self.tree_calls += 1
        self.calls += 1
        page = self._reader.named(verb, **params)
        self.bytes += sum(len(str(item.get("row_json") or "")) + 64 for item in page)
        if verb == rows.VERB and page:
            self.pages.append((page[0]["total_rows"], page[0]["roots_in_tree"]))
            if self.pages[0][0] > MAX_TREE_PAGES * PAGE_ROWS:
                raise TreeCallCap
        return page


def credentials_available() -> bool:
    """transport.make_client's credential resolution: a boto3 Session under resolve_aws_profile() with frozen credentials."""
    try:
        import boto3  # noqa: PLC0415

        credentials = boto3.Session(profile_name=resolve_aws_profile()).get_credentials()
        if credentials is None:
            return False
        credentials.get_frozen_credentials()
    except Exception:  # noqa: BLE001 -- profile, STS or credential-chain failure: one fixed answer
        return False
    return True


def acceptance_for(cls: str) -> str:
    return (
        'AWS_PROFILE=agent_platform bin/venv-python -m scripts.telemetry_capture verify --tree "$(ls -t '
        f'~/.claude/projects/*/*.jsonl | head -1)" --no-alarms --assert-zero {cls}'
    )


def closes_of(expected: Any, session_id: str) -> dict[str, str]:
    """Agent id -> the close outcome the expected emission stores."""
    prefix = f"{session_id}:"
    return {
        row["session_ref"][len(prefix) :]: row["outcome"]
        for row in expected.agents
        if row.get("event_kind") == "close" and str(row.get("session_ref", "")).startswith(prefix) and "outcome" in row
    }


def _non_real(project_ref: str) -> bool:
    prefixes = compare.field_semantics()["ops_tables"]["ops_projects"]["derivations"]["purpose"]["prefix_map"]
    return any(project_ref.startswith(prefix) for prefix in prefixes)


def _last_pass(diagnostics: list[dict[str, Any]]) -> dict[str, Any] | None:
    passes = [record for record in diagnostics if record.get("event") != "verify"]
    return passes[-1] if passes else None


def _same_pass(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    def key(record: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
        return None if record is None else tuple(record.get(k) for k in ("event", "at", "outcome"))

    return key(left) == key(right)


def _model_call_refs(result: Any) -> set[str]:
    return {row["external_ref"] for row in result.observations if row.get("observation_type") == "model_call"}


def _record_inputs(
    view: Any, findings: list[ledger.Finding], expected: Any, final_expected: Any
) -> list[stored_ledger.RecordInput]:
    inherited = inherited_lines(read_lines(view.read(ROOT) or b""))
    selected, deferred = _model_call_refs(expected), _model_call_refs(final_expected)
    out = []
    for cut_index, cut_line in sorted({(f.cut_index, f.cut_line) for f in findings}):
        models: dict[str, stored_ledger.ModelRefs] = {}
        for model, items in ledger.prefix_calls(view, cut_line).items():
            groups: dict[str, list[str]] = {"selected": [], "pending": [], "inherited": [], "unemitted": []}
            for stream, line, message_id in items:
                ref = model_call_ref(message_id)
                if stream == ROOT and line in inherited:
                    groups["inherited"].append(ref)
                elif ref in selected:
                    groups["selected"].append(ref)
                else:
                    groups["pending" if ref in deferred else "unemitted"].append(ref)
            models[model] = stored_ledger.ModelRefs(**{name: tuple(refs) for name, refs in groups.items()})
        for finding in findings:
            if finding.cut_index == cut_index and finding.model:
                models.setdefault(finding.model, stored_ledger.ModelRefs())
        out.append(stored_ledger.RecordInput(cut_index, ledger.file_line(view, cut_line) in inherited, models))
    return out


@contextlib.contextmanager
def _silenced() -> Iterator[None]:
    """No logging, stdout or stderr: the reader client's and the portal's messages can carry an ARN or a Function URL host."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous)


class _Check:
    def __init__(
        self,
        transcript_path: Path,
        session_id: str,
        *,
        trigger: str,
        scheduling_record: Mapping[str, Any] | None,
        reader: Any,
        portal: Callable[[str, dict[str, Any]], Any] | None,
        budget: Budget,
        alarms: bool,
        now: float,
        credentials: Callable[[], bool],
        lock_wait_s: float,
    ) -> None:
        self.path, self.session_id, self.trigger, self.scheduling = transcript_path, session_id, trigger, scheduling_record
        self.raw_reader, self.portal, self.budget, self.alarms_on = reader, portal, budget, alarms
        self.now, self.credentials, self.lock_wait_s = now, credentials, lock_wait_s
        self.state = CaptureState(state_dir(transcript_path, session_id))
        self.reader = CountingReader(reader, budget)
        self.result = VerifyResult("completed", trigger)
        self.counts: Counter[str] = Counter()
        self.vstate: dict[str, Any] = {}
        self.catalog: str | None = None
        self.tree_catalog: str | None = None
        self.view_dropped: tuple[str, ...] = ()
        self.seen_pass: dict[str, Any] | None = None
        self.cursor: Any = None
        self.findings: list[ledger.Finding] = []
        self.inputs: list[stored_ledger.RecordInput] = []
        self.ledger_lines: dict[int, int] = {}
        self.stored_ran = False
        self.held_cuts: set[int] = set()
        self.alarm_at: dict[str, float] = {}
        self.started = time.monotonic()

    def _locked(self, work: Callable[[], Any]) -> bool:
        lock = self.state.acquire(min(self.lock_wait_s, self.budget.remaining()))
        if lock is None:
            return False
        try:
            work()
        finally:
            lock.release()
        return True

    def _guard(self) -> None:
        if self.budget.remaining() <= 0:
            raise BudgetExhausted

    def _load(self) -> None:
        loaded = self.state.load_cursor(FsTree(self.path).read(ROOT) or b"")
        held = self.state.verify_state()
        # A lost envelope names no catalog, so the check neither re-keys the verify state nor records a catalog for it.
        lost = loaded.status == state.LOST
        self.vstate = dict(held) if lost else state.verify_view(held, loaded.catalog)
        self.view_dropped = tuple(name for name in state.CATALOG_KEYED if name in held and name not in self.vstate)
        self.tree_catalog = None if lost else loaded.catalog
        self.seen_pass = _last_pass(self.state.diagnostics())
        token = None
        if self.state.halt() is not None:
            token = "halted"
        elif loaded.cursor is None:
            token = "no_cursor" if loaded.status == state.LOST else "cursor_tail_mismatch"
        elif (loaded.cursor.producer, loaded.cursor.parser_version) != (PRODUCER, PARSER_VERSION):
            token = "parser_version_skip"
        elif self.catalog is not None and self.catalog != loaded.catalog:
            token = "catalog_skip"
        elif _non_real(loaded.cursor.project_ref):
            token = "non_real_project"
        elif self.scheduling is not None and not _same_pass(self.seen_pass, self.scheduling):
            token = "superseded_by_pass"
        if token is not None:
            raise _Stop(token)
        self.cursor = loaded.cursor

    def _unchanged(self) -> None:
        if self.state.cursor_on_disk() != self.cursor or not _same_pass(_last_pass(self.state.diagnostics()), self.seen_pass):
            raise _Stop("tree_moved")

    def _transcript_legs(self, view: Any, expected: Any) -> None:
        self.findings = ledger.reconcile_ledger(view)
        if self.findings:
            final = expected if self.cursor.finalized else cursor_tree.expected_rows(view, self.cursor, session_final=True)
            self.inputs = _record_inputs(view, self.findings, expected, final)
        cuts = {f.cut_index: f.cut_line for f in self.findings}
        self.ledger_lines = {index: ledger.file_line(view, cut) for index, cut in sorted(cuts.items())}
        forked = {(r.cut_index, m) for r in self.inputs for m, refs in r.models.items() if r.inherited or refs.inherited}
        for finding in self.findings:
            cls = "ledger_fork_unmeasured" if finding.alarm and (finding.cut_index, finding.model) in forked else finding.cls
            self.counts[cls] += 1
        self.counts["ledger_records"] = len({f.cut_index for f in self.findings if f.cls != "ledger_unavailable"})
        observed = shape.check_shape(view, closes=closes_of(expected, self.cursor.root_session_ref))
        self.counts.update(item.name for item in observed.findings)
        pinned_absent = [name for name, sha in self.cursor.sidecars.items() if sha is None]
        self.counts["sidecar_pinned_absent_present"] += sum(
            FsTree(self.path).sidecar_size(n) is not None for n in pinned_absent
        )
        self.counts["pinned_absent"] = len(pinned_absent)
        self.counts["root_calls"] = len(ledger.last_responses(ledger.read_stream(view, ROOT)))
        self.counts["sync_spawns"] = sum(self.counts[name] for name in SHAPE_SPAWNS)

    def _root_session_id(self) -> str | None:
        cached = self.vstate.get("root_session_id")
        if cached:
            return str(cached)
        day = datetime.fromtimestamp(self.cursor.session_started_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for row in self.reader.named("telemetry_root_sessions", day_from=day, day_to=day):
            if row.get("entity_ref") == self.cursor.root_session_ref:
                self.vstate["root_session_id"] = str(row["session_id"])
                return str(row["session_id"])
        return None

    def _read_stored(self, expected: Any) -> dict[str, list[dict[str, Any]]] | None:
        """The stored tree, or None with compare_skipped set (the transcript legs stand either way)."""
        result = self.result
        fingerprint = self.vstate.get("producer_fingerprint")
        if self.vstate.get("producer_changed") or fingerprint not in (None, verify_due.producer_fingerprint()):
            result.compare_skipped = "producer_modified"
            return None
        if sum(len(batch) for _, batch in expected.batches) > MAX_TREE_PAGES * PAGE_ROWS:
            result.compare_skipped = "tree_too_large"
            return None
        if not self.credentials():
            raise _Stop("no_credentials")
        has_root = any(row.get("entity_ref") == self.cursor.root_session_ref for row in expected.sessions)
        session_id = self._root_session_id()
        if session_id is None:
            result.compare_skipped = "root_absent" if has_root else "root_not_due"
            self.counts["root_checked"] = int(has_root)
            self.counts["root_absent"] = int(has_root)
            return None
        self.counts["root_checked"] = int(has_root)
        self._guard()
        try:
            pages = rows.read_tree(self.reader.named, session_id, PAGE_ROWS)
        except TreeCallCap:
            result.compare_skipped = "tree_too_large"
            return None
        except rows.TreeMovedError:
            raise _Stop("tree_moved") from None
        if self.reader.pages and self.reader.pages[0][1] > 1:
            result.compare_skipped = "tree_collision"
            return None
        return rows.decode_rows(pages)

    def _judge(self, expected: Any, stored: dict[str, list[dict[str, Any]]]) -> None:
        compared = compare.compare_tree(expected.batches, stored, producer=PRODUCER, parser_version=PARSER_VERSION)
        self.counts.update({name: value for name, value in compared.classes().items() if value})
        self.counts["rows_equal"] = compared.total("rows_equal")
        self.result.columns = dict(compared.columns)
        if expected.diagnostics.get("notice_after_boundary", 0) > 0:
            self.result.masked = {name: self.counts.pop(name, 0) for name in R7_MASKED}
            self.counts["r7_boundary_divergence"] = sum(self.result.masked.values())
        calls: dict[str, list[dict[str, Any]]] = {}
        for row in stored.get(OBSERVATIONS, ()):
            if row.get("observation_type") == "model_call" and row.get("producer") == PRODUCER:
                calls.setdefault(row["external_ref"], []).append(row)
        defects = {ref for table, ref in compared.defects if table == OBSERVATIONS}
        classes = {(f.cut_index, f.model): f for f in self.findings if f.model}
        outcome = stored_ledger.reconcile(self.inputs, calls, classes, parser_version=PARSER_VERSION, compare_defects=defects)
        self.counts.update(outcome.counts)
        self.counts["stored_ledger_tested"] = outcome.tested_models
        self.counts["stored_ledger_records_inherited"] = outcome.records_inherited
        self.held_cuts, self.stored_ran = outcome.held_cut_indexes, True
        self.result.stored_models = list(outcome.models)

    def _raise(self, findings: Mapping[str, int]) -> None:
        scope = self.cursor.root_session_ref if self.cursor is not None else self.session_id
        last = {cls: datetime.fromtimestamp(at, tz=timezone.utc) for cls, at in (self.vstate.get("alarm_at") or {}).items()}
        outcome = raise_alarms(
            findings,
            scope=scope,
            alarm_at=last,
            now=datetime.fromtimestamp(self.now, tz=timezone.utc),
            portal=self.portal,
            file=FILE,
            acceptance_for=acceptance_for,
            declared=DECLARED_CLASSES,
            parser_version=PARSER_VERSION,
            reader=self.raw_reader,
        )
        self.alarm_at = {cls: at.timestamp() for cls, at in last.items()}
        self.result.outcome = outcome

    def _classes(self) -> dict[str, int]:
        return {cls: n for cls, n in self.counts.items() if n > 0 and (cls in ALARM_CLASSES or cls in DECLARED_CLASSES)}

    def _write(self) -> None:
        result, fresh = self.result, self.state.verify_state()
        record: dict[str, Any] = {
            "event": "verify",
            "trigger": self.trigger,
            "status": result.status,
            "compare_skipped": result.compare_skipped,
            "counts": {name: int(n) for name, n in sorted(self.counts.items()) if n},
            "columns": dict(sorted(result.columns.items())),
            "r7_masked": result.masked,
            "alarms": result.alarms,
            "reader_calls": result.reader_calls,
            "response_bytes": result.response_bytes,
            "elapsed_s": result.elapsed_s,
            "at": int(self.now),
        }
        self.state.append_diagnostic({k: v for k, v in record.items() if v not in ({}, [])})
        fields: dict[str, Any] = {"root_session_id": self.vstate.get("root_session_id")}
        if self.alarm_at:
            fields["alarm_at"] = {**(fresh.get("alarm_at") or {}), **self.alarm_at}
        if self.trigger == "detached":
            fields.update(self._settlement(fresh))
            if self.tree_catalog is not None:
                fields["verify_catalog"] = self.tree_catalog
        owner = CHECK if self.trigger == "detached" else OPERATOR
        updates = {k: v for k, v in fields.items() if v is not None}
        updates.update({name: None for name in self.view_dropped if fields.get(name) is None})
        self.state.save_verify_state(state.merge_verify_state(fresh, updates, owner))

    def _settlement(self, fresh: Mapping[str, Any]) -> dict[str, Any]:
        result, vstate = self.result, self.vstate
        if not result.completed:
            retry = int(vstate.get("retry_s") or verify_due.VERIFY_RETRY_S)
            out: dict[str, Any] = {
                "next_attempt_at": self.now + retry,
                "retry_s": min(retry * 2, verify_due.VERIFY_RETRY_MAX_S),
            }
            if result.status in PER_TREE_SKIPS:
                out["settled_at"] = vstate.get("scheduled_at")
            if result.status not in BENIGN:
                out["incomplete_streak"] = int(vstate.get("incomplete_streak") or 0) + 1
            return out
        out = {"settled_at": vstate.get("scheduled_at"), "next_attempt_at": None, "retry_s": None, "incomplete_streak": 0}
        if self.ledger_lines:
            out["held_ledger_line"] = max([vstate.get("held_ledger_line", -1), *self.ledger_lines.values()])
            if self.stored_ran:
                floor = min(self.held_cuts, default=None)
                covered = [line for index, line in self.ledger_lines.items() if floor is None or index < floor]
                if covered:
                    out["last_ledger_line"] = max([vstate.get("last_ledger_line", -1), *covered])
        return out

    def run(self) -> VerifyResult:
        self.vstate = self.state.verify_state()
        if self.credentials():
            try:
                self.catalog = getattr(self.raw_reader, "catalog", None)
            except Exception as exc:  # noqa: BLE001 -- resolved before the lock: a counted token with a backoff
                self.result.status = f"verify_error:{type(exc).__name__}"
                self._finish()
                return self.result
        if not self._locked(self._step_load):
            self.result.status = "busy"
            return self.result
        if self.result.completed:
            try:
                self._analyse()
            except _Stop as stop:
                self.result.status = stop.token
            except BudgetExhausted:
                self.result.status = "budget_exhausted"
            except cursor_tree.SidecarMutated:
                self.result.status = "sidecar_mutated"
            except Exception as exc:  # noqa: BLE001 -- a failing check is a counted token with a backoff, never a gate
                self.result.status = f"verify_error:{type(exc).__name__}"
        self._finish()
        return self.result

    def _step_load(self) -> None:
        try:
            self._load()
        except _Stop as stop:
            self.result.status = stop.token
        except Exception as exc:  # noqa: BLE001
            self.result.status = f"verify_error:{type(exc).__name__}"

    def _analyse(self) -> None:
        view = cursor_tree.CursorTree(FsTree(self.path), self.cursor)
        expected = cursor_tree.expected_rows(view, self.cursor)
        self._guard()
        self._transcript_legs(view, expected)
        stored = self._read_stored(expected)
        if stored is not None:
            if not self._locked(self._unchanged):
                raise _Stop("busy")
            self._judge(expected, stored)

    def _finish(self) -> None:
        result = self.result
        result.reader_calls, result.response_bytes = self.reader.calls, self.reader.bytes
        result.elapsed_s = int(time.monotonic() - self.started)
        streak = int(self.vstate.get("incomplete_streak") or 0) + 1
        if (
            self.trigger == "detached"
            and not result.completed
            and result.status not in BENIGN
            and streak >= VERIFY_INCOMPLETE_STREAK
        ):
            self.counts["verify_incomplete"] = 1
        if self.alarms_on and result.status != "busy":
            filed = self._classes() if result.completed else {"verify_incomplete": self.counts["verify_incomplete"]}
            try:
                self._raise(filed)
            except Exception as exc:  # noqa: BLE001 -- the alarms are best effort; the failure is counted in the record
                self.counts[f"alarm_error:{type(exc).__name__}"] += 1
        result.counts = dict(self.counts)
        result.alarms = sorted(cls for cls in ALARM_CLASSES if self.counts.get(cls, 0) > 0)
        if result.status != "busy":
            try:
                self._locked(self._write)
            except Exception:  # noqa: BLE001 -- a dropped final write leaves the check unsettled, so it is retried
                pass


def run_verify(
    transcript_path: Path,
    session_id: str,
    *,
    trigger: str,
    scheduling_record: Mapping[str, Any] | None,
    reader: Any,
    portal: Callable[[str, dict[str, Any]], Any] | None,
    budget: Budget,
    alarms: bool = True,
    now: float | None = None,
    credentials: Callable[[], bool] = credentials_available,
    lock_wait_s: float = LOCK_WAIT_S,
) -> VerifyResult:
    """One check. Never raises: every failure is a status token on the result."""
    check = _Check(
        Path(transcript_path),
        session_id,
        trigger=trigger,
        scheduling_record=scheduling_record,
        reader=reader,
        portal=portal,
        budget=budget,
        alarms=alarms,
        now=time.time() if now is None else now,
        credentials=credentials,
        lock_wait_s=lock_wait_s,
    )
    with _silenced():
        try:
            return check.run()
        except Exception as exc:  # noqa: BLE001
            check.result.status = f"verify_error:{type(exc).__name__}"
            return check.result
