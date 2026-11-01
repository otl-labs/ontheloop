"""The telemetry causal-chain probe (T3.2, telemetry slice 5): one synthetic root session through the production pipeline.

PRODUCE -> TRANSPORT -> PERSIST -> QUERY -> ASSERT. The probe produces one tiny root session through the real claude_code
producer (a tool result large enough to take the claim-check route), appends it through the production telemetry writer, proves
it readable through the production telemetry reader within SLA_SECONDS, then re-reads earlier probe sessions (the post-persist
leg, lambda_telemetry_causal_chain_prior) to catch rows lost or changed after commit. Each failing stage has its own exit code
and files its own telemetry_verify alarm; an infrastructure that cannot answer is exit 3 and files nothing (Decision 215 cl.2).

An alarm, not a gate (Decision 210 cl.2), run by .github/workflows/telemetry-causal-chain.yml every 6 hours. Output is one
CAUSAL_CHAIN line of counts and class names only: never a session id, project id, ref, URL, row value or body (Decision 101).
scripts/telemetry_verify stays the producer-independent oracle; this module imports from it and nothing flows back.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.convergence_health.approvals import _make_github_caller
from scripts.ducklake_smoke import lambda_telemetry_causal_chain_prior as prior
from scripts.ducklake_smoke import lambda_telemetry_golden_gates as golden
from scripts.telemetry_capture import transport
from scripts.telemetry_capture.state import DEFAULT_CATALOG, paired_catalog
from scripts.telemetry_verify import compare, derive, monitor_compare
from scripts.telemetry_verify.alarms import raise_alarms
from scripts.telemetry_verify.rows import decode_rows, read_tree
from src.telemetry_client import blob_delivery as bd
from src.turn_capture.record_turn import PARSER_VERSION, PRODUCER

SLA_SECONDS = 180
POLL_INTERVAL_S = 10
WRITER_RETRIES = 2
BACKOFF_S = 2.0
WARMUP_READS = 2
MODULE_FILE = "scripts/ducklake_smoke/lambda_telemetry_causal_chain.py"
STATE_VERB, WINDOW_VERB = "session_state_and_duration", "sessions_window"
CALLED_VERBS = (STATE_VERB, golden.TREE_VERB, WINDOW_VERB)
EXIT_CODES = {
    "pass": 0,
    "incomplete": 1,
    "unavailable": 3,
    "produce": 10,
    "transit": 11,
    "persist": 12,
    "query": 13,
    "projection": 14,
    "post_persist_loss": 15,
}
LOSS_STAGES = ("produce", "transit", "persist", "query", "projection")
ALARM_PREFIX = "causal_chain_"
ACCEPTANCE = " ".join(
    (
        "AWS_PROFILE=agent_platform bin/venv-python -m scripts.ducklake_smoke.lambda_telemetry_causal_chain",
        "--no-alarms --assert-pass",
    )
)
ACCEPTANCE_PRIOR = ACCEPTANCE + " --require-prior"


class Stop(Exception):
    """A stage failed; *stage* is a key of EXIT_CODES. Carries a class name only, never a body or a value."""

    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(f"{stage} {detail}".strip())
        self.stage, self.detail = stage, detail


def acceptance_for(cls: str) -> str:
    return ACCEPTANCE_PRIOR if cls == ALARM_PREFIX + "post_persist_loss" else ACCEPTANCE


@dataclass
class Hooks:
    """Everything outside the production pipeline, injectable: Actions caller, sleep, wall clock, git and the portal."""

    caller: Callable[[str], Any] | None = None
    sleep: Callable[[float], None] = time.sleep
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    git_show: prior.GitShow = prior.git_show_file
    portal: Callable[..., Any] | None = None


@dataclass
class Report:
    stage: str = "pass"
    mode: str = "local"
    elapsed_s: int = 0
    visible_after_s: str = "na"
    reader_calls: int = 0
    writer_calls: int = 0
    blobs: int = 0
    real_window_hits: str = "na"
    prior: prior.PriorResult | None = None
    prior_mode: str = "skipped"
    skew: int = 0
    alarm_filing: str = "none"
    exit_code: int = 0
    detail: str = ""

    def line(self) -> str:
        done = self.prior or prior.PriorResult()
        return (
            f"CAUSAL_CHAIN stage={self.stage} exit={self.exit_code} mode={self.mode} elapsed_s={self.elapsed_s} "
            f"visible_after_s={self.visible_after_s} reader_calls={self.reader_calls} writer_calls={self.writer_calls} "
            f"blobs={self.blobs} real_window_hits={self.real_window_hits} prior_mode={self.prior_mode} "
            f"prior_checked={done.checked} prior_lost={done.lost} prior_skipped={done.skipped} skew={self.skew} "
            f"alarm_filing={self.alarm_filing}" + (f" class={self.detail}" if self.detail else "")
        )


def _transport_stop(status: int) -> str:
    if status in (0, 403, 404):
        return "transit"
    return "unavailable" if status in (429, 502, 503, 504) else "persist"


def _blob_stage(exc: bd.BlobDeliveryError) -> str:
    if exc.kind == "transient":
        return "transit" if exc.status is None else "unavailable"
    if exc.kind == "proxy":
        return "transit"
    if exc.kind == "refused":
        return "persist"
    return "persist" if exc.status is not None else "produce"


class _Probe:
    def __init__(self, env: golden.Env, hooks: Hooks, report: Report) -> None:
        self.env, self.hooks, self.report = env, hooks, report
        self.protocol: bd.BlobProtocol | None = None
        self.tenant_id = self.project_id = ""
        self.tables: dict[str, list[dict[str, Any]]] = {}

    def write(self, payload: dict[str, Any], retry: bool = False) -> tuple[int, dict[str, Any]]:
        payload = json.loads(transport.encode(payload))
        for attempt in range(WRITER_RETRIES + 1):
            self.report.writer_calls += 1
            try:
                status, body = self.env.writer(payload)
            except (OSError, TimeoutError):
                status, body = 0, {}
            if retry and status in transport.RETRY_STATUSES and attempt < WRITER_RETRIES:
                self.hooks.sleep(BACKOFF_S * 2**attempt)
                continue
            return status, body
        raise AssertionError("unreachable")  # pragma: no cover

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.report.reader_calls += 1
        return list(self.env.reader.named(verb, **params))

    def warm(self) -> None:
        """Stage 0: the writer and the reader both answer and both serve the deploy output's catalog."""
        status, body = self.write({"action": "describe"})
        if status != 200:
            raise Stop("unavailable", "describe")
        try:
            self.protocol = bd.protocol_from_describe(body)
        except bd.HandshakeError:
            raise Stop("incomplete", "describe_mismatch") from None
        if paired_catalog(body.get("catalog")) != self.env.catalog:
            raise Stop("incomplete", "writer_catalog_mismatch")
        rows: list[dict[str, Any]] | None = None
        for _ in range(WARMUP_READS):
            try:
                rows = self.named("telemetry_health_count")
                break
            except Exception:  # noqa: BLE001 -- a cold or failing reader is unavailable; the class is not interesting
                continue
        if rows is None:
            raise Stop("unavailable", "reader_warmup")
        own_reader = self.env.catalog != DEFAULT_CATALOG
        try:
            binding = self.env.reader.binding() if own_reader else None
        except Exception:  # noqa: BLE001
            raise Stop("unavailable", "reader_binding") from None
        tables = [row.get("table_name") for row in rows]
        if (
            set(tables) != golden.HEALTH_TABLES
            or len(tables) != len(golden.HEALTH_TABLES)
            or any(row.get("meta_schema") != self.env.catalog for row in rows)
            or (own_reader and paired_catalog(binding) != self.env.catalog)
        ):
            raise Stop("incomplete", "reader_catalog_mismatch")

    def identity(self) -> prior.RunIdentity:
        """Stage 2 identity: Actions mode takes the run's id, attempt and start from the API, local mode from the clock."""
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if run_id.isdigit() and self.hooks.caller is not None:
            self.report.mode = "actions"
            try:
                return prior.this_run(self.hooks.caller, run_id)
            except prior.ActionsUnavailable as exc:
                raise Stop("unavailable", "actions_" + str(exc)) from None
        now = self.hooks.utcnow().replace(microsecond=0)
        return prior.RunIdentity(f"local-{int(now.timestamp())}-{secrets.token_hex(3)}", 1, now)

    def register(self) -> None:
        status, body = self.write({"action": "register_tenant"}, retry=True)
        if status != 200 or not isinstance(body.get("tenant_id"), str):
            raise Stop(_transport_stop(status), "register_tenant")
        self.tenant_id = body["tenant_id"]
        status, body = self.write({"action": "register_project", "project_ref": prior.PROBE_REF}, retry=True)
        if status != 200 or not isinstance(body.get("project_id"), str):
            raise Stop(_transport_stop(status), "register_project")
        self.project_id = body["project_id"]

    def produce(self, identity: prior.RunIdentity) -> tuple[Any, list[tuple[str, list[dict[str, Any]]]]]:
        assert self.protocol is not None
        try:
            result, batches = prior.expected_batches(identity)
        except Exception as exc:  # noqa: BLE001 -- the failure's class only
            raise Stop("produce", type(exc).__name__) from None
        if not result.sessions:
            raise Stop("produce", "empty_result")
        sizes = [row["content_bytes"] for row in result.transcripts if row.get("content_bytes")]
        if not sizes or not (self.protocol.inline_threshold_bytes < max(sizes) <= self.protocol.cap_bytes):
            raise Stop("produce", "probe_payload_outside_blob_band")
        return result, batches

    def send(self, batches: list[tuple[str, list[dict[str, Any]]]]) -> None:
        """Stage 3: deliver the claim-check blobs, then append every row through the production writer."""
        assert self.protocol is not None
        items: list[tuple[str, dict[str, Any]]] = []
        for table, rows in batches:
            if table == "telemetry_transcripts" and rows:
                try:
                    done = bd.deliver_blobs(
                        rows,
                        project_ref=prior.PROBE_REF,
                        invoke=self.write,
                        protocol=self.protocol,
                        component=transport.COMPONENT,
                        put=self.env.put,
                        sleep=self.hooks.sleep,
                    )
                except bd.BlobDeliveryError as exc:
                    raise Stop(_blob_stage(exc), "blob_" + exc.kind) from None
                self.report.blobs += done.uploaded + done.existed
                rows = done.wire_rows
            items.extend((table, row) for row in rows)
        limits = transport.Limits(self.protocol.max_items, self.protocol.inline_threshold_bytes, self.protocol.cap_bytes)
        for request in transport.chunk(items, prior.PROBE_REF, limits):
            # A retry after a mid-flight connection reset could race the first commit (race_duplicate_rows); the client
            # timeout exceeds the writer Lambda's, so only a reset leaves that residual, and the query leg counts it.
            status, _ = self.write(transport.append_body(prior.PROBE_REF, request), retry=True)
            if status != 200:
                raise Stop(_transport_stop(status), "append")

    def query(self, batches: list[tuple[str, list[dict[str, Any]]]], session_id: str) -> list[list[dict[str, Any]]]:
        """Stage 4: poll the tree until every sent row reads back, within SLA_SECONDS from the last append's answer."""
        begun, answered = self.env.clock(), False
        while True:
            try:
                pages = read_tree(self.named, session_id)
                answered = True
            except Exception:  # noqa: BLE001 -- a read that does not answer 200 is not evidence of loss
                pages = None
            if pages is not None:
                stored = decode_rows(pages)
                result = compare.compare_tree(batches, stored, producer=PRODUCER, parser_version=PARSER_VERSION)
                if pages and not result.total("missing_in_store"):
                    self.report.visible_after_s = str(int(self.env.clock() - begun))
                    self.tables = stored
                    self._fresh_tree(result, pages)
                    return pages
            if self.env.clock() - begun >= SLA_SECONDS:
                raise Stop("query" if answered else "unavailable", "visible_within_sla")
            self.hooks.sleep(POLL_INTERVAL_S)

    def _fresh_tree(self, result: compare.CompareResult, pages: list[list[dict[str, Any]]]) -> None:
        found = {c: result.total(c) for c in (*compare.ALARM_CLASSES, "race_duplicate_rows") if c != "missing_in_store"}
        collided = {row["roots_in_tree"] for page in pages for row in page} != {1}
        if any(found.values()) or collided:
            raise Stop("persist", "stored_rows_differ")

    def project(self, session_id: str, identity: prior.RunIdentity) -> None:
        """Stage 5: the deployed state verb equals the oracle's derivation, and the probe stays out of real reads."""
        as_of = self.hooks.utcnow()
        as_of = as_of.replace(microsecond=as_of.microsecond // 1000 * 1000)
        try:
            deployed = self.named(STATE_VERB, session_id=session_id, as_of=as_of.isoformat())
            window = self.named(
                WINDOW_VERB,
                day_from=identity.started.date().isoformat(),
                day_to=as_of.date().isoformat(),
                as_of=as_of.isoformat(),
            )
        except Exception:  # noqa: BLE001
            raise Stop("unavailable", "projection_read") from None
        # One bounded answer: the reader caps a window read, so a very large real window could hide a leaked probe row. The
        # exclusion itself (Decision 236 cl.1) is by project purpose; this is the standing end-to-end check of it.
        derived = derive.session_state_and_duration(self.tables, session_id, as_of)
        hits = sum(1 for row in window if row.get("project_id") == self.project_id or row.get("session_id") == session_id)
        self.report.real_window_hits = str(hits)
        row = deployed[0] if deployed else {}
        if (
            monitor_compare.compare_session(STATE_VERB, deployed, derived)
            or row.get("state") != "success"
            or row.get("duration_seconds") is None
            or hits
        ):
            raise Stop("projection", "state_or_window")


def _commit() -> str:
    done = subprocess.run(
        ["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, encoding="utf-8", cwd=golden.ROOT
    )
    return done.stdout.strip() or "unknown"


def file_alarms(findings: dict[str, int], hooks: Hooks) -> str:
    """One telemetry_verify alarm per class, counted only; a filing failure is reported and never changes the exit code."""
    try:
        raise_alarms(
            findings,
            scope=_commit(),
            alarm_at=None,
            now=hooks.utcnow(),
            portal=hooks.portal,
            file=MODULE_FILE,
            acceptance_for=acceptance_for,
        )
        return "ok"
    except Exception:  # noqa: BLE001
        return "failed"


def _prior_leg(probe: _Probe, identity: prior.RunIdentity, hooks: Hooks, report: Report) -> None:
    if hooks.caller is None:
        return
    report.prior_mode = "unavailable"
    now = hooks.utcnow()
    try:
        runs = prior.successful_runs(hooks.caller, now - timedelta(days=prior.AGED_MAX_DAYS))
    except prior.ActionsUnavailable:
        return
    current = identity.run_id if report.mode == "actions" else ""
    chosen = prior.select_prior(runs, current, now)
    report.prior = prior.post_persist(
        chosen,
        named=probe.named,
        tenant_id=probe.tenant_id,
        project_id=probe.project_id,
        git_show=hooks.git_show,
        clock=probe.env.clock,
    )
    report.prior_mode = report.prior.mode


def run(env: golden.Env, hooks: Hooks, report: Report, args: argparse.Namespace) -> None:
    probe = _Probe(env, hooks, report)
    started = env.clock()
    skew, warmed = 0, False
    failure: Stop | None = None
    identity: prior.RunIdentity | None = None
    try:
        probe.warm()
        skew, warmed = int(golden.skewed(env, CALLED_VERBS)), True
        identity = probe.identity()
        probe.register()
        _, batches = probe.produce(identity)
        probe.send(batches)
        session_id = prior.session_id_for(probe.tenant_id, probe.project_id, identity)
        probe.query(batches, session_id)
        probe.project(session_id, identity)
    except Stop as stop:
        failure = stop
        report.detail = stop.detail
    if failure is None or (warmed and failure.stage in LOSS_STAGES):
        skew |= int(_guarded_skew(env))
    if identity is not None and probe.project_id and (failure is None or failure.stage in LOSS_STAGES):
        _prior_leg(probe, identity, hooks, report)
    stage = failure.stage if failure else "pass"
    done = report.prior
    if stage == "pass" and done is not None and done.lost:
        stage = "post_persist_loss"
    elif stage == "pass" and (
        report.prior_mode == "unavailable" or (args.require_prior and (done is None or not done.checked))
    ):
        stage = "unavailable"
    report.skew, report.stage, report.exit_code = skew, stage, EXIT_CODES[stage]
    report.elapsed_s = int(env.clock() - started)
    findings: dict[str, int] = {ALARM_PREFIX + stage: 1} if stage in LOSS_STAGES else {}
    if done is not None and done.lost:
        findings[ALARM_PREFIX + "post_persist_loss"] = done.lost
    if findings and not args.no_alarms and not skew:
        report.alarm_filing = file_alarms(findings, hooks)


def _guarded_skew(env: golden.Env) -> bool:
    try:
        return bool(golden.skewed(env, CALLED_VERBS))
    except Exception:  # noqa: BLE001 -- a failing guard cannot explain the failure; the stage code stands
        return False


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description="Run the telemetry causal-chain probe against the production telemetry path.")
    built.add_argument("--no-alarms", action="store_true", help="never file telemetry_verify recs")
    built.add_argument("--assert-pass", action="store_true", help="exit 0 only after a clean, unskewed pass")
    built.add_argument("--require-prior", action="store_true", help="exit 3 when no earlier probe session was re-read")
    built.add_argument("--profile", default=None)
    built.add_argument("--region", default="eu-west-2")
    return built


def main(argv: list[str] | None = None, *, env: golden.Env | None = None, hooks: Hooks | None = None) -> int:
    logging.disable(logging.CRITICAL)
    args = parser().parse_args(argv)
    hooks = hooks or Hooks()
    if hooks.caller is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        hooks.caller = _make_github_caller(token) if token else None
    report = Report()
    if env is None:
        try:
            env = golden.live_env(args.profile, args.region)
        except golden.ReplayStop as stop:
            report.stage, report.exit_code = "incomplete", 1
            print(f"{report.line()} class={stop.kind}")
            return 1
        except Exception as exc:  # noqa: BLE001 -- credentials or network: nothing was written
            report.stage, report.exit_code = "unavailable", 3
            print(f"{report.line()} class={type(exc).__name__}")
            return 3
    try:
        run(env, hooks, report, args)
    except Exception as exc:  # noqa: BLE001 -- the class only
        report.stage, report.exit_code = "incomplete", 1
        print(f"{report.line()} class={type(exc).__name__}")
        return 1
    print(report.line())
    if report.stage == "incomplete":
        return 1
    if report.skew and args.assert_pass and report.exit_code == 0:
        return 1
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
