from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from scripts.personas import execute, keyed_run

ROOT = Path(__file__).resolve().parents[2]
ROUTE = "canary_persona"
SHA = "a" * 40
GOOD = {"change_id": "o/r#1", "head_sha": SHA, "plan": "", "notes": "BLOCKING: x", "fixture_guidance": ""}
ENV = {
    "GITHUB_RUN_ID": "1",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_REPOSITORY": "o/r",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_SHA": SHA,
    "GITHUB_JOB": "run",
}
EVENTS = {"model_call": [{"n": 1}]}


def _tmp_root(tmp_path: Path, edit: Callable[[dict], None]) -> Path:
    root = tmp_path / "root"
    for rel in (execute.INDEX, "config/persona_image.yaml", f"{execute.CONTRACTS}/{ROUTE}.yaml"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, root / rel)
    index = root / execute.INDEX
    doc = yaml.safe_load(index.read_text(encoding="utf-8"))
    edit(next(row for row in doc["personas"] if row["persona"] == ROUTE))
    index.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return root


def _record(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[list[str]]]:
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [ROUTE], "plan_paths": []}))
    (work / "prepared" / f"input-{ROUTE}.json").write_text(json.dumps(GOOD))
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(keyed_run, "project_ref", lambda r: "ref")
    monkeypatch.setattr(keyed_run.telemetry_rows, "main", fake_main)
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}", "events": EVENTS}).encode()
    assert keyed_run.record(ENV, work, root, raw) == 0
    return work, calls


def test_record_tags_the_routed_personas_registry_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tmp_root(tmp_path, lambda row: row.update(registry_key="canary-persona-alt"))
    work, calls = _record(tmp_path, root, monkeypatch)
    sidecar = json.loads((work / "telemetry-in" / ROUTE / "host-run.json").read_text())
    assert sidecar["agent"] == "canary-persona-alt" and len(calls) == 1


def test_record_refuses_telemetry_for_a_route_with_no_registry_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tmp_root(tmp_path, lambda row: row.pop("registry_key"))
    work, calls = _record(tmp_path, root, monkeypatch)
    assert (work / "results" / f"telemetry-{ROUTE}.status").read_text() == "exit 2 REFUSED registry_key\n"
    assert not (work / "telemetry-in" / ROUTE / "host-run.json").exists() and calls == []
    assert json.loads((work / "results" / f"{ROUTE}.json").read_text())["stdout"] == "{}"


def test_registry_key_reads_the_index_row() -> None:
    assert execute.registry_key(ROOT, "canary_persona") == "canary-persona"
    assert execute.registry_key(ROOT, "code_reviewer") == "code-review"
    assert execute.registry_key(ROOT, "no_such_persona") is None


def test_registry_key_is_none_for_a_malformed_key(tmp_path: Path) -> None:
    root = _tmp_root(tmp_path, lambda row: row.update(registry_key="Bad Key"))
    assert execute.registry_key(root, ROUTE) is None
    assert execute.registry_key(tmp_path / "empty", ROUTE) is None


def test_implemented_verdict_route_is_accepted_and_pending_refused(tmp_path: Path) -> None:
    schema = execute.schema_for(ROOT, "code_reviewer", "input")
    assert schema is not None
    body = dict.fromkeys(schema["required"], "x")
    body["head_sha"] = SHA
    work = tmp_path / "ok"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": ["code_reviewer"]}))
    (work / "prepared" / "input-code_reviewer.json").write_text(json.dumps(body))
    assert keyed_run.prepared_request(work, ROOT) == ("code_reviewer", body)
    pending = tmp_path / "pending"
    (pending / "prepared").mkdir(parents=True)
    (pending / "prepared" / "routes.json").write_text(json.dumps({"routes": ["plan_critic"]}))
    with pytest.raises(keyed_run.Refusal) as exc:
        keyed_run.prepared_request(pending, ROOT)
    assert str(exc.value) == "route_kind"


def _hits(*rows: tuple[str, bool]) -> str:
    return json.dumps({"terms": [{"term": "t", "hits": [{"path": p, "line": 1, "in_bundle": b} for p, b in rows]}]})


def _record_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: object, body: dict | None = None) -> dict:
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [ROUTE], "plan_paths": []}))
    (work / "prepared" / f"input-{ROUTE}.json").write_text(json.dumps(body or GOOD))
    (work / "prepared" / "resolution.json").write_text(json.dumps({"head_sha": "b" * 40}))
    monkeypatch.setattr(keyed_run, "checked_out_sha", lambda root: "c" * 40)
    monkeypatch.setattr(keyed_run, "_telemetry", lambda *a: None)
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}", "events": events}).encode()
    assert keyed_run.record(ENV, work, ROOT, raw) == 0
    return json.loads((work / "results" / f"{ROUTE}.json").read_text())


