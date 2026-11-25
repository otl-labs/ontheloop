"""The fixture-persona schema helpers: only a route in fixture_kind.personas has a schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.personas import execute, keyed_run

ROOT = Path(__file__).resolve().parents[2]


def test_fixture_personas_lists_the_canary() -> None:
    assert "canary_persona" in execute.fixture_personas(ROOT)


def test_a_fixture_route_has_input_and_output_schemas() -> None:
    assert execute.schema_for(ROOT, "canary_persona", "input")["required"] == ["change_id", "head_sha", "plan", "notes"]
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
    raw = json.dumps({"exit_code": 0, "timed_out": False, "stdout": "{}"}).encode()
    assert keyed_run.record({}, work, ROOT, raw) == 0
    out = json.loads((work / "results" / "canary_persona.json").read_text())
    assert out["image_digest"] == image_pin.load_pin(ROOT).digest and out["stdout"] == "{}"


def test_record_sidecar_maps(monkeypatch: pytest.MonkeyPatch) -> None:

    env = {
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
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

    assert keyed_run.request({}, tmp_path, ROOT) == keyed_run.EXIT_REFUSED
    captured = capfd.readouterr()
    assert captured.out == "" and "REFUSED" in captured.err
    work = tmp_path / "w"
    (work / "prepared").mkdir(parents=True)
    (work / "prepared" / "routes.json").write_text(json.dumps({"routes": ["canary_persona"], "plan_paths": []}))
    body = {"change_id": "o/r#1", "head_sha": "a" * 40, "plan": "", "notes": "NIT: x"}
    (work / "prepared" / "input-canary_persona.json").write_text(json.dumps(body))
    monkeypatch.setattr(keyed_run, "assume_role", lambda env: {})
    monkeypatch.setattr(keyed_run, "_ssm_value", lambda *a: "SENTINEL-KEY-VALUE")

    def broken(fd: int, data: bytes) -> None:
        raise OSError("sentinel path")

    monkeypatch.setattr(keyed_run, "_write_all", broken)
    assert keyed_run.request({"RUNNER_TEMP": str(tmp_path)}, work, ROOT) == keyed_run.EXIT_REFUSED
    captured = capfd.readouterr()
    assert captured.out == "" and "SENTINEL-KEY-VALUE" not in captured.err + captured.out


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


GOOD = {"change_id": "o/r#1", "head_sha": SHA, "plan": "", "notes": "BLOCKING: x"}


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
