"""One capture pass over one transcript tree (rules RN-1..RN-10).

lock -> halt check -> cursor load and tail check -> project_ref (cursor pin, else the SessionStart pin, else defer) ->
record_turn -> describe check -> catalog reset or confirm (the whole tree, re-emitted) -> blob delivery -> chunked appends in
record_turn order -> cursor persisted with describe's catalog only after every request is acknowledged -> one diagnostics
line. Capture follows the catalog the deployed writer serves; no switch or config file names it. Nothing is ever printed;
diagnostics carry counts and fixed tokens only.
"""

from __future__ import annotations

import random
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.telemetry_capture import responses, switch, verify_due
from scripts.telemetry_capture.state import (  # noqa: F401 -- DEFAULT_CATALOG and KNOWN_CATALOGS are re-exported
    DEFAULT_CATALOG,
    KNOWN_CATALOGS,
    LOST,
    RUNNER,
    TAIL_MISMATCH,
    CaptureState,
    Loaded,
    merge_verify_state,
    paired_catalog,
    state_dir,
)
from scripts.telemetry_capture.transport import (
    MAX_INVOKE_BODY_BYTES,
    MAX_ROWS_PER_REQUEST,
    MAX_ROWS_PER_TABLE,
    Budget,
    CaptureUnavailable,
    Item,
    Limits,
    WriterClient,
    append_body,
    chunk,
    make_client,
)
from src.telemetry_client.blob_delivery import (
    BlobDeliveryError,
    BlobProtocol,
    HandshakeError,
    deliver_blobs,
    protocol_from_describe,
)
from src.turn_capture.cursor import CursorError
from src.turn_capture.record_turn import CaptureResult, record_turn
from src.turn_capture.transcript import ROOT, FsTree, project_ref_pin

TRANSCRIPTS = "telemetry_transcripts"
STOP_BUDGET_S = 600.0
RESET_CONFIRM_AFTER_S = 1800
RESET_CONFLICT = "grain_conflict_after_catalog_reset"
ClientFactory = Callable[..., WriterClient]


@dataclass(frozen=True)
class PassResult:
    outcome: str
    diag: dict[str, Any] = field(default_factory=dict)


class SnapshotTree(FsTree):
    """An FsTree whose root file is read once, so the tail check and record_turn see the same bytes."""

    def __init__(self, root_file: Path) -> None:
        super().__init__(root_file)
        self.root_bytes: bytes | None = root_file.read_bytes() if root_file.is_file() else None

    def read(self, key: str) -> bytes | None:
        return self.root_bytes if key == ROOT else super().read(key)


