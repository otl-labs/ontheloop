"""Mirror tests for scripts/checks/roadmap/validate_work_item_pilot.py -- the CD.45 work-item pilot fixture
evaluator (PLAN-work-item-pilot-fixture-evaluator). Each leg has a red-before case that fails on its own broken
input. The model-level cases and the model-driven budget case live in test__work_item_pilot_model.py."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from scripts.checks import _common, registry
from scripts.checks.roadmap import _manifest
from scripts.checks.roadmap import _work_item_pilot_model as model
from scripts.checks.roadmap.validate_work_item_pilot import validate_work_item_pilot
from scripts.checks.structural._classify import classify_path

_LABEL = "Work-item pilot fixture"
_HEAD = "# test fixture comment\n"
_DECISIONS = "## Decision 197: Work-item boundary (Decided)\nbody\n"
_ROADMAP: dict[str, Any] = {
    "candidate_decisions": [{"id": "CD.45", "state": "pending"}],
    "tier_items": [
        {"id": "T4.23", "exit_criteria": [{"id": "c6", "text": "target model", "status": "open"}]},
        {"id": "T3.3", "exit_criteria": ["a bare string criterion"]},
        {"id": "T9.1", "exit_criteria": [{"id": "c1", "text": "structured", "status": "open"}]},
    ],
}


def _reg(n):
    return {
        "why": "Capture turns so friction is observable without transcript reading.",
        "how": "Wire record_turn to the writer and derive metrics at read time.",
        "planes": ["data_plane"],
        "maturity": {
            "rung": "read_all",
            "transitions": [
                {"from": a, "to": b, "trigger": {"metric": "coverage ratio over window", "comparator": ">=", "threshold": 0.9}}
                for a, b in zip(model.RUNGS, model.RUNGS[1:])
            ],
        },
        "failure_signal": {
            "signal": f"capture rate drops for item {n}",
            "metric": f"capture_rate_{n}",
            "source": "ops reader",
        },
        "verification": ["c1"],
        "rollback": "Disable the producer wiring flag and fall back to manual review.",
        "evidence": [{"kind": "decision", "ref": "197"}],
        "settled": [{"id": "s1", "text": "Capture is data-plane only."}],
        "contested": [],
        "open_questions": [],
    }


def _item(n=1):
    ext = {"consideration_register": _reg(n)}
    return {"id": f"pwi-item-{n}", "kind": "epic", "title": "Capture producer wiring item", "extension": ext}


def _crit(n=1, cid="c1", method=None):
    method = method or {"kind": "execution", "check": "validate_placement"}
    return {
        "work_item": f"pwi-item-{n}",
        "criterion_id": cid,
        "text": "Capture rate is measured per turn.",
        "method": method,
        "status": "open",
    }


def _fixture(n_items=1, edges=(), **header):
    head = {
        "id": "telemetry-feedback-loop",
        "status": "provisional_v0",
        "authority": "CD.45",
        "grain": "decision-197-clause-3",
        "item_count_cap": model.ITEM_COUNT_CAP,
        "sunset": {
            "first_of": [{"criterion_met": model.SUNSET_CRITERION}, {"review_on": model.SUNSET_REVIEW_DATE.isoformat()}],
            "outcome": "lift rows into the store, or restructure per the operator's review; never delete",
        },
        "edges_disposition": "pilot_only -- re-authored by the implementation merge on lift, never lifted as-is",
    }
    head.update(header)
    return {
        "fixture": head,
        "work_items": [_item(n) for n in range(1, n_items + 1)],
        "work_item_criteria": [_crit(n) for n in range(1, n_items + 1)],
        "work_item_edges": list(edges),
    }


def _reg_of(data, i=0):
    return data["work_items"][i]["extension"]["consideration_register"]


def _canon(data):
    return _HEAD + model.render(model.Fixture.model_validate(data))


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    def _run(data=None, *, text=None, roadmap=_ROADMAP, decisions=_DECISIONS, files=None, changed=(), today=None, git=True):
        data = _fixture() if data is None else data
        tree = {
            model.FIXTURE_PATH: text if text is not None else _canon(data),
            "docs/ROADMAP-PLATFORM.yaml": roadmap if isinstance(roadmap, str) else yaml.safe_dump(roadmap),
            "docs/DECISIONS.md": decisions,
            "docs/notes.md": "x\n" * 5,
            **(files or {}),
        }
        for rel, content in tree.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content, encoding="utf-8")
        if git:
            _common.run(["git", "init", "-q"], cwd=tmp_path)
            _common.run(["git", "add", "-A"], cwd=tmp_path)
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        monkeypatch.setattr(_common, "get_changed_files", lambda root=None: list(changed))
        registry.pop_declaration()
        failed: list[str] = []
        validate_work_item_pilot(failed, today=today)
        return failed, capsys.readouterr().out

    return _run


@pytest.fixture
def green(run):
    def _green(data=None, **kwargs):
        failed, out = run(data, **kwargs)
        assert failed == [], out

    return _green


@pytest.fixture
def red(run):
    def _red(needle, data=None, **kwargs):
        explicit_text = "text" in kwargs
        if not explicit_text and data is not None:
            try:
                kwargs["text"] = _canon(data)
            except ValidationError:
                kwargs["text"] = yaml.safe_dump(data, sort_keys=False)
        failed, out = run(data, **kwargs)
        assert failed == [_LABEL], out
        assert needle in out, out
        assert explicit_text or "FAIL: L2" not in out, out

    return _red


class TestSkeleton:
    def test_empty_skeleton_passes_and_declares_examined(self, run):
        failed, out = run(_fixture(0))
        decl = registry.pop_declaration()
        assert failed == [] and out.count("PASS") == 6, out
        assert decl is not None and (decl.kind, decl.count, decl.unit) == ("examined", 1, "fixtures")

    def test_one_valid_item_passes(self, green):
        green()

    def test_absent_fixture_fails(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(_common, "ROOT", tmp_path)
        failed: list[str] = []
        validate_work_item_pilot(failed)
        assert failed == [_LABEL] and "absent" in capsys.readouterr().out


def _mut(path, value=None, *, drop=False):
    def apply(d):
        target = _reg_of(d)
        *keys, last = path
        for key in keys:
            target = target[key]
        target.pop(last) if drop else target.__setitem__(last, value)

    return apply


def _second_item_shares(signal, metric):
    def apply(d):
        d["work_items"].append(_item(2))
        d["work_item_criteria"].append(_crit(2))
        _reg_of(d, 1)["failure_signal"].update(signal=signal, metric=metric)

    return apply


def _top(key, value):
    return lambda d: d["work_items"][0].__setitem__(key, value)


def _crit0(**changes):
    return lambda d: d["work_item_criteria"][0].update(**changes)


_SCHEMA_CASES = {
    "unknown key": (_top("bogus", 1), "Extra inputs"),
    "kind plan": (_top("kind", "plan"), "epic"),
    "missing metric": (_mut(["failure_signal", "metric"], drop=True), "Field required"),
    "missing source": (_mut(["failure_signal", "source"], drop=True), "Field required"),
    "shared signal and metric": (
        _second_item_shares("capture rate drops for item 1", "capture_rate_1"),
        "shares its (failure",
    ),
    "shared pair folded": (_second_item_shares("  CAPTURE   rate drops for item 1", "Capture_Rate_1"), "shares its (failure"),
    "verification names absent criterion": (_mut(["verification"], ["c1", "c2"]), "absent criterion 'c2'"),
    "duplicate item": (lambda d: d["work_items"].append(copy.deepcopy(d["work_items"][0])), "duplicate work item id"),
    "duplicate criterion": (lambda d: d["work_item_criteria"].append(_crit()), "duplicate criterion"),
    "criterion names absent item": (_crit0(work_item="pwi-ghost"), "absent work item"),
    "too many criteria": (
        lambda d: d["work_item_criteria"].extend(_crit(1, f"c{i}") for i in range(2, 6)),
        "above MAX_CRITERIA",
    ),
}
_REVIEW = {"kind": "review", "resolver": "Operator reviews the report.", "evidence_requirement": "A signed review note."}


class TestSchema:
    @pytest.mark.parametrize("name", sorted(_SCHEMA_CASES))
    def test_broken_input_is_red(self, name, red):
        mutate, needle = _SCHEMA_CASES[name]
        data = _fixture()
        mutate(data)
        red(needle, data)

    def test_non_yaml_is_red(self, red):
        red("not valid YAML", text="a: [unclosed\n")

    def test_review_method_with_resolver_is_green(self, green):
        data = _fixture()
        _crit0(method=_REVIEW)(data)
        green(data)


class TestCap:
    def test_twelve_items_pass(self, green):
        green(_fixture(12))

    def test_thirteen_items_fail(self, red):
        red("ITEM_COUNT_CAP=12", _fixture(13), text=_canon(_fixture(13)))

    def test_per_item_line_ceiling(self, red, monkeypatch):
        monkeypatch.setattr(model, "PER_ITEM_LINE_CEILING", 5)
        monkeypatch.setattr(model, "HEADER_LINE_CEILING", 5)
        red("effective lines", _fixture(), text=_canon(_fixture()))

    def test_block_style_relayout_fails_render_equality(self, red):
        data = _fixture()
        red("canonical layout", data, text=_HEAD + yaml.safe_dump(data, default_flow_style=False, sort_keys=False))


_SECOND = f"{model.PILOT_DIR}/second.yaml"
_CD_ONLY = _DECISIONS + "## Decision 300: Ratify\nCD.45 is ratified.\n"
_CD_NEAR_MISS = _DECISIONS + f"## Decision 300: Other\nCD.450 moves to {_SECOND} and {model.PILOT_DIR}/\n"
_PATH_GLUED = _DECISIONS + f"## Decision 300: Other\nCD.45 moves {_SECOND}.old\n"
_CD_GLUED = _DECISIONS + f"## Decision 300: Other\nXCD.45 moves {_SECOND} and {model.PILOT_DIR}/\n"
_DIR_GLUED = _DECISIONS + f"## Decision 300: Other\nCD.45 moves x{model.PILOT_DIR}/\n"
_SENTENCE_END = _DECISIONS + f"## Decision 300: Rehome\nCD.45 moves to {_SECOND}.\n"
_SPLIT_PATH = _DECISIONS + f"## Decision 300: Rehome\n{_SECOND} and {model.PILOT_DIR}/\n## Decision 301: Other\nCD.45 noted.\n"


class TestSingleInstance:
    @pytest.mark.parametrize(
        ("needle", "files", "decisions"),
        [
            ("second file", {_SECOND: "x: 1\n"}, _DECISIONS),
            ("column-0", {"config/other.yaml": "work_item_edges: []\n"}, _DECISIONS),
            ("second file", {_SECOND: "x: 1\n"}, _DECISIONS + f"## Decision 300: Rehome\n{_SECOND}\n"),
            ("second file", {_SECOND: "x: 1\n"}, _CD_ONLY),
            ("second file", {_SECOND: "x: 1\n"}, _SPLIT_PATH),
            ("second file", {_SECOND: "x: 1\n"}, _CD_NEAR_MISS),
            ("second file", {_SECOND: "x: 1\n"}, _PATH_GLUED),
            ("second file", {_SECOND: "x: 1\n"}, _CD_GLUED),
        ],
    )
    def test_second_fixture_is_red(self, needle, files, decisions, red):
        red(needle, files=files, decisions=decisions)

    def test_indented_key_is_not_a_second_fixture(self, green):
        green(files={"config/other.yaml": "a:\n  work_items: []\n"})

    def test_decision_naming_path_and_cd45_admits(self, green):
        green(files={_SECOND: "x: 1\n"}, decisions=_DECISIONS + f"## Decision 300: Rehome\nCD.45 moves to {_SECOND}\n")

    def test_decision_naming_path_at_a_sentence_end_admits(self, green):
        green(files={_SECOND: "x: 1\n"}, decisions=_SENTENCE_END)


def _edge(to, kind="depends_on"):
    return {"from": "pwi-item-1", "to": to, "kind": kind}


class TestEdges:
    def test_pilot_item_tier_item_and_cd_targets_pass(self, green):
        green(_fixture(2, edges=[_edge("pwi-item-2"), _edge("T4.23"), _edge("CD.45", "part_of")]))

    @pytest.mark.parametrize(
        ("target", "needle"),
        [("T4.23:c6", "criterion id"), ("T3.3:c1", "criterion id"), ("rec-4026", "rec id"), ("T9.99", "archived")],
    )
    def test_bad_target_is_red(self, target, needle, red):
        red(needle, _fixture(edges=[_edge(target)]))

    @pytest.mark.parametrize(
        ("edges", "needle"),
        [
            ([_edge("T4.23", "informs")], "depends_on"),
            ([_edge("pwi-item-1")], "self-edge"),
            ([_edge("T4.23"), _edge("T4.23")], "duplicate edge"),
        ],
    )
    def test_malformed_edges_are_red(self, edges, needle, red):
        red(needle, _fixture(edges=edges))

    def test_absent_roadmap_fails_closed(self, red):
        red("fail closed", _fixture(edges=[_edge("T4.23")]), roadmap={"tier_items": []})

    @pytest.mark.parametrize(
        ("edges", "needle"),
        [
            ([{"from": "pwi-ghost", "to": "T4.23", "kind": "depends_on"}], "absent work item"),
            ([_edge(t) for t in ("T4.23", "CD.45", "T9.1", "T3.3")], "above MAX_EDGES_FROM_ITEM"),
        ],
    )
    def test_edge_integrity_is_red(self, edges, needle, red):
        red(needle, _fixture(edges=edges))

    @pytest.mark.parametrize(
        "needle",
        ["edge targets cannot resolve", "yaml unreadable (fail closed)", "sunset cannot be evaluated"],
    )
    def test_unreadable_roadmap_fails_closed_everywhere_it_is_read(self, needle, red):
        data = _fixture(edges=[_edge("T4.23")])
        _reg_of(data)["evidence"] = [{"kind": "roadmap", "ref": "T4.23"}]
        red(needle, data, roadmap="a: [unclosed\n")

    def test_git_failure_fails_the_single_instance_scan_closed(self, red):
        red("git ls-files failed", git=False)


_EVIDENCE_RED = {
    "unknown decision": ("decision", "999", "no '## Decision 999:' header"),
    "unregistered check": ("check", "no_such_check", "not a registered check"),
    "scaffold name": ("check", "lint", "not a registered check"),
    "file_line past end": ("file_line", "docs/notes.md:99", "past the end"),
    "file_line untracked": ("file_line", "docs/missing.md:1", "not a tracked file"),
    "file_line malformed": ("file_line", "docs/notes.md", "path:line"),
    "bare criterion": ("roadmap", "T3.3:c1", "bare-string"),
    "absent structured criterion": ("roadmap", "T4.23:c9", "no structured criterion"),
    "absent tier item": ("roadmap", "T9.99", "may have been archived"),
}
_EVIDENCE_GREEN = [
    ("decision", "197"),
    ("check", "validate_sloc_budget_raises"),
    ("file_line", "docs/notes.md:3"),
    ("roadmap", "T4.23:c6"),
    ("roadmap", "T9.1"),
    ("roadmap", "CD.45"),
]


def _evidence(*rows):
    data = _fixture()
    _reg_of(data)["evidence"] = [{"kind": k, "ref": r} for k, r in rows]
    return data


class TestEvidence:
    @pytest.mark.parametrize("name", sorted(_EVIDENCE_RED))
    def test_unresolvable_ref_is_red(self, name, red):
        kind, ref, needle = _EVIDENCE_RED[name]
        red(needle, _evidence((kind, ref)))

    @pytest.mark.parametrize(("kind", "ref"), _EVIDENCE_GREEN)
    def test_resolvable_ref_is_green(self, kind, ref, green):
        green(_evidence((kind, ref)))

    def test_rec_ref_is_format_checked_beside_an_anchor(self, green, red):
        green(_evidence(("decision", "197"), ("rec", "rec-12")))
        red("rec-NNNN", _evidence(("decision", "197"), ("rec", "rec-abc")))

    def test_execution_check_must_be_registered(self, red):
        data = _fixture()
        _crit0(method={"kind": "execution", "check": "lint"})(data)
        red("method check 'lint'", data)


def _archived_roadmap(stub_name="Archived item"):
    record = {"id": "T9.1", "tier": "T9", "name": "Archived item", "depends_on": [], "status": "complete"}
    record["exit_criteria"] = [{"id": "c1", "text": "structured", "status": "met", "met_by": "a" * 40}]
    roadmap = copy.deepcopy(_ROADMAP)
    stub = {k: record[k] for k in ("id", "tier", "depends_on", "status")} | {"name": stub_name, "archived": True}
    roadmap["tier_items"] = roadmap["tier_items"][:2] + [stub]
    archive = {"archive_of": "docs/ROADMAP-PLATFORM.yaml", "tier_items": [record]}
    return {"roadmap": roadmap, "files": {"docs/ROADMAP-PLATFORM-ARCHIVE.yaml": yaml.safe_dump(archive)}}


class TestArchivedRoadmapRefs:
    """Decision 227: _Context.roadmap() hydrates archived stubs, so a ref to an archived item's structured
    criterion resolves, and a broken archive leaves the roadmap unreadable (fail closed)."""

    def test_a_ref_to_an_archived_structured_criterion_resolves(self, green):
        green(_evidence(("roadmap", "T9.1:c1")), **_archived_roadmap())

    def test_a_broken_archive_fails_closed(self, red):
        red("unreadable (fail closed)", _evidence(("roadmap", "T9.1:c1")), **_archived_roadmap(stub_name="Drifted stub"))


def _moved(first_of_index, key, value):
    data = _fixture(0)
    data["fixture"]["sunset"]["first_of"][first_of_index][key] = value
    return data


class TestHeader:
    @pytest.mark.parametrize(
        ("data", "needle"),
        [
            (_fixture(0, item_count_cap=13), "item_count_cap 13"),
            (_moved(1, "review_on", "2027-02-28"), "review_on '2027-02-28'"),
            (_moved(0, "criterion_met", "T4.23:c7"), "criterion_met 'T4.23:c7'"),
        ],
    )
    def test_header_differing_from_code_is_red(self, data, needle, red):
        red(needle, data, text=_canon(data))


_TOUCHED = [model.FIXTURE_PATH]
_AFTER = date(2027, 2, 1)


def _roadmap(c6="open", cd_state="pending", drop=()):
    doc = copy.deepcopy(_ROADMAP)
    doc["tier_items"][0]["exit_criteria"][0]["status"] = c6
    doc["candidate_decisions"][0]["state"] = cd_state
    doc["candidate_decisions"] = [] if "cd" in drop else doc["candidate_decisions"]
    doc["tier_items"] = doc["tier_items"][1:] if "t423" in drop else doc["tier_items"]
    doc["tier_items"][0]["exit_criteria"] = [] if "c6" in drop else doc["tier_items"][0]["exit_criteria"]
    return doc


_RESTRUCTURE = _DECISIONS + f"## Decision 301: Restructure\nCD.45 fixture now lives under {model.PILOT_DIR}/\n"


class TestSunset:
    def test_day_before_review_touched_passes(self, green):
        green(changed=_TOUCHED, today=date(2027, 1, 30))

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"changed": _TOUCHED, "today": date(2027, 1, 31)},
            {"changed": _TOUCHED, "today": date(2026, 10, 1), "roadmap": _roadmap(c6="met")},
            {"changed": [f"{model.PILOT_DIR}/other.txt"], "today": _AFTER},
            {
                "changed": _TOUCHED,
                "today": _AFTER,
                "decisions": _DECISIONS + f"## Decision 301: Other\n{model.PILOT_DIR}/ only\n",
            },
            {"changed": _TOUCHED, "today": _AFTER, "decisions": _CD_ONLY},
            {"changed": _TOUCHED, "today": _AFTER, "decisions": _SPLIT_PATH},
            {"changed": _TOUCHED, "today": _AFTER, "decisions": _CD_NEAR_MISS},
            {"changed": _TOUCHED, "today": _AFTER, "decisions": _CD_GLUED},
            {"changed": _TOUCHED, "today": _AFTER, "decisions": _DIR_GLUED},
        ],
    )
    def test_triggered_and_touched_is_frozen(self, kwargs, red):
        red("write-frozen", **kwargs)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"roadmap": _roadmap(cd_state="ratified")},
            {"roadmap": _roadmap(cd_state="superseded")},
            {"decisions": _RESTRUCTURE},
        ],
    )
    def test_operator_only_exits_release_the_freeze(self, kwargs, green):
        green(changed=_TOUCHED, today=_AFTER, **kwargs)

    def test_triggered_untouched_passes_declared_with_report(self, run):
        failed, out = run(changed=["docs/notes.md"], today=_AFTER)
        decl = registry.pop_declaration()
        assert failed == [] and "SUNSET REACHED" in out
        assert decl is not None and decl.count == 1

    @pytest.mark.parametrize("drop", [("cd",), ("t423",), ("c6",)])
    def test_archived_anchor_fails_closed(self, drop, red):
        red("fail closed", roadmap=_roadmap(drop=drop))


class TestRegistration:
    def test_manifest_entry_is_ungated_in_pre_and_full(self):
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_work_item_pilot")
        assert entry.pre is True and entry.pre_globs is None and entry.full_segment == "full_after_lint"
        assert entry.module == "scripts.checks.roadmap.validate_work_item_pilot" and entry.attr == "validate_work_item_pilot"
        assert "validate_work_item_pilot" in [s.name for s in registry.pre_sequence()]
        assert "validate_work_item_pilot" in [s.name for s in registry.full_sequence()]
        assert registry.resolve("validate_work_item_pilot") is validate_work_item_pilot

    def test_taxonomy_row_is_schema_drift(self):
        table = yaml.safe_load((_common.ROOT / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))[
            "function_to_category"
        ]
        assert table["validate_work_item_pilot"] == "schema_drift"


class TestShippedFixture:
    def test_shipped_fixture_passes_every_leg_and_classifies_residual(self, capsys):
        failed: list[str] = []
        registry.pop_declaration()
        validate_work_item_pilot(failed)
        decl = registry.pop_declaration()
        assert failed == [], capsys.readouterr().out
        assert decl is not None and decl.count == 1
        assert classify_path(model.FIXTURE_PATH) == "residual"