def test_record_carries_reads_and_not_examined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    none = _record_reads(tmp_path / "a", monkeypatch, {"turns": [{"kind": "tool"}]})
    assert none["reads"] == [] and none["trees"] == {"head": "b" * 40, "main": "c" * 40} and "not_examined" not in none
    reads = [
        {"tree": "head", "op": "read", "path": "./src/a.py", "result": "ok"},
        {"tree": "head", "op": "read", "path": "src/b.py", "result": "refused"},
        {"tree": "main", "op": "read", "path": "src/c.py", "result": "ok"},
        {"tree": "head", "op": "search", "path": "src/d.py", "result": "ok"},
    ]
    body = {
        **GOOD,
        "reference_hits": _hits(
            ("src/a.py", False), ("src/b.py", False), ("src/c.py", False), ("src/d.py", False), ("src/e.py", True)
        ),
    }
    body["reference_hits"] = json.loads(body["reference_hits"])
    got = _record_reads(tmp_path / "b", monkeypatch, {"turns": [{"reads": reads[:2]}, {"reads": reads[2:]}]}, body)
    assert len(got["reads"]) == 4 and got["not_examined"] == ["src/b.py", "src/c.py", "src/d.py"]
    many = [{"tree": "head", "op": "list", "path": str(i), "result": "ok"} for i in range(keyed_run.MAX_READS + 5)]
    dropped = _record_reads(tmp_path / "c", monkeypatch, {"turns": [{"reads": many}]})
    assert len(dropped["reads"]) == keyed_run.MAX_READS and dropped["reads_dropped"] is True
    for unreadable in (None, {"turns": "x"}, {"turns": [{"reads": "x"}]}, {"turns": [{"reads": ["x"]}]}):
        got = _record_reads(tmp_path / f"u{id(unreadable)}", monkeypatch, unreadable)
        assert got["reads_unknown"] is True and "reads" not in got and got["trees"]["head"] == "b" * 40


def _work(tmp_path: Path, body: dict) -> Path:
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [ROUTE], "plan_paths": []}))
    (work / "prepared" / f"input-{ROUTE}.json").write_text(json.dumps(body))
    return work


def _request_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], tar: bytes | None) -> bytes:
    work = _work(tmp_path, body=GOOD)
    if tar is not None:
        (work / "prepared" / "head.tar").write_bytes(tar)
    monkeypatch.setattr(keyed_run, "assume_role", lambda env: {})
    monkeypatch.setattr(keyed_run, "_ssm_value", lambda creds, region, parameter: "KEY-1")
    monkeypatch.setattr(keyed_run, "tree_names", lambda root, route: names)
    out = tmp_path / "stdout"
    saved = os.dup(1)
    with out.open("wb") as handle:
        os.dup2(handle.fileno(), 1)
        try:
            assert keyed_run.request({**ENV, "PERSONA_ROLE": "primary", "RUNNER_TEMP": str(tmp_path)}, work, ROOT) == 0
        finally:
            os.dup2(saved, 1)
            os.close(saved)
    return out.read_bytes()


def _through_the_launcher_reader(raw: bytes, mode: str = "run") -> tuple[dict, bytes]:
    import threading

    from scripts.personas import egress_launcher as L

    read_fd, write_fd = os.pipe()

    def feed() -> None:
        with os.fdopen(write_fd, "wb") as sink:
            sink.write(raw)

    threading.Thread(target=feed).start()
    with os.fdopen(read_fd, "rb") as stream:
        line = stream.readline(L.MAX_REQUEST + 2)
        assert line.endswith(b"\n")
        request = L.parse_request(line[:-1], mode, {"deepseek/": {}})
        size = request.get("head_tree_bytes", 0)
        return request, stream.read(size) + (b"EXTRA" if stream.read(1) else b"")


def test_request_bytes_reach_the_launcher_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain, tail = _through_the_launcher_reader(_request_bytes(tmp_path / "a", monkeypatch, [], None))
    assert "trees" not in plain and "head_tree_bytes" not in plain and tail == b""
    tar = bytes(range(256)) * 5000
    request, tail = _through_the_launcher_reader(_request_bytes(tmp_path / "b", monkeypatch, ["head", "main"], tar))
    assert request["trees"] == ["head", "main"] and request["head_tree_bytes"] == len(tar) and tail == tar
    main_only, tail = _through_the_launcher_reader(_request_bytes(tmp_path / "c", monkeypatch, ["main"], None))
    assert main_only["trees"] == ["main"] and "head_tree_bytes" not in main_only and tail == b""


