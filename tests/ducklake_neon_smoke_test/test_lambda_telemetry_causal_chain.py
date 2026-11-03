"""Tests for scripts/ducklake_smoke/lambda_telemetry_causal_chain.py over a fake deployment (no network).

The fake writer, reader and blob PUT reuse the golden-replay deployment (a real in-memory catalog through the real append
kernel and event-read engine), widened with the claim-check route, an idempotent register_project and fault injection.
"""

from __future__ import annotations

import copy
import json
import re
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytest.importorskip("duckdb", reason="env: duckdb is a test-extra dependency")

from scripts.ducklake_smoke import lambda_telemetry_causal_chain as probe  # noqa: E402
from scripts.ducklake_smoke import lambda_telemetry_causal_chain_prior as prior  # noqa: E402
from scripts.ducklake_smoke import lambda_telemetry_golden_gates as gates  # noqa: E402
from scripts.executor.acceptance_lint import lint_acceptance_command  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.telemetry.append import append_events  # noqa: E402
from src.telemetry.timestamps import parse_wire_timestamp  # noqa: E402
from src.telemetry_client import blob_delivery as bd  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import TENANT  # noqa: E402

PLANTED = "PLANTED-VALUE-424242"
T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
GOOD_SOURCE = f"PROBE_SHAPE_VERSION = {prior.PROBE_SHAPE_VERSION}\n"
SHA40 = "a" * 40


