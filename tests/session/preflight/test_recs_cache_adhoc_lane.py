"""Tests for the /orient ad-hoc lane (audit PDB-01, remedies B1-R4 + B4-O1):
scripts.preflight.recs_cache's `_landed_followon_rec_ids`, `_derive_followon_recs` and
`_derive_open_critical_recs`, the report keys `main()` writes from them, and the render
contract those keys are surfaced under in .claude/commands/orient.md, .claude/skills/orient/
SKILL.md and docs/contracts/exit-criteria-ledger.yaml.

A NEW, boto3-free sibling of tests/session/preflight/test_recs_cache.py -- that module carries a
module-level pytest skip-if-boto3-absent guard at its own :14, which makes every class in it
INVISIBLE to pr-validate's fast tier (boto3 ships only via requirements.txt, the post-merge
main-validate job; pr-validate installs requirements-fast + requirements-dev, neither of which
carries it). This module carries NO such module-level skip guard -- both `scripts.preflight.recs_cache`
and `tests.fixtures.session_preflight_module` import cleanly with boto3 blocked (VP step 16 pins
this; its grep is against the literal pytest helper name, so this docstring never spells it out).
Precedent: tests/session/preflight/test_ci_rca_gauges_abstention_label.py, split out of a
boto3-gated module for the identical reason.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.preflight import recs_cache
from scripts.preflight.recs_signals_source import RecsSignalInputs
from tests.fixtures.session_preflight_module import preflight as _preflight

ROOT = Path(__file__).resolve().parents[3]


def _write_plan(
    plans_dir: Path,
    slug: str,
    *,
    implementation_declared: bool | None = None,
    followon_recs: list[str] | None = None,
) -> Path:
    """Write a minimal PLAN-<slug>.yaml fixture under plans_dir and return its path."""
    lines = [f"slug: {slug}", "intent: fixture plan"]
    if implementation_declared is not None:
        lines.append(f"implementation_declared: {'true' if implementation_declared else 'false'}")
    if followon_recs is not None:
        if followon_recs:
            lines.append("followon_recs:")
            lines.extend(f"  - {rec_id}" for rec_id in followon_recs)
        else:
            lines.append("followon_recs: []")
    path = plans_dir / f"PLAN-{slug}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestAdHocLaneDerivations:
    """Pure derivations over already-pulled rows -- zero reader call (Decision 88 invariant ii)."""

    def test_readiness_gate_discriminates_landed_from_unlanded_parent(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "landed-parent", implementation_declared=True, followon_recs=["rec-1001"])
        _write_plan(tmp_path, "unlanded-parent", implementation_declared=False, followon_recs=["rec-1002"])
        landed = recs_cache._landed_followon_rec_ids(tmp_path)
        assert landed == {"rec-1001": "landed-parent"}

    def test_closed_rec_and_dangling_id_are_absent(self, tmp_path: Path) -> None:
        _write_plan(
            tmp_path,
            "landed-parent",
            implementation_declared=True,
            followon_recs=["rec-2001", "rec-2002"],
        )
        rows = [{"id": "rec-2001", "status": "closed", "title": "already closed"}]
        # rec-2002 is named by the landed plan but has no matching row at all (dangling id).
        result = recs_cache._derive_followon_recs(rows, plans_dir=tmp_path)
        assert result == []

    def test_supplied_landed_map_skips_the_plan_walk(self, tmp_path: Path) -> None:
        rows = [{"id": "rec-7", "status": "open", "title": "t", "created_timestamp": "2026-01-01T00:00:00Z"}]
        with patch.object(recs_cache, "_landed_followon_rec_ids", side_effect=AssertionError("plan walk repeated")):
            result = recs_cache._derive_followon_recs(rows, landed={"rec-7": "parent"})
        assert [r["parent_plan"] for r in result] == ["parent"]

    def test_plan_never_naming_the_key_is_never_parsed(self, tmp_path: Path) -> None:
        """The T-1.23 substring gate skips a plan file before yaml.safe_load ever runs on it."""
        unrelated = tmp_path / "PLAN-unrelated.yaml"
        unrelated.write_text("slug: unrelated\nintent: no followon key here at all\n", encoding="utf-8")

        def _guarded_safe_load(_stream: object) -> None:
            raise AssertionError("substring gate did not skip a plan that never names followon_recs")

        with patch("scripts.preflight.recs_cache.yaml.safe_load", side_effect=_guarded_safe_load):
            result = recs_cache._landed_followon_rec_ids(tmp_path)
        assert result == {}

    def test_both_derivations_never_call_the_reader(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "landed-parent", implementation_declared=True, followon_recs=["rec-3001"])
        rows = [
            {
                "id": "rec-3001",
                "status": "open",
                "title": "ready follow-on",
                "priority": "High",
                "source": "implement-session",
                "created_timestamp": "2026-01-01T00:00:00Z",
            }
        ]
        with patch(
            "scripts.preflight._common._make_reader",
            side_effect=AssertionError("a derivation reached the reader"),
        ):
            followon = recs_cache._derive_followon_recs(rows, plans_dir=tmp_path)
            critical = recs_cache._derive_open_critical_recs(rows)
        assert followon == [{"id": "rec-3001", "title": "ready follow-on", "parent_plan": "landed-parent"}]
        assert critical == []

    def test_critical_filter_excludes_exact_ci_rca_but_surfaces_dispute(self) -> None:
        """The discriminator: the EXACT predicate `source != 'ci_rca'` surfaces an open Critical
        `ci_rca_evidence_dispute` row -- the rejected `startswith('ci_rca')` predicate would drop
        it too, leaving it with no lane at all (rec-3054's own complaint)."""
        rows = [
            {"id": "rec-a", "status": "open", "priority": "Critical", "source": "ci_rca", "title": "excluded exact"},
            {
                "id": "rec-b",
                "status": "open",
                "priority": "Critical",
                "source": "ci_rca_evidence_dispute",
                "title": "surfaced dispute",
            },
            {"id": "rec-c", "status": "open", "priority": "High", "source": "implement-session", "title": "not critical"},
            {"id": "rec-d", "status": "closed", "priority": "Critical", "source": "implement-session", "title": "closed"},
            {"id": "rec-e", "status": "open", "priority": "Critical", "source": "implement-session", "title": "ordinary"},
        ]
        ids = {r["id"] for r in recs_cache._derive_open_critical_recs(rows)}
        assert ids == {"rec-b", "rec-e"}

    def test_ordering_oldest_first_unparseable_last_ties_by_id(self) -> None:
        base = {"status": "open", "priority": "Critical", "source": "x"}
        rows = [
            {**base, "id": "rec-z", "created_timestamp": "not-a-date"},
            {**base, "id": "rec-b", "created_timestamp": "2026-02-01T00:00:00Z"},
            {**base, "id": "rec-a2", "created_timestamp": "2026-01-01T00:00:00Z"},
            {**base, "id": "rec-a1", "created_timestamp": "2026-01-01T00:00:00Z"},
        ]
        ids = [r["id"] for r in recs_cache._derive_open_critical_recs(rows)]
        assert ids == ["rec-a1", "rec-a2", "rec-b", "rec-z"]


def _base_main_patches() -> list:
    """Fresh, unstarted patch objects for a main() call -- entered via ExitStack per test
    (a `with (*tuple, ...)` does not unpack; it builds a tuple literal instead)."""
    return [
        patch("scripts.preflight.env_git.check_venv", return_value=True),
        patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
        patch(
            "scripts.preflight.env_git.check_main_freshness",
            return_value={
                "status": "ok",
                "fetched_at": "2026-05-24T00:00:00+00:00",
                "commits_behind": 0,
                "commits_ahead": 0,
                "main_files_changed_since_branch": [],
            },
        ),
        patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
        patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
        patch(
            "scripts.preflight.session_history.read_session_history",
            return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
        ),
        patch(
            "scripts.preflight.context_docs.read_context_files",
            return_value={
                "roadmap_phase": "Phase 1.5",
                "open_decisions_count": 0,
                "strategic_review_due": False,
                "recommendations_count": 0,
            },
        ),
        patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
        patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None),
    ]


