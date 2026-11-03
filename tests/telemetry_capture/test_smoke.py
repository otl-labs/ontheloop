"""Mirror test for scripts/telemetry_capture/smoke.py on a fake writer (the live run is VP step 8)."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts.telemetry_capture import __main__ as cli
from scripts.telemetry_capture import smoke, waits
from scripts.telemetry_capture.runner import SnapshotTree
from scripts.telemetry_capture.transport import Budget, WriterClient
from src.turn_capture.record_turn import TABLES, record_turn
from src.turn_capture.transcript import project_ref_pin

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".claude" / "hooks"
REF = "synthetic:pc-smoke-0000abcd"
URL = "https://abc.lambda-url.eu-west-2.on.aws"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_smoke_hook_{name}", HOOKS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Writer:
    """The live writer's observable contract in memory: registration, describe, blobs, append no-op re-sends, probe."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.objects: set[str] = set()
        self.projects: set[str] = set()
        self.refuse: str | None = None
        self.forget_on_probe = False
        self.probes = 0
        self.extra_rows = 0

    def __call__(self, method: str, url: str, data: bytes, headers: Any, timeout: float) -> tuple[int, bytes]:
        if method == "PUT":
            self.objects.add(hashlib.sha256(data).hexdigest())
            return 200, b""
        payload = json.loads(data)
        status, body = self.answer(payload)
        return status, json.dumps(body).encode()

    def answer(self, payload: dict[str, Any]) -> tuple[int, Any]:
        action = payload["action"]
        if action == self.refuse:
            return 403, {"ok": False, "error_type": "forbidden"}
        if action == "describe":
            caps = {"max_invoke_body_bytes": 5242880, "max_rows_per_table": 2000, "max_rows_per_request": 5000}
            return 200, {
                "ok": True,
                "actions": ["append_telemetry"],
                "verbs": {"append_telemetry": {"caps": caps}},
                "catalog": {"meta_schema": "ducklake_smoke", "data_path_is_smoke": True},
                "blob_protocol": {"version": 1, "inline_threshold_bytes": 65536, "cap_bytes": 8388608, "max_items": 200},
            }
        if action in ("register_tenant", "register_project"):
            self.projects.add(payload.get("project_ref", ""))
            return 200, {"ok": True, "created": True}
        if action == "begin_blobs":
            items = [
                {"existed": True, "upload": None}
                if i["content_sha256"] in self.objects
                else {"existed": False, "upload": {"url": "https://bucket.s3.invalid/o", "headers": {}}}
                for i in payload["items"]
            ]
            return 200, {"ok": True, "items": items}
        if action == "telemetry_probe":
            owner = payload["session_ref"]
            counts = {
                t: {
                    "rows": sum(
                        1 for k, r in self.rows.items() if k[0] == t and (r.get("session_ref") or r.get("entity_ref")) == owner
                    ),
                    "keys": 0,
                }
                for t in TABLES
            }
            counts["telemetry_sessions"]["rows"] += self.extra_rows
            self.probes += 1
            if self.forget_on_probe and self.probes == 2:
                self.rows.clear()
            return 200, {"ok": True, "grain_counts": counts}
        assert payload["project_ref"] in self.projects
        tables = {}
        for batch in payload["batches"]:
            inserted = 0
            for row in batch["rows"]:
                key = (batch["table"], row["external_ref"])
                if key not in self.rows:
                    self.rows[key] = row
                    inserted += 1
            tables[batch["table"]] = {"submitted": len(batch["rows"]), "collapsed": 0, "inserted": inserted}
        return 200, {"ok": True, "tables": tables}


def fake_time() -> tuple[Any, Any]:
    """A clock that advances on every read and a no-op sleep, so no drive waits on the real clock."""
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 0.5
        return ticks[0]

    return clock, lambda seconds: None


def run_smoke(tmp_path: Path, writer: Writer, out: list[str]) -> None:
    def factory(*args: Any, **kw: Any) -> WriterClient:
        budget = args[2] if len(args) > 2 else Budget.start(130)
        return WriterClient(URL, lambda body: {}, budget, http=writer, sleep=lambda s: None, rng=lambda: 0.0)

    stop, end = load("stop_log_turn"), load("session_end_log_turn")

    clock, sleep = fake_time()

    def stop_entry(stdin_text: str, env: dict[str, str]) -> object:
        def detach(args, environ):
            return cli.main(["run", *args], env=environ, client_factory=factory, clock=clock, sleep=sleep)

        return stop.main(stdin_text, env=env, detach=detach)

    def end_entry(stdin_text: str, env: dict[str, str]) -> object:
        return end.main(stdin_text, env=env, client_factory=factory, detach=lambda a, e: pytest.fail("must not detach"))

    smoke.Smoke(
        client_factory=lambda: factory(),
        stop=stop_entry,
        session_end=end_entry,
        env={smoke.SMOKE_ENV: REF},
        out=out.append,
        sleep=lambda s: None,
    ).run(REF, tmp_path)


