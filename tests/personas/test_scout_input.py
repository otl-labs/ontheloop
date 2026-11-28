"""The decision_scout provider: contract-valid input, head plan as data, main-side fields from base_root, computed fields."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from scripts.north_star_models import load_north_star
from scripts.personas import execute, materialise, scout_input
from scripts.personas.materialise import Unusable

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
PERSONA = "decision_scout"
PLAN_REL = "docs/plans/PLAN-sample.yaml"
SIDECAR_REL = "docs/plans/approved/sample.yaml"
COPIED = (
    scout_input.NORTH_STAR,
    scout_input.INDEX,
    scout_input.DECISIONS,
    scout_input.ROADMAP,
    "docs/ROADMAP-PLATFORM-ARCHIVE.yaml",
    execute.INDEX,
    f"{execute.CONTRACTS}/{PERSONA}.yaml",
)
PLAN = {"slug": "sample", "intent": "x", "scope": [{"file": "scripts/personas/route.py", "action": "Modify"}]}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@pytest.fixture(scope="module")
def real_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("real_base")
    for rel in COPIED:
        if (ROOT / rel).exists():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, base / rel)
    return base


@pytest.fixture
def base(real_base: Path, tmp_path: Path) -> Path:
    out = tmp_path / "base"
    shutil.copytree(real_base, out)
    return out


def _snap(tmp_path: Path, plan: Any = PLAN, extra: dict[str, str] | None = None) -> Path:
    snap = tmp_path / "snap"
    text = plan if isinstance(plan, str) else yaml.safe_dump(plan)
    _write(snap, {PLAN_REL: text, **(extra or {})})
    return snap


def _ctx(base: Path, plan_paths: list[str] | None = None) -> dict[str, Any]:
    resolution = {"change_id": "o/r#7", "head_sha": SHA}
    return {
        "route": PERSONA,
        "resolution": resolution,
        "fixture_paths": [],
        "plan_paths": [PLAN_REL] if plan_paths is None else plan_paths,
        "base_root": base,
    }


def _build(snap: Path, base: Path, plan_paths: list[str] | None = None) -> dict[str, Any]:
    return materialise.build_input(
        snap, PERSONA, _ctx(base)["resolution"], _ctx(base, plan_paths)["plan_paths"], base_root=base
    )


def _fails(snap: Path, base: Path, failure: str, plan_paths: list[str] | None = None) -> str:
    with pytest.raises(Unusable) as exc:
        _build(snap, base, plan_paths)
    assert exc.value.failure == failure
    return exc.value.detail


def test_decision_scout_input_validates_against_contract(tmp_path: Path, base: Path) -> None:
    body = _build(_snap(tmp_path), base)
    contract = yaml.safe_load((ROOT / execute.CONTRACTS / f"{PERSONA}.yaml").read_text(encoding="utf-8"))
    jsonschema.validate(body, contract["input_schema"])
    assert set(body) == set(contract["input_schema"]["required"])
    assert body["change_id"] == "o/r#7" and body["head_sha"] == SHA
    for field in ("tag_shortlist", "north_star_carriers", "roadmap_projection"):
        assert isinstance(json.loads(body[field]), dict)


def test_head_plan_is_read_as_data_never_executed(tmp_path: Path, base: Path) -> None:
    marker = tmp_path / "ran"
    evil = f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n"
    snap = _snap(tmp_path, extra={"scripts/evil_scout_probe.py": evil, "conftest.py": evil, "sitecustomize.py": evil})
    _build(snap, base)
    assert not marker.exists() and "evil_scout_probe" not in sys.modules
    tagged = "slug: sample\nintent: !!python/object/apply:os.system ['true']\n"
    assert "plan does not parse" in _fails(_snap(tmp_path / "t", tagged), base, "input_unusable")
    aliased = "slug: sample\nintent: &a x\nscope: *a\n"
    assert "plan does not parse" in _fails(_snap(tmp_path / "a", aliased), base, "input_unusable")


def test_main_side_fields_read_from_base_root(tmp_path: Path, base: Path) -> None:
    head = dict.fromkeys((scout_input.NORTH_STAR, scout_input.INDEX, scout_input.DECISIONS), "HEAD VERSION\n")
    body = _build(_snap(tmp_path, extra=head), base)
    assert body["north_star"] == (base / scout_input.NORTH_STAR).read_text(encoding="utf-8")
    assert body["decisions_index"] == (base / scout_input.INDEX).read_text(encoding="utf-8")
    assert body["decisions"] == (base / scout_input.DECISIONS).read_text(encoding="utf-8")
    assert all("HEAD VERSION" not in v for v in body.values() if isinstance(v, str))
    assert body["plan"] == (tmp_path / "snap" / PLAN_REL).read_text(encoding="utf-8")


def test_approved_text_is_the_head_sidecar_or_empty(tmp_path: Path, base: Path) -> None:
    assert _build(_snap(tmp_path / "none"), base)["approved_text"] == ""
    words = "blocks:\n  - {id: b, file: docs/DECISIONS.md, before: a, after: b}\n"
    assert _build(_snap(tmp_path / "some", extra={SIDECAR_REL: words}), base)["approved_text"] == words
    snap = _snap(tmp_path / "dir", extra={SIDECAR_REL + "/x": "y"})
    assert "approved-text sidecar" in _fails(snap, base, "input_unusable")


def test_tag_shortlist_and_carriers_follow_the_index(tmp_path: Path, base: Path) -> None:
    doc = load_north_star(base / scout_input.NORTH_STAR)
    principle = next(p.id for p in doc.principles if p.status == "active")
    rows = [
        {"number": 5, "live": True, "category_tags": ["deploy", "lambda"], "serves": [principle], "title": "t"},
        {"number": 3, "live": True, "category_tags": ["deploy"], "serves": [], "title": "t"},
        {"number": 2, "live": False, "category_tags": ["deploy"], "serves": [principle], "title": "t"},
    ]
    (base / scout_input.INDEX).write_text(json.dumps({"decisions": rows}), encoding="utf-8")
    body = _build(_snap(tmp_path), base)
    assert json.loads(body["tag_shortlist"]) == {"tags": {"deploy": [3, 5], "lambda": [5]}, "live_count": 2}
    carriers = json.loads(body["north_star_carriers"])
    active = [p.id for p in doc.principles if p.status == "active"]
    assert list(carriers) == sorted(["mission", *active])
    assert carriers["mission"]["carried_by"] == list(doc.mission.carried_by)
    carried = set(next(p.carried_by for p in doc.principles if p.id == principle))
    assert carriers[principle]["serves_only"] == ([5] if 5 not in carried else [])
    assert 2 not in carriers[principle]["serves_only"]


def test_roadmap_projection_unavailable_names_the_reason(tmp_path: Path, base: Path) -> None:
    available = json.loads(_build(_snap(tmp_path / "ok"), base)["roadmap_projection"])
    assert available["status"] == "available" and isinstance(available["rows"], list)
    (base / scout_input.ROADMAP).unlink()
    body = _build(_snap(tmp_path / "gone"), base)
    assert json.loads(body["roadmap_projection"]) == {"status": "unavailable", "reason": "FileNotFoundError"}


def test_missing_plan_or_main_file_fails_by_name(tmp_path: Path, base: Path) -> None:
    snap = _snap(tmp_path)
    _fails(snap, base, "input_incomplete", plan_paths=[])
    _write(tmp_path / "empty", {PLAN_REL: ""})
    _fails(tmp_path / "empty", base, "input_incomplete")
    for rel in (scout_input.NORTH_STAR, scout_input.INDEX, scout_input.DECISIONS):
        (base / rel).rename(base / f"{rel}.bak")
        assert rel in _fails(snap, base, "input_unusable")
        (base / f"{rel}.bak").rename(base / rel)
    (base / scout_input.INDEX).write_text("{not json", encoding="utf-8")
    assert scout_input.INDEX in _fails(snap, base, "input_unusable")
    (base / scout_input.INDEX).write_text(json.dumps({"decisions": []}), encoding="utf-8")
    (base / scout_input.NORTH_STAR).write_text("mission: [", encoding="utf-8")
    assert scout_input.NORTH_STAR in _fails(snap, base, "input_unusable")


def test_both_providers_register_through_materialise(tmp_path: Path, base: Path) -> None:
    assert {"code_reviewer", "decision_scout"} <= set(materialise.PROVIDERS)
    assert materialise.PROVIDERS["decision_scout"] is scout_input.build
    with pytest.raises(Unusable) as exc:
        materialise.build_input(
            _snap(tmp_path), "no_such_persona", {"change_id": "c", "head_sha": SHA}, [PLAN_REL], base_root=base
        )
    assert exc.value.failure == "input_incomplete"
