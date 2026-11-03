"""The deployed-engine leg of the telemetry golden harness (telemetry slice 4, rec-4025): replay the golden transcript trees
through the deployed telemetry writer and read them back through the reader serving the writer's catalog (the routed reader,
scripts/telemetry_capture/read_route.py). Before any registration the run asserts that the writer and the reader both serve
the catalog the deploy output names.

Run by .github/workflows/telemetry-golden-replay.yml after every successful DuckLake deploy and daily. For every replayable
(tree, pass) of the two derived_golden files it mints one synthetic project (Decision 199 cl.3: a fresh project re-derives
every id), runs the real producer over the case, sends the batches on the capture transport's request shape, then asserts
through the reader that session_tree_rows holds exactly the sent rows and that every session-bound expectation holds. The
cases are read as data from tests/fixtures/ (scripts never import tests/). Output is counts, verb names and tree names only:
never a row value, a response body, a session id or a project ref. A mismatch or a run that cannot complete files a
telemetry_verify alarm; a replay of one commit against another's deployed code proves nothing, so a version skew files nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from scripts.ducklake_smoke import core
from scripts.telemetry_capture import transport
from scripts.telemetry_capture.state import DEFAULT_CATALOG, paired_catalog
from scripts.telemetry_verify.alarms import raise_alarms
from scripts.telemetry_verify.rows import decode_rows, read_tree
from src.common import ducklake_event_reads as ev
from src.telemetry.identity import derive_entity_key
from src.telemetry_client import blob_delivery as bd
from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import MemTree

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "turn_capture" / "golden"
EXPECTATIONS = ("derived_golden_state.yaml", "derived_golden_rollups.yaml")
EXPECTATION_DIR = ROOT / "tests" / "telemetry" / "fixtures"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-ducklake-lambdas.yml"
WRITER_FUNCTION = "agent-platform-ducklake-telemetry-writer"
HEALTH_TABLES = frozenset({"telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"})
RECORD_BUCKET = "agent-platform-data-lake"
SESSION_VERBS = (
    "session_state_and_duration",
    "agent_run_state_and_duration",
    "agent_run_token_rollup",
    "session_process_event_rollup",
    "session_friction_rollup",
    "model_call_cost_estimate",
)
TREE_VERB = "session_tree_rows"
CALLED_VERBS = (*SESSION_VERBS, TREE_VERB)
WRITER_URL_ENV, READER_URL_ENV = "DUCKLAKE_TELEMETRY_WRITER_URL", "DUCKLAKE_READER_URL"
WRITER_SSM, READER_SSM = transport.SSM_PARAMETER, "/agent-platform/ducklake/reader_url"
ACCEPTANCE = (
    "AWS_PROFILE=agent_platform bin/venv-python -m scripts.ducklake_smoke.lambda_telemetry_golden_gates "
    "--no-alarms --assert-zero-mismatches"
)
_ROOT_FILE = re.compile(r"^([^/]+)\.jsonl$")
_TIMED = frozenset({"as_of", "last_event_timestamp", "session_started_at", "event_timestamp"})


class ReplayStop(RuntimeError):
    """The run cannot complete; carries a failure class and the tree and pass it stopped in, never a body or a value."""

    def __init__(self, kind: str, where: str = "") -> None:
        super().__init__(f"{kind} {where}".strip())
        self.kind = kind


@dataclass
class Stats:
    trees: int = 0
    expectations: int = 0
    window_skipped: int = 0
    seam_skipped: int = 0
    reader_calls: int = 0
    writer_calls: int = 0
    max_response_bytes: int = 0
    mismatches: Counter[str] = field(default_factory=Counter)

    def line(self, elapsed: float, skew: int) -> str:
        return (
            f"GOLDEN_SMOKE trees={self.trees} expectations={self.expectations} window_skipped={self.window_skipped} "
            f"reader_calls={self.reader_calls} writer_calls={self.writer_calls} elapsed_s={int(elapsed)} "
            f"max_response_bytes={self.max_response_bytes} seam_skipped={self.seam_skipped} skew={skew} "
            f"mismatches={sum(self.mismatches.values())}"
        )


@dataclass
class Env:
    """Everything the run touches outside this process, injectable: writer, reader, blob PUT and the skew inputs."""

    writer: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]
    reader: Any
    put: Callable[..., tuple[int, bytes]]
    record: Callable[[str], dict[str, Any]]
    unchanged: Callable[[str, list[str]], bool]
    catalog: str
    clock: Callable[[], float] = time.monotonic


def deploy_paths(workflow: Path = DEPLOY_WORKFLOW) -> list[str]:
    """The push paths of deploy-ducklake-lambdas (the code a deploy of this commit would ship)."""
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    triggers = doc.get("on", doc.get(True))
    return [str(p) for p in triggers["push"]["paths"]]


def deploy_functions(env: Env) -> tuple[str, str]:
    """The deploy-record pair: the function serving the routed reader's event verbs, and the telemetry writer."""
    return env.reader.event_function, WRITER_FUNCTION