def test_the_synthetic_tree_pins_the_ref_and_spans_all_four_tables(tmp_path: Path) -> None:
    root = smoke.write_tree(tmp_path, REF)
    assert project_ref_pin(SnapshotTree(root)) == REF
    expected = smoke.expected_rows(root, REF)
    assert set(expected) == set(TABLES) and all(expected.values())
    built = record_turn(SnapshotTree(root), None, project_ref=REF, session_final=True)
    assert any(len(r.get("content", "").encode()) > 65536 for r in built.transcripts)


def test_synthetic_tree_builds_every_new_column(tmp_path: Path) -> None:
    root = smoke.write_tree(tmp_path, REF)
    built = record_turn(SnapshotTree(root), None, project_ref=REF, session_final=True)
    opens = [r for r in built.sessions if r["event_kind"] == "open" and r["workflow"] != "subagent"]
    assert len(opens) == 1 and opens[0]["harness_session_key"] == "session_smoke0000"
    assert (opens[0]["checkout_sha"], opens[0]["checkout_base_sha"], opens[0]["checkout_dirty"]) == ("1" * 40, "2" * 40, False)
    calls = [r for r in built.observations if r["observation_type"] == "model_call"]
    assert calls and all(
        r["requested_model"] == "claude-smoke-1[1m]"
        and r["reasoning_effort"] == "high"
        and (r["cache_creation_1h_tokens"], r["cache_creation_5m_tokens"]) == (15, 25)
        and r["usage_final"] is True
        for r in calls
    )
    corrected = [r for r in calls if r["usage_source"] == "spawn_result"]
    assert len(corrected) == 1 and corrected[0]["tokens_output"] == 25


def test_smoke_passes_against_a_conforming_writer(tmp_path: Path) -> None:
    out: list[str] = []
    writer = Writer()
    run_smoke(tmp_path, writer, out)
    assert REF in writer.projects and writer.objects
    assert any(line.startswith("telemetry_agents built=") for line in out) and "blob rows delivered=1" in out
    assert all("inserted=0" in line for line in out if "replay" in line)


def test_smoke_fails_on_a_refused_registration_or_a_non_idempotent_replay(tmp_path: Path) -> None:
    writer = Writer()
    writer.refuse = "register_project"
    with pytest.raises(smoke.SmokeFailure, match="register_project status=403 error_type=forbidden"):
        run_smoke(tmp_path / "a", writer, [])
    writer = Writer()
    writer.forget_on_probe = True
    (tmp_path / "b").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="replay was not an identical-resend no-op"):
        run_smoke(tmp_path / "b", writer, [])
    replay_writer = Writer()
    replay_writer.extra_rows = 1
    (tmp_path / "c").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="read-back differs"):
        run_smoke(tmp_path / "c", replay_writer, [])


def test_a_stop_pass_that_writes_no_cursor_fails_after_the_poll(tmp_path: Path) -> None:
    clock = {"t": 0.0}

    def sleep(s: float) -> None:
        clock["t"] += s

    runner = smoke.Smoke(
        client_factory=lambda: WriterClient(URL, lambda b: {}, Budget.start(130), http=Writer()),
        stop=lambda stdin, env: None,
        session_end=lambda stdin, env: None,
        env={},
        out=lambda line: None,
        sleep=sleep,
        clock=lambda: clock["t"],
        poll_s=2.0,
    )
    with pytest.raises(smoke.SmokeFailure, match="did not finish"):
        runner.run(REF, tmp_path)


