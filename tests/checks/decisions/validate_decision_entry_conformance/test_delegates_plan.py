"""The conformance check's delegates and plan rules end to end on tmp roots (Decision 273 clause 1).
test_entry_rules.py is at 500 SLOC, so these live here."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.decisions.validate_decision_entry_conformance import validate_decision_entry_conformance

_CONTRACT = {
    "required_markers": ["Status", "Date", "Decision"],
    "metadata_envelope": {
        "required_fields": ["number", "significance", "kind"],
        "optional_fields": ["delegates", "plan"],
        "closed_key_set": "closed",
        "field_shapes": {"delegates": {"change_rules": ["contract_amendment", "operator_approval"]}},
    },
    "significance": {"routing": {"numbered_decision": "A commitment."}},
}
_KEY = "config/knob.yaml#sec.cap"
_GOOD = f"delegates:\n  - key: {_KEY}\n    change_rule: contract_amendment\n    bounds: {{min: 1, max: 9}}\n"


def _entry(extra: str = "") -> str:
    return (
        "## Decision 9: Entry (Decided)\n\n```yaml\nnumber: 9\nsignificance:\n  value: numbered_decision\n  justification: j\n"
        f"kind:\n  value: direction\n  justification: j\n{extra}```\n\n**Status:** Decided\n**Date:** 2026-01-01\n\n"
        "**Decision:** x\n\n---\n"
    )


def _root(tmp_path: Path, extra: str, knob: str | None = "sec:\n  cap: 2\n", plan: bool = False) -> Path:
    files = {
        "docs/contracts/decision-entry.yaml": yaml.safe_dump(_CONTRACT),
        "docs/contracts/decision-kind.yaml": "values: [direction, mechanism]\n",
        "docs/DECISIONS.md": _entry(extra),
        "docs/DECISIONS_ARCHIVE.md": "",
    }
    if knob is not None:
        files["config/knob.yaml"] = knob
    if plan:
        files["docs/plans/PLAN-demo.yaml"] = "slug: demo\n"
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _run(root: Path) -> tuple[list[str], str]:
    failed: list[str] = []
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        validate_decision_entry_conformance(failed, root=root, baseline_reader=lambda r: set())
    return failed, buffer.getvalue()


class TestDelegatesAndPlan:
    def test_a_well_formed_delegates_passes(self, tmp_path: Path) -> None:
        assert _run(_root(tmp_path, _GOOD))[0] == []

    @pytest.mark.parametrize(
        ("extra", "knob", "needle"),
        [
            ("delegates: []\n", "sec:\n  cap: 2\n", "non-empty list"),
            (_GOOD.replace("contract_amendment", "vibes"), "sec:\n  cap: 2\n", "change_rule"),
            (_GOOD, None, "does not resolve"),
            (_GOOD, "sec: {}\n", "does not resolve"),
            ("plan: Not A Slug\n", "sec:\n  cap: 2\n", "slug"),
        ],
    )
    def test_a_malformed_or_unresolvable_field_fails(self, tmp_path: Path, extra: str, knob: str | None, needle: str) -> None:
        failed, out = _run(_root(tmp_path, extra, knob))
        assert failed and needle in out

    def test_an_unknown_plan_slug_warns_without_failing(self, tmp_path: Path) -> None:
        failed, out = _run(_root(tmp_path, "plan: demo\n"))
        assert failed == [] and "WARN: Decision 9 plan 'demo' has no docs/plans/PLAN-demo.yaml" in out

    def test_a_known_plan_slug_is_clean(self, tmp_path: Path) -> None:
        failed, out = _run(_root(tmp_path, "plan: demo\n", plan=True))
        assert failed == [] and "WARN" not in out

    def test_an_older_contract_without_change_rules_ignores_the_field(self, tmp_path: Path) -> None:
        root = _root(tmp_path, _GOOD.replace(_KEY, "bad key"))
        contract = dict(_CONTRACT, metadata_envelope=dict(_CONTRACT["metadata_envelope"], field_shapes={}))
        (root / "docs/contracts/decision-entry.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
        assert _run(root)[0] == []


class TestCompactionMessagesNameTheNewContract:
    def test_a_malformed_stub_points_at_decision_compaction_yaml(self, tmp_path: Path) -> None:
        root = _root(tmp_path, "")
        stub = "## Decision 3: Old (Decided)\n\n**Status:** Superseded\n\n---\n"
        (root / "docs/DECISIONS.md").write_text(stub + _entry(), encoding="utf-8")
        failed: list[str] = []
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            validate_decision_entry_conformance(failed, root=root, baseline_reader=lambda r: {3})
        assert failed and "docs/contracts/decision-compaction.yaml compaction.stub_grammar" in buffer.getvalue()
        assert "decision-entry.yaml compaction.stub_grammar" not in buffer.getvalue()
