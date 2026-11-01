"""Fetch a failed Actions run into a bounded, typed CI-RCA evidence envelope."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional, cast

from scripts.ci_rca.log_evidence import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    SCHEMA,
    bound_text_windowed,
    publish_envelope,
    recovery_url,
)

_TRANSIENT_ERROR_RE = re.compile(r"^(?:failed to get (?:jobs|run)\b|HTTP 5\d\d\b|Service Unavailable)", re.I | re.M)
_Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class FetchOutcome:
    fetched: bool
    attempts_used: int
    diagnostic: Optional[str] = None


@dataclass
class LogResult:
    returncode: int
    stdout: str
    stderr: str
    truncated: bool
    truncation_reason: str | None
    tail: str = ""


def _run(runner: _Runner, command: list[str]) -> subprocess.CompletedProcess:
    return runner(command, capture_output=True, text=True, encoding="utf-8", errors="replace")


_STDOUT_CHUNK_BYTES = 8192


def _head_piece(chunk: bytes, head: bytearray, head_lines: int, max_bytes: int, max_lines: int) -> bytes:
    """The prefix of `chunk` that fits within the still-remaining head byte/line budget --
    factored out so _run_log's own branch count (Decision 43) absorbs one Call node. Only called
    while head is not yet full (head_lines < max_lines, len(head) < max_bytes), so the remaining
    line budget is always positive here."""
    piece = chunk[: max_bytes - len(head)]
    head_room_lines = max_lines - head_lines
    newline_positions = [i for i, byte in enumerate(piece) if byte == 0x0A]
    if len(newline_positions) >= head_room_lines:
        piece = piece[: newline_positions[head_room_lines - 1] + 1]
    return piece


def _complete_prefix_len(data: bytes) -> int:
    """Length of `data` without a trailing incomplete UTF-8 character (invalid bytes elsewhere still count)."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0xC0 != 0x80:
            need = 1 if byte < 0xC0 else 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return len(data) - (back if need > back else 0)
    return len(data)


def _trim_tail_overlap(head: bytes, tail: bytes, total: int) -> bytes:
    """Drop the leading bytes of `tail` that the decoded head already holds. `tail` covers stream
    bytes [total - len(tail), total); the decoded head ends at the last complete character it
    holds, so the cut lands on a character start and never repeats or drops a character."""
    head_end = _complete_prefix_len(head)
    return tail[max(0, head_end - (total - len(tail))) :]


def _run_log(
    command: list[str],
    max_bytes: int,
    max_lines: int,
    *,
    drain_max_bytes: int = 64 * 1024 * 1024,
    drain_timeout_s: float = 300.0,
) -> LogResult:
    """Stream a subprocess's stdout/stderr without blocking on a full OS pipe (which would hang).

    Retains a bounded HEAD buffer (first max_bytes bytes / max_lines lines -- same semantics as
    the prior 1-byte-read implementation, now filled via 8192-byte chunked reads) plus a bounded
    ROLLING TAIL buffer (the most recent up-to-max_bytes bytes seen), so an oversized log's
    "Failed checks:" tail survives even though the head alone cannot hold it. Draining continues
    past the head cap (never killing early) until EOF or an explicit drain ceiling
    (drain_max_bytes total bytes read, or drain_timeout_s wall-clock) is hit, at which point the
    child is killed -- truncated=True, truncation_reason="drain_ceiling", returncode=0 (a killed
    child must never poison the exit code). A head-capped-but-fully-drained run is NOT killed:
    truncated=True, truncation_reason="head_tail_window", and the real process returncode is
    preserved. `tail` is populated only when the head was actually capped -- otherwise head IS
    the complete content and no separate tail is needed. The tail is trimmed so head plus tail
    never duplicates bytes (rec-3002).
    """
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    head = bytearray()
    tail = bytearray()
    stderr = bytearray()
    head_lines = 0
    head_full = False
    total_drained = 0
    started_at = time.monotonic()
    killed = False
    reason: str | None = None
    while selector.get_map() and not killed:
        if time.monotonic() - started_at > drain_timeout_s:
            killed, reason = True, "drain_ceiling"
            break
        for key, _ in selector.select(timeout=1.0):
            stream = cast(BinaryIO, key.fileobj)
            # os.read() (not stream.read()) -- a BufferedReader.read(n) blocks trying to fill
            # the full n bytes even after select() reports the fd merely READY (some data, not
            # necessarily n bytes), which silently defeats drain_timeout_s against a child that
            # writes a little then goes quiet without closing the pipe.
            chunk = os.read(stream.fileno(), _STDOUT_CHUNK_BYTES)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            if key.data == "stderr":
                if len(stderr) < 8192:
                    stderr.extend(chunk[: 8192 - len(stderr)])
                continue
            total_drained += len(chunk)
            if total_drained > drain_max_bytes:
                killed, reason = True, "drain_ceiling"
                break
            if not head_full:
                piece = _head_piece(chunk, head, head_lines, max_bytes, max_lines)
                head.extend(piece)
                head_lines += piece.count(b"\n")
                head_full = len(head) >= max_bytes or head_lines >= max_lines
            tail.extend(chunk)
            if len(tail) > max_bytes:
                del tail[: len(tail) - max_bytes]
        if killed:
            break
    try:
        if killed:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
    finally:
        selector.close()
    if killed:
        return LogResult(
            returncode=0,
            stdout=bytes(head).decode("utf-8", errors="ignore"),
            stderr=bytes(stderr).decode("utf-8", errors="replace"),
            truncated=True,
            truncation_reason=reason,
        )
    # Genuine truncation is "more stdout was drained than head retained" -- NOT merely "head
    # reached its cap", which also fires when the true content ends EXACTLY at the cap (no
    # overflow). total_drained counts every stdout byte read regardless of head_full state, so
    # this stays correct across chunk boundaries and both the byte-cap and line-cap paths.
    genuinely_truncated = total_drained > len(head)
    return LogResult(
        returncode=process.returncode,
        stdout=bytes(head).decode("utf-8", errors="ignore"),
        stderr=bytes(stderr).decode("utf-8", errors="replace"),
        truncated=genuinely_truncated,
        truncation_reason="head_tail_window" if genuinely_truncated else None,
        tail=_trim_tail_overlap(bytes(head), bytes(tail), total_drained).decode("utf-8", errors="ignore")
        if genuinely_truncated
        else "",
    )


