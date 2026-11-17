"""Materialise: shared fields, plan selection, input_incomplete, the probe marker, no network, no snapshot import."""

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
PROBE = "verdict_probe.proceed"


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


def test_probe_input_has_shared_fields_and_marker(tmp_path: Path) -> None:
    root = _snap(tmp_path, {f"{materialise.PROBE_MARKER_DIR}proceed.txt": "proceed\n", "docs/plans/PLAN-a.yaml": "slug: a\n"})
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], ["docs/plans/PLAN-a.yaml"], usable=True)
    assert json.loads((out / f"input-{PROBE}.json").read_text()) == {
        "change_id": "o/r#4",
        "head_sha": SHA,
        "plan": "slug: a\n",
        "marker": "proceed\n",
    }


def test_no_plan_gives_an_empty_plan_and_two_plans_are_unusable(tmp_path: Path) -> None:
    root = _snap(
        tmp_path,
        {
            f"{materialise.PROBE_MARKER_DIR}proceed.txt": "proceed",
            "docs/plans/PLAN-a.yaml": "a",
            "docs/plans/PLAN-b.yaml": "b",
        },
    )
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True)
    assert json.loads((out / f"input-{PROBE}.json").read_text())["plan"] == ""
    out2 = tmp_path / "second"
    out2.mkdir()
    materialise.materialise(root, out2, RES, [PROBE], ["docs/plans/PLAN-a.yaml", "docs/plans/PLAN-b.yaml"], usable=True)
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


def test_missing_marker_file_is_unusable(tmp_path: Path) -> None:
    out = _out(tmp_path)
    materialise.materialise(_snap(tmp_path, {}), out, RES, [PROBE], [], usable=True)
    assert (out / f"failure-{PROBE}.json").exists()


def test_planted_symlink_is_refused(tmp_path: Path) -> None:
    root = _snap(tmp_path, {"real.txt": "x"})
    marker = root / materialise.PROBE_MARKER_DIR
    marker.mkdir(parents=True)
    os.symlink(root / "real.txt", marker / "proceed.txt")
    out = _out(tmp_path)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True)
    assert (out / f"failure-{PROBE}.json").exists() and not (out / f"input-{PROBE}.json").exists()


def test_snapshot_modules_are_never_imported(tmp_path: Path) -> None:
    import sys

    root = _snap(
        tmp_path,
        {f"{materialise.PROBE_MARKER_DIR}proceed.txt": "proceed", "json.py": "raise SystemExit(99)", "sitecustomize.py": "x"},
    )
    out = _out(tmp_path)
    before = set(sys.modules)
    materialise.materialise(root, out, RES, [PROBE], [], usable=True)
    assert not any(str(getattr(sys.modules[m], "__file__", "") or "").startswith(str(root)) for m in set(sys.modules) - before)
    assert str(root) not in sys.path


def test_main_reads_the_prepared_files_and_the_snapshot_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    prepared = _out(tmp_path)
    (prepared / "resolution.json").write_text(json.dumps(RES))
    (prepared / "routes.json").write_text(json.dumps({"routes": [PROBE], "plan_paths": []}))
    (prepared / "snapshot.json").write_text(json.dumps({"ok": True}))
    root = _snap(tmp_path, {f"{materialise.PROBE_MARKER_DIR}proceed.txt": "proceed"})
    monkeypatch.setenv("PERSONA_SNAPSHOT", str(root))
    assert materialise.main([]) == 0
    assert (prepared / f"input-{PROBE}.json").exists()
    (prepared / "snapshot.json").write_text(json.dumps({"ok": False}))
    (prepared / f"input-{PROBE}.json").unlink()
    materialise.main([])
    assert (prepared / f"failure-{PROBE}.json").exists()
    assert snapshot.STATE == Path("prepared/snapshot.json") and route.ROUTES == Path("prepared/routes.json")