class TestPlanWalkStub:
    def test_main_level_tests_see_a_stubbed_plan_walk(self) -> None:
        walk = recs_cache._landed_followon_rec_ids
        assert isinstance(walk, MagicMock)
        assert walk() == {}


class TestAdHocLaneReportWiring:
    """main() writes both keys at report top level; a degraded read yields null for both."""

    def test_main_writes_both_keys_and_surfaces_the_injected_critical_row(self, tmp_path: Path) -> None:
        preflight_report = tmp_path / ".preflight-report.json"
        injected = {
            "id": "rec-9001",
            "status": "open",
            "priority": "Critical",
            "source": "implement-session",
            "title": "injected critical",
            "created_timestamp": "2026-01-01T00:00:00Z",
        }
        with contextlib.ExitStack() as stack:
            for cm in _base_main_patches():
                stack.enter_context(cm)
            stack.enter_context(
                patch(
                    "session_preflight.fetch_recs_signal_inputs",
                    return_value=RecsSignalInputs(
                        "ok", {"open_count": 1, "aging_count": 0, "non_automatable_count": 0}, [injected], None, {}
                    ),
                )
            )
            stack.enter_context(patch("session_preflight.PREFLIGHT_REPORT", preflight_report))
            stack.enter_context(patch("builtins.print"))
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert "followon_recs" in data
        assert "open_critical_recs" in data
        assert [r["id"] for r in data["open_critical_recs"]] == ["rec-9001"]
        assert data["followon_recs"] == []

    def test_degraded_recommendation_pull_yields_null_never_empty_or_missing(self, tmp_path: Path) -> None:
        preflight_report = tmp_path / ".preflight-report.json"
        with contextlib.ExitStack() as stack:
            for cm in _base_main_patches():
                stack.enter_context(cm)
            stack.enter_context(
                patch("session_preflight.fetch_recs_signal_inputs", return_value=RecsSignalInputs("unavailable", error="down"))
            )
            stack.enter_context(patch("session_preflight.PREFLIGHT_REPORT", preflight_report))
            stack.enter_context(patch("builtins.print"))
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert "followon_recs" in data and data["followon_recs"] is None
        assert "open_critical_recs" in data and data["open_critical_recs"] is None


