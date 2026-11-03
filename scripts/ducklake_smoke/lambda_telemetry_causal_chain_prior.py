"""Actions API and post-persist leg of the telemetry causal-chain probe (T3.2, telemetry slice 5).

The probe's transcript is a deterministic function of (PROBE_SHAPE_VERSION, run id, run attempt, run start), so an earlier
run's expected rows are rebuilt here rather than stored: post_persist re-reads each selected earlier probe session through
the telemetry reader and compares it with those rebuilt rows (compare_tree), which catches rows lost or changed after commit
(merge, GC). Runs recorded under another shape version, or an unreadable commit, are skipped and counted, never compared.
Output is counts only (Decision 101).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts import repo_identity
from scripts.telemetry_verify import compare
from scripts.telemetry_verify.rows import decode_rows, read_tree
from src.telemetry.identity import derive_entity_key
from src.turn_capture.record_turn import PARSER_VERSION, PRODUCER, record_turn
from src.turn_capture.transcript import MemTree

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = "scripts/ducklake_smoke/lambda_telemetry_causal_chain_prior.py"
WORKFLOW_FILE = "telemetry-causal-chain.yml"
PROBE_REF = "synthetic:causal-chain-probe"
SESSION_DOMAIN = "telemetry_sessions:session_id"
PROBE_SHAPE_VERSION = 1
PROBE_TOOL_RESULT_BYTES = 70400
SHAPE_DIGESTS = {1: "f9a9cb9eb854f99a7690a72433faab02c62c817da40adeb80488a65212af282b"}  # pragma: allowlist secret
PIN_IDENTITY_ARGS = ("1", 1, datetime(2026, 1, 1, tzinfo=timezone.utc))
POST_PERSIST_BUDGET_S = 600
RECENT_RUNS, AGED_RUNS = 4, 4
AGED_MIN_DAYS, AGED_MAX_DAYS = 7, 8
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PAYLOAD_LINE = "causal-chain-probe payload line\n"
_FIXED_MODEL = "claude-test-1"

GhCaller = Callable[[str], Any]
GitShow = Callable[[str], str | None]
Named = Callable[..., list[dict[str, Any]]]


class ActionsUnavailable(RuntimeError):
    """The Actions API could not answer; the probe classes this as unavailable, never as a loss."""


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    attempt: int
    started: datetime

    @property
    def entity_ref(self) -> str:
        return f"causal-chain-probe:v{PROBE_SHAPE_VERSION}:{self.run_id}.{self.attempt}"


@dataclass(frozen=True)
class PriorRun:
    identity: RunIdentity
    head_sha: str


@dataclass
class PriorResult:
    mode: str = "checked"
    checked: int = 0
    lost: int = 0
    skipped: int = 0
    lost_classes: Counter[str] = field(default_factory=Counter)


def parse_moment(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def _payload() -> str:
    text = _PAYLOAD_LINE * (PROBE_TOOL_RESULT_BYTES // len(_PAYLOAD_LINE) + 1)
    return text[:PROBE_TOOL_RESULT_BYTES]


def build_files(identity: RunIdentity) -> dict[str, str]:
    """The probe transcript tree: one prompt, one tool call whose result is the claim-check payload, a final message."""
    ref = identity.entity_ref
    tag = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12]
    payload = _payload()

    def stamp(offset: int) -> str:
        return (identity.started + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    base = {"cwd": "/w", "gitBranch": "probe", "sessionId": ref, "version": "2.1.0"}
    usage = {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "input_tokens": 1, "output_tokens": 1}
    tool = [
        {"caller": {"type": "direct"}, "id": f"tu-{tag}", "input": {"command": "true"}, "name": "Bash", "type": "tool_use"}
    ]
    result = [{"content": payload, "tool_use_id": f"tu-{tag}", "type": "tool_result"}]

    def user(uuid: str, offset: int, content: Any, **extra: Any) -> dict[str, Any]:
        message = {"content": content, "role": "user"}
        return {**base, "message": message, "timestamp": stamp(offset), "type": "user", "uuid": uuid, **extra}

    def assistant(n: int, offset: int, content: Any, stop: str) -> dict[str, Any]:
        message = {"content": content, "id": f"m{n}-{tag}", "model": _FIXED_MODEL, "role": "assistant"}
        message |= {"stop_reason": stop, "type": "message", "usage": usage}
        return {**base, "message": message, "requestId": f"req-{n}-{tag}", "timestamp": stamp(offset)} | {
            "type": "assistant",
            "uuid": f"a{n}-{tag}",
        }

    records = [
        user(f"u-{tag}", 0, "probe", promptId=f"p-{tag}"),
        assistant(1, 1, tool, "tool_use"),
        user(f"r-{tag}", 2, result, promptId=f"p-{tag}", toolUseResult=payload),
        assistant(2, 3, [{"text": "done", "type": "text"}], "end_turn"),
    ]
    return {f"{ref}.jsonl": "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)}


def shape_digest(identity: RunIdentity) -> str:
    return hashlib.sha256(json.dumps(build_files(identity), sort_keys=True).encode("utf-8")).hexdigest()


def expected_batches(identity: RunIdentity) -> tuple[Any, list[tuple[str, list[dict[str, Any]]]]]:
    """(the record_turn result, its batches) for a probe identity, as the producer emits them."""
    result = record_turn(MemTree(build_files(identity)), None, project_ref=PROBE_REF, session_final=True)
    return result, result.batches


def session_id_for(tenant_id: str, project_id: str, identity: RunIdentity) -> str:
    return derive_entity_key(SESSION_DOMAIN, tenant_id, project_id, identity.entity_ref, identity.started)


def _runs_url(suffix: str) -> str:
    owner, repo = repo_identity.owner_and_name()
    return f"https://api.github.com/repos/{owner}/{repo}/actions/{suffix}"


def _identity_of(run: Mapping[str, Any]) -> RunIdentity:
    return RunIdentity(str(run["id"]), int(run["run_attempt"]), parse_moment(run["run_started_at"]))


def this_run(caller: GhCaller, run_id: str) -> RunIdentity:
    """run_id, run_attempt and run_started_at of the running workflow run, from the Actions API."""
    try:
        data = caller(_runs_url(f"runs/{run_id}"))
        if not isinstance(data, Mapping):
            raise ActionsUnavailable("no run answer")
        return _identity_of(data)
    except ActionsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- the class only
        raise ActionsUnavailable(type(exc).__name__) from None


def successful_runs(caller: GhCaller, since: datetime) -> list[PriorRun]:
    """This workflow's successful runs on main created since *since*, newest first."""
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = _runs_url(f"workflows/{WORKFLOW_FILE}/runs?status=success&branch=main&per_page=100&created=%3E%3D{stamp}")
    try:
        data = caller(url)
        if not isinstance(data, Mapping) or not isinstance(data.get("workflow_runs"), list):
            raise ActionsUnavailable("no runs answer")
        runs = [PriorRun(_identity_of(r), str(r.get("head_sha") or "")) for r in data["workflow_runs"]]
    except ActionsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ActionsUnavailable(type(exc).__name__) from None
    return sorted(runs, key=lambda r: r.identity.started, reverse=True)


