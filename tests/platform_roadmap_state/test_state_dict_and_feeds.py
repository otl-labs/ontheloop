"""Tests for scripts/platform_roadmap_state.py: stale-cache detection, compute_state_dict shape
and error handling, the YAML loader, ratifiable_cds()/realized_but_pending_cds() CD feeds, the
live-roadmap gate-evaluation anchors, and compute_followon_state().

Migrated from the retired tests/test_platform_roadmap_state.py monolith (Decision 128
decompose-don't-raise / Decision 131 mirror convention). Shared fixture helpers live in
tests/fixtures/platform_roadmap_state.py -- never import from a sibling test_*.py module.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest
import yaml

from scripts.roadmap.platform_roadmap import (
    PlatformRoadmapState,
    RoadmapDocument,
    compute_followon_state,
    compute_state_dict,
    load,
)
from tests.fixtures.platform_roadmap_state import (
    _BASE_DOC,
    _LIVE_ROADMAP,
    _cd,
    _doc,
    _item,
    _state_from_doc,
    _write_fixture_yaml,
    live_state_dict,
)

# ---------------------------------------------------------------------------
# TestStaleCacheNote
# ---------------------------------------------------------------------------


class TestStaleCacheNote:
    def _write_yaml(self, items: list | None = None) -> Path:
        return _write_fixture_yaml(items or [_item("T0.1")])

    def test_stale_cache_note_present_when_yaml_newer(self) -> None:
        path = self._write_yaml()
        try:
            # A decision timestamp far in the past -- YAML mtime will be newer
            result = compute_state_dict(path, latest_decision_ts="2020-01-01T00:00:00+00:00")
            assert "stale_cache_note" in result, f"expected stale_cache_note; got keys: {list(result)}"
            assert "roadmap edits awaiting ratification" in result["stale_cache_note"]
        finally:
            path.unlink(missing_ok=True)

    def test_stale_cache_note_absent_when_decision_newer(self) -> None:
        path = self._write_yaml()
        try:
            # A decision timestamp far in the future -- YAML mtime will be older
            result = compute_state_dict(path, latest_decision_ts="2099-01-01T00:00:00+00:00")
            assert "stale_cache_note" not in result, f"unexpected stale_cache_note: {result.get('stale_cache_note')}"
        finally:
            path.unlink(missing_ok=True)

    def test_stale_cache_note_absent_when_ts_none(self) -> None:
        path = self._write_yaml()
        try:
            result = compute_state_dict(path, latest_decision_ts=None)
            assert "stale_cache_note" not in result
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TestComputeStateDict (error branch + shape)
# ---------------------------------------------------------------------------


class TestComputeStateDict:
    def test_error_on_missing_file(self) -> None:
        result = compute_state_dict(Path("/nonexistent/ROADMAP.yaml"))
        assert "error" in result
        assert result["next_eligible"] == []
        assert result["in_progress"] == []
        assert result["blocked"] == []
        assert result["strategic_pending"] == []
        assert result["active_tier"] is None

    def test_error_on_bad_yaml(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8")
        tmp.write(":: bad: : :\n")
        tmp.close()
        try:
            result = compute_state_dict(Path(tmp.name))
            assert "error" in result
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def test_shape_on_valid_yaml(self) -> None:
        path = _write_fixture_yaml([_item("T0.1"), _item("T0.2", depends_on=["T0.1"])])
        try:
            result = compute_state_dict(path)
            for key in ("next_eligible", "in_progress", "blocked", "strategic_pending", "active_tier"):
                assert key in result, f"missing key: {key}"
        finally:
            path.unlink(missing_ok=True)

    def test_item_shape_has_required_fields(self) -> None:
        path = _write_fixture_yaml([_item("T0.1")])
        try:
            result = compute_state_dict(path)
            assert result["next_eligible"]
            item = result["next_eligible"][0]
            for field in ("id", "tier", "name", "effort", "strategic"):
                assert field in item, f"missing field: {field}"
        finally:
            path.unlink(missing_ok=True)

    def test_blocked_item_has_blocked_on_field(self) -> None:
        path = _write_fixture_yaml(
            [
                _item("T0.dep"),
                _item("T0.blocked", depends_on=["T0.dep"]),
            ]
        )
        try:
            result = compute_state_dict(path)
            assert result["blocked"]
            blocked_item = result["blocked"][0]
            assert "blocked_on" in blocked_item
            assert blocked_item["blocked_on"] == ["T0.dep"]
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TestComputeStateDictDecisionTsEdgeCases -- closes coverage gaps in the
# latest_decision_ts branch of compute_state_dict: naive-datetime tzinfo
# backfill, and the outer except-guard for a wholly unparseable timestamp.
# Distinct from TestStaleCacheNote above, whose fixtures always pass an
# explicit UTC offset ("+00:00"), so decision_dt.tzinfo is never None there.
# ---------------------------------------------------------------------------


class TestComputeStateDictDecisionTsEdgeCases:
    def _write_yaml(self) -> Path:
        return _write_fixture_yaml([_item("T0.1")])

    def test_naive_decision_ts_backfilled_to_utc(self) -> None:
        path = self._write_yaml()
        try:
            # No UTC offset -- datetime.fromisoformat produces a naive datetime,
            # exercising the tzinfo-is-None backfill branch before comparison.
            result = compute_state_dict(path, latest_decision_ts="2020-01-01T00:00:00")
            assert "stale_cache_note" in result, f"expected stale_cache_note; got keys: {list(result)}"
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_decision_ts_format_silently_ignored(self) -> None:
        path = self._write_yaml()
        try:
            # Not parseable by datetime.fromisoformat at all -- caught by the
            # outer except-guard; function must not raise and must omit the note.
            result = compute_state_dict(path, latest_decision_ts="not-a-real-timestamp")
            assert "stale_cache_note" not in result
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TestLoad
# ---------------------------------------------------------------------------


class TestLoad:
    def test_loads_live_yaml(self) -> None:
        doc = load(_LIVE_ROADMAP)
        assert len(doc.tier_items) >= 30
        assert len(doc.candidate_decisions) >= 20

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load("/nonexistent/path/ROADMAP.yaml")

    def test_invalid_yaml_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False, encoding="utf-8") as f:
            f.write(":: not valid yaml: [[[")
            tmp = f.name
        try:
            with pytest.raises((yaml.YAMLError, Exception)):
                load(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TestRatifiableCds -- candidate-decision-ratification lane surfacing
# ---------------------------------------------------------------------------


class TestRatifiableCds:
    def test_defaults_to_none_and_excluded(self) -> None:
        doc = _doc(candidate_decisions=[_cd("CD.1")])
        result = _state_from_doc(doc).ratifiable_cds()
        assert result == []

    def test_pending_with_evidence_included(self) -> None:
        doc = _doc(candidate_decisions=[_cd("CD.6", realization_evidence="Realized 2026-05-28: ...")])
        result = _state_from_doc(doc).ratifiable_cds()
        assert len(result) == 1
        assert result[0]["id"] == "CD.6"
        assert result[0]["realization_evidence"] == "Realized 2026-05-28: ..."

    def test_ratified_with_evidence_excluded(self) -> None:
        doc = _doc(candidate_decisions=[_cd("CD.36", state="ratified", realization_evidence="already ratified")])
        result = _state_from_doc(doc).ratifiable_cds()
        assert result == []

    def test_pending_without_evidence_excluded(self) -> None:
        doc = _doc(candidate_decisions=[_cd("CD.29")])
        result = _state_from_doc(doc).ratifiable_cds()
        assert result == []

    def test_present_in_to_preflight_dict(self) -> None:
        doc = _doc(candidate_decisions=[_cd("CD.6", realization_evidence="Realized")])
        full = _state_from_doc(doc).to_preflight_dict()
        assert "ratifiable_cds" in full
        assert [c["id"] for c in full["ratifiable_cds"]] == ["CD.6"]


# ---------------------------------------------------------------------------
# TestRealizedButPendingCds -- close-audit-ulf-02: prose-'[Realized' corroboration signal,
# kept distinct from the deliberate realization_evidence-keyed ratifiable_cds()
# ---------------------------------------------------------------------------


class TestRealizedButPendingCds:
    def test_pending_with_realized_marker_and_no_evidence_surfaced(self) -> None:
        doc = _doc(
            candidate_decisions=[
                {**_cd("CD.2"), "detail": "Some prose. [Realized 2026-05-30: CC-web dev surface operational."}
            ]
        )
        result = _state_from_doc(doc).realized_but_pending_cds()
        assert len(result) == 1
        assert result[0]["id"] == "CD.2"
        assert result[0]["realized_hint"].startswith("[Realized")

    def test_pending_with_evidence_excluded_belongs_to_ratifiable(self) -> None:
        doc = _doc(
            candidate_decisions=[
                {
                    **_cd("CD.6", realization_evidence="Realized 2026-05-28: ..."),
                    "detail": "[Realized 2026-05-28: shipped.",
                }
            ]
        )
        result = _state_from_doc(doc).realized_but_pending_cds()
        assert result == []

    def test_pending_without_marker_excluded(self) -> None:
        doc = _doc(candidate_decisions=[{**_cd("CD.1"), "detail": "Plain detail, no marker."}])
        result = _state_from_doc(doc).realized_but_pending_cds()
        assert result == []

    def test_ratified_with_marker_excluded(self) -> None:
        doc = _doc(candidate_decisions=[{**_cd("CD.99", state="ratified"), "detail": "[Realized 2026-01-01: done."}])
        result = _state_from_doc(doc).realized_but_pending_cds()
        assert result == []

    def test_present_in_to_preflight_dict(self) -> None:
        doc = _doc(candidate_decisions=[{**_cd("CD.2"), "detail": "[Realized 2026-05-30: shipped."}])
        full = _state_from_doc(doc).to_preflight_dict()
        assert "realized_but_pending_cds" in full
        assert [c["id"] for c in full["realized_but_pending_cds"]] == ["CD.2"]


# ---------------------------------------------------------------------------
# TestLiveGateEvaluations -- T-1.20 live-YAML anchors
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _LIVE_ROADMAP.exists(), reason="live ROADMAP-PLATFORM.yaml not present")
class TestLiveGateEvaluations:
    def _result(self):  # type: ignore[return]
        return live_state_dict()

    def test_all_four_gates_have_verdict(self) -> None:
        gates = {g["id"]: g for g in self._result().get("gate_evaluations", [])}
        for gid in ("G.1", "G.8", "G.9", "G.10"):
            assert gid in gates, f"gate {gid} missing from gate_evaluations"
            assert "verdict" in gates[gid], f"gate {gid} missing 'verdict' key"
            assert gates[gid]["verdict"] in ("pass", "fail", "deferred")

    def test_g1_passes(self) -> None:
        # T-1.4 and T-1.5 are both complete -> G.1 must pass
        gates = {g["id"]: g for g in self._result().get("gate_evaluations", [])}
        g1 = gates.get("G.1")
        assert g1 is not None
        assert g1["verdict"] == "pass", f"G.1: T-1.4 and T-1.5 are complete so rule must pass: {g1}"

    def test_g8_defers_on_the_runtime_run_verdict(self) -> None:
        # T3.2 is complete -> the status conjunct holds and latest_run.verdict is a runtime path (rec-4689) -> deferred
        gates = {g["id"]: g for g in self._result().get("gate_evaluations", [])}
        g8 = gates.get("G.8")
        assert g8 is not None
        assert g8["verdict"] == "deferred" and "latest_run.verdict" in g8["reason"], f"G.8 must defer on the run verdict: {g8}"

    def test_g9_non_deferred(self) -> None:
        # T4.2 is not_started -> verdict is fail (not deferred)
        gates = {g["id"]: g for g in self._result().get("gate_evaluations", [])}
        g9 = gates.get("G.9")
        assert g9 is not None
        assert g9["verdict"] in ("pass", "fail"), f"G.9 verdict must not be deferred: {g9}"

    def test_g10_non_deferred(self) -> None:
        # T2.1/T2.2/T2.3 statuses are statically resolvable; grace_period_elapsed is computable
        gates = {g["id"]: g for g in self._result().get("gate_evaluations", [])}
        g10 = gates.get("G.10")
        assert g10 is not None
        assert g10["verdict"] in ("pass", "fail"), f"G.10 verdict must not be deferred: {g10}"

    def test_blocked_on_cd_non_empty(self) -> None:
        # The live roadmap always carries not_started eligible items gated by a pending CD
        # (e.g. the T-1 contract-wave items under pending CD.1/CD.25). Anchored on the
        # invariant rather than a specific id so this plan's own bookkeeping (which may
        # flip an anchor item out of eligible) does not make the regression test fragile.
        result = self._result()
        assert result.get("blocked_on_cd"), "expected at least one blocked-on-CD item on the live roadmap"

    def test_blocked_on_cd_references_only_pending_cds(self) -> None:
        # Core semantic invariant: blocked_on_cd never surfaces a ratified/non-pending CD.
        doc = load(_LIVE_ROADMAP)
        pending = {cd.id for cd in doc.candidate_decisions if cd.state == "pending"}
        for entry in self._result().get("blocked_on_cd", []):
            for cd_id in entry["blocking_cds"]:
                assert cd_id in pending, f"{entry['id']} blocked on non-pending CD {cd_id}"

    def test_blocked_on_cd_relationships_are_valid_types(self) -> None:
        valid = {"related", "gates", "decision_required_before"}
        for entry in self._result().get("blocked_on_cd", []):
            for cd_id, rel in entry["relationships"].items():
                assert rel in valid, f"{entry['id']} CD {cd_id} has invalid relationship {rel!r}"

    def test_blocked_on_cd_items_have_required_keys(self) -> None:
        result = self._result()
        for entry in result.get("blocked_on_cd", []):
            for key in ("id", "name", "blocking_cds", "relationships", "bootstrap_completion_exempt"):
                assert key in entry, f"blocked_on_cd entry {entry.get('id')} missing key '{key}'"
            assert isinstance(entry["blocking_cds"], list)
            assert isinstance(entry["relationships"], dict)


# ---------------------------------------------------------------------------
# TestComputeFollowonState -- T-1.23
# ---------------------------------------------------------------------------


def _install_safe_load_outcome_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch scripts.platform_roadmap_state's yaml.safe_load to record each call's outcome
    while still delegating to the real loader. Must re-raise rather than swallow -- a
    swallowing spy would record "raised" while safe_load appears to the caller to return
    None, which hits the isinstance-dict branch instead of the except branch."""
    import scripts.platform_roadmap_state as prs

    outcomes: list[str] = []
    real_safe_load = prs.yaml.safe_load

    def _spy(stream: object) -> object:
        try:
            loaded = real_safe_load(stream)
        except Exception:
            outcomes.append("raised")
            raise
        outcomes.append("returned-dict" if isinstance(loaded, dict) else "returned-non-dict")
        return loaded

    monkeypatch.setattr(prs.yaml, "safe_load", _spy)
    return outcomes