class TestOrientAdHocLaneContract:
    """The orient command and skill render the keys; the ledger contract documents the rule."""

    _COMMAND = ROOT / ".claude" / "commands" / "orient.md"
    _SKILL = ROOT / ".claude" / "skills" / "orient" / "SKILL.md"
    _LEDGER = ROOT / "docs" / "contracts" / "exit-criteria-ledger.yaml"

    def test_command_carries_the_capped_lane_rule(self) -> None:
        text = self._COMMAND.read_text(encoding="utf-8")
        step2 = text.split("## Step 2: Load Inputs", 1)[1].split("## Step 3", 1)[0]
        for key in ("followon_recs", "open_critical_recs", "priority_queue", "recs_read_status"):
            assert key in step2, f"Step 2 input list is missing {key!r}"

        step3 = text.split("## Step 3", 1)[1]
        assert "cap" in step3.lower() and " 3" in step3, "lane spec is missing the cap-of-3 rule"
        assert "displace" in step3.lower(), "lane spec is missing the 3+ Critical displacement clause"
        assert "DEGRADED" in step3 and "recs_read_status" in step3, "lane spec is missing the DEGRADED branch"

    def test_skill_points_at_the_lane_and_states_the_rule_once(self) -> None:
        text = self._SKILL.read_text(encoding="utf-8")
        assert "critical recs" in text.lower()
        assert "non-blocking" in text.lower()
        marker = 'Preserve the "do NOT surface'
        assert text.count(marker) == 1, f"expected the restatement marker exactly once, found {text.count(marker)}"

    def test_ledger_contract_documents_followon_readiness(self) -> None:
        from scripts.contracts import load_contract, resolve_refs  # noqa: PLC0415

        doc = load_contract(self._LEDGER)
        resolve_refs(doc, self._LEDGER.parent)
        assert doc.fields is not None, "contract carries no fields mapping at all"
        field = doc.fields.get("followon_recs_readiness")
        assert field is not None, "followon_recs_readiness field is missing from the ledger contract"
        assert field.derivation, "followon_recs_readiness carries no derivation block"
        derivation_text = str(field.derivation)
        for token in ("implementation_declared", "followon_recs", "open"):
            assert token in derivation_text, f"derivation block is missing token {token!r}"