def test_a_head_route_without_its_tar_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    work = _work(tmp_path, body=GOOD)
    monkeypatch.setattr(keyed_run, "tree_names", lambda root, route: ["head"])
    assert keyed_run.request({**ENV, "PERSONA_ROLE": "primary"}, work, ROOT) == keyed_run.EXIT_REFUSED
    captured = capfd.readouterr()
    assert captured.out == "" and "REFUSED head_tree" in captured.err


def _rows_file(results: Path, route: str) -> None:
    rows = {"batches": [{"table": "telemetry_agents", "rows": [{"session_started_at": "2026-11-27T10:00:00Z"}]}]}
    (results / f"telemetry-{route}.json").write_text(json.dumps(rows))


def test_record_carries_run_ref_definition_hash_and_served_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = {
        "header": {"rendered_prompt_sha256": "c" * 64},
        "turns": [
            {"record": {"model": "m-b", "requested_model": "req"}},
            {"record": {"requested_model": "m-a"}},
            {"record": {"model": "m-b"}},
            {"attempts": [{"model": "attempt-only"}]},
        ],
    }
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [ROUTE], "plan_paths": []}))
    (work / "prepared" / f"input-{ROUTE}.json").write_text(json.dumps(GOOD))
    (work / "results").mkdir()
    monkeypatch.setattr(keyed_run, "_telemetry", lambda env, w, results, route, ev, root: _rows_file(results, route))
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}", "events": events}).encode()
    assert keyed_run.record(ENV, work, ROOT, raw) == 0
    got = json.loads((work / "results" / f"{ROUTE}.json").read_text())
    assert got["run_ref"] == "litellm:gha:1/1/run/1/run" and got["session_started_at"] == "2026-11-27T10:00:00Z"
    assert got["definition_sha256"] == "c" * 64 and got["served_models"] == ["m-a", "m-b"]
    assert got["served_model"] == "m-b"
    fallback = {"turns": [{"record": {"model": "b-model"}}, {"record": {"model": "a-model"}}, {"attempts": []}]}
    assert keyed_run.served_model(fallback) == "a-model" and keyed_run.served_models(fallback) == ["a-model", "b-model"]
    assert keyed_run.served_model({"turns": [{"record": {}}]}) is None and keyed_run.served_model(None) is None
    assert keyed_run.run_ref({**ENV, "GITHUB_JOB": "run-fallback"}) == "litellm:gha:1/1/run-fallback/1/run"
    (work / "results" / f"telemetry-{ROUTE}.json").unlink()
    monkeypatch.setattr(keyed_run, "_telemetry", lambda *a: None)
    bad = {"header": {"rendered_prompt_sha256": "short"}, "turns": "x"}
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}", "events": bad}).encode()
    assert keyed_run.record(ENV, work, ROOT, raw) == 0
    got = json.loads((work / "results" / f"{ROUTE}.json").read_text())
    assert (got["session_started_at"], got["definition_sha256"], got["served_models"]) == (None, None, [])


def test_early_return_records_carry_run_ref_or_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(name: str, env: dict[str, str], failure_file: bool, raw: bytes) -> dict:
        work = tmp_path / name
        (work / "prepared").mkdir(parents=True)
        (work / "prepared" / "routes.json").write_text(json.dumps({"routes": [ROUTE], "plan_paths": []}))
        if failure_file:
            (work / "prepared" / f"failure-{ROUTE}.json").write_text(json.dumps({"failure": "input_incomplete"}))
        assert keyed_run.record(env, work, ROOT, raw) == 0
        return json.loads((work / "results" / f"{ROUTE}.json").read_text())

    ref = "litellm:gha:1/1/run/1/run"
    assert run("a", ENV, True, b"")["run_ref"] == ref
    assert run("b", ENV, False, b"not json")["run_ref"] == ref
    got = run("c", {**ENV, "GITHUB_RUN_ID": "x1"}, False, b"not json")
    assert "run_ref" not in got and got["failure"] == "launcher_no_result"
    assert "run_ref" not in run("d", {**ENV, "GITHUB_JOB": "other"}, True, b"")
    assert keyed_run.run_ref({**ENV, "GITHUB_RUN_ATTEMPT": ""}) is None
