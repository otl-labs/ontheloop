"""Mirror tests for scripts/ducklake_smoke/lambda_telemetry_golden_gates.py over a fake deployment (no network).

The fake writer applies the producer's request bodies to an in-memory DuckDB telemetry catalog through the real append kernel
and the fake reader answers through the real event-read engine, so the replay is exercised end to end.
"""

from __future__ import annotations

import ast
import copy
import re
import shlex
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ducklake_smoke import lambda_telemetry_golden_gates as gates  # noqa: E402
from scripts.executor.acceptance_lint import lint_acceptance_command  # noqa: E402
from scripts.telemetry_capture import read_route  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.telemetry.append import append_events  # noqa: E402
from src.telemetry.timestamps import parse_wire_timestamp  # noqa: E402
from tests.fixtures import telemetry_golden_chain as chain  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402
from tests.fixtures.turn_capture_corpus import TENANT  # noqa: E402

PLANTED = "PLANTED-VALUE-424242"
SHA = "abc12345"
SMOKE, PROD = "ducklake_smoke", "ducklake_telemetry"


class Deployment:
    """A fake telemetry writer, reader and deploy records over one in-memory catalog."""

    def __init__(self, catalog: str = SMOKE) -> None:
        self.con = db.open_memory_catalog()
        self.catalog = catalog
        self.served = {"meta_schema": catalog, "data_path_is_smoke": catalog == SMOKE}
        self.bound: Any = dict(self.served)
        self.health = [{"table_name": t, "record_count": 0, "meta_schema": catalog} for t in sorted(gates.HEALTH_TABLES)]
        self.event_function = (
            "agent-platform-ducklake-reader" if catalog == SMOKE else "agent-platform-ducklake-telemetry-reader"
        )
        self.records: list[str] = []
        self.projects: dict[str, str] = {}
        self.writes: list[str] = []
        self.skew_after_writes: int | None = None
        self.record_sha = SHA
        self.append_status = 200
        self.reader_hook: Any = None
        self.reader_error: Exception | None = None
        self.stale_stamp = False

    def writer(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        action = payload["action"]
        self.writes.append(action)
        if action == "describe":
            protocol = {"version": 1, "inline_threshold_bytes": 10**9, "cap_bytes": 2 * 10**9, "max_items": 8}
            return 200, {"actions": ["append_telemetry"], "blob_protocol": protocol, "catalog": self.served}
        if action == "register_tenant":
            return 200, {"tenant_id": TENANT}
        if action == "register_project":
            project_id = f"01BX5ZZKBKACTAV9WEVGEMM{len(self.projects):03d}"
            self.projects[payload["project_ref"]] = project_id
            db.insert_projects(
                self.con,
                [
                    {
                        "project_id": project_id,
                        "tenant_id": TENANT,
                        "project_ref": payload["project_ref"],
                        "purpose": "synthetic",
                    }
                ],
            )
            return 200, {"project_id": project_id}
        if self.append_status != 200:
            return self.append_status, {"error": PLANTED}
        project_id = self.projects[payload["project_ref"]]
        for batch in payload["batches"]:
            rows = copy.deepcopy(batch["rows"])
            for row in rows:
                for key in ("event_timestamp", "session_started_at"):
                    row[key] = parse_wire_timestamp(row[key])
            moment = max(r["event_timestamp"] for r in rows) + timedelta(seconds=1)
            append_events(
                self.con,
                chain.specs()[batch["table"]],
                rows,
                tenant_id=TENANT,
                project_id=project_id,
                catalog=db.ALIAS,
                now=moment,
            )
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
        return sha == SHA and bool(paths)

    def env(self) -> gates.Env:
        def record(function: str) -> dict[str, Any]:
            self.records.append(function)
            return {"source_git_sha": self.record_sha}

        return gates.Env(self.writer, self, lambda *a, **k: (200, b""), record, self.unchanged, self.catalog)


@pytest.fixture
def alarms(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    real = gates.raise_alarms
    monkeypatch.setattr(
        gates, "raise_alarms", lambda *a, **k: real(*a, **{**k, "portal": lambda *c: calls.append(c), "rows": []})
    )
    return calls


def _main(deployment: Deployment, *flags: str) -> int:
    return gates.main(list(flags), env=deployment.env())


def test_replay_registers_one_project_per_tree_and_pass() -> None:
    deployment = Deployment()
    stats, skew = gates.run(deployment.env())
    assert skew == 0 and not stats.mismatches
    replayed = [k for k in gates.expectation_passes() if gates.replayable(k[0])[0] is not None]
    assert stats.trees == len(replayed) == len(deployment.projects) > 0
    assert len(set(deployment.projects.values())) == len(deployment.projects)
    assert all(ref.startswith("synthetic:golden-replay-") for ref in deployment.projects)
    assert stats.expectations > 0 and deployment.writes.count("register_project") == stats.trees


def test_replay_reports_counts_only_and_skips_window_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    deployment = Deployment()
    assert _main(deployment, "--no-alarms") == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("GOLDEN_SMOKE"))
    assert re.fullmatch(
        r"GOLDEN_SMOKE trees=[1-9]\d* expectations=[1-9]\d* window_skipped=[1-9]\d* reader_calls=\d+ writer_calls=\d+ "
        r"elapsed_s=\d+ max_response_bytes=\d+ seam_skipped=\d+ skew=0 mismatches=0",
        line,
    )
    assert "synthetic:" not in out and not re.search(r"[0-9A-HJKMNP-TV-Z]{26}", out)
    window = sum(
        1 for k, v in gates.expectation_passes().items() if gates.replayable(k[0])[0] for e in v if "session" not in e
    )
    assert f"window_skipped={window} " in line


def test_a_mismatch_names_tree_and_verb_never_a_value(
    capsys: pytest.CaptureFixture[str], alarms: list[tuple[str, dict[str, Any]]]
) -> None:
    deployment = Deployment()

    def corrupt(verb: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if verb != "agent_run_token_rollup":
            return rows
        return [{**r, "tokens_input_total": int(re.sub(r"\D", "", PLANTED))} for r in rows]

    deployment.reader_hook = corrupt
    assert _main(deployment) == 1
    out = capsys.readouterr().out
    assert re.search(r"GOLDEN_MISMATCH verb=agent_run_token_rollup tree=corpus:\w+ pass=(final|stop)", out)
    assert "424242" not in out and "424242" not in repr(alarms)
    assert [c[0] for c in alarms] == ["file"]
    assert alarms[0][1]["title"] == "telemetry verification alarm: golden_replay_mismatch:agent_run_token_rollup"
    assert alarms[0][1]["source"] == "telemetry_verify"


def _dropping(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**r, "total_rows": len(rows) - 1} for r in rows[1:]]


def test_tree_rows_must_match_the_sent_set_with_one_root(capsys: pytest.CaptureFixture[str]) -> None:
    for label, hook in (
        ("a dropped row", lambda verb, rows: _dropping(rows) if verb == "session_tree_rows" else rows),
        ("two roots", lambda verb, rows: [{**r, "roots_in_tree": 2} for r in rows] if verb == "session_tree_rows" else rows),
    ):
        deployment = Deployment()
        deployment.reader_hook = hook
        assert _main(deployment, "--no-alarms") == 1, label
        assert "GOLDEN_MISMATCH verb=session_tree_rows" in capsys.readouterr().out
    clean = Deployment()
    stats, _ = gates.run(clean.env())
    assert not stats.mismatches, "a tree that stores no row answers as an absent session and is not a mismatch"


def test_a_run_that_cannot_complete_files_one_error_alarm(
    capsys: pytest.CaptureFixture[str], alarms: list[tuple[str, dict[str, Any]]]
) -> None:
    deployment = Deployment()
    deployment.append_status = 500
    assert _main(deployment) == 1
    out = capsys.readouterr().out
    assert "GOLDEN_STOP class=append_status_500" in out and PLANTED not in out and PLANTED not in repr(alarms)
    assert [c[1]["title"] for c in alarms] == ["telemetry verification alarm: golden_replay_error"]
    unreachable = Deployment()
    unreachable.reader_error = ConnectionError(PLANTED)
    assert _main(unreachable, "--no-alarms") == 1
    assert "GOLDEN_STOP class=reader_ConnectionError" in capsys.readouterr().out


def test_other_stops_are_named_by_class(capsys: pytest.CaptureFixture[str]) -> None:
    class Broken(Deployment):
        def writer(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if payload["action"] == "describe":
                return (500, {}) if self.mode == "status" else (200, {"blob_protocol": {"version": 9}})
            if payload["action"] == "register_tenant":
                return 500, {}
            return super().writer(payload)

        mode = "status"

    for mode, want in (("status", "describe_failed"), ("protocol", "describe_mismatch")):
        broken = Broken()
        broken.mode = mode
        assert _main(broken, "--no-alarms") == 1
        assert f"GOLDEN_STOP class={want}" in capsys.readouterr().out

    class NoTenant(Deployment):
        def writer(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            return (500, {}) if payload["action"] in ("register_tenant", "register_project") else super().writer(payload)

    assert _main(NoTenant(), "--no-alarms") == 1
    assert "GOLDEN_STOP class=register_tenant_failed" in capsys.readouterr().out


def test_never_imports_tests() -> None:
    tree = ast.parse(Path(gates.__file__).read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not [n for n in names if n == "tests" or n.startswith("tests.")]


def test_version_skew_skips_without_alarms(
    capsys: pytest.CaptureFixture[str], alarms: list[tuple[str, dict[str, Any]]]
) -> None:
    for label, setup in (
        ("a deployed stamp", lambda d: setattr(d, "stale_stamp", True)),
        ("a deploy record", lambda d: setattr(d, "record_sha", "other")),
        ("a deploy mid-run", lambda d: setattr(d, "skew_after_writes", 3)),
    ):
        deployment = Deployment()
        setup(deployment)
        deployment.reader_hook = lambda verb, rows: [{**r, "tokens_input_total": 1} for r in rows]
        assert _main(deployment) == 0, label
        assert "skew=1" in capsys.readouterr().out
        assert alarms == []
        assert _main(deployment, "--assert-zero-mismatches") == 1, label
        capsys.readouterr()
    failing = Deployment()
    failing.append_status = 500
    failing.skew_after_writes = 3
    assert _main(failing) == 0 and "skew=1" in capsys.readouterr().out and alarms == []


def test_assert_zero_mismatches_parses_and_lints_as_decidable() -> None:
    command = gates.ACCEPTANCE
    assert lint_acceptance_command(command, require_decidable=True) == (True, None)
    args = gates.parser().parse_args(shlex.split(command.split("lambda_telemetry_golden_gates", 1)[1]))
    assert args.no_alarms and args.assert_zero_mismatches
    assert gates.main(["--no-alarms", "--assert-zero-mismatches"], env=Deployment().env()) == 0


def test_alarm_filing_failure_is_reported_by_class_only(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(PLANTED)

    monkeypatch.setattr(gates, "raise_alarms", broken)
    gates.file_alarms({"golden_replay_error": 1})
    assert "GOLDEN_ALARMS_FAILED class=RuntimeError" in capsys.readouterr().out


def test_live_env_resolves_urls_and_reads_deploy_records(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3  # noqa: PLC0415

    from scripts.ducklake_smoke import core  # noqa: PLC0415

    class Body:
        def read(self) -> bytes:
            return b'{"source_git_sha": "deadbeef"}'

    class Client:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def get_parameter(self, Name: str) -> dict[str, Any]:  # noqa: N803
            return {"Parameter": {"Value": f"https://{Name.rsplit('/', 1)[-1]}.example/"}}

        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            assert Bucket == gates.RECORD_BUCKET and Key.startswith("deploy-records/ducklake/")
            return {"Body": Body()}

    class Session:
        def __init__(self, **_k: Any) -> None:
            pass

        def client(self, kind: str, **_k: Any) -> Client:
            return Client(kind)

    class Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"tenant_id": "T"}

    monkeypatch.setattr(boto3, "Session", Session)
    monkeypatch.delenv(gates.WRITER_URL_ENV, raising=False)
    monkeypatch.delenv(gates.READER_URL_ENV, raising=False)
    seen: list[str] = []
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, payload, **k: seen.append(url) or Resp())
    monkeypatch.setenv(read_route.CATALOG_ENV, SMOKE)
    built: list[Any] = []
    real = read_route.make_routed_reader
    monkeypatch.setattr(read_route, "make_routed_reader", lambda profile=None: built.append(profile) or real(profile))
    env = gates.live_env("p", "eu-west-2")
    assert built == ["p"] and env.catalog == SMOKE and isinstance(env.reader, read_route.RoutedReader)
    assert env.writer({"action": "register_tenant"}) == (200, {"tenant_id": "T"})
    assert seen == ["https://telemetry_writer_url.example"] and env.record("f") == {"source_git_sha": "deadbeef"}
    import os  # noqa: PLC0415

    assert os.environ[gates.READER_URL_ENV] == "https://reader_url.example"
    monkeypatch.delenv(gates.WRITER_URL_ENV)
    monkeypatch.delenv(gates.READER_URL_ENV)

    class NotJson(Resp):
        def json(self) -> dict[str, Any]:
            raise ValueError("no body")

    monkeypatch.setattr(core, "_sigv4_invoke", lambda *a, **k: NotJson())
    assert gates.live_env("p", "eu-west-2").writer({"action": "x"}) == (200, {})
    monkeypatch.delenv(gates.WRITER_URL_ENV, raising=False)
    monkeypatch.delenv(gates.READER_URL_ENV, raising=False)


def test_git_diff_guard_and_commit_label(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gates._git_unchanged("", ["src"]) is False
    assert gates._git_unchanged("0" * 40, ["src"]) is False
    head = gates._commit()
    assert head and head != "unknown"
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=gates.ROOT).stdout.strip()
    assert gates._git_unchanged(head_sha, ["src"])
    assert gates.deploy_paths() and all(isinstance(p, str) for p in gates.deploy_paths())


def _with_passes(monkeypatch: pytest.MonkeyPatch, extra: dict[tuple[str, bool], list[dict[str, Any]]]) -> None:
    real = gates.expectation_passes()
    monkeypatch.setattr(
        gates, "expectation_passes", lambda: {**{k: v for k, v in real.items() if k[0].endswith("tool_result_error")}, **extra}
    )


def test_seam_cases_are_skipped_and_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    for tree in (
        "corpus:secrets",
        "corpus:full_output_omitted_over_cap",
        "verification:resume_with_cost_state",
        "corpus:nope",
    ):
        assert gates.replayable(tree)[0] is None
    _with_passes(monkeypatch, {("corpus:full_output_omitted_over_cap", True): [], ("corpus:secrets", True): []})
    stats, skew = gates.run(Deployment().env())
    assert (stats.seam_skipped, stats.trees, skew) == (2, 1, 0)


def test_an_expectation_for_a_missing_session_is_a_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    ghost = {"verb": "agent_run_token_rollup", "session": "agent:ghost", "params": {}, "rows": []}
    _with_passes(monkeypatch, {("corpus:tool_result_error", False): [ghost]})
    stats, _ = gates.run(Deployment().env())
    assert stats.mismatches == {"agent_run_token_rollup": 1}


def test_stops_in_registration_blob_delivery_and_reads_are_named_by_class(capsys: pytest.CaptureFixture[str]) -> None:
    class NoProject(Deployment):
        def writer(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            return (500, {}) if payload["action"] == "register_project" else super().writer(payload)

    class Spilling(Deployment):
        def writer(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if payload["action"] == "describe":
                status, body = super().writer(payload)
                body["blob_protocol"]["inline_threshold_bytes"] = 4
                return status, body
            if payload["action"] == "begin_blobs":
                return 400, {"error": PLANTED}
            return super().writer(payload)

    class FailingVerb(Deployment):
        def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            if verb == "agent_run_token_rollup":
                raise TimeoutError(PLANTED)
            return super().named(verb, **params)

    class FailingTree(Deployment):
        def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            if verb == gates.TREE_VERB:
                raise ConnectionError(PLANTED)
            return super().named(verb, **params)

    for deployment, want in (
        (NoProject(), "register_project_failed"),
        (Spilling(), "blob_refused"),
        (FailingVerb(), "reader_TimeoutError"),
        (FailingTree(), "reader_ConnectionError"),
    ):
        assert _main(deployment, "--no-alarms") == 1
        out = capsys.readouterr().out
        assert f"GOLDEN_STOP class={want}" in out and PLANTED not in out


def test_unexpected_failures_in_the_guard_are_named_by_class(capsys: pytest.CaptureFixture[str]) -> None:
    class Describing(Deployment):
        def __init__(self, fail_on: int) -> None:
            super().__init__()
            self.calls, self.fail_on = 0, fail_on

        def describe(self) -> dict[str, Any]:
            self.calls += 1
            if self.calls == self.fail_on:
                raise RuntimeError(PLANTED)
            return super().describe()

    for fail_on, want in ((1, "run_RuntimeError"), (2, "guard_RuntimeError")):
        assert _main(Describing(fail_on), "--no-alarms") == 1
        assert f"GOLDEN_STOP class={want}" in capsys.readouterr().out

    class Records(Deployment):
        def env(self) -> gates.Env:
            real, count = super().env(), {"n": 0}
            inner = real.record

            def record(function: str) -> dict[str, Any]:
                count["n"] += 1
                if count["n"] > 2:
                    raise OSError(PLANTED)
                return inner(function)

            real.record = record
            return real

    failing = Records()
    failing.append_status = 500
    assert _main(failing, "--no-alarms") == 1
    assert "GOLDEN_STOP class=append_status_500" in capsys.readouterr().out


def _mismatched(field: str, value: Any, catalog: str = SMOKE) -> Deployment:
    deployment = Deployment(catalog)
    setattr(deployment, field, value(deployment) if callable(value) else value)
    return deployment


@pytest.mark.parametrize(
    ("deployment", "want"),
    [
        (lambda: _mismatched("served", {"meta_schema": PROD, "data_path_is_smoke": False}), "writer_catalog_mismatch"),
        (lambda: _mismatched("served", {"meta_schema": SMOKE, "data_path_is_smoke": False}), "writer_catalog_mismatch"),
        (
            lambda: _mismatched("health", lambda d: [{**d.health[0], "meta_schema": PROD}, *d.health[1:]]),
            "reader_catalog_mismatch",
        ),
        (lambda: _mismatched("health", []), "reader_catalog_mismatch"),
        (lambda: _mismatched("health", lambda d: d.health[1:]), "reader_catalog_mismatch"),
        (lambda: _mismatched("health", lambda d: [*d.health, d.health[0]]), "reader_catalog_mismatch"),
        (lambda: _mismatched("bound", {"meta_schema": SMOKE, "data_path_is_smoke": True}, PROD), "reader_catalog_mismatch"),
        (lambda: _mismatched("bound", None, PROD), "reader_catalog_mismatch"),
    ],
)
def test_catalog_mismatches_stop_before_any_registration(
    deployment: Any, want: str, capsys: pytest.CaptureFixture[str], alarms: list[tuple[str, dict[str, Any]]]
) -> None:
    built = deployment()
    assert _main(built) == 1
    assert capsys.readouterr().out.startswith(f"GOLDEN_STOP class={want}\n")
    assert built.writes == ["describe"] and built.projects == {}
    assert [c[1]["title"] for c in alarms] == ["telemetry verification alarm: golden_replay_error"]


def test_deploy_records_follow_the_routed_reader() -> None:
    for catalog, function in ((SMOKE, "agent-platform-ducklake-reader"), (PROD, "agent-platform-ducklake-telemetry-reader")):
        deployment = Deployment(catalog)
        env = deployment.env()
        assert gates.deploy_functions(env) == (function, gates.WRITER_FUNCTION)
        stats, skew = gates.run(env)
        assert skew == 0 and not stats.mismatches and stats.trees > 0
        assert set(deployment.records) == {function, gates.WRITER_FUNCTION}


def test_an_unresolved_catalog_stops_with_a_class_only_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], alarms: list[tuple[str, dict[str, Any]]]
) -> None:
    monkeypatch.setattr(gates, "_resolve_urls", lambda profile, region: ("https://w.example", "https://r.example"))
    monkeypatch.setattr(read_route, "make_routed_reader", lambda profile=None: read_route.RoutedReader(object(), profile))
    monkeypatch.setenv(read_route.CATALOG_ENV, PLANTED)
    assert gates.main([]) == 1
    out, err = capsys.readouterr()
    stop, filed = out.splitlines()
    assert (stop, err) == ("GOLDEN_STOP class=catalog_unresolved", "") and filed.startswith("GOLDEN_ALARMS filed=1 ")
    assert [c[1]["title"] for c in alarms] == ["telemetry verification alarm: golden_replay_error"]
    assert gates.main(["--no-alarms"]) == 1 and len(alarms) == 1
    assert capsys.readouterr().out == "GOLDEN_STOP class=catalog_unresolved\n"