def select_prior(runs: Sequence[PriorRun], current_run_id: str, now: datetime) -> list[PriorRun]:
    """The 4 newest earlier runs plus up to 4 aged 7 to 8 days (spans every 6-hourly merge and the weekly gc_ops)."""
    others = [r for r in runs if r.identity.run_id != current_run_id]
    recent = others[:RECENT_RUNS]
    low, high = now - timedelta(days=AGED_MAX_DAYS), now - timedelta(days=AGED_MIN_DAYS)
    aged = [r for r in others[RECENT_RUNS:] if low <= r.identity.started <= high]
    return [*recent, *aged[:AGED_RUNS]]


def git_show_file(sha: str) -> str | None:
    done = subprocess.run(
        ["git", "show", f"{sha}:{MODULE_PATH}"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT
    )
    return done.stdout if done.returncode == 0 else None


def shape_version_at(sha: str, git_show: GitShow = git_show_file) -> int | None:
    """PROBE_SHAPE_VERSION as the literal in this module at *sha* (never executed); None when unreadable."""
    if not _SHA.match(sha or ""):
        return None
    source = git_show(sha)
    if source is None:
        return None
    try:
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PROBE_SHAPE_VERSION" for t in node.targets
            ):
                value = ast.literal_eval(node.value)
                return value if isinstance(value, int) and not isinstance(value, bool) else None
    except (SyntaxError, ValueError):
        return None
    return None


def _loss_classes(result: compare.CompareResult) -> dict[str, int]:
    classes = result.classes()
    return {c: classes[c] for c in (*compare.ALARM_CLASSES, "race_duplicate_rows") if classes[c] > 0}


def post_persist(
    selected: Sequence[PriorRun],
    *,
    named: Named,
    tenant_id: str,
    project_id: str,
    git_show: GitShow = git_show_file,
    clock: Callable[[], float] = time.monotonic,
) -> PriorResult:
    """Re-read each selected earlier probe session and compare it with its rebuilt rows; unavailable stops further reads."""
    out = PriorResult()
    started = clock()
    for index, prior in enumerate(selected):
        if out.mode == "unavailable" or clock() - started >= POST_PERSIST_BUDGET_S:
            out.skipped += len(selected) - index
            break
        if shape_version_at(prior.head_sha, git_show) != PROBE_SHAPE_VERSION:
            out.skipped += 1
            continue
        _, batches = expected_batches(prior.identity)
        try:
            pages = read_tree(named, session_id_for(tenant_id, project_id, prior.identity))
        except Exception:  # noqa: BLE001 -- a read that did not answer 200 is unavailable, never a loss
            out.mode = "unavailable"
            out.skipped += len(selected) - index
            break
        result = compare.compare_tree(batches, decode_rows(pages), producer=PRODUCER, parser_version=PARSER_VERSION)
        if result.total("other_generation_rows"):
            out.skipped += 1
            continue
        out.checked += 1
        classes = _loss_classes(result)
        if not pages:
            classes = {"missing_in_store": max(1, classes.get("missing_in_store", 0))}
        if classes:
            out.lost += 1
            out.lost_classes.update(classes)
    return out
