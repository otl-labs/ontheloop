from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts.checks.roadmap import _plan_category_verdicts as cat
from scripts.roadmap.plan_gate_rounds import GateRound


def _draft(title: str) -> str:
    env = "significance:\n  value: numbered_decision\n  justification: r.\nkind:\n  value: direction\n  justification: j.\n"
    return f"## Decision {{{{N}}}}: {title}\n\n```yaml\nnumber: {{{{N}}}}\n{env}```\n\n**Status:** Decided\n"


def _row(round_: int = 1, entries: list[tuple[str, str]] | None = None, **extra) -> GateRound:
    data: dict = {"gate": "plan-critique", "round": round_, "verdict": "PROCEED", **extra}
    if entries is not None:
        data["category_consistency"] = [{"entry": t, "verdict": v} for t, v in entries]
    return GateRound.model_validate(data)


def _plan(tmp_path: Path, titles: list[str], rows: list[GateRound], version: int = 5):
    path = tmp_path / "PLAN-demo.yaml"
    path.write_text(
        yaml.safe_dump({"slug": "demo", "intent": "x", "context": ["\n".join(_draft(title=t) for t in titles)]}),
        encoding="utf-8",
    )
    return path, SimpleNamespace(slug="demo", schema_version=version, gate_rounds=rows)


def test_changed_sidecar_slugs_seam_and_plans_dir(tmp_path: Path) -> None:
    assert cat.changed_sidecar_slugs({"a"}, None) == {"a"}
    assert cat.changed_sidecar_slugs(None, tmp_path) == set()


def test_changed_sidecar_slugs_reads_the_diff() -> None:
    diff = [
        ("A", "docs/plans/approved/one.yaml"),
        ("M", "docs/plans/approved/two.yaml"),
        ("??", "docs/plans/approved/three.yaml"),
        ("D", "docs/plans/approved/gone.yaml"),
        ("M", "docs/plans/PLAN-x.yaml"),
        ("A", "docs/plans/approved/sub/deep.yaml"),
    ]
    with patch.object(cat._common, "get_status_aware_diff", return_value=diff):
        assert cat.changed_sidecar_slugs(None, None) == {"one", "two", "three"}


def test_a_complete_last_row_has_no_gap_and_a_revise_entry_is_kept(tmp_path: Path) -> None:
    path, doc = _plan(tmp_path, ["A", "B"], [_row(1, [("A", "PASS"), ("B", "PASS")])])
    assert cat.category_gaps(path, doc, tmp_path) == ([], [])
    kept = _row(
        2,
        [("A", "REVISE"), ("B", "PASS")],
        verdict="REVISE",
        blocking=1,
        escalated=True,
        escalation_choice="accept-with-deferral",
    )
    path, doc = _plan(tmp_path, ["A", "B"], [kept])
    assert cat.category_gaps(path, doc, tmp_path) == ([], [])


def test_missing_unknown_and_absent_are_gaps(tmp_path: Path) -> None:
    path, doc = _plan(tmp_path, ["A", "B"], [_row(1, [("A", "PASS")])])
    gaps, _ = cat.category_gaps(path, doc, tmp_path)
    assert len(gaps) == 1 and "omits" in gaps[0] and "'B'" in gaps[0]
    path, doc = _plan(tmp_path, ["A"], [_row(1, [("A", "PASS"), ("Z", "PASS")])])
    gaps, _ = cat.category_gaps(path, doc, tmp_path)
    assert len(gaps) == 1 and "not a drafted title" in gaps[0]
    path, doc = _plan(tmp_path, ["A"], [_row(1)])
    gaps, _ = cat.category_gaps(path, doc, tmp_path)
    assert len(gaps) == 1 and "single-quote the title" in gaps[0]


def test_earlier_rows_without_the_field_are_advisories(tmp_path: Path) -> None:
    path, doc = _plan(tmp_path, ["A"], [_row(1), _row(2, [("A", "PASS")])])
    gaps, advisories = cat.category_gaps(path, doc, tmp_path)
    assert gaps == [] and len(advisories) == 1 and "round 1" in advisories[0]


def test_no_draft_no_critique_or_old_schema_is_exempt(tmp_path: Path) -> None:
    path, doc = _plan(tmp_path, [], [_row(1)])
    assert cat.category_gaps(path, doc, tmp_path) == ([], [])
    path, doc = _plan(tmp_path, ["A"], [])
    assert cat.category_gaps(path, doc, tmp_path) == ([], [])
    path, doc = _plan(tmp_path, ["A"], [_row(1)], version=4)
    assert cat.category_gaps(path, doc, tmp_path) == ([], [])


def test_run_triggers_on_added_or_sidecar_changed_only(tmp_path: Path) -> None:
    path, doc = _plan(tmp_path, ["A"], [_row(1)])
    assert cat.run(path, doc, tmp_path, set(), set()) == ([], [])
    assert len(cat.run(path, doc, tmp_path, {path.name}, set())[0]) == 1
    assert len(cat.run(path, doc, tmp_path, set(), {"demo"})[0]) == 1
