"""The fixture-persona schema helpers: only a route in fixture_kind.personas has a schema."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.personas import execute, keyed_run

ROOT = Path(__file__).resolve().parents[2]
ENV_RUN = {"PERSONA_ROLE": "primary", "GITHUB_JOB": "run"}
ENV_FALLBACK = {"PERSONA_ROLE": "warm_fetched_escape_hatch", "GITHUB_JOB": "run-fallback"}


def test_fixture_personas_lists_the_canary() -> None:
    assert "canary_persona" in execute.fixture_personas(ROOT)


def test_a_fixture_route_has_input_and_output_schemas() -> None:
    assert execute.schema_for(ROOT, "canary_persona", "input")["required"] == [
        "change_id",
        "head_sha",
        "plan",
        "notes",
        "fixture_guidance",
    ]
    assert execute.schema_for(ROOT, "canary_persona", "output") is not None


def test_a_non_fixture_route_has_no_schema() -> None:
    assert execute.schema_for(ROOT, "code_reviewer", "input") is None
    assert execute.failure_schema_for(ROOT, "code_reviewer") is None


def test_failure_schema_enumerates_the_contract_failures() -> None:
    schema = execute.failure_schema_for(ROOT, "canary_persona")
    assert schema is not None and "injection_suspected" in schema["properties"]["failure"]["enum"]


def test_missing_index_gives_no_routes(tmp_path: Path) -> None:
    assert execute.fixture_personas(tmp_path) == []


def test_probe_route_is_gone_and_fixture_route_runs_pinned_image(tmp_path: Path) -> None:
    from src.persona_hosts import image_pin

    assert not hasattr(execute, "PROBE_DIR") and not hasattr(execute, "execute")
    assert execute.fixture_personas(ROOT) == ["canary_persona"]
    work = tmp_path / "work"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": ["canary_persona"], "plan_paths": []}))
    (work / "prepared" / "input-canary_persona.json").write_text(json.dumps(GOOD))
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}"}).encode()
    assert keyed_run.record({"GITHUB_JOB": "run"}, work, ROOT, raw) == 0
    out = json.loads((work / "results" / "canary_persona.json").read_text())
    assert out["image_digest"] == image_pin.load_pin(ROOT).digest and out["stdout"] == "{}"


def test_record_sidecar_maps(monkeypatch: pytest.MonkeyPatch) -> None:

    env = {
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_JOB": "run",
    }
    monkeypatch.setattr(keyed_run, "project_ref", lambda root: "ref")
    body = json.loads(keyed_run.sidecar_bytes(env, ROOT) or b"null")
    assert body["agent"] == "canary-persona" and body["content_policy"] == "withheld"
    assert body["host"]["job"] == "run" and body["host"]["workflow_path"] == keyed_run.WORKFLOW_PATH
    monkeypatch.setattr(keyed_run, "project_ref", lambda root: None)
    assert keyed_run.sidecar_bytes(env, ROOT) is None


def test_request_refusal_keeps_the_key_off_both_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:

    assert keyed_run.request(ENV_RUN, tmp_path, ROOT) == keyed_run.EXIT_REFUSED
    captured = capfd.readouterr()
    assert captured.out == "" and "REFUSED" in captured.err
    work = tmp_path / "w"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": ["canary_persona"], "plan_paths": []}))
    body = {"change_id": "o/r#1", "head_sha": "a" * 40, "plan": "", "notes": "NIT: x", "fixture_guidance": ""}
    (work / "prepared" / "input-canary_persona.json").write_text(json.dumps(body))
    monkeypatch.setattr(keyed_run, "assume_role", lambda env: {})
    monkeypatch.setattr(keyed_run, "_ssm_value", lambda *a: "SENTINEL-KEY-VALUE")

    def broken(fd: int, data: bytes) -> None:
        raise OSError("sentinel path")

    monkeypatch.setattr(keyed_run, "_write_all", broken)
    assert keyed_run.request({**ENV_RUN, "RUNNER_TEMP": str(tmp_path)}, work, ROOT) == keyed_run.EXIT_REFUSED
    captured = capfd.readouterr()
    assert captured.out == "" and "SENTINEL-KEY-VALUE" not in captured.err + captured.out
    assert "REFUSED input" not in captured.err


SHA = "a" * 40
ROUTE = "canary_persona"


def _work(tmp_path: Path, route: str = ROUTE, body: dict[str, str] | None = None, routes: list[str] | None = None) -> Path:
    work = tmp_path / "work"
    prepared = work / "prepared"
    prepared.mkdir(parents=True)
    (prepared / "routes.json").write_text(json.dumps({"routes": routes if routes is not None else [route], "plan_paths": []}))
    if body is not None:
        (prepared / f"input-{route}.json").write_text(json.dumps(body))
    return work


GOOD = {"change_id": "o/r#1", "head_sha": SHA, "plan": "", "notes": "BLOCKING: x", "fixture_guidance": ""}


def test_valid_request_is_returned(tmp_path: Path) -> None:
    assert keyed_run.prepared_request(_work(tmp_path, body=GOOD), ROOT) == (ROUTE, GOOD)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"routes": []}, "routes"),
        ({"routes": [ROUTE, "x"]}, "routes"),
        ({"route": "code_reviewer", "body": GOOD}, "route_kind"),
        ({"body": {"change_id": "o/r#1"}}, "input"),
    ],
)
def test_bad_requests_are_refused(tmp_path: Path, kwargs: dict, reason: str) -> None:
    with pytest.raises(keyed_run.Refusal) as exc:
        keyed_run.prepared_request(_work(tmp_path, **kwargs), ROOT)
    assert str(exc.value) == reason


def test_a_failure_file_is_refused(tmp_path: Path) -> None:
    work = _work(tmp_path, body=GOOD)
    (work / "prepared" / f"failure-{ROUTE}.json").write_text("{}")
    with pytest.raises(keyed_run.Refusal):
        keyed_run.prepared_request(work, ROOT)


def test_launcher_result_shape() -> None:
    ok = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}"}).encode()
    assert keyed_run.launcher_result(ok) is not None
    assert keyed_run.launcher_result(b"nope") is None
    assert keyed_run.launcher_result(json.dumps({"exit_code": True, "timed_out": False, "stdout": ""}).encode()) is None


def test_record_without_a_launcher_result(tmp_path: Path) -> None:
    work = _work(tmp_path, body=GOOD)
    assert keyed_run.record({}, work, ROOT, b"") == 0
    assert json.loads((work / "results" / f"{ROUTE}.json").read_text()) == {"route": ROUTE, "failure": "launcher_no_result"}


def test_record_carries_a_failure_file(tmp_path: Path) -> None:
    work = _work(tmp_path)
    (work / "prepared" / f"failure-{ROUTE}.json").write_text(json.dumps({"failure": "input_incomplete"}))
    keyed_run.record({}, work, ROOT, b"")
    assert json.loads((work / "results" / f"{ROUTE}.json").read_text())["failure"] == "input_incomplete"


def test_request_reads_the_fallback_role_key_on_a_fallback_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    work = _work(tmp_path, body=GOOD)
    asked: list[str] = []
    monkeypatch.setattr(keyed_run, "assume_role", lambda env: {})
    monkeypatch.setattr(keyed_run, "_ssm_value", lambda creds, region, parameter: asked.append(parameter) or "KEY-1")
    out = tmp_path / "stdout"
    saved = os.dup(1)
    with out.open("wb") as handle:
        os.dup2(handle.fileno(), 1)
        try:
            code = keyed_run.request({**ENV_FALLBACK, "RUNNER_TEMP": str(tmp_path)}, work, ROOT)
        finally:
            os.dup2(saved, 1)
            os.close(saved)
    assert code == 0 and asked == ["/secret/agent-platform-anthropic-api-key"]
    assert json.loads(out.read_text())["provider"] == "anthropic/"
    err = capfd.readouterr().err
    assert "PERSONA-REQUEST role=warm_fetched_escape_hatch provider=anthropic/" in err and "KEY-1" not in err


@pytest.mark.parametrize(
    "env",
    [
        {"PERSONA_ROLE": "primary", "GITHUB_JOB": "run-fallback"},
        {"PERSONA_ROLE": "warm_fetched_escape_hatch", "GITHUB_JOB": "run"},
        {"PERSONA_ROLE": "aggregator", "GITHUB_JOB": "run"},
        {"GITHUB_JOB": "run"},
        {"PERSONA_ROLE": "primary"},
        {"PERSONA_ROLE": "primary", "GITHUB_JOB": "probe"},
    ],
)
def test_request_refuses_a_bad_role_pairing_with_nothing_on_stdout(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], env: dict[str, str]
) -> None:
    assert keyed_run.request(env, _work(tmp_path, body=GOOD), ROOT) == keyed_run.EXIT_REFUSED
    captured = capfd.readouterr()
    assert captured.out == "" and "REFUSED role" in captured.err


def _record(tmp_path: Path, job: str, result: dict[str, object]) -> str | None:
    work = _work(tmp_path, body=GOOD)
    out = tmp_path / "gh_output"
    env = {"GITHUB_JOB": job, "GITHUB_OUTPUT": str(out)}
    assert keyed_run.record(env, work, ROOT, json.dumps(result).encode()) == 0
    return out.read_text() if out.exists() else None


def _failed(upstream: object) -> dict[str, object]:
    return {"exit_code": 4, "timed_out": False, "stdout": "", "upstream": upstream}


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_failed({"ok": 0, "outage": 1, "inflight": 0}), "true"),
        (_failed({"ok": 0, "outage": 0, "inflight": 1}), "true"),
        (_failed({"ok": 1, "outage": 1, "inflight": 0}), "false"),
        (_failed({"ok": 0, "outage": 0, "inflight": 0}), "false"),
        (_failed({"ok": 0, "outage": True, "inflight": 0}), "false"),
        (_failed(None), "false"),
        ({"exit_code": 0, "timed_out": False, "stdout": "", "upstream": {"ok": 0, "outage": 2, "inflight": 0}}, "false"),
        ({"exit_code": None, "timed_out": True, "stdout": "", "upstream": {"ok": 0, "outage": 0, "inflight": 1}}, "true"),
    ],
)
def test_primary_outage_only_from_proxy_counts_on_run(tmp_path: Path, result: dict[str, object], expected: str) -> None:
    assert _record(tmp_path, "run", result) == f"primary_outage={expected}\n"


def test_primary_outage_is_false_without_a_launcher_result_and_unwritten_on_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, out = _work(tmp_path, body=GOOD), tmp_path / "o"
    assert keyed_run.record({"GITHUB_JOB": "run", "GITHUB_OUTPUT": str(out)}, work, ROOT, b"junk") == 0
    assert out.read_text() == "primary_outage=false\n"
    other = tmp_path / "f"
    assert _record(other, "run-fallback", _failed({"ok": 0, "outage": 5, "inflight": 0})) is None
    assert keyed_run.record({"GITHUB_JOB": "other", "GITHUB_OUTPUT": str(out)}, work, ROOT, b"{}") == 0
    assert out.read_text() == "primary_outage=false\n"
    monkeypatch.setattr(keyed_run, "project_ref", lambda root: "ref")
    assert keyed_run.sidecar_bytes({"GITHUB_JOB": "other", "GITHUB_SHA": "a" * 40}, ROOT) is None


def test_host_job_follows_github_job(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "o/r", "GITHUB_REF": "refs/heads/main"}
    monkeypatch.setattr(keyed_run, "project_ref", lambda root: "ref")
    for job in ("run", "run-fallback"):
        body = json.loads(keyed_run.sidecar_bytes({**env, "GITHUB_SHA": "a" * 40, "GITHUB_JOB": job}, ROOT) or b"null")
        assert body["host"]["job"] == job


def _contract_root(tmp_path: Path, text: str | None, route: str = "r_one") -> Path:
    root = tmp_path / "croot"
    (root / "docs/contracts/personas").mkdir(parents=True)
    if text is not None:
        (root / f"docs/contracts/personas/{route}.yaml").write_text(text, encoding="utf-8")
    return root


def test_guidance_slots_reads_any_contract(tmp_path: Path) -> None:
    assert execute.guidance_slots(_contract_root(tmp_path / "a", None), "r_one") == []
    assert execute.guidance_slots(_contract_root(tmp_path / "b", "persona: r_one\n"), "r_one") == []
    assert execute.guidance_slots(_contract_root(tmp_path / "c", "guidance_slots: [a, b]\n"), "r_one") == ["a", "b"]
    for name, text in (
        ("bad", "guidance_slots: [unclosed\n"),
        ("null", "guidance_slots:\n"),
        ("str", "guidance_slots: a\n"),
        ("elem", "guidance_slots: [1]\n"),
        ("map", "- x\n"),
    ):
        with pytest.raises(ValueError):
            execute.guidance_slots(_contract_root(tmp_path / name, text), "r_one")
    with pytest.raises(ValueError):
        execute.guidance_slots(_contract_root(tmp_path / "off", None), "../x")


def test_record_keeps_guidance_slot_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}"}).encode()

    def run(sub: str, slots: list[str] | Exception, body: dict[str, str]) -> dict:
        work = _work(tmp_path / sub, body=body)

        def fake(root: Path, route: str) -> list[str]:
            if isinstance(slots, Exception):
                raise slots
            return slots

        monkeypatch.setattr(execute, "guidance_slots", fake)
        keyed_run.record({}, work, ROOT, raw)
        return json.loads((work / "results" / f"{ROUTE}.json").read_text())

    assert run("a", ["focus"], {**GOOD, "focus": "V"})["guidance"] == {"focus": "V"}
    assert "guidance" not in run("b", [], GOOD)
    assert run("c", ["focus"], GOOD) == {"route": ROUTE, "failure": "input_unusable"}
    assert run("d", ValueError("x"), GOOD) == {"route": ROUTE, "failure": "input_unusable"}
    work = _work(tmp_path / "e", body=GOOD)
    monkeypatch.setattr(execute, "guidance_slots", lambda root, route: [])

    def boom(root: Path) -> None:
        raise ValueError("pin")

    monkeypatch.setattr(keyed_run.image_pin, "load_pin", boom)
    with pytest.raises(ValueError, match="pin"):
        keyed_run.record({}, work, ROOT, raw)


def test_record_telemetry_survives_guidance_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = {"model_call": [{"n": 1}]}
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}", "events": events}).encode()
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(list(argv))
        return 0

    monkeypatch.setattr(keyed_run, "sidecar_bytes", lambda env, root: b"{}")
    monkeypatch.setattr(keyed_run.telemetry_rows, "main", fake_main)

    def run(sub: str, slots: list[str] | Exception, body: dict[str, str]) -> tuple[Path, dict]:
        calls.clear()
        work = _work(tmp_path / sub, body=body)

        def fake(root: Path, route: str) -> list[str]:
            if isinstance(slots, Exception):
                raise slots
            return slots

        monkeypatch.setattr(execute, "guidance_slots", fake)
        assert keyed_run.record({}, work, ROOT, raw) == 0
        return work, json.loads((work / "results" / f"{ROUTE}.json").read_text())

    for sub, slots in (("raises", ValueError("x")), ("missing", ["focus"])):
        work, out = run(sub, slots, GOOD)
        assert out == {"route": ROUTE, "failure": "input_unusable"}
        assert len(calls) == 1
        staged = work / "telemetry-in" / ROUTE / "events.json"
        assert calls[0][calls[0].index("--events") + 1] == str(staged)
        assert json.loads(staged.read_text()) == events

    work, out = run("ok", ["focus"], {**GOOD, "focus": "V"})
    assert len(calls) == 1 and "image_digest" in out and out["guidance"] == {"focus": "V"}