def records_skewed(env: Env) -> bool:
    """True when either deploy record names a commit that differs from this checkout on a deploy path."""
    paths = deploy_paths()
    return any(not env.unchanged(str(env.record(fn).get("source_git_sha") or ""), paths) for fn in deploy_functions(env))


def skewed(env: Env, called: tuple[str, ...] = CALLED_VERBS) -> bool:
    """True when the deployed reader's stamps or either deploy record's commit differ from this checkout on a deploy path."""
    described = env.reader.describe()
    want = ev.describe_all()
    if any((described.get(v) or {}).get("read_version") != want[v]["read_version"] for v in called):
        return True
    return records_skewed(env)


def _cases() -> list[str]:
    return json.loads((GOLDEN / "index.json").read_text(encoding="utf-8"))["cases"]


def replayable(tree: str) -> tuple[dict[str, str] | None, str]:
    """(files, name) of a corpus case, or (None, name) when the case needs a test-only seam or is not a corpus tree."""
    kind, _, name = tree.partition(":")
    if kind != "corpus" or f"{name}.json" not in _cases():
        return None, name
    body = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    if name == "secrets" or "full_output_cap_bytes" in body:
        return None, name
    return body["files"], name


def expectation_passes() -> dict[tuple[str, bool], list[dict[str, Any]]]:
    """(tree, final?) -> every expectation of both files, in file order."""
    out: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for name in EXPECTATIONS:
        for entry in yaml.safe_load((EXPECTATION_DIR / name).read_text(encoding="utf-8"))["trees"]:
            out.setdefault((entry["tree"], entry["pass"] == "final"), []).extend(entry["expect"])
    return out