def test_hook_entry_runs_the_script_with_the_given_stdin_and_env(tmp_path: Path) -> None:
    script = tmp_path / "echo.py"
    script.write_text("import os, sys\nsys.stdout.write(sys.stdin.read() + os.environ['MARK'])\n")
    result = smoke.hook_entry(script)("in", {"MARK": "env", "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0 and result.stdout == "inenv"


def test_main_prints_one_pass_or_fail_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []
    monkeypatch.setattr(smoke.Smoke, "run", lambda self, ref, directory: seen.append(ref))
    assert smoke.main() == 0 and capsys.readouterr().out == "PC_SMOKE PASS\n"
    assert seen[0].startswith("synthetic:pc-smoke-") and len(seen[0]) == len("synthetic:pc-smoke-") + 8

    def fail(self, ref, directory):
        raise smoke.SmokeFailure("telemetry_agents read-back differs from what record_turn built")

    monkeypatch.setattr(smoke.Smoke, "run", fail)
    assert smoke.main() == 1
    assert capsys.readouterr().out == "PC_SMOKE FAIL telemetry_agents read-back differs from what record_turn built\n"

    def crash(self, ref, directory):
        raise KeyError("a value")

    monkeypatch.setattr(smoke.Smoke, "run", crash)
    assert smoke.main() == 1 and capsys.readouterr().out == "PC_SMOKE FAIL KeyError\n"


def test_scripts_never_import_tests() -> None:
    for path in sorted((REPO / "scripts" / "telemetry_capture").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(name == "tests" or name.startswith("tests.") for name in names), path


def test_smoke_fails_on_a_halt_an_unfinalized_cursor_no_counts_or_no_blob(tmp_path: Path, monkeypatch) -> None:
    halting = Writer()
    original = halting.answer
    halting.answer = lambda p: (  # type: ignore[method-assign]
        (409, {"ok": False, "error_type": "grain_conflict"}) if p["action"] == "append_telemetry" else original(p)
    )
    (tmp_path / "a").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="halt recorded"):
        run_smoke(tmp_path / "a", halting, [])
    blind = Writer()
    probe = blind.answer
    blind.answer = lambda p: (200, {"ok": True}) if p["action"] == "telemetry_probe" else probe(p)  # type: ignore[method-assign]
    (tmp_path / "b").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="no grain counts"):
        run_smoke(tmp_path / "b", blind, [])
    deferred = Writer()
    describe = deferred.answer
    deferred.answer = lambda p: (500, {}) if p["action"] == "describe" else describe(p)  # type: ignore[method-assign]
    (tmp_path / "d").mkdir()
    with pytest.raises(smoke.SmokeFailure, match=r"wrote no cursor \(outcome stopped\)"):
        run_smoke(tmp_path / "d", deferred, [])
    monkeypatch.setattr(smoke, "SIDECAR_LINES", 10)
    (tmp_path / "c").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="no blob row was delivered"):
        run_smoke(tmp_path / "c", Writer(), [])


def test_smoke_fails_when_session_end_does_not_finalize(tmp_path: Path) -> None:
    writer = Writer()
    stop = load("stop_log_turn")

    def factory(*args: Any, **kw: Any) -> WriterClient:
        budget = args[2] if len(args) > 2 else Budget.start(130)
        return WriterClient(URL, lambda body: {}, budget, http=writer, sleep=lambda s: None, rng=lambda: 0.0)

    clock, sleep = fake_time()

    def stop_entry(stdin_text: str, env: dict[str, str]) -> object:
        detach = lambda a, e: cli.main(["run", *a], env=e, client_factory=factory, clock=clock, sleep=sleep)  # noqa: E731
        return stop.main(stdin_text, env=env, detach=detach)

    runner = smoke.Smoke(
        client_factory=factory, stop=stop_entry, session_end=lambda s, e: None, env={smoke.SMOKE_ENV: REF}, out=print
    )
    with pytest.raises(smoke.SmokeFailure, match="did not finalize"):
        runner.run(REF, tmp_path)


def test_synthetic_tree_closes_turns_at_witness_and_error_and_opens_the_last(tmp_path: Path) -> None:
    root = smoke.write_tree(tmp_path, REF)
    final = record_turn(SnapshotTree(root), None, project_ref=REF, session_final=True)
    stop = record_turn(SnapshotTree(root), None, project_ref=REF)

    def root_turns(result: Any, kind: str) -> dict[str, dict[str, Any]]:
        rows = [r for r in result.observations if r["observation_type"] == "turn" and ":" not in r["session_ref"]]
        return {r["entity_ref"].rsplit("/", 1)[1]: r for r in rows if r["event_kind"] == kind}

    closes = root_turns(final, "close")
    assert set(closes) == {"p1", "p2"} and closes["p1"]["external_ref"] == "w1#0/turn_close"
    assert (closes["p1"]["outcome"], closes["p2"]["outcome"]) == ("success", "error")
    assert closes["p2"]["external_ref"] == "e1#0/turn_close"
    assert set(root_turns(final, "open")) == {"p1", "p2", "p3"} == set(root_turns(stop, "open"))
    assert set(root_turns(stop, "close")) == {"p1", "p2"}
    assert {r["external_ref"] for r in stop.observations} <= {r["external_ref"] for r in final.observations}


def test_smoke_fails_when_a_drive_reports_a_wait_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waits, "wait_for_witness", lambda *args: waits.WITNESS_TIMEOUT)
    (tmp_path / "a").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="the Stop drive reported witness_wait_timeout"):
        run_smoke(tmp_path / "a", Writer(), [])
    monkeypatch.setattr(waits, "wait_for_witness", lambda *args: "witness_unknown")
    (tmp_path / "b").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="the Stop drive did not report witness_seen"):
        run_smoke(tmp_path / "b", Writer(), [])
    monkeypatch.undo()
    monkeypatch.setattr(waits, "wait_for_prompt", lambda *args: waits.PROMPT_TIMEOUT)
    (tmp_path / "c").mkdir()
    with pytest.raises(smoke.SmokeFailure, match="the UserPromptSubmit drive reported prompt_wait_timeout"):
        run_smoke(tmp_path / "c", Writer(), [])