class TestComputeFollowonState:
    """compute_followon_state: in-flight plan vs no plan; live-items-only scoping."""

    def _make_doc(self, items: list[dict]) -> RoadmapDocument:
        d = copy.deepcopy(_BASE_DOC)
        d["tier_items"] = items
        return RoadmapDocument.model_validate(d)

    def _in_progress_item(self, item_id: str, criteria: list[dict]) -> dict:
        item = _item(item_id, status="in_progress")
        item["exit_criteria"] = criteria
        return item

    def test_no_plan_needs_followon(self, tmp_path: Path) -> None:
        doc = self._make_doc([self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}])])
        result = compute_followon_state(doc, tmp_path)
        assert result["A"]["open_criteria_count"] == 1
        assert result["A"]["all_plans_actioned"] is True
        assert result["A"]["needs_followon_plan"] is True

    def test_in_flight_plan_suppresses_followon(self, tmp_path: Path) -> None:
        doc = self._make_doc([self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}])])
        plan_data = {
            "schema_version": 1,
            "slug": "test-plan",
            "intent": "test",
            "plan_type": "IMPLEMENTATION",
            "verification_tier": "V1",
            "plan_path": "docs/plans/PLAN-test-plan.yaml",
            "phase": "T0",
            "scope": [{"file": "f.py", "action": "Modify", "purpose": "p"}],
            "acceptance_criteria": ["ac"],
            "verification_plan": [
                {"step": 1, "phase": "pre-deploy", "action": "a", "command": "echo x", "expected": "x", "fix_if": "f"}
            ],
            "execution_steps": ["step 1"],
            "closes_criteria": ["A:c1"],
        }
        plan_file = tmp_path / "PLAN-test-plan.yaml"
        plan_file.write_text(yaml.dump(plan_data))
        result = compute_followon_state(doc, tmp_path)
        assert result["A"]["open_criteria_count"] == 1
        assert result["A"]["all_plans_actioned"] is False
        assert result["A"]["needs_followon_plan"] is False

    def test_zero_open_criteria_no_followon_needed(self, tmp_path: Path) -> None:
        doc = self._make_doc(
            [
                self._in_progress_item(
                    "A",
                    [
                        {"id": "c1", "text": "x", "status": "met", "met_by": "some-plan"},
                        {"id": "c2", "text": "y", "status": "rehomed", "met_by": "B"},
                    ],
                ),
                _item("B"),
            ]
        )
        result = compute_followon_state(doc, tmp_path)
        assert result["A"]["open_criteria_count"] == 0
        assert result["A"]["needs_followon_plan"] is False

    def test_deferred_post_mvp_excluded(self, tmp_path: Path) -> None:
        deferred = _item("D", status="deferred_post_mvp")
        deferred["exit_criteria"] = [{"id": "c1", "text": "x", "status": "open"}]
        doc = self._make_doc([_item("A", status="not_started"), deferred])
        result = compute_followon_state(doc, tmp_path)
        assert "D" not in result, "deferred_post_mvp items must not appear in followon state"

    def test_not_started_excluded(self, tmp_path: Path) -> None:
        doc = self._make_doc([_item("A", status="not_started")])
        result = compute_followon_state(doc, tmp_path)
        assert "A" not in result, "not_started items must not appear in followon state"

    def test_malformed_plan_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        doc = self._make_doc([self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}])])
        # Must carry `closes_criteria` so the line-111 substring pre-filter doesn't
        # short-circuit before yaml.safe_load is reached, and still fail to parse so the
        # except-Exception branch this test exists to cover actually fires.
        (tmp_path / "PLAN-bad.yaml").write_text("closes_criteria: [T-1: {")
        outcomes = _install_safe_load_outcome_spy(monkeypatch)
        result = compute_followon_state(doc, tmp_path)
        assert outcomes == ["raised"], f"expected exactly one raising safe_load call; got: {outcomes}"
        assert result["A"]["needs_followon_plan"] is True

    def test_plans_dir_default_is_absolute_repo_anchored(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Default plans_dir resolves to repo-anchored absolute path, not CWD-relative (rec-2349)."""
        import inspect

        src = inspect.getsource(PlatformRoadmapState.to_preflight_dict)
        assert 'Path("docs/plans")' not in src, "Default plans_dir must not be CWD-relative string literal"

        # Behavioral: from a different CWD the default must still reach the real plans dir.
        doc = load(_LIVE_ROADMAP)
        state = PlatformRoadmapState(doc)

        real_plans_dir = _LIVE_ROADMAP.parent / "plans"
        baseline = state.to_preflight_dict(plans_dir=real_plans_dir)

        monkeypatch.chdir(tmp_path)
        default = state.to_preflight_dict()

        baseline_map = {i["id"]: i for i in baseline["in_progress"]}
        for item in default["in_progress"]:
            if item["id"] in baseline_map:
                assert item["needs_followon_plan"] == baseline_map[item["id"]]["needs_followon_plan"], (
                    f"{item['id']}: default plans_dir produced different needs_followon_plan than explicit absolute path"
                )

    def test_plan_closes_different_item_does_not_suppress(self, tmp_path: Path) -> None:
        doc = self._make_doc(
            [
                self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}]),
                self._in_progress_item("B", [{"id": "c1", "text": "y", "status": "open"}]),
            ]
        )
        plan_data = {
            "schema_version": 1,
            "slug": "only-b",
            "intent": "x",
            "plan_type": "IMPLEMENTATION",
            "verification_tier": "V1",
            "plan_path": "docs/plans/PLAN-only-b.yaml",
            "phase": "T0",
            "scope": [{"file": "f.py", "action": "Modify", "purpose": "p"}],
            "acceptance_criteria": ["ac"],
            "verification_plan": [
                {"step": 1, "phase": "pre-deploy", "action": "a", "command": "echo x", "expected": "x", "fix_if": "f"}
            ],
            "execution_steps": ["step 1"],
            "closes_criteria": ["B:c1"],
        }
        (tmp_path / "PLAN-only-b.yaml").write_text(yaml.dump(plan_data))
        result = compute_followon_state(doc, tmp_path)
        assert result["A"]["needs_followon_plan"] is True, "A's criterion not covered by the B plan"
        assert result["B"]["needs_followon_plan"] is False, "B's criterion is covered"


# ---------------------------------------------------------------------------
# TestComputeFollowonStateMalformedPlanShapes -- closes coverage gaps in the
# per-plan-file skip branches of compute_followon_state. Distinct from
# test_malformed_plan_skipped above (which exercises the OUTER yaml.safe_load
# exception path, e.g. genuine YAML syntax errors): these exercise
# successfully-PARSED-but-wrong-shaped YAML, which the outer except never sees.
# ---------------------------------------------------------------------------


class TestComputeFollowonStateMalformedPlanShapes:
    def _make_doc(self, items: list[dict]) -> RoadmapDocument:
        d = copy.deepcopy(_BASE_DOC)
        d["tier_items"] = items
        return RoadmapDocument.model_validate(d)

    def _in_progress_item(self, item_id: str, criteria: list[dict]) -> dict:
        item = _item(item_id, status="in_progress")
        item["exit_criteria"] = criteria
        return item

    def test_plan_yaml_top_level_not_dict_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Valid YAML that parses to a list (not a dict) at the top level. Its first entry is
        # the literal `closes_criteria` so the line-111 substring pre-filter doesn't
        # short-circuit before yaml.safe_load is reached.
        doc = self._make_doc([self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}])])
        (tmp_path / "PLAN-list-toplevel.yaml").write_text("- closes_criteria\n- b\n")
        outcomes = _install_safe_load_outcome_spy(monkeypatch)
        result = compute_followon_state(doc, tmp_path)
        assert outcomes == ["returned-non-dict"], f"expected one non-dict-returning safe_load call; got: {outcomes}"
        assert result["A"]["needs_followon_plan"] is True

    def test_closes_criteria_not_a_list_skipped(self, tmp_path: Path) -> None:
        # closes_criteria present but not list-shaped (a bare scalar string).
        doc = self._make_doc([self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}])])
        (tmp_path / "PLAN-bad-closes.yaml").write_text("closes_criteria: not-a-list-value\n")
        result = compute_followon_state(doc, tmp_path)
        assert result["A"]["needs_followon_plan"] is True

    def test_closes_criteria_entry_malformed_skipped(self, tmp_path: Path) -> None:
        # One entry is a non-str (int); the other is a str missing the
        # required ':' item_id/crit_id separator. Both hit the same continue.
        doc = self._make_doc([self._in_progress_item("A", [{"id": "c1", "text": "x", "status": "open"}])])
        (tmp_path / "PLAN-bad-entries.yaml").write_text("closes_criteria:\n  - 123\n  - malformed-ref-without-colon\n")
        result = compute_followon_state(doc, tmp_path)
        assert result["A"]["needs_followon_plan"] is True


class TestKeylessPlanSkipsYamlParse:
    """The scan reads only `closes_criteria`, so a plan whose text never mentions the key is
    skipped without a YAML parse.

    This is a load-bearing performance property, not an incidental one: the scan runs on every
    compute_state_dict() call, and full-parsing every plan in docs/plans/ to read one optional
    key dominated that function's runtime (~350 documents parsed per call). These tests pin the
    mechanism so the N+1 cannot silently return, and pin the complement so the skip can never
    swallow a plan that does carry the key.
    """

    def _doc_with_one_open_criterion(self) -> RoadmapDocument:
        d = copy.deepcopy(_BASE_DOC)
        item = _item("A", status="in_progress")
        item["exit_criteria"] = [{"id": "c1", "text": "x", "status": "open"}]
        d["tier_items"] = [item]
        return RoadmapDocument.model_validate(d)

    def test_plan_without_the_key_is_never_yaml_parsed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import scripts.platform_roadmap_state as prs

        (tmp_path / "PLAN-unrelated.yaml").write_text("slug: unrelated\nintent: no criteria refs here\n")
        parsed: list[str] = []
        real_safe_load = prs.yaml.safe_load

        def _spy(stream: object) -> object:
            parsed.append(str(stream)[:40])
            return real_safe_load(stream)

        monkeypatch.setattr(prs.yaml, "safe_load", _spy)
        compute_followon_state(self._doc_with_one_open_criterion(), tmp_path)
        assert parsed == [], f"keyless plan must not be YAML-parsed; parsed: {parsed}"

    def test_plan_carrying_the_key_is_still_parsed_and_honoured(self, tmp_path: Path) -> None:
        """Complement of the skip: the optimisation must not over-skip. A plan that does declare
        closes_criteria still suppresses the follow-on, alongside a keyless sibling."""
        (tmp_path / "PLAN-unrelated.yaml").write_text("slug: unrelated\nintent: no criteria refs here\n")
        (tmp_path / "PLAN-covers.yaml").write_text('closes_criteria: ["A:c1"]\n')
        result = compute_followon_state(self._doc_with_one_open_criterion(), tmp_path)
        assert result["A"]["all_plans_actioned"] is False
        assert result["A"]["needs_followon_plan"] is False

    def test_quoted_key_form_is_not_skipped(self, tmp_path: Path) -> None:
        """The skip keys off the raw substring, so a quoted mapping key -- which still contains
        it -- must be parsed and honoured like the bare form."""
        (tmp_path / "PLAN-quoted.yaml").write_text('"closes_criteria": ["A:c1"]\n')
        result = compute_followon_state(self._doc_with_one_open_criterion(), tmp_path)
        assert result["A"]["needs_followon_plan"] is False


# ---------------------------------------------------------------------------
# TestFollowonNoInProgressGuard -- rec-4007: a zero-in_progress roadmap never globs plans_dir.
# ---------------------------------------------------------------------------


class _RefusingPlansDir:
    """A plans_dir stand-in whose glob() always raises -- a stub object rather than a Path.glob
    monkeypatch, so pytest internals never see a patched Path."""

    def is_dir(self) -> bool:
        return True

    def glob(self, pattern):
        raise AssertionError("plans_dir globbed ... with zero in_progress items")


class TestFollowonNoInProgressGuard:
    def _make_doc(self, items: list[dict]) -> RoadmapDocument:
        d = copy.deepcopy(_BASE_DOC)
        d["tier_items"] = items
        return RoadmapDocument.model_validate(d)

    def test_zero_in_progress_never_globs_plans_dir(self) -> None:
        doc = self._make_doc([_item("A", status="not_started"), _item("B", status="complete")])
        assert compute_followon_state(doc, _RefusingPlansDir()) == {}

    def test_one_in_progress_still_globs_plans_dir(self) -> None:
        doc = self._make_doc([_item("A", status="in_progress")])
        with pytest.raises(AssertionError, match="plans_dir globbed"):
            compute_followon_state(doc, _RefusingPlansDir())