def _moment(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _equal(actual: Any, wanted: Any, key: str) -> bool:
    if key in _TIMED and isinstance(wanted, str):
        return actual is not None and _moment(actual) == _moment(wanted)
    if isinstance(wanted, dict) and isinstance(actual, dict):
        return all(k in actual and _equal(actual[k], v, k) for k, v in wanted.items())
    if isinstance(wanted, list) and isinstance(actual, list):
        return len(wanted) == len(actual) and all(_equal(a, w, key) for a, w in zip(actual, wanted))
    return bool(actual == wanted)


def _resolve(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, dict) and set(value) == {"session"}:
        return ids.get(value["session"])
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


def rows_hold(actual: list[dict[str, Any]], expect: dict[str, Any], ids: dict[str, str]) -> bool:
    wanted = _resolve(expect["rows"], ids)
    return len(actual) == len(wanted) and all(_equal(got, want, "") for got, want in zip(actual, wanted))


class _Run:
    def __init__(self, env: Env, stats: Stats) -> None:
        self.env, self.stats = env, stats
        self.tenant_id = ""
        self.protocol: bd.BlobProtocol | None = None

    def invoke(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.stats.writer_calls += 1
        return self.env.writer(payload)

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.stats.reader_calls += 1
        rows = self.env.reader.named(verb, **params)
        self.stats.max_response_bytes = max(self.stats.max_response_bytes, len(json.dumps(rows, default=str)))
        return list(rows)

    def start(self) -> None:
        status, body = self.invoke({"action": "describe"})
        if status != 200:
            raise ReplayStop("describe_failed")
        try:
            self.protocol = bd.protocol_from_describe(body)
        except bd.HandshakeError:
            raise ReplayStop("describe_mismatch") from None
        self.assert_catalog(body)
        status, body = self.invoke({"action": "register_tenant"})
        if status != 200 or not isinstance(body.get("tenant_id"), str):
            raise ReplayStop("register_tenant_failed")
        self.tenant_id = body["tenant_id"]

    def assert_catalog(self, describe: dict[str, Any]) -> None:
        """The writer and the reader both serve the deploy output's catalog (RN-5 pairing), before any registration."""
        catalog = self.env.catalog
        own_reader = catalog != DEFAULT_CATALOG
        if paired_catalog(describe.get("catalog")) != catalog:
            raise ReplayStop("writer_catalog_mismatch")
        try:
            rows = self.named("telemetry_health_count")
            binding = self.env.reader.binding() if own_reader else None
        except Exception as exc:  # noqa: BLE001 -- the failure's class only, never its message
            raise ReplayStop("reader_" + type(exc).__name__) from None
        tables = [row.get("table_name") for row in rows]
        if (
            len(tables) != len(HEALTH_TABLES)
            or set(tables) != HEALTH_TABLES
            or any(row.get("meta_schema") != catalog for row in rows)
            or (own_reader and paired_catalog(binding) != catalog)
        ):
            raise ReplayStop("reader_catalog_mismatch")

    def register_project(self, where: str) -> tuple[str, str]:
        ref = "synthetic:golden-replay-" + uuid.uuid4().hex
        status, body = self.invoke({"action": "register_project", "project_ref": ref})
        if status != 200 or not isinstance(body.get("project_id"), str):
            raise ReplayStop("register_project_failed", where)
        return ref, body["project_id"]

    def send(self, result: Any, ref: str, where: str) -> list[tuple[str, dict[str, Any]]]:
        assert self.protocol is not None
        items: list[tuple[str, dict[str, Any]]] = []
        for table, rows in result.batches:
            if table == "telemetry_transcripts" and rows:
                try:
                    rows = bd.deliver_blobs(
                        rows,
                        project_ref=ref,
                        invoke=self.invoke,
                        protocol=self.protocol,
                        component=transport.COMPONENT,
                        put=self.env.put,
                    ).wire_rows
                except bd.BlobDeliveryError as exc:
                    raise ReplayStop("blob_" + exc.kind, where) from None
            items.extend((table, row) for row in rows)
        limits = transport.Limits(self.protocol.max_items, self.protocol.inline_threshold_bytes, self.protocol.cap_bytes)
        for request in transport.chunk(items, ref, limits):
            status, _ = self.invoke(json.loads(transport.encode(transport.append_body(ref, request))))
            if status != 200:
                raise ReplayStop(f"append_status_{status}", where)
        return items

    def check(self, tree: str, final: bool, files: dict[str, str], expectations: list[dict[str, Any]]) -> None:
        where = f"tree={tree} pass={'final' if final else 'stop'}"
        ref, project_id = self.register_project(where)
        result = record_turn(MemTree(files), None, project_ref=ref, session_final=final)
        items = self.send(result, ref, where)
        self.stats.trees += 1
        root_ref = next(m.group(1) for name in files if (m := _ROOT_FILE.match(name)))
        ids = {
            ("root" if row["entity_ref"] == root_ref else "agent:" + row["entity_ref"].removeprefix(root_ref + ":")): (
                derive_entity_key(
                    "telemetry_sessions:session_id", self.tenant_id, project_id, row["entity_ref"], row["session_started_at"]
                )
            )
            for row in result.sessions
        }
        if ids:
            self.check_tree(where, items, ids)
        for expect in expectations:
            if "session" not in expect:
                self.stats.window_skipped += 1
                continue
            self.stats.expectations += 1
            if expect["session"] not in ids:
                self.mismatch(expect["verb"], where)
                continue
            params = dict(expect["params"], session_id=ids[expect["session"]])
            try:
                got = self.named(expect["verb"], **params)
            except Exception as exc:  # noqa: BLE001 -- a reader failure is classified by type only
                raise ReplayStop("reader_" + type(exc).__name__, where) from None
            if not rows_hold(got, expect, ids):
                self.mismatch(expect["verb"], where)

    def check_tree(self, where: str, items: list[tuple[str, dict[str, Any]]], ids: dict[str, str]) -> None:
        try:
            pages = read_tree(self.named, ids.get("root") or next(iter(ids.values())), 2000)
        except Exception as exc:  # noqa: BLE001
            raise ReplayStop("reader_" + type(exc).__name__, where) from None
        stored = {(t, r["producer"], r["external_ref"]) for t, rows in decode_rows(pages).items() for r in rows}
        sent = {(t, r["producer"], r["external_ref"]) for t, r in items}
        roots = {r["roots_in_tree"] for page in pages for r in page}
        if stored != sent or roots != {1 if "root" in ids else 0}:
            self.mismatch(TREE_VERB, where)

    def mismatch(self, verb: str, where: str) -> None:
        self.stats.mismatches[verb] += 1
        print(f"GOLDEN_MISMATCH verb={verb} {where}")


def run(env: Env) -> tuple[Stats, int]:
    """Replay every replayable tree; returns (stats, skew). Raises ReplayStop when the run cannot complete."""
    stats = Stats()
    run_ = _Run(env, stats)
    try:
        if skewed(env):
            return stats, 1
        run_.start()
        for (tree, final), expectations in expectation_passes().items():
            files, _ = replayable(tree)
            if files is None:
                stats.seam_skipped += int(tree.startswith("corpus:"))
                continue
            run_.check(tree, final, files, expectations)
    except ReplayStop:
        raise
    except Exception as exc:  # noqa: BLE001 -- the failure's class only, never its message
        raise ReplayStop("run_" + type(exc).__name__) from None
    try:
        return stats, int(skewed(env))
    except Exception as exc:  # noqa: BLE001
        raise ReplayStop("guard_" + type(exc).__name__) from None


def _commit() -> str:
    done = subprocess.run(
        ["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, encoding="utf-8", cwd=ROOT
    )
    return done.stdout.strip() or "unknown"


def file_alarms(findings: dict[str, int], *, portal: Callable[..., Any] | None = None) -> None:
    """One telemetry_verify alarm per class, counted only; a filing failure is reported by class and never fails the run."""
    try:
        outcome = raise_alarms(
            findings,
            scope=_commit(),
            alarm_at=None,
            now=datetime.now(timezone.utc),
            portal=portal,
            file="scripts/ducklake_smoke/lambda_telemetry_golden_gates.py",
            acceptance_for=lambda _cls: ACCEPTANCE,
        )
        print(f"GOLDEN_ALARMS filed={outcome.filed} updated={outcome.updated} unchanged={outcome.unchanged}")
    except Exception as exc:  # noqa: BLE001
        print(f"GOLDEN_ALARMS_FAILED class={type(exc).__name__}")


def _git_unchanged(sha: str, paths: list[str]) -> bool:
    if not sha:
        return False
    done = subprocess.run(["git", "diff", "--quiet", sha, "HEAD", "--", *paths], cwd=ROOT, capture_output=True)
    return done.returncode == 0


def _resolve_urls(profile: str | None, region: str) -> tuple[str, str]:
    import os  # noqa: PLC0415

    import boto3  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    ssm = boto3.Session(profile_name=resolve_aws_profile(profile)).client("ssm", region_name=region)
    urls = []
    for env_name, parameter in ((WRITER_URL_ENV, WRITER_SSM), (READER_URL_ENV, READER_SSM)):
        url = os.environ.get(env_name) or ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
        os.environ[env_name] = url.rstrip("/")
        urls.append(url.rstrip("/"))
    return urls[0], urls[1]


def live_env(profile: str | None, region: str) -> Env:
    import boto3  # noqa: PLC0415

    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415
    from scripts.telemetry_capture import read_route  # noqa: PLC0415

    writer_url, _ = _resolve_urls(profile, region)
    reader = read_route.make_routed_reader(profile)
    try:
        catalog = reader.catalog
    except read_route.TelemetryCatalogUnresolved:
        raise ReplayStop("catalog_unresolved") from None
    s3 = boto3.Session(profile_name=resolve_aws_profile(profile)).client("s3", region_name=region)

    def writer(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        resp = core._sigv4_invoke(writer_url, payload, profile=profile, region=region)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return resp.status_code, body if isinstance(body, dict) else {}

    def record(function: str) -> dict[str, Any]:
        raw = s3.get_object(Bucket=RECORD_BUCKET, Key=f"deploy-records/ducklake/{function}.json")["Body"].read()
        return dict(json.loads(raw))

    return Env(writer, reader, bd.urllib_put, record, _git_unchanged, catalog)


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description="Replay the golden transcript trees through the deployed telemetry engine.")
    built.add_argument("--no-alarms", action="store_true", help="never file telemetry_verify recs")
    built.add_argument("--assert-zero-mismatches", action="store_true", help="exit 0 only after a clean, unskewed run")
    built.add_argument("--profile", default=None)
    built.add_argument("--region", default="eu-west-2")
    return built


def main(argv: list[str] | None = None, *, env: Env | None = None) -> int:
    args = parser().parse_args(argv)
    if env is None:
        try:
            env = live_env(args.profile, args.region)
        except ReplayStop as stop:
            print(f"GOLDEN_STOP class={stop.kind}")
            if not args.no_alarms:
                file_alarms({"golden_replay_error": 1})
            return 1
    started = env.clock()
    stats = Stats()
    try:
        stats, skew = run(env)
    except ReplayStop as stop:
        print(f"GOLDEN_STOP class={stop.kind}")
        try:
            skew = int(records_skewed(env))
        except Exception:  # noqa: BLE001 -- a failing record re-read cannot explain the stop
            skew = 0
        print(stats.line(env.clock() - started, skew))
        if skew:
            return 1 if args.assert_zero_mismatches else 0
        if not args.no_alarms:
            file_alarms({"golden_replay_error": 1})
        return 1
    print(stats.line(env.clock() - started, skew))
    if skew:
        return 1 if args.assert_zero_mismatches else 0
    if stats.mismatches:
        if not args.no_alarms:
            file_alarms({f"golden_replay_mismatch:{verb}": n for verb, n in stats.mismatches.items()})
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