class _Stop(Exception):
    def __init__(self, outcome: str, reason: str, halt: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.outcome, self.reason, self.halt = outcome, reason, halt


def default_client_factory(
    env: Mapping[str, str],
    state: CaptureState,
    budget: Budget,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> WriterClient:
    return make_client(env, state, budget, sleep=sleep, rng=rng)


def _describe(client: WriterClient) -> tuple[BlobProtocol, str]:
    """RN-5: the writer serves append_telemetry on a known catalog with its paired data path, with caps no lower than TR-3's
    and a blob protocol. Returns the protocol and the catalog."""
    status, body = client.invoke({"action": "describe"})
    catalog = None
    try:
        caps = body["verbs"]["append_telemetry"]["caps"]
        catalog = paired_catalog(body["catalog"])
        ok = (
            status == 200
            and "append_telemetry" in body["actions"]
            and catalog is not None
            and caps["max_invoke_body_bytes"] >= MAX_INVOKE_BODY_BYTES
            and caps["max_rows_per_table"] >= MAX_ROWS_PER_TABLE
            and caps["max_rows_per_request"] >= MAX_ROWS_PER_REQUEST
        )
        protocol = protocol_from_describe(body)
    except (KeyError, TypeError, HandshakeError):
        ok = False
    if not ok or catalog is None:
        raise _Stop("stopped", "describe_mismatch")
    return protocol, catalog


class _Sender:
    """RN-6 and RN-7 over one pass's rows: delivery, chunked appends, the response table."""

    def __init__(
        self, client: WriterClient, protocol: BlobProtocol, project_ref: str, sleep: Callable[[float], None], diag: Counter
    ) -> None:
        self.client, self.protocol, self.project_ref, self.sleep, self.diag = client, protocol, project_ref, sleep, diag
        self.originals: dict[int, dict[str, Any]] = {}
        self.registered = False
        self.requests: list[dict[str, dict[str, int]]] = []

    def deliver(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            delivery = deliver_blobs(
                rows,
                project_ref=self.project_ref,
                invoke=self.client.invoke_once,
                protocol=self.protocol,
                component="turn_capture",
                put=self.client.put,
                sleep=self.sleep,
            )
        except BlobDeliveryError as exc:
            if exc.kind in ("refused", "defect"):
                halt = {"reason": "blob_" + exc.kind, "error_class": "BlobDeliveryError", "status": exc.status}
                raise _Stop("halted", "blob_" + exc.kind, {k: v for k, v in halt.items() if v is not None}) from None
            raise _Stop("stopped", "blob_" + exc.kind) from None
        self.diag["blobs_existed"] += delivery.existed
        self.diag["blobs_uploaded"] += delivery.uploaded
        return delivery.wire_rows

    def items(self, batches: list[tuple[str, list[dict[str, Any]]]]) -> list[Item]:
        out: list[Item] = []
        for table, rows in batches:
            wire = self.deliver(rows) if table == TRANSCRIPTS and rows else rows
            for original, row in zip(rows, wire, strict=True):
                if table == TRANSCRIPTS:
                    self.originals[id(row)] = original
                out.append((table, row))
        return out

    def redeliver(self, request: list[Item]) -> None:
        rows = [self.originals[id(row)] for table, row in request if table == TRANSCRIPTS and id(row) in self.originals]
        if rows:
            self.deliver(rows)

    def _register_tenant(self) -> bool:
        if self.registered:
            return False
        self.registered = True
        status, body = self.client.invoke({"action": "register_tenant"})
        return responses.classify(status, body) == responses.OK

    def _acknowledged(self, body: Mapping[str, Any]) -> None:
        raw = body.get("tables")
        tables: Mapping[Any, Any] = raw if isinstance(raw, Mapping) else {}
        counts: dict[str, dict[str, int]] = {}
        for table, numbers in tables.items():
            if isinstance(numbers, Mapping):
                counts[str(table)] = {k: int(numbers.get(k) or 0) for k in ("submitted", "inserted")}
        self.requests.append(counts)

    def send(self, items: list[Item], conflict_reason: str) -> None:
        limits = Limits(self.protocol.max_items, self.protocol.inline_threshold_bytes, self.protocol.cap_bytes)
        queue = deque(chunk(items, self.project_ref, limits))
        redelivered: set[int] = set()
        while queue:
            request = queue[0]
            if self.client.budget.remaining() <= 0:
                raise _Stop("stopped", "deadline")
            status, body = self.client.invoke(append_body(self.project_ref, request))
            action = responses.classify(status, body)
            if action == responses.OK:
                self._acknowledged(body)
                queue.popleft()
            elif action == responses.REGISTER_TENANT:
                if not self._register_tenant():
                    raise _Stop("stopped", "tenant_unresolved")
            elif action == responses.REDELIVER and id(request) not in redelivered:
                redelivered.add(id(request))
                self.redeliver(request)
            elif action == responses.RECHUNK and len(request) > 1:
                half = len(request) // 2
                queue.popleft()
                queue.extendleft([request[half:], request[:half]])
            elif action in (responses.REDELIVER, responses.RECHUNK, responses.HALT):
                reason = conflict_reason if status == 409 else "writer_refused"
                raise _Stop("halted", reason, {"reason": reason, **responses.halt_fields(status, body)})
            elif self.client.budget.remaining() <= 0:
                raise _Stop("stopped", "deadline")
            else:
                raise _Stop("stopped", f"status_{status}" if status else "transport")


def _verify_hook(
    state: CaptureState,
    record: dict[str, Any],
    cursor: Any,
    root: bytes,
    event: str,
    final: bool,
    outcome: str,
    catalog_reset: bool = False,
) -> None:
    """Decide whether the in-session source check is due after this pass and schedule it; never changes the outcome.

    Runs under the pass lock, after the cursor is persisted. The runner owns sent_passes, checks_by_day, the producer
    fingerprint, producer_changed, scheduled_at and the scheduling next_attempt_at; the check owns the rest (state.py).
    """
    if final:
        return
    try:
        now = time.time()
        held = state.verify_state()
        fresh = dict(held)
        if outcome == "sent":
            fresh["sent_passes"] = int(held.get("sent_passes") or 0) + 1
            fingerprint = verify_due.producer_fingerprint()
            if held.get("producer_fingerprint") is None:
                fresh["producer_fingerprint"] = fingerprint
            elif held["producer_fingerprint"] != fingerprint:
                fresh["producer_changed"] = True
        if catalog_reset and _checks_today(fresh, now) < verify_due.MAX_CHECKS_PER_TREE_PER_DAY:
            fresh["scheduled_at"] = now
        due = verify_due.verify_due(cursor, root, fresh, event=event, final=final, outcome=outcome, now=now)
        record["verify_due"] = due
        if due:
            by_day = dict(fresh.get("checks_by_day") or {})
            today = verify_due.utc_day(now)
            by_day[today] = by_day.get(today, 0) + 1
            fresh["checks_by_day"] = dict(sorted(by_day.items())[-3:])
            fresh["scheduled_at"], fresh["next_attempt_at"] = now, now + verify_due.VERIFY_GUARD_S
        merged = merge_verify_state(held, fresh, RUNNER)
        if merged != held:
            state.save_verify_state(merge_verify_state(state.verify_state(), fresh, RUNNER))
    except Exception as exc:  # noqa: BLE001 -- the check is an alarm, never a gate: the outcome stands
        record.pop("verify_due", None)
        record["verify_due_error"] = type(exc).__name__


def _checks_today(verify_state: Mapping[str, Any], now: float) -> int:
    return int((verify_state.get("checks_by_day") or {}).get(verify_due.utc_day(now), 0))


def _finish(state: CaptureState, record: dict[str, Any], outcome: str, diag: Counter) -> PassResult:
    record.update(outcome=outcome, diag=dict(sorted(diag.items())))
    state.append_diagnostic(record)
    return PassResult(outcome, record)


def _locked_pass(
    root_file: Path,
    state: CaptureState,
    event: str,
    *,
    final: bool,
    budget: Budget,
    env: Mapping[str, str],
    smoke_ref: str | None,
    switch_source: str | None,
    client_factory: ClientFactory,
    detach_below: float | None,
    sleep: Callable[[float], None],
    rng: Callable[[], float],
    waited: Counter[str],
) -> PassResult:
    diag: Counter[str] = Counter(waited)
    diag[f"switch_source:{switch_source or 'none'}"] += 1
    record: dict[str, Any] = {"event": event, "final": final, "at": int(time.time())}
    if state.halt() is not None:
        return _finish(state, record, "halted", diag)
    tree = SnapshotTree(root_file)
    root = tree.root_bytes or b""
    loaded = state.load_cursor(root)
    if not loaded.valid:
        diag[loaded.status] += 1
    project_ref = loaded.cursor.project_ref if loaded.cursor is not None else project_ref_pin(tree)
    if project_ref is None:
        diag["deferred_no_pin"] += 1
        return _finish(state, record, "deferred", diag)
    if smoke_ref is not None and project_ref != smoke_ref:
        diag["smoke_pin_mismatch"] += 1
        return _finish(state, record, "off", diag)
    now = time.time()
    try:
        result = _record(tree, loaded, project_ref, final, record, resend_tree=False)
    except CursorError as exc:
        return _producer_halt(state, record, exc, diag)
    if detach_below is not None and budget.remaining() < detach_below:
        return _finish(state, record, "short_budget", diag)
    if not _rows(result, record):
        kept = (loaded.catalog, loaded.reset_at)
        _persist_and_hook(state, record, result.next_cursor, root, loaded, diag, (event, final, "no_rows"), kept)
        return _finish(state, record, "no_rows", diag)
    conflict = "grain_conflict_after_cursor_loss" if loaded.status in (LOST, TAIL_MISMATCH) else "grain_conflict"
    sender: _Sender | None = None
    try:
        client = client_factory(env, state, budget, sleep=sleep, rng=rng)
        protocol, catalog = _describe(client)
        reset, confirm, reset_at = _reset_window(loaded, catalog, now)
        if reset or confirm:
            if detach_below is not None:
                diag["catalog_reset_detached"] += 1
                return _finish(state, record, "short_budget", diag)
            diag["catalog_reset" if reset else "catalog_reset_confirm"] += 1
            result, conflict = _record(tree, loaded, project_ref, final, record, resend_tree=True), RESET_CONFLICT
            _rows(result, record)
        sender = _Sender(client, protocol, project_ref, sleep, diag)
        sender.send(sender.items(result.batches), conflict)
    except CursorError as exc:
        return _producer_halt(state, record, exc, diag)
    except CaptureUnavailable as exc:
        diag[exc.reason] += 1
        return _finish(state, record, "stopped", diag)
    except _Stop as stop:
        return _stopped(state, record, stop, sender, diag)
    record["requests"] = sender.requests
    persisted = (catalog, reset_at)
    _persist_and_hook(state, record, result.next_cursor, root, loaded, diag, (event, final, "sent", reset), persisted)
    return _finish(state, record, "sent", diag)


def _stopped(state: CaptureState, record: dict[str, Any], stop: _Stop, sender: _Sender | None, diag: Counter) -> PassResult:
    diag[stop.reason] += 1
    if sender is not None:
        record["requests"] = sender.requests
    if stop.halt is not None:
        state.write_halt(stop.halt)
        record["halt"] = stop.halt
    return _finish(state, record, stop.outcome, diag)


def _reset_window(loaded: Loaded, catalog: str, now: float) -> tuple[bool, bool, float | None]:
    """RN-9 and RN-10 for a pass whose writer answered *catalog*: (reset, confirm, the reset_at to persist)."""
    reset = loaded.valid and catalog != loaded.catalog
    confirm = not reset and loaded.valid and loaded.reset_at is not None and now - loaded.reset_at >= RESET_CONFIRM_AFTER_S
    reset_at = now if reset else None if confirm or not loaded.valid else loaded.reset_at
    return reset, confirm, reset_at


def _record(
    tree: SnapshotTree, loaded: Loaded, project_ref: str, final: bool, record: dict[str, Any], *, resend_tree: bool
) -> CaptureResult:
    result = record_turn(tree, loaded.cursor, project_ref=project_ref, session_final=final, resend_tree=resend_tree)
    record["record_turn"] = result.diagnostics
    return result


def _rows(result: CaptureResult, record: dict[str, Any]) -> int:
    record["rows"] = {table: len(rows) for table, rows in result.batches}
    return sum(record["rows"].values())


def _producer_halt(state: CaptureState, record: dict[str, Any], exc: CursorError, diag: Counter) -> PassResult:
    halt = {"reason": "producer_error", "error_class": type(exc).__name__}
    state.write_halt(halt)
    record["halt"] = halt
    return _finish(state, record, "halted", diag)


def _persist_and_hook(
    state: CaptureState,
    record: dict[str, Any],
    cursor: Any,
    root: bytes,
    loaded: Loaded,
    diag: Counter,
    pass_kind: tuple[Any, ...],
    persisted: tuple[str, float | None],
) -> None:
    if cursor is not None and _persist(state, cursor, root, loaded, diag, persisted):
        _verify_hook(state, record, cursor, root, *pass_kind)


def _persist(
    state: CaptureState, cursor: Any, root: bytes, loaded: Loaded, diag: Counter, persisted: tuple[str, float | None]
) -> bool:
    catalog, reset_at = persisted
    if not state.save_cursor(cursor, root, loaded, catalog=catalog, reset_at=reset_at):
        diag["cursor_regression"] += 1
        return False
    return True


def run_pass(
    transcript_path: Path,
    session_id: str,
    event: str,
    *,
    final: bool,
    budget: Budget,
    env: Mapping[str, str],
    repo_root: Path,
    client_factory: ClientFactory = default_client_factory,
    lock_wait: float = 0.0,
    detach_below: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
    wait: Callable[[], str] | None = None,
) -> PassResult:
    """RN-1..RN-8. "off" before any state is touched; "busy" when another pass holds the tree's lock.

    *wait* (rule RW-1, opt-in) runs after the switch and smoke checks and before the lock; its token is counted in the pass's
    diagnostics and a timeout still runs the pass.
    """
    mode, switch_source = switch.switch_mode(env, repo_root)
    if mode == switch.OFF:
        return PassResult("off")
    root_file = Path(transcript_path)
    smoke_ref = switch.smoke_ref(env) if mode == switch.SMOKE else None
    if smoke_ref is not None and project_ref_pin(SnapshotTree(root_file)) != smoke_ref:
        return PassResult("off", {"smoke_pin_mismatch": 1})
    waited: Counter[str] = Counter()
    if wait is not None:
        waited[wait()] += 1
    state = CaptureState(state_dir(root_file, session_id))
    lock = state.acquire(0.0, sleep=sleep)
    if lock is None and lock_wait > 0:
        lock = state.acquire(min(lock_wait, budget.remaining()), sleep=sleep)
        if lock is not None:
            waited["lock_waited"] += 1
    if lock is None:
        return PassResult("busy")
    try:
        return _locked_pass(
            root_file,
            state,
            event,
            final=final,
            budget=budget,
            env=env,
            smoke_ref=smoke_ref,
            switch_source=switch_source,
            client_factory=client_factory,
            detach_below=detach_below,
            sleep=sleep,
            rng=rng,
            waited=waited,
        )
    finally:
        lock.release()
