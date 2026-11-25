"""Materialise: shared fields, plan selection, input_incomplete, the fixture notes, no network, no snapshot import."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from scripts.personas import materialise, resolve_pr, route, snapshot

SHA = "a" * 40
RES = {"change_id": "o/r#4", "head_sha": SHA}
PROBE = "canary_persona"
FX = f"{route.FIXTURE_DIR}one.txt"


def _snap(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "snap"
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "prepared"
    out.mkdir()
    return out


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("network call")

    monkeypatch.setattr(resolve_pr, "request", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_fixture_input_has_shared_fields_and_notes(tmp_path: Path) -> None:
    root = _snap(tmp_path, {FX: "BLOCKING: a\n", "docs/plans/PLAN-a.yaml": "slug: a\n"})
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], ["docs/plans/PLAN-a.yaml"], usable=True, fixture_paths=[FX])
    assert json.loads((out / f"input-{PROBE}.json").read_text()) == {
        "change_id": "o/r#4",
        "head_sha": SHA,
        "plan": "slug: a\n",
        "notes": "BLOCKING: a",
    }


def test_no_plan_gives_an_empty_plan_and_two_plans_are_unusable(tmp_path: Path) -> None:
    root = _snap(
        tmp_path,
        {
            FX: "NIT: a",
            "docs/plans/PLAN-a.yaml": "a",
            "docs/plans/PLAN-b.yaml": "b",
        },
    )
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True, fixture_paths=[FX])
    assert json.loads((out / f"input-{PROBE}.json").read_text())["plan"] == ""
    out2 = tmp_path / "second"
    out2.mkdir()
    materialise.materialise(
        root, out2, RES, [PROBE], ["docs/plans/PLAN-a.yaml", "docs/plans/PLAN-b.yaml"], usable=True, fixture_paths=[FX]
    )
    assert json.loads((out2 / f"failure-{PROBE}.json").read_text())["failure"] == "input_unusable"


def test_route_without_a_provider_is_input_incomplete_and_a_registered_one_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _snap(tmp_path, {})
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, ["some_persona"], [], usable=True)
    assert json.loads((out / "failure-some_persona.json").read_text())["failure"] == "input_incomplete"
    monkeypatch.setitem(materialise.PROVIDERS, "some_persona", lambda r, ctx: {"extra": "x"})
    materialise.materialise(root, out, RES, ["some_persona"], [], usable=True)
    assert json.loads((out / "input-some_persona.json").read_text())["extra"] == "x"


def test_missing_fixture_file_is_unusable(tmp_path: Path) -> None:
    out = _out(tmp_path)
    materialise.materialise(_snap(tmp_path, {}), out, RES, [PROBE], [], usable=True, fixture_paths=[FX])
    assert (out / f"failure-{PROBE}.json").exists()


def test_planted_symlink_is_refused(tmp_path: Path) -> None:
    root = _snap(tmp_path, {"real.txt": "x"})
    marker = root / route.FIXTURE_DIR
    marker.mkdir(parents=True)
    os.symlink(root / "real.txt", marker / "one.txt")
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True, fixture_paths=[FX])
    assert (out / f"failure-{PROBE}.json").exists() and not (out / f"input-{PROBE}.json").exists()


def test_snapshot_modules_are_never_imported(tmp_path: Path) -> None:
    import sys

    root = _snap(
        tmp_path,
        {FX: "NIT: a", "json.py": "raise SystemExit(99)", "sitecustomize.py": "x"},
    )
    out = _out(tmp_path)
    before = set(sys.modules)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True, fixture_paths=[FX])
    assert not any(str(getattr(sys.modules[m], "__file__", "") or "").startswith(str(root)) for m in set(sys.modules) - before)
    assert str(root) not in sys.path


def test_main_reads_the_prepared_files_and_the_snapshot_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    prepared = _out(tmp_path)
    (prepared / "resolution.json").write_text(json.dumps(RES))
    (prepared / "routes.json").write_text(json.dumps({"routes": [PROBE], "plan_paths": [], "fixture_paths": [FX]}))
    (prepared / "snapshot.json").write_text(json.dumps({"ok": True}))
    root = _snap(tmp_path, {FX: "NIT: a"})
    monkeypatch.setenv("PERSONA_SNAPSHOT", str(root))
    assert materialise.main([]) == 0
    assert (prepared / f"input-{PROBE}.json").exists()
    (prepared / "snapshot.json").write_text(json.dumps({"ok": False}))
    (prepared / f"input-{PROBE}.json").unlink()
    materialise.main([])
    assert (prepared / f"failure-{PROBE}.json").exists()
    assert snapshot.STATE == Path("prepared/snapshot.json") and route.ROUTES == Path("prepared/routes.json")


def test_fixture_path_outside_the_fixture_dir_and_empty_notes_are_unusable(tmp_path: Path) -> None:
    root = _snap(tmp_path, {"other.txt": "x", f"{route.FIXTURE_DIR}empty.txt": "\n"})
    for rel in ("other.txt", f"{route.FIXTURE_DIR}empty.txt"):
        out = tmp_path / rel.replace("/", "_")
        out.mkdir()
        materialise.materialise(root, out, RES, [PROBE], [], usable=True, fixture_paths=[rel])
        assert (out / f"failure-{PROBE}.json").exists()


def test_pr_body_joins_the_notes_and_an_oversized_body_is_unusable(tmp_path: Path) -> None:
    root = _snap(tmp_path, {FX: "NIT: a"})
    out = _out(tmp_path)
    materialise.materialise(root, out, {**RES, "body": "BLOCKING: b"}, [PROBE], [], usable=True, fixture_paths=[FX])
    assert json.loads((out / f"input-{PROBE}.json").read_text())["notes"] == "NIT: a\nBLOCKING: b"
    out2 = tmp_path / "big"
    out2.mkdir()
    materialise.materialise(root, out2, {**RES, "body_oversized": True}, [PROBE], [], usable=True, fixture_paths=[FX])
    assert (out2 / f"failure-{PROBE}.json").exists()


def test_fixture_input_carries_every_schema_field(tmp_path: Path) -> None:
    from scripts.personas import execute

    root = _snap(tmp_path, {FX: "NIT: a"})
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True, fixture_paths=[FX])
    body = json.loads((out / f"input-{PROBE}.json").read_text())
    schema = execute.schema_for(execute.ROOT, PROBE, "input")
    assert schema is not None and set(schema["required"]) == set(body) and set(schema["properties"]) == set(body)