def _decode_jobs_payload(payload: str) -> list[dict[str, Any]]:
    decoded = json.loads(payload)
    jobs = decoded.get("jobs") if isinstance(decoded, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("GitHub response has no jobs array")
    return jobs


def _failed_jobs(payload: str) -> list[dict[str, Any]]:
    jobs = _decode_jobs_payload(payload)
    selected: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("conclusion") not in {"failure", "timed_out", "cancelled"}:
            continue
        job_id = job.get("databaseId")
        if not isinstance(job_id, int) or job_id < 1:
            raise ValueError("failed job has no numeric database id")
        steps = job.get("steps", [])
        failed_steps = [
            {"step_index": index, "conclusion": step["conclusion"]}
            for index, step in enumerate(steps, 1)
            if isinstance(step, dict) and step.get("conclusion") in {"failure", "timed_out", "cancelled"}
        ]
        selected.append({"job_id": job_id, "conclusion": job["conclusion"], "failed_steps": failed_steps})
    selected.sort(key=lambda item: item["job_id"])
    if not selected:
        raise ValueError("run metadata contains no failed jobs")
    return selected


_FAILED_STEP_CONCLUSIONS = {"failure", "timed_out", "cancelled"}
_SKIPPED_PRIMARY = LogResult(returncode=1, stdout="", stderr="", truncated=False, truncation_reason=None)


def _is_unfinalized(job: dict[str, Any]) -> bool:
    steps = [step for step in job.get("steps", []) if isinstance(step, dict)]
    has_failed_step = any(step.get("conclusion") in _FAILED_STEP_CONCLUSIONS for step in steps)
    return not has_failed_step or any(not step.get("conclusion") for step in steps)


def _unfinalized_job_ids(payload: str, failed_jobs: list[dict[str, Any]]) -> set[int]:
    """Failed jobs whose step metadata GitHub has not finalized (rec-4776): no failed step, or a
    step with no conclusion. Their step-split logs omit the unfinished step, so they read the raw job log."""
    failed_ids = {job["job_id"] for job in failed_jobs}
    return {
        job["databaseId"]
        for job in _decode_jobs_payload(payload)
        if isinstance(job, dict) and job.get("databaseId") in failed_ids and _is_unfinalized(job)
    }


def _job_log_command(run_id: str, repo: str, job_id: int, unfinalized: bool) -> list[str]:
    if unfinalized:
        return ["gh", "api", f"repos/{repo}/actions/jobs/{job_id}/logs"]
    return ["gh", "run", "view", run_id, "--repo", repo, "--job", str(job_id), "--log"]


def _window_fragment(result: Any, remaining: int, remaining_lines: int) -> str:
    """A fragment is windowed only when the transport returned a tail (head and tail both observed)."""
    tail = getattr(result, "tail", "")
    return bound_text_windowed(result.stdout + tail, remaining, remaining_lines)[0] if tail else result.stdout


def _step_scope(
    payload: str,
    job_ids: set[int],
    queried_job_ids: set[int],
    retrieval_path: str,
) -> list[dict[str, Any]]:
    """Build the per-job evidence-scope table: EVERY executed step of each failed job, with
    log_retrieved derived STRUCTURALLY (AC3) -- never by searching `body`.

    On retrieval_path="primary", `--log-failed` returns only failed steps' logs, so
    log_retrieved is true exactly for steps whose conclusion is in the failed-step domain
    (failure/timed_out/cancelled). On "fallback", `gh run view --job <id> --log` returns a
    queried job's WHOLE log, so log_retrieved is true for every step of a queried job and
    false for every step of an unqueried job. No step names are emitted (AC17).
    """
    jobs = _decode_jobs_payload(payload)
    scope: list[dict[str, Any]] = []
    for job in jobs:
        job_id = job.get("databaseId") if isinstance(job, dict) else None
        if job_id not in job_ids:
            continue
        job_retrieved = job_id in queried_job_ids
        steps = job.get("steps", [])
        entries: list[dict[str, Any]] = []
        for index, step in enumerate(steps, 1):
            conclusion = (step.get("conclusion") if isinstance(step, dict) else None) or None
            if retrieval_path == "primary":
                retrieved = conclusion in {"failure", "timed_out", "cancelled"}
            else:
                retrieved = job_retrieved
            entries.append({"step_index": index, "conclusion": conclusion, "log_retrieved": retrieved})
        scope.append({"job_id": job_id, "steps": entries})
    scope.sort(key=lambda item: item["job_id"])
    return scope


def _diagnose(*stderrs: str) -> str:
    for stderr in stderrs:
        match = _TRANSIENT_ERROR_RE.search(stderr or "")
        if match:
            return f"gh reported a transient error: {match.group(0)!r}"
    return "log empty, unavailable, or gh reported a non-transient error"


def _fallback_logs(
    run_id: str,
    repo: str,
    jobs: list[dict[str, Any]],
    injected_runner: Optional[_Runner],
    max_bytes: int,
    max_lines: int,
    unfinalized_ids: set[int],
) -> tuple[str, list[int], list[str], bool, str | None]:
    fragments: list[str] = []
    queried: list[int] = []
    errors: list[str] = []
    truncated = False
    reason = None
    for job in jobs:
        command = _job_log_command(run_id, repo, job["job_id"], job["job_id"] in unfinalized_ids)
        remaining = max_bytes - len("".join(fragments).encode("utf-8"))
        remaining_lines = max_lines - len("".join(fragments).splitlines(keepends=True))
        if remaining < 1 or remaining_lines < 1:
            return "".join(fragments), queried, errors, True, "byte_limit" if remaining < 1 else "line_limit"
        result = (
            _run(injected_runner, command) if injected_runner is not None else _run_log(command, remaining, remaining_lines)
        )
        queried.append(job["job_id"])
        if result.returncode != 0 or not result.stdout:
            errors.append(result.stderr)
            continue
        fragments.append(_window_fragment(result, remaining, remaining_lines))
        truncated = truncated or bool(getattr(result, "truncated", False))
        reason = getattr(result, "truncation_reason", None) or reason
    return "".join(fragments), queried, errors, truncated, reason


def fetch_run_log(
    run_id: str,
    repo: str,
    out_path: Path,
    attempts: int = 3,
    sleep_fn: Optional[Callable[[int], None]] = None,
    runner: Optional[_Runner] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> FetchOutcome:
    if max_bytes < 1 or max_lines < 1:
        raise ValueError("evidence limits must be positive")
    sleep_fn = sleep_fn or time.sleep
    injected_runner = runner
    runner = runner or subprocess.run
    out_path.unlink(missing_ok=True)
    diagnostic = None
    for attempt in range(1, attempts + 1):
        metadata = _run(runner, ["gh", "run", "view", run_id, "--repo", repo, "--json", "jobs"])
        if metadata.returncode != 0:
            diagnostic = _diagnose(metadata.stderr)
        else:
            try:
                jobs = _failed_jobs(metadata.stdout)
            except (ValueError, json.JSONDecodeError) as exc:
                diagnostic = f"invalid failed-job metadata: {exc}"
            else:
                unfinalized_ids = _unfinalized_job_ids(metadata.stdout, jobs)
                primary_command = ["gh", "run", "view", run_id, "--repo", repo, "--log-failed"]
                primary = (
                    _SKIPPED_PRIMARY
                    if unfinalized_ids
                    else _run(injected_runner, primary_command)
                    if injected_runner is not None
                    else _run_log(primary_command, max_bytes, max_lines)
                )
                log = primary.stdout if primary.returncode == 0 else ""
                transport_truncated = bool(getattr(primary, "truncated", False))
                transport_reason = getattr(primary, "truncation_reason", None)
                retrieval_path = "primary"
                fallback_errors: list[str] = []
                queried_job_ids: list[int] = []
                if not log:
                    retrieval_path = "fallback"
                    log, queried_job_ids, fallback_errors, transport_truncated, transport_reason = _fallback_logs(
                        run_id, repo, jobs, injected_runner, max_bytes, max_lines, unfinalized_ids
                    )
                    if fallback_errors:
                        log = ""
                else:
                    queried_job_ids = [job["job_id"] for job in jobs]
                if log:
                    # Ownership (ci-rca-evidence-fidelity): bound_text_windowed is the SOLE
                    # producer of the published body. The primary transport (_run_log) may
                    # additionally supply `tail` -- a rolling sample of the stream's own end,
                    # captured only when its head buffer was capped -- which is appended before
                    # windowing so the tail anchor survives even though the transport's own head
                    # alone could not hold it; bound_text_windowed then re-derives a SMALLER,
                    # budget-correct head+marker+tail from that composition (never re-truncated
                    # from the head alone, which would silently drop the tail again).
                    primary_tail = getattr(primary, "tail", "") if retrieval_path == "primary" else ""
                    text_for_window = log + primary_tail if primary_tail else log
                    body, limits = bound_text_windowed(text_for_window, max_bytes, max_lines)
                    # A reliable window (head AND tail both genuinely observed) needs no
                    # override -- bound_text_windowed's own accurate counts stand. Any other
                    # transport truncation (a killed drain_ceiling primary fetch, or ANY fallback
                    # truncation, which composes per-job windows already cut to budget) means the true
                    # extent is genuinely unknown, matching the pre-existing "unknown" contract.
                    reliable_window = retrieval_path == "primary" and transport_reason == "head_tail_window"
                    if transport_truncated and not reliable_window:
                        limits.update(
                            complete=False,
                            truncation_reason=transport_reason or "source_unavailable",
                            observed_bytes=None,
                            observed_lines=None,
                            omitted_bytes=None,
                            omitted_lines=None,
                        )
                    if body:
                        scope = _step_scope(
                            metadata.stdout,
                            {job["job_id"] for job in jobs},
                            set(queried_job_ids),
                            retrieval_path,
                        )
                        envelope = {
                            "schema": SCHEMA,
                            "identity": {"repository": repo, "run_id": int(run_id)},
                            "failed_jobs": jobs,
                            "retrieval_path": retrieval_path,
                            "scope": scope,
                            "fallback_selection": {
                                "queried_job_ids": queried_job_ids,
                                "unqueried_job_ids": [job["job_id"] for job in jobs if job["job_id"] not in queried_job_ids],
                                "unqueried_reason": (
                                    "aggregate_limit"
                                    if retrieval_path == "fallback" and len(queried_job_ids) < len(jobs)
                                    else None
                                ),
                            },
                            "body": body,
                            "limits": limits,
                            "recovery": {"url": recovery_url(repo, run_id), "state": "available"},
                        }
                        publish_envelope(out_path, envelope)
                        return FetchOutcome(True, attempt)
                diagnostic = _diagnose(primary.stderr, *fallback_errors)
        if attempt < attempts:
            sleep_fn(attempt * 10)
    return FetchOutcome(False, attempts, diagnostic)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch bounded failed-run evidence for CI-RCA.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    args = parser.parse_args(argv)
    try:
        outcome = fetch_run_log(
            args.run_id, args.repo, args.out, args.attempts, max_bytes=args.max_bytes, max_lines=args.max_lines
        )
    except (OSError, ValueError) as exc:
        outcome = FetchOutcome(False, 0, str(exc))
    if outcome.fetched:
        return 0
    print(
        "::error::ci-rca: bounded log evidence unavailable; refusing agent invocation "
        f"after {outcome.attempts_used} attempts (rec-2117 / rec-2118 / rec-2718). {outcome.diagnostic or ''}".rstrip()
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
