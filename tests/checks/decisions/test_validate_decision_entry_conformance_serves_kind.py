"""serves/kind/closed-key/number rules wired into validate_decision_entry_conformance
(PLAN-decision-serves-kind-grammar, Decision 243 clause 8). Synthetic contracts in tmp_path; baseline injected."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.decisions.validate_decision_entry_conformance import validate_decision_entry_conformance

REPO = Path(__file__).resolve().parents[3]

_ENTRY_CONTRACT = """\
required_markers: [Status, Date, Decision]
metadata_envelope:
  required_fields: [number, significance, kind]
  optional_fields: [status, decided_date, amends, supersedes, serves]
  closed_key_set: A new-in-diff envelope may carry only required_fields and optional_fields keys.
  number_assignment: Assigned at implementation.
  field_shapes:
    serves:
      resolves_against: docs/contracts/north-star.yaml
significance:
  routing:
    numbered_decision: A durable commitment.
"""
_KIND_CONTRACT = "values: [direction, mechanism]\ntest: Would undoing it change a goal?\n"


def _principle(i: int) -> dict:
    return {
        "id": f"NS.{i}",
        "kind": "north_star",
        "principle": "p",
        "detail": "Why: d",
        "failure_signal": "f",
        "binds": "repository",
        "carried_by": [1],
    }


def _north_star(ids: tuple[int, ...] = (1, 2), carried_by: list[int] | None = None) -> str:
    principles = [_principle(i) | ({"carried_by": carried_by} if carried_by else {}) for i in ids]
    doc = {
        "contract": {"id": "north-star"},
        "amendment_log": [],
        "semantics": {},
        "mission": {
            "kind": "north_star",
            "statement": "s",
            "detail": "d",
            "failure_signal": "f",
            "binds": "repository",
            "carried_by": [1],
        },
        "principles": principles,
        "partial_order": {"rule": "r", "groups": [["NS.1"]]},
    }
    return yaml.safe_dump(doc)


def _entry(number: int, extra: str = "", kind: str | None = "direction", justification: str = "j") -> str:
    lines = [f"number: {number}", "significance:", "  value: numbered_decision", "  justification: r"]
    if kind:
        lines += ["kind:", f"  value: {kind}", f"  justification: {justification!r}"]
    body = "\n".join(lines) + ("\n" + extra if extra else "")
    tail = "\n\n**Status:** Decided\n**Date:** 2026-11-03\n**Decision:** x\n---\n"
    return f"## Decision {number}: Title\n\n```yaml\n{body}\n```{tail}"


def _setup(
    root: Path,
    entry_contract: str = _ENTRY_CONTRACT,
    kind: bool = True,
    ns: str | None = None,
    md: str = "",
    archive: str = "",
) -> None:
    contracts = root / "docs" / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    (contracts / "decision-entry.yaml").write_text(entry_contract, encoding="utf-8")
    if kind:
        (contracts / "decision-kind.yaml").write_text(_KIND_CONTRACT, encoding="utf-8")
    (contracts / "north-star.yaml").write_text(ns if ns is not None else _north_star(), encoding="utf-8")
    (root / "docs" / "DECISIONS.md").write_text(md, encoding="utf-8")
    (root / "docs" / "DECISIONS_ARCHIVE.md").write_text(archive, encoding="utf-8")


def _run(root: Path, baseline: set[int] | None = None, capsys=None) -> tuple[list[str], str]:
    failed: list[str] = []
    validate_decision_entry_conformance(failed, root=root, baseline_reader=lambda r: baseline if baseline is not None else {1})
    return failed, capsys.readouterr().out if capsys else ""


def _new(tmp_path: Path, entry: str, capsys, **kw) -> tuple[list[str], str]:
    _setup(tmp_path, md=_entry(1) + entry, **kw)
    return _run(tmp_path, capsys=capsys)


class TestFailRules:
    def test_conforming_new_entry_passes(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2, "serves: [NS.1]"), capsys)
        assert failed == [] and "PASS" in out

    def test_missing_kind_fails_naming_the_field(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2, kind=None), capsys)
        assert failed and "kind element is missing" in out

    def test_kind_value_outside_the_contract_fails(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2, kind="north_star"), capsys)
        assert failed and "north_star" in out

    def test_empty_justification_fails(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2, justification=""), capsys)
        assert failed and "kind.justification" in out

    def test_bad_duplicate_and_unresolved_serves_fail(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2, "serves: [NS1]"), capsys)
        assert failed and "match" in out
        failed, out = _new(tmp_path, _entry(2, "serves: [NS.1, NS.1]"), capsys)
        assert failed and "duplicate" in out
        failed, out = _new(tmp_path, _entry(2, "serves: [NS.9]"), capsys)
        assert failed and "NS.9" in out

    def test_unknown_key_fails(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2, "bogus: 1"), capsys)
        assert failed and "bogus" in out

    def test_number_not_above_baseline_max_fails(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1) + _entry(3) + _entry(2))
        failed, out = _run(tmp_path, baseline={1, 3}, capsys=capsys)
        assert failed and "not above the baseline maximum 3" in out

    def test_leftover_placeholder_heading_fails_with_new_entries(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, "## Decision {{N}}: Draft\n\nbody\n", capsys)
        assert failed and "unfilled placeholder heading" in out

    def test_leftover_placeholder_heading_fails_on_the_no_new_entries_path(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1) + "## Decision {{N}}: Draft\n\nbody\n")
        failed, out = _run(tmp_path, capsys=capsys)
        assert failed and "unfilled placeholder heading" in out

    def test_declared_kind_contract_missing_fails_closed(self, tmp_path: Path, capsys) -> None:
        failed, out = _new(tmp_path, _entry(2), capsys, kind=False)
        assert failed and "decision-kind.yaml" in failed[0]

    def test_declared_north_star_missing_fails_closed_when_serves_is_used(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1) + _entry(2, "serves: [NS.1]"))
        (tmp_path / "docs/contracts/north-star.yaml").unlink()
        failed, _ = _run(tmp_path, capsys=capsys)
        assert failed and "north-star.yaml" in failed[0]


class TestWarnRules:
    def test_dangling_baseline_serves_warns_without_failing(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1, "serves: [NS.9]"))
        failed, out = _run(tmp_path, capsys=capsys)
        assert failed == [] and "  WARN:" in out and "retired" in out

    def test_mechanism_in_carried_by_warns_without_failing(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1, kind="mechanism"))
        failed, out = _run(tmp_path, capsys=capsys)
        assert failed == [] and "  WARN:" in out and "carried_by" in out


class TestPassesAndDeclarations:
    def test_adding_a_value_to_the_kind_contract_admits_it_with_no_code_change(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1) + _entry(2, kind="policy"))
        (tmp_path / "docs/contracts/decision-kind.yaml").write_text(
            "values: [direction, mechanism, policy]\n", encoding="utf-8"
        )
        failed, _ = _run(tmp_path, capsys=capsys)
        assert failed == []

    def test_a_contract_declaring_none_of_the_new_rules_leaves_an_old_style_entry_passing(
        self, tmp_path: Path, capsys
    ) -> None:
        old = (
            "required_markers: [Status, Date, Decision]\nmetadata_envelope:\n  required_fields: [number, significance]\n"
            "significance:\n  routing:\n    numbered_decision: x\n"
        )
        failed, _ = _new(tmp_path, _entry(2, kind=None) + "", capsys, entry_contract=old, kind=False)
        assert failed == []


class TestRealContract:
    def _copy(self, tmp_path: Path, md: str) -> None:
        contracts = tmp_path / "docs" / "contracts"
        contracts.mkdir(parents=True, exist_ok=True)
        for name in ("decision-entry.yaml", "decision-kind.yaml"):
            shutil.copy(REPO / "docs/contracts" / name, contracts / name)
        (contracts / "north-star.yaml").write_text(_north_star(), encoding="utf-8")
        (tmp_path / "docs" / "DECISIONS.md").write_text(md, encoding="utf-8")
        (tmp_path / "docs" / "DECISIONS_ARCHIVE.md").write_text("", encoding="utf-8")

    def test_committed_contracts_reject_a_new_entry_without_kind_and_accept_a_conforming_one(
        self, tmp_path: Path, capsys
    ) -> None:
        self._copy(tmp_path, _entry(1) + _entry(2, kind=None))
        failed, out = _run(tmp_path, capsys=capsys)
        assert failed and "kind element is missing" in out
        self._copy(tmp_path, _entry(1) + _entry(2, "serves: [NS.1]"))
        failed, _ = _run(tmp_path, capsys=capsys)
        assert failed == []


class TestRec3260Unit:
    def test_the_no_new_entries_path_records_decision_entries_checked(self, tmp_path: Path, monkeypatch) -> None:
        _setup(tmp_path, md=_entry(1))
        seen: list[str] = []
        monkeypatch.setattr(registry, "examined", lambda count, unit=None: seen.append(unit))
        _run(tmp_path)
        assert seen == ["decision_entries_checked"]


class TestLoaderFailures:
    def test_an_unreadable_or_unparseable_entry_contract_declares_no_new_rules(self, tmp_path: Path) -> None:
        from scripts.checks.decisions.validate_decision_entry_conformance import _load_grammar_config

        assert _load_grammar_config(tmp_path) == {}
        (tmp_path / "docs/contracts").mkdir(parents=True)
        (tmp_path / "docs/contracts/decision-entry.yaml").write_text("a: [", encoding="utf-8")
        assert _load_grammar_config(tmp_path) == {}

    def test_a_kind_contract_without_values_fails_closed(self, tmp_path: Path, capsys) -> None:
        _setup(tmp_path, md=_entry(1) + _entry(2))
        (tmp_path / "docs/contracts/decision-kind.yaml").write_text("values: []\n", encoding="utf-8")
        failed, _ = _run(tmp_path, capsys=capsys)
        assert failed and "no values list" in failed[0]
