"""`python -m scripts.telemetry_capture <cmd>`: the runner entry the hooks detach (`run`) and the operator CLI (rule OP-1).

run (silent, exit 0 always); pass --tree [--final]; status --tree; probe --tree [--require t1,t2]; observe --tree;
mark --tree; clear-halt --tree; verify --tree [--no-alarms] [--assert-zero CLASS]; register --project-ref --name --remote.
No sweep exists: a pass starts on a tree only through its own hook or an explicit --tree. Operator output is counts and fixed
tokens, never content or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.telemetry_capture import waits
from scripts.telemetry_capture.runner import STOP_BUDGET_S, ClientFactory, default_client_factory, run_pass
from scripts.telemetry_capture.state import CaptureState, state_dir
from scripts.telemetry_capture.transport import MAX_TIMEOUT_S, Budget, CaptureUnavailable
from src.turn_capture.render import render_datetime
from src.turn_capture.transcript import PIN_SCRIPT_STEM, ROOT, FsTree, hook_script, project_ref_pin, read_lines, record_time

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
OK_OUTCOMES = ("sent", "no_rows")
VERIFY_COUNTS = (
    "rows_equal missing_in_store unexpected_in_store column_mismatch key_mismatch duplicate_ref_rows race_duplicate_rows "
    "r7_boundary_divergence other_generation_rows other_producer_rows root_checked ledger_records stored_ledger_tested "
    "root_calls sync_spawns pinned_absent"
).split()
WAIT_BOUND_S = {"Stop": waits.WITNESS_WAIT_S + waits.SETTLE_BOUND_S, "UserPromptSubmit": waits.PROMPT_WAIT_S}
COMPARE_CLASSES = ("missing_in_store", "unexpected_in_store", "column_mismatch", "key_mismatch", "duplicate_ref_rows")
DENOMINATOR_OF = {
    "root_absent": "root_checked",
    "nonfinal_root_call": "root_calls",
    "spawn_usage_mismatch": "sync_spawns",
    "sidecar_pinned_absent_present": "pinned_absent",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.telemetry_capture")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--event", required=True)
    run.add_argument("--transcript", required=True)
    run.add_argument("--session-id", required=True)
    run.add_argument("--final", action="store_true")
    run.add_argument("--lock-wait", type=float, default=0.0)
    run.add_argument("--prompt-id", default=None)
    for name in ("pass", "status", "probe", "observe", "mark", "clear-halt", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--tree", required=True, type=Path)
        if name == "pass":
            cmd.add_argument("--final", action="store_true")
        if name == "probe":
            cmd.add_argument("--require", default="")
        if name == "verify":
            cmd.add_argument("--session-id", default=None)
            cmd.add_argument("--no-alarms", action="store_true")
            cmd.add_argument("--assert-zero", default=None)
    register = sub.add_parser("register")
    register.add_argument("--project-ref", required=True)
    register.add_argument("--name", required=True)
    register.add_argument("--remote", required=True)
    return parser


def _state(tree: Path) -> CaptureState:
    return CaptureState(state_dir(tree, tree.stem))


def _event_wait(
    args: argparse.Namespace, clock: Callable[[], float], sleep: Callable[[float], None]
) -> Callable[[], str] | None:
    """Rule RW-1: Stop waits for its witness, UserPromptSubmit for its prompt record; every other event does not wait."""
    root = Path(args.transcript)
    if args.event == "Stop":
        return lambda: waits.wait_for_witness(root, clock, sleep)
    if args.event == "UserPromptSubmit" and args.prompt_id:
        return lambda: waits.wait_for_prompt(root, args.prompt_id, clock, sleep)
    return None


def _cmd_run(
    args: argparse.Namespace,
    env: Mapping[str, str],
    repo_root: Path,
    factory: ClientFactory,
    reader_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    try:
        result = run_pass(
            Path(args.transcript),
            args.session_id,
            args.event,
            final=args.final,
            budget=Budget.start(WAIT_BOUND_S.get(args.event, 0.0) + args.lock_wait + STOP_BUDGET_S),
            env=env,
            repo_root=repo_root,
            client_factory=factory,
            lock_wait=args.lock_wait,
            wait=_event_wait(args, clock, sleep),
        )
        if result.diag.get("verify_due") is True:
            _run_check(args, result, reader_factory)
    except Exception:  # noqa: BLE001 -- the detached runner is silent on every path
        pass
    return 0


def _run_check(args: argparse.Namespace, result: Any, reader_factory: Callable[[], Any] | None) -> None:
    """The in-session source check, in this already-detached process; imported here so an import failure changes nothing."""
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    if reader_factory is None:
        from scripts.telemetry_capture import read_route  # noqa: PLC0415

        reader_factory = read_route.make_routed_reader
    verify.run_verify(
        Path(args.transcript),
        args.session_id,
        trigger="detached",
        scheduling_record=result.diag,
        reader=reader_factory(),
        portal=None,
        budget=Budget.start(verify.VERIFY_BUDGET_S),
    )


def _cmd_pass(args: argparse.Namespace, env: Mapping[str, str], repo_root: Path, factory: ClientFactory, out: Callable) -> int:
    if project_ref_pin(FsTree(args.tree)) is None:
        out("refused: the tree has no pinning SessionStart attachment")
        return 1
    result = run_pass(
        args.tree,
        args.tree.stem,
        "operator",
        final=args.final,
        budget=Budget.start(STOP_BUDGET_S),
        env=env,
        repo_root=repo_root,
        client_factory=factory,
    )
    out(json.dumps({"outcome": result.outcome, "rows": result.diag.get("rows", {}), "diag": result.diag.get("diag", {})}))
    return 0 if result.outcome in OK_OUTCOMES else 1


def _cmd_status(args: argparse.Namespace, out: Callable) -> int:
    state = _state(args.tree)
    cursor = state.cursor_on_disk()
    last = (state.diagnostics() or [{}])[-1]
    summary = {
        "lines_consumed": cursor.lines_consumed if cursor else None,
        "finalized": cursor.finalized if cursor else None,
        "finalize_cuts": len(cursor.finalize_cuts) if cursor else None,
        "halt": state.halt(),
        "last": {k: last[k] for k in ("event", "outcome", "rows", "requests", "diag") if k in last},
    }
    out(json.dumps(summary, sort_keys=True))
    return 0


def _cmd_probe(args: argparse.Namespace, env: Mapping[str, str], factory: ClientFactory, out: Callable) -> int:
    state = _state(args.tree)
    cursor = state.cursor_on_disk()
    if cursor is None:
        out("probe: no cursor for this tree")
        return 1
    started = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=cursor.session_started_at_ms)
    try:
        client = factory(env, state, Budget.start(MAX_TIMEOUT_S))
        status, body = client.invoke(
            {
                "action": "telemetry_probe",
                "project_ref": cursor.project_ref,
                "session_ref": cursor.root_session_ref,
                "session_started_at": render_datetime(started),
            }
        )
    except CaptureUnavailable as exc:
        out(f"probe: {exc.reason}")
        return 1
    counts = body.get("grain_counts") if status == 200 and isinstance(body, Mapping) else None
    if not isinstance(counts, Mapping):
        out(f"probe: status {status}, no grain counts")
        return 1
    for table in sorted(counts):
        out(f"{table} rows={counts[table].get('rows')} keys={counts[table].get('keys')}")
    required = [t for t in args.require.split(",") if t]
    short = [t for t in required if not (counts.get(t) or {}).get("rows")]
    halt = state.halt()
    if halt is not None:
        out("probe: halt recorded")
    if short:
        out("probe: zero rows in " + ",".join(short))
    return 1 if short or halt is not None else 0


def _prefix_sha(tree: Path, size: int) -> str | None:
    try:
        with tree.open("rb") as handle:
            data = handle.read(size)
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest() if len(data) == size else None


def _boot_id(boot_id: Path) -> str | None:
    try:
        return boot_id.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _cmd_mark(args: argparse.Namespace, out: Callable, boot_id: Path) -> int:
    state = _state(args.tree)
    size = args.tree.stat().st_size
    marker = {"boot_id": _boot_id(boot_id), "prefix_bytes": size, "prefix_sha256": _prefix_sha(args.tree, size)}
    state.write_marker(marker)
    out(f"mark: written prefix_bytes={size}")
    return 0


def _late_sidecars(tree: Path) -> tuple[int, int]:
    """(sidecars whose mtime is later than their tool_result record's timestamp, sidecars seen)."""
    files = [tree, *sorted((tree.parent / tree.stem / "subagents").glob("agent-*.jsonl"))]
    late = seen = 0
    for path in files:
        for item in read_lines(path.read_bytes() if path.is_file() else b""):
            info = item.data.get("toolUseResult") if item.data is not None else None
            persisted = info.get("persistedOutputPath") if isinstance(info, dict) else None
            when = record_time(item.data) if item.data is not None else None
            if not isinstance(persisted, str) or when is None:
                continue
            sidecar = tree.parent / tree.stem / "tool-results" / persisted.rsplit("/", 1)[-1]
            if sidecar.is_file():
                seen += 1
                late += int(sidecar.stat().st_mtime > when.timestamp())
    return late, seen


def _cmd_observe(args: argparse.Namespace, out: Callable, boot_id: Path) -> int:
    sources: Counter[str] = Counter()
    for item in read_lines(FsTree(args.tree).read(ROOT) or b""):
        att = item.data.get("attachment") if item.data is not None else None
        if not isinstance(att, dict) or att.get("hookEvent") != "SessionStart":
            continue
        if hook_script(att.get("command"))[0] == PIN_SCRIPT_STEM:
            sources[str(att.get("hookName") or "SessionStart").partition(":")[2] or "unknown"] += 1
    state = _state(args.tree)
    late, seen = _late_sidecars(args.tree)
    ends = sum(1 for line in state.diagnostics() if line.get("event") == "SessionEnd")
    out(json.dumps({"session_start_by_source": dict(sorted(sources.items())), "session_end_passes": ends}, sort_keys=True))
    out(f"tool_results_sidecars late={late} seen={seen}")
    marker = state.marker()
    if marker is None:
        out("reclaim: unproven (no marker)")
        return 0
    now = _boot_id(boot_id)
    reclaimed = now is not None and marker.get("boot_id") is not None and now != marker.get("boot_id")
    prefix_equal = _prefix_sha(args.tree, int(marker.get("prefix_bytes") or 0)) == marker.get("prefix_sha256")
    if not reclaimed:
        out(f"reclaim: no reclaim seen, unproven; marker_present=True prefix_hash_equal={prefix_equal}")
    else:
        out(f"reclaim: boot_id_changed=True marker_present=True prefix_hash_equal={prefix_equal}")
    return 0


def _cmd_clear_halt(args: argparse.Namespace, out: Callable) -> int:
    out("clear-halt: cleared" if _state(args.tree).clear_halt() else "clear-halt: no halt recorded")
    return 0


def _denominator_met(cls: str, counts: Mapping[str, int]) -> bool:
    """Whether a completed check had any input for *cls*: a leg with no input is never green."""
    if cls in COMPARE_CLASSES:
        return counts.get("rows_equal", 0) >= 1 and counts.get("r7_boundary_divergence", 0) == 0
    if cls.startswith("stored_ledger_"):
        return counts.get("stored_ledger_tested", 0) >= 1
    if cls.startswith("ledger_"):
        return counts.get("ledger_records", 0) >= 1
    return cls == "verify_incomplete" or counts.get(DENOMINATOR_OF.get(cls, ""), 0) >= 1


def _verify_exit(result: Any, assert_zero: str | None) -> int:
    if not result.completed:
        return 2
    if assert_zero is None:
        return 1 if result.alarms else 0
    if result.counts.get(assert_zero, 0) > 0:
        return 1
    return 0 if _denominator_met(assert_zero, result.counts) else 2


def _cmd_verify(args: argparse.Namespace, reader_factory: Callable[[], Any] | None, out: Callable) -> int:
    from scripts.telemetry_capture import verify  # noqa: PLC0415

    if args.assert_zero is not None and args.assert_zero not in verify.ALARM_CLASSES:
        out(f"verify: {args.assert_zero} is not an alarm class")
        return 2
    if reader_factory is None:
        from scripts.telemetry_capture import read_route  # noqa: PLC0415

        reader_factory = read_route.make_routed_reader
    reader = reader_factory()
    result = verify.run_verify(
        args.tree,
        args.session_id or args.tree.stem,
        trigger="operator",
        scheduling_record=None,
        reader=reader,
        portal=None,
        budget=Budget.start(verify.VERIFY_BUDGET_S),
        alarms=not args.no_alarms,
    )
    fields = {name: result.counts.get(name, 0) for name in VERIFY_COUNTS}
    fields.update(reader_calls=result.reader_calls, elapsed_s=result.elapsed_s)
    head = f"VERIFY status={result.status} compare_skipped={result.compare_skipped}"
    tail = " ".join(f"{name}={value}" for name, value in fields.items())
    out(f"{head} {tail} alarms={','.join(result.alarms) or 'none'}")
    return _verify_exit(result, args.assert_zero)


def _cmd_register(args: argparse.Namespace, env: Mapping[str, str], factory: ClientFactory, out: Callable) -> int:
    try:
        client = factory(env, None, Budget.start(MAX_TIMEOUT_S))
    except CaptureUnavailable as exc:
        out(f"register: {exc.reason}")
        return 1
    for payload in (
        {"action": "register_tenant"},
        {"action": "register_project", "project_ref": args.project_ref, "name": args.name, "remote": args.remote},
    ):
        status, body = client.invoke(payload)
        ok = status == 200 and isinstance(body, Mapping) and body.get("ok") is True
        created = body.get("created") if ok else None
        error = body.get("error_type") if isinstance(body, Mapping) else None
        out(f"{payload['action']}: " + (f"ok created={created}" if ok else f"failed status={status} error_type={error}"))
        if not ok:
            return 1
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
    client_factory: ClientFactory = default_client_factory,
    out: Callable[[str], Any] = print,
    boot_id: Path = BOOT_ID,
    reader_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    environ = dict(os.environ) if env is None else env
    if args.cmd == "run":
        return _cmd_run(args, environ, repo_root, client_factory, reader_factory, clock, sleep)
    if args.cmd == "pass":
        return _cmd_pass(args, environ, repo_root, client_factory, out)
    if args.cmd == "status":
        return _cmd_status(args, out)
    if args.cmd == "probe":
        return _cmd_probe(args, environ, client_factory, out)
    if args.cmd == "observe":
        return _cmd_observe(args, out, boot_id)
    if args.cmd == "mark":
        return _cmd_mark(args, out, boot_id)
    if args.cmd == "clear-halt":
        return _cmd_clear_halt(args, out)
    if args.cmd == "verify":
        return _cmd_verify(args, reader_factory, out)
    return _cmd_register(args, environ, client_factory, out)


if __name__ == "__main__":
    sys.exit(main())
