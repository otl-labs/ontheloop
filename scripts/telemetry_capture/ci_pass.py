"""`ci-pass`: the synchronous final capture pass for the `claude -p` sessions of one CI job
(PLAN-telemetry-claude-p-capture-adapter).

Takes the retry wrapper's manifest globs and every host fact as arguments (Decision 212: no GITHUB_* is read here; the
composite action's delegate reads them). Validates the facts, applies the allow-list gate (switch.ci_gate), pins each
session with a write-once host-run sidecar, then runs one final pass per session through the runner. Never runs the in-session
source check or any reader call. Every printed line is a count or a fixed token (Decision 101): no id, ref, sha, path
or content.
Exits 0 on every path except argparse usage.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.telemetry_capture import switch
from scripts.telemetry_capture.runner import ClientFactory, run_pass
from scripts.telemetry_capture.transport import Budget
from src.turn_capture.host_run import ACTIONS_EVENTS, SCHEMA_VERSION, valid_host_facts, valid_project_ref
from src.turn_capture.transcript import HOST_RUN_FILE, PIN_FLAG, PIN_SCRIPT_STEM, hook_script

SETTINGS_FILE = Path(".claude") / "settings.json"
DEFAULT_BUDGET_S = 180.0
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STARTED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
_COUNTED = ("sent", "no_rows", "deferred", "stopped", "halted", "skipped")
_OUTCOME_GROUP = {"sent": "sent", "no_rows": "no_rows", "deferred": "deferred", "halted": "halted"}


@dataclass(frozen=True)
class Attempt:
    session_id: str
    ordinal: int


def add_arguments(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--manifest", action="append", required=True)
    for name in ("workflow-path", "ref", "repository", "run-id", "run-attempt", "job", "event-name", "sha"):
        cmd.add_argument(f"--{name}", required=True)
    cmd.add_argument("--checkout-sha", default=None)
    cmd.add_argument("--projects-dir", type=Path, default=Path.home() / ".claude" / "projects")
    cmd.add_argument("--budget-s", type=float, default=DEFAULT_BUDGET_S)


def read_manifests(patterns: list[str], diag: Counter[str]) -> list[Attempt]:
    """Every start and result line of every manifest, merged by session id, ranked by (started_at, file order, line order)."""
    started: dict[str, tuple[str, int]] = {}
    order = 0
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            try:
                lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                diag["manifest_unreadable"] += 1
                continue
            for line in lines:
                order += 1
                try:
                    data = json.loads(line)
                except ValueError:
                    data = None
                sid = data.get("session_id") if isinstance(data, dict) else None
                if not isinstance(sid, str) or not _UUID.fullmatch(sid):
                    diag["manifest_malformed"] += 1
                elif "attempt" in data:
                    when = data.get("started_at")
                    if isinstance(when, str) and _STARTED.fullmatch(when) and sid not in started:
                        started[sid] = (when, order)
                    else:
                        diag["manifest_malformed"] += 1
    ranked = sorted(started.items(), key=lambda item: item[1])
    return [Attempt(sid, rank) for rank, (sid, _) in enumerate(ranked, start=1)]


def project_ref_home(repo_root: Path) -> str | None:
    """Decision 224: project.ref's one home is the --project-ref flag of the SessionStart pin hook in .claude/settings.json."""
    text, _ = switch.read_repo_file(repo_root, SETTINGS_FILE)
    try:
        groups = json.loads(text)["hooks"]["SessionStart"] if text is not None else []
        commands = [h.get("command") for group in groups for h in group.get("hooks", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    refs = []
    for command in commands:
        stem, tokens = hook_script(command)
        if stem == PIN_SCRIPT_STEM:
            refs += [t[len(PIN_FLAG) :] for t in tokens if t.startswith(PIN_FLAG)]
    return refs[0] if len(refs) == 1 and valid_project_ref(refs[0]) else None


def _facts(args: argparse.Namespace) -> dict[str, Any]:
    host: dict[str, Any] = {
        "kind": "gha",
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "job": args.job,
        "retry_ordinal": 1,
        "event_name": args.event_name,
        "repository": args.repository,
        "workflow_path": args.workflow_path,
        "ref": args.ref,
        "sha": args.sha,
    }
    if args.checkout_sha:
        host["checkout_sha"] = args.checkout_sha
    return host


def _sidecar_bytes(project_ref: str, entry: Mapping[str, Any], host: dict[str, Any], ordinal: int) -> bytes:
    body = {
        "schema_version": SCHEMA_VERSION,
        "project_ref": project_ref,
        "agent": entry["agent"],
        "content_policy": entry["content_policy"],
        "host": {**host, "retry_ordinal": ordinal},
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_sidecar(root_file: Path, data: bytes) -> bool:
    """Write-once: identical bytes reuse the file, different bytes are never overwritten. False on a conflict."""
    target = root_file.parent / root_file.stem / HOST_RUN_FILE
    try:
        if target.is_file():
            return target.read_bytes() == data
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{HOST_RUN_FILE}.{os.getpid()}.tmp")
        temp.write_bytes(data)
        os.replace(temp, target)
    except OSError:
        return False
    return True


def _transcript(projects: Path, sid: str) -> tuple[Path | None, str]:
    found = sorted(projects.glob(f"*/{sid}.jsonl"))
    if len(found) == 1:
        return found[0], ""
    return None, "transcript_absent" if not found else "transcript_ambiguous"


def _off(out: Callable[[str], Any], reason: str, sessions: int) -> int:
    out(f"CI_CAPTURE off reason={reason} sessions={sessions}")
    return 0


def main_ci_pass(
    args: argparse.Namespace,
    env: Mapping[str, str],
    repo_root: Path,
    factory: ClientFactory,
    out: Callable[[str], Any],
    clock: Callable[[], float] = time.monotonic,
) -> int:
    started_at = clock()
    diag: Counter[str] = Counter()
    attempts = read_manifests(args.manifest, diag)
    host = _facts(args)
    if args.event_name not in ACTIONS_EVENTS:
        return _off(out, "unsupported_event", len(attempts))
    if not valid_host_facts(host):
        return _off(out, "bad_host_fact", len(attempts))
    gate = switch.ci_gate(repo_root, workflow_path=args.workflow_path, ref=args.ref, repository=args.repository)
    if gate.mode != switch.CI or gate.entry is None:
        return _off(out, gate.reason or "switch_off", len(attempts))
    project_ref = project_ref_home(repo_root)
    if project_ref is None:
        return _off(out, "no_project_ref", len(attempts))
    counts: Counter[str] = Counter()
    for attempt in attempts:
        outcome, rows = _one(args, env, repo_root, factory, gate, host, project_ref, attempt, started_at, clock)
        counts[
            _OUTCOME_GROUP.get(outcome, "skipped" if outcome.startswith(("transcript_", "pin_sidecar")) else "stopped")
        ] += 1
        out(f"CI_CAPTURE session={attempt.ordinal} outcome={outcome} rows={rows}")
    tail = " ".join(f"{name}={counts[name]}" for name in _COUNTED)
    out(f"CI_CAPTURE done sessions={len(attempts)} {tail}")
    return 0


def _one(
    args: argparse.Namespace,
    env: Mapping[str, str],
    repo_root: Path,
    factory: ClientFactory,
    gate: switch.CiGate,
    host: dict[str, Any],
    project_ref: str,
    attempt: Attempt,
    started_at: float,
    clock: Callable[[], float],
) -> tuple[str, int]:
    root_file, missing = _transcript(args.projects_dir, attempt.session_id)
    if root_file is None:
        return missing, 0
    assert gate.entry is not None
    if not _write_sidecar(root_file, _sidecar_bytes(project_ref, gate.entry, host, attempt.ordinal)):
        return "pin_sidecar_conflict", 0
    remaining = args.budget_s - (clock() - started_at)
    if remaining <= 0:
        return "stopped", 0
    try:
        result = run_pass(
            root_file,
            attempt.session_id,
            "ci_final",
            final=True,
            budget=Budget.start(remaining),
            env=env,
            repo_root=repo_root,
            client_factory=factory,
            ci_gate=gate,
        )
    except Exception:  # noqa: BLE001 -- ci-pass is total: a failed pass is a counted stop, never a nonzero exit
        return "stopped", 0
    rows = result.diag.get("rows", {})
    return result.outcome, sum(rows.values()) if isinstance(rows, dict) else 0