class ProbeDeployment:
    """A fake telemetry writer and reader over one in-memory catalog (the real append kernel and event-read engine), with the
    claim-check route, an idempotent register_project, per-action fault injection and a fake clock."""

    def __init__(self) -> None:
        self.con = db.open_memory_catalog()
        self.catalog = "ducklake_smoke"
        self.served: dict[str, Any] = {"meta_schema": self.catalog, "data_path_is_smoke": True}
        self.bound: Any = dict(self.served)
        self.health = [{"table_name": t, "record_count": 0, "meta_schema": self.catalog} for t in sorted(gates.HEALTH_TABLES)]
        self.event_function = "agent-platform-ducklake-reader"
        self.projects: dict[str, str] = {}
        self.writes: list[str] = []
        self.record_sha = "abc12345"
        self.skew_after_writes: int | None = None
        self.stale_stamp = False
        self.reader_hook: Any = None
        self.reader_error: Exception | None = None
        self.faults: dict[str, list[Any]] = {}
        self.sent: dict[str, list[dict[str, Any]]] = {}
        self.threshold = 65536
        self.t = 0.0
        self.sleeps: list[float] = []
        self.ticks = 0

    def writer(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        action = payload["action"]
        json.dumps(payload)  # the wire is JSON: a datetime in a body is a defect the real writer URL would hit
        fault = self.fault(action)
        if fault is not None:
            self.writes.append(action)
            if isinstance(fault, Exception):
                raise fault
            return fault
        if action == "describe":
            self.writes.append(action)
            protocol = {"version": 1, "inline_threshold_bytes": self.threshold, "cap_bytes": 8388608, "max_items": 8}
            return 200, {"actions": ["append_telemetry"], "blob_protocol": protocol, "catalog": self.served}
        if action == "begin_blobs":
            self.writes.append(action)
            return 200, {
                "ok": True,
                "items": [{"upload": {"url": "https://example.invalid/put", "headers": {}}}] * len(payload["items"]),
            }
        if action == "register_project" and payload["project_ref"] in self.projects:
            self.writes.append(action)
            return 200, {"project_id": self.projects[payload["project_ref"]]}
        if action == "append_telemetry":
            for batch in payload["batches"]:
                self.sent.setdefault(batch["table"], []).extend(dict(r) for r in batch["rows"])
            for batch in payload["batches"]:
                for row in batch["rows"]:
                    if batch["table"] == "telemetry_transcripts" and row.get("content") is None:
                        row["content_uri"] = "s3://fake/" + str(row["content_sha256"])
        return self.apply(payload)

    def apply(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        action = payload["action"]
        self.writes.append(action)
        if action == "register_tenant":
            return 200, {"tenant_id": TENANT}
        if action == "register_project":
            project_id = f"01BX5ZZKBKACTAV9WEVGEMM{len(self.projects):03d}"
            self.projects[payload["project_ref"]] = project_id
            row = {
                "project_id": project_id,
                "tenant_id": TENANT,
                "project_ref": payload["project_ref"],
                "purpose": "synthetic",
            }
            db.insert_projects(self.con, [row])
            return 200, {"project_id": project_id}
        project_id = self.projects[payload["project_ref"]]
        for batch in payload["batches"]:
            rows = copy.deepcopy(batch["rows"])
            for row in rows:
                for key in ("event_timestamp", "session_started_at"):
                    row[key] = parse_wire_timestamp(row[key])
            moment = max(r["event_timestamp"] for r in rows) + timedelta(seconds=1)
            spec = chain.specs()[batch["table"]]
            append_events(self.con, spec, rows, tenant_id=TENANT, project_id=project_id, catalog=db.ALIAS, now=moment)
        return 200, {"tables": {}}

    def describe(self) -> dict[str, Any]:
        stamps = {v: {"read_version": e["read_version"]} for v, e in ev.describe_all().items()}
        if self.stale_stamp:
            stamps["session_tree_rows"] = {"read_version": "0" * 16}
        return stamps

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if self.reader_error is not None:
            raise self.reader_error
        if verb == "telemetry_health_count":
            return copy.deepcopy(self.health)
        rows = ev.execute(self.con, verb=verb, params=params)
        return self.reader_hook(verb, rows) if self.reader_hook else rows

    def binding(self) -> Any:
        return self.bound

    def unchanged(self, sha: str, paths: list[str]) -> bool:
        if self.skew_after_writes is not None and len(self.writes) > self.skew_after_writes:
            return False
        return sha == "abc12345" and bool(paths)

    def env(self) -> gates.Env:
        def record(function: str) -> dict[str, Any]:
            return {"source_git_sha": self.record_sha}

        return gates.Env(self.writer, self, lambda *a, **k: (200, b""), record, self.unchanged, self.catalog)

    def fault(self, action: str) -> Any:
        """The next injected answer for *action*: [response, remaining]; remaining None repeats forever."""
        entry = self.faults.get(action)
        if not entry:
            return None
        if entry[1] is not None:
            entry[1] -= 1
            if entry[1] <= 0:
                del self.faults[action]
        return entry[0]

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def utcnow(self) -> datetime:
        self.ticks += 1
        return T0 + timedelta(seconds=60 * self.ticks)

    def probe_env(self) -> Any:
        base = self.env()
        base.clock = self.clock
        return base

    def hooks(self, **overrides: Any) -> probe.Hooks:
        values: dict[str, Any] = {"sleep": self.sleep, "utcnow": self.utcnow, "git_show": lambda sha: GOOD_SOURCE}
        return probe.Hooks(**{**values, **overrides})


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_RUN_ID", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def alarms(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    real = probe.raise_alarms
    monkeypatch.setattr(
        probe, "raise_alarms", lambda *a, **k: real(*a, **{**k, "portal": lambda *c: calls.append(c), "rows": []})
    )
    return calls


def run(dep: ProbeDeployment, *flags: str, **hooks: Any) -> int:
    return probe.main(list(flags), env=dep.probe_env(), hooks=dep.hooks(**hooks))


def line_of(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("CAUSAL_CHAIN"))
    return dict(pair.split("=", 1) for pair in line.split()[1:])


def actions_caller(runs: list[dict[str, Any]], current: dict[str, Any] | None = None) -> Any:
    def call(url: str) -> Any:
        if "/workflows/" in url:
            return {"workflow_runs": runs}
        if current is not None and url.endswith(f"/runs/{current['id']}"):
            return current
        raise OSError("no such run")

    return call


def ago(hours: float) -> str:
    return (T0 - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_clean_local_run_passes_with_one_count_only_line(capsys: pytest.CaptureFixture[str]) -> None:
    dep = ProbeDeployment()
    assert run(dep, "--no-alarms", "--assert-pass") == 0
    out = capsys.readouterr().out
    assert re.fullmatch(
        r"CAUSAL_CHAIN stage=pass exit=0 mode=local elapsed_s=\d+ visible_after_s=\d+ reader_calls=\d+ "
        r"writer_calls=\d+ blobs=1 "
        r"real_window_hits=0 prior_mode=skipped prior_checked=0 prior_lost=0 prior_skipped=0 skew=0 alarm_filing=none\n",
        out,
    )
    assert list(dep.projects) == [prior.PROBE_REF] and dep.writes.count("begin_blobs") == 1
    for secret in (*dep.projects.values(), TENANT, prior.PROBE_REF, "example.invalid"):
        assert secret not in out
    assert not re.search(r"[0-9A-HJKMNP-TV-Z]{26}", out)


def test_actions_mode_takes_identity_from_the_run_and_runs_post_persist(
    capsys: pytest.CaptureFixture[str], monkeypatch: Any
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "777")
    dep = ProbeDeployment()
    current = {"id": 777, "run_attempt": 2, "run_started_at": ago(0.5)}
    assert run(dep, "--no-alarms", caller=actions_caller([], current)) == 0
    fields = line_of(capsys)
    assert fields["mode"] == "actions" and fields["prior_mode"] == "checked" and fields["prior_checked"] == "0"
    sessions = dep.sent["telemetry_sessions"]
    assert {r["entity_ref"] for r in sessions} == {f"causal-chain-probe:v{prior.PROBE_SHAPE_VERSION}:777.2"}
    assert min(r["session_started_at"] for r in sessions).startswith(ago(0.5)[:19])


def tree_hook(dep: ProbeDeployment, change: Any) -> None:
    def hook(verb: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return change(rows) if verb == "session_tree_rows" else rows

    dep.reader_hook = hook


def edit(rows: list[dict[str, Any]], table: str, **changes: Any) -> list[dict[str, Any]]:
    out = [dict(r) for r in rows]
    target = next(r for r in out if r["table_name"] == table)
    target["row_json"] = json.dumps({**json.loads(target["row_json"]), **changes})
    return out


def extra(rows: list[dict[str, Any]], table: str, **changes: Any) -> list[dict[str, Any]]:
    source = next(r for r in rows if r["table_name"] == table)
    clone = {**source, "row_json": json.dumps({**json.loads(source["row_json"]), **changes})}
    return [{**r, "total_rows": len(rows) + 1} for r in [*rows, clone]]


def fail(status: int, body: dict[str, Any] | None = None, times: int | None = None) -> list[Any]:
    return [(status, body if body is not None else {"error_type": "x"}), times]


def alarm_titles(alarms: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [c[1]["title"].rsplit(": ", 1)[1] for c in alarms]


@pytest.mark.parametrize(
    ("name", "setup", "stage", "code"),
    [
        ("append 422", lambda d: d.faults.update(append_telemetry=fail(422, {"error_type": "row_rule"})), "persist", 12),
        (
            "blob_not_verified",
            lambda d: d.faults.update(append_telemetry=fail(422, {"error_type": "blob_not_verified"})),
            "persist",
            12,
        ),
        ("register 500", lambda d: d.faults.update(register_project=fail(500)), "persist", 12),
        (
            "append connection error",
            lambda d: d.faults.update(append_telemetry=[ConnectionError(PLANTED), None]),
            "transit",
            11,
        ),
        ("append url 403", lambda d: d.faults.update(append_telemetry=fail(403, {})), "transit", 11),
        ("begin_blobs 403 not writer json", lambda d: d.faults.update(begin_blobs=fail(403, {})), "transit", 11),
        (
            "begin_blobs connection error",
            lambda d: d.faults.update(begin_blobs=[ConnectionError(PLANTED), None]),
            "transit",
            11,
        ),
        ("begin_blobs 500", lambda d: d.faults.update(begin_blobs=fail(500)), "persist", 12),
        ("begin_blobs 400", lambda d: d.faults.update(begin_blobs=fail(400)), "persist", 12),
        ("payload outside the blob band", lambda d: setattr(d, "threshold", 10**9), "produce", 10),
        (
            "a value mismatch",
            lambda d: tree_hook(d, lambda r: edit(r, "telemetry_transcripts", content_bytes=1)),
            "persist",
            12,
        ),
        (
            "unexpected_in_store",
            lambda d: tree_hook(d, lambda r: extra(r, "telemetry_observations", external_ref="zzz")),
            "persist",
            12,
        ),
        ("key_mismatch", lambda d: tree_hook(d, lambda r: edit(r, "telemetry_sessions", session_id="0" * 26)), "persist", 12),
        (
            "duplicate_ref_rows",
            lambda d: tree_hook(d, lambda r: extra(r, "telemetry_observations", event_id="0" * 26)),
            "persist",
            12,
        ),
        ("race_duplicate_rows", lambda d: tree_hook(d, lambda r: extra(r, "telemetry_observations")), "persist", 12),
        ("a tree collision", lambda d: tree_hook(d, lambda r: [{**x, "roots_in_tree": 2} for x in r]), "persist", 12),
        ("rows missing past the SLA", lambda d: tree_hook(d, lambda r: []), "query", 13),
    ],
)
def test_each_failing_stage_has_its_own_code_and_alarm(
    name: str,
    setup: Any,
    stage: str,
    code: int,
    alarms: list[tuple[str, dict[str, Any]]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    dep = ProbeDeployment()
    setup(dep)
    assert run(dep) == code, name
    out = capsys.readouterr().out
    assert f"stage={stage} exit={code}" in out and PLANTED not in out and PLANTED not in repr(alarms)
    assert alarm_titles(alarms) == [f"causal_chain_{stage}"]
    assert alarms[0][1]["acceptance"] == probe.acceptance_for(f"causal_chain_{stage}")


def test_a_producer_fault_is_class_only(monkeypatch: pytest.MonkeyPatch, alarms: list[tuple[str, dict[str, Any]]]) -> None:
    def boom(_identity: Any) -> None:
        raise RuntimeError(PLANTED)

    monkeypatch.setattr(prior, "expected_batches", boom)
    dep = ProbeDeployment()
    assert run(dep) == 10 and alarm_titles(alarms) == ["causal_chain_produce"] and PLANTED not in repr(alarms)
    assert dep.sent == {}
    empty = type("R", (), {"sessions": [], "transcripts": []})()
    monkeypatch.setattr(prior, "expected_batches", lambda _i: (empty, []))
    assert run(ProbeDeployment()) == 10


def test_the_query_polls_every_ten_seconds_for_the_sla_then_classes_query() -> None:
    dep = ProbeDeployment()
    tree_hook(dep, lambda r: [])
    assert run(dep, "--no-alarms") == 13
    assert set(dep.sleeps) == {float(probe.POLL_INTERVAL_S)} and sum(dep.sleeps) == probe.SLA_SECONDS


def test_an_unanswering_pipeline_is_unavailable_and_files_nothing(alarms: list[tuple[str, dict[str, Any]]]) -> None:
    cases = {
        "reader never answers": lambda d: setattr(d, "reader_error", RuntimeError("504")),
        "writer 503 after retries": lambda d: d.faults.update(append_telemetry=fail(503)),
        "writer 429 after retries": lambda d: d.faults.update(register_tenant=fail(429)),
        "describe fails": lambda d: d.faults.update(describe=fail(503)),
        "begin_blobs 429": lambda d: d.faults.update(begin_blobs=fail(429)),
        "projection read 504": lambda d: setattr(
            d, "reader_hook", lambda v, rows: (_ for _ in ()).throw(RuntimeError("504")) if v == "sessions_window" else rows
        ),
    }
    for name, setup in cases.items():
        dep = ProbeDeployment()
        setup(dep)
        assert run(dep) == 3, name
    assert alarms == []


def test_tree_reads_that_never_answer_after_a_first_200_class_query_not_unavailable() -> None:
    dep = ProbeDeployment()
    calls = {"n": 0}

    def hook(verb: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if verb == "session_tree_rows":
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("504")
            return []
        return rows

    dep.reader_hook = hook
    assert run(dep, "--no-alarms") == 13


def test_register_and_append_retry_at_most_twice_with_backoff() -> None:
    dep = ProbeDeployment()
    dep.faults["append_telemetry"] = fail(429, None, times=2)
    assert run(dep, "--no-alarms") == 0
    assert dep.sleeps[:2] == [2.0, 4.0]
    third = ProbeDeployment()
    third.faults["register_tenant"] = fail(503, None, times=3)
    assert run(third, "--no-alarms") == 3 and third.writes.count("register_tenant") == 3


@pytest.mark.parametrize(
    ("error", "stage"),
    [
        (bd.BlobDeliveryError("transient"), "transit"),
        (bd.BlobDeliveryError("transient", 429), "unavailable"),
        (bd.BlobDeliveryError("proxy", 403), "transit"),
        (bd.BlobDeliveryError("refused", 403), "persist"),
        (bd.BlobDeliveryError("defect", 500), "persist"),
        (bd.BlobDeliveryError("defect"), "produce"),
    ],
)
def test_blob_delivery_errors_map_to_stages(error: bd.BlobDeliveryError, stage: str) -> None:
    assert probe._blob_stage(error) == stage


def test_a_projection_disagreement_or_a_probe_row_in_the_window_is_projection(
    alarms: list[tuple[str, dict[str, Any]]],
) -> None:
    skewed_state = ProbeDeployment()
    skewed_state.reader_hook = lambda v, rows: (
        [{**r, "duration_seconds": 99999} for r in rows] if v == probe.STATE_VERB else rows
    )
    assert run(skewed_state) == 14
    leaking = ProbeDeployment()

    def leak(verb: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return (
            [{"session_id": "s", "project_id": next(iter(leaking.projects.values()))}] if verb == probe.WINDOW_VERB else rows
        )

    leaking.reader_hook = leak
    assert run(leaking) == 14
    assert alarm_titles(alarms) == ["causal_chain_projection"] * 2


def test_an_actions_lookup_failure_is_unavailable_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch, alarms: list[tuple[str, dict[str, Any]]]
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "777")
    dep = ProbeDeployment()
    assert run(dep, caller=actions_caller([], None)) == 3
    assert alarms == [] and dep.sent == {} and not dep.projects and set(dep.writes) == {"describe"}


@pytest.mark.parametrize("trouble", ["stale_stamp", "record_sha", "skew_after_writes"])
def test_a_skewed_run_runs_every_stage_files_nothing_and_never_satisfies_assert_pass(
    trouble: str, alarms: list[tuple[str, dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    for fault in (False, True):
        dep = ProbeDeployment()
        setattr(dep, trouble, {"stale_stamp": True, "record_sha": "other", "skew_after_writes": 3}[trouble])
        if fault:
            dep.faults["append_telemetry"] = fail(422)
        assert run(dep) == (12 if fault else 0)
        assert "skew=1" in capsys.readouterr().out and alarms == []
        assert run(dep, "--assert-pass") == (12 if fault else 1)
        capsys.readouterr()


def test_prior_runs_are_re_read_and_a_loss_exits_fifteen_with_its_own_alarm(
    monkeypatch: pytest.MonkeyPatch, alarms: list[tuple[str, dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.fixtures import telemetry_read_db as db  # noqa: PLC0415

    dep = ProbeDeployment()
    first = {"id": 100, "run_attempt": 1, "run_started_at": ago(6), "head_sha": SHA40}
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    assert run(dep, "--no-alarms", caller=actions_caller([], first)) == 0
    capsys.readouterr()
    second = {"id": 200, "run_attempt": 1, "run_started_at": ago(0), "head_sha": SHA40}
    monkeypatch.setenv("GITHUB_RUN_ID", "200")
    caller = actions_caller([first, second], second)
    assert run(dep, "--require-prior", caller=caller) == 0
    fields = line_of(capsys)
    assert (fields["prior_checked"], fields["prior_lost"], fields["prior_mode"]) == ("1", "0", "checked")
    for table in db.TABLES:
        dep.con.execute(f"DELETE FROM {db.ALIAS}.{table} WHERE session_started_at < TIMESTAMPTZ '{ago(1)}'")
    third = {"id": 300, "run_attempt": 1, "run_started_at": ago(-0.1), "head_sha": SHA40}
    monkeypatch.setenv("GITHUB_RUN_ID", "300")
    assert run(dep, caller=actions_caller([first, third], third)) == 15
    fields = line_of(capsys)
    assert fields["stage"] == "post_persist_loss" and fields["prior_lost"] == "1"
    assert alarm_titles(alarms) == ["causal_chain_post_persist_loss"]
    assert alarms[0][1]["acceptance"].endswith("--assert-pass --require-prior")


def test_require_prior_turns_only_a_zero_exit_into_three(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run(ProbeDeployment(), "--no-alarms", "--assert-pass") == 0
    assert run(ProbeDeployment(), "--no-alarms", "--require-prior") == 3
    assert "prior_mode=skipped" in capsys.readouterr().out.split("CAUSAL_CHAIN")[-1]
    broken = actions_caller([], None)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert run(ProbeDeployment(), "--no-alarms", caller=lambda url: (_ for _ in ()).throw(OSError("x"))) == 3
    assert "prior_mode=unavailable" in capsys.readouterr().out.split("CAUSAL_CHAIN")[-1]
    assert run(ProbeDeployment(), "--no-alarms", "--require-prior", caller=broken) == 3
    assert "prior_checked=0" in capsys.readouterr().out.split("CAUSAL_CHAIN")[-1]
    faulty = ProbeDeployment()
    faulty.faults["append_telemetry"] = fail(422)
    assert run(faulty, "--no-alarms", "--require-prior", caller=broken) == 12


def test_local_mode_with_a_token_runs_post_persist(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(ProbeDeployment(), "--no-alarms", caller=actions_caller([], None)) == 0
    fields = line_of(capsys)
    assert fields["mode"] == "local" and fields["prior_mode"] == "checked"


def test_a_failed_alarm_filing_keeps_the_loss_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(PLANTED)

    monkeypatch.setattr(probe, "raise_alarms", broken)
    dep = ProbeDeployment()
    dep.faults["append_telemetry"] = fail(422)
    assert run(dep) == 12
    out = capsys.readouterr().out
    assert "alarm_filing=failed" in out and PLANTED not in out


def test_unreachable_infrastructure_exits_without_a_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe.golden, "live_env", lambda *_a: (_ for _ in ()).throw(OSError(PLANTED)))
    assert probe.main(["--no-alarms"], hooks=probe.Hooks()) == 3
    stop = probe.golden.ReplayStop("catalog_unresolved")
    monkeypatch.setattr(probe.golden, "live_env", lambda *_a: (_ for _ in ()).throw(stop))
    assert probe.main(["--no-alarms"], hooks=probe.Hooks()) == 1
    assert PLANTED not in capsys.readouterr().out


def test_mismatched_pairing_is_incomplete_and_files_nothing(alarms: list[tuple[str, dict[str, Any]]]) -> None:
    dep = ProbeDeployment()
    dep.served = {"meta_schema": "ducklake_telemetry", "data_path_is_smoke": False}
    assert run(dep) == 1
    other = ProbeDeployment()
    other.health = other.health[:2]
    assert run(other) == 1 and alarms == [] and other.sent == {}


def test_acceptance_commands_are_decidable_and_the_prior_class_requires_prior() -> None:
    for cls in ("produce", "transit", "persist", "query", "projection"):
        command = probe.acceptance_for(f"causal_chain_{cls}")
        assert lint_acceptance_command(command, require_decidable=True) == (True, None)
        assert "--require-prior" not in command
    command = probe.acceptance_for("causal_chain_post_persist_loss")
    args = probe.parser().parse_args(shlex.split(command.split("lambda_telemetry_causal_chain", 1)[1]))
    assert args.no_alarms and args.assert_pass and args.require_prior
    assert lint_acceptance_command(command, require_decidable=True) == (True, None)


def test_the_stored_session_is_the_one_the_prior_leg_recomputes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "5")
    dep = ProbeDeployment()
    current = {"id": 5, "run_attempt": 1, "run_started_at": ago(6)}
    assert run(dep, "--no-alarms", caller=actions_caller([], current)) == 0
    who = prior.RunIdentity("5", 1, T0 - timedelta(hours=6))
    session_id = prior.session_id_for(TENANT, next(iter(dep.projects.values())), who)
    assert dep.named("session_tree_rows", session_id=session_id, offset=0, max_rows=10)


def test_a_loss_already_counted_is_not_masked_by_a_later_unavailable_read(
    monkeypatch: pytest.MonkeyPatch, alarms: list[tuple[str, dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    result = prior.PriorResult(mode="unavailable", checked=1, lost=1)
    monkeypatch.setattr(prior, "post_persist", lambda *a, **k: result)
    assert run(ProbeDeployment(), "--require-prior", caller=actions_caller([], None)) == 15
    assert alarm_titles(alarms) == ["causal_chain_post_persist_loss"]


def test_gateway_timeouts_from_the_writer_are_unavailable_and_failures_name_their_class(
    alarms: list[tuple[str, dict[str, Any]]], capsys: pytest.CaptureFixture[str]
) -> None:
    dep = ProbeDeployment()
    dep.faults["append_telemetry"] = fail(504)
    assert run(dep) == 3 and alarms == []
    capsys.readouterr()
    odd = ProbeDeployment()
    odd.health = odd.health[:2]
    assert run(odd) == 1
    assert "class=reader_catalog_mismatch" in capsys.readouterr().out
