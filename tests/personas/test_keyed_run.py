from __future__ import annotations

import json
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
