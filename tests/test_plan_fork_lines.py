"""Engine tests for scripts/roadmap/plan_fork_lines.py (rec-3530)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.roadmap import plan_fork_lines as pfl

ROOT = Path(__file__).resolve().parents[1]

GOOD = (
    "fork: which store | chosen: the index | class: consistency-only | precedent: Decision 10 | "
    "precedent_kind: decision | reversible: yes | persisted: the plan"
)
ASKED = (
    "fork: which store | chosen: the index | class: asked | precedent: none | precedent_kind: none | "
    "reversible: no | persisted: the plan"
)


def _line(**over: str) -> str:
    base = {
        "fork": "which store",
        "chosen": "the index",
        "class": "consistency-only",
        "precedent": "Decision 10",
        "precedent_kind": "decision",
        "reversible": "yes",
        "persisted": "the plan",
    }
    base.update(over)
    return " | ".join(f"{k}: {v}" for k, v in base.items())


def _fails(text: str, read=lambda rel: None, plan_path: str = "docs/plans/PLAN-self.yaml") -> list[pfl.Finding]:
    return [f for f in pfl.judge_line(text, plan_path=plan_path, read=read) if f.severity == "fail"]


class TestGrammar:
    def test_conforming_lines_parse(self) -> None:
        values, findings = pfl.judge_grammar(GOOD)
        assert findings == [] and values is not None and values["class"] == "consistency-only"
        assert pfl.judge_grammar(ASKED)[1] == []

    def test_pipe_inside_prose_is_tolerated(self) -> None:
        assert pfl.judge_grammar(_line(fork="a | b choice", chosen="x | y"))[1] == []

    @pytest.mark.parametrize(
        "text, rule",
        [
            (GOOD.replace("fork: ", "decision: ", 1), "near-miss"),
            ("note | class: asked | rest", "near-miss"),
            (GOOD.replace(" | persisted: the plan", ""), "missing-key"),
            (
                GOOD.replace(" | chosen: the index", "").replace("persisted: the plan", "persisted: p | chosen: c"),
                "key-order",
            ),
            (_line(chosen=""), "empty-value"),
            (GOOD + " | extra: y", "extra-key"),
            (_line(**{"class": "consistency-only (seen)"}), "enum"),
            (_line(precedent_kind="decision, see 10"), "enum"),
            (_line(reversible="yes - mostly"), "enum"),
            (_line(**{"class": "maybe"}), "enum"),
            (_line(**{"class": "decided-with-notice"}), "enum"),
            (_line(precedent="none", precedent_kind="decision"), "none-pairing"),
            (_line(precedent="none", precedent_kind="none"), "none-on-consistency-only"),
            (_line(precedent="Decision 10", precedent_kind="none"), "none-on-consistency-only"),
        ],
    )
    def test_each_rule_fails_its_case(self, text: str, rule: str) -> None:
        assert rule in {f.rule for f in pfl.judge_grammar(text)[1] if f.severity == "fail"}

    def test_notice_tier_message(self) -> None:
        msgs = [f.message for f in pfl.judge_grammar(_line(**{"class": "decided-with-notice"}))[1]]
        assert any("the notice tier, not a class: write class: consistency-only" in m for m in msgs)

    def test_fork_records_labels(self) -> None:
        recs = pfl.fork_records(["prose", ASKED, "x | class: asked | y", GOOD, 7])
        assert [(r.index, r.label) for r in recs] == [(1, "fork 1"), (2, "context[2]"), (3, "fork 2")]


def _tree(tmp_path: Path) -> dict[str, str]:
    rows = [
        {"number": 10, "currency": "current", "live": True, "status": "Decided", "supersedes": [], "superseded_by": None},
        {"number": 20, "currency": "superseded_compacted", "live": True, "status": "x", "superseded_by": 30, "supersedes": []},
        {"number": 21, "currency": None, "live": False, "status": "Superseded by Decision 31", "superseded_by": None},
        {"number": 80, "currency": "superseded_pointer", "live": True, "status": "x", "superseded_by": 81, "supersedes": []},
    ]
    files = {
        pfl.DECISIONS_INDEX: json.dumps({"decisions": rows}),
        "docs/contracts/my-c.yaml": yaml.safe_dump({"a-b": {"c": {"d": 1}}, "top": 1}),
        "docs/plans/PLAN-other.yaml": yaml.safe_dump({"context": ["prose", GOOD, ASKED]}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return files


class TestConsistencyOnlyPointers:
    @pytest.fixture()
    def read(self, tmp_path: Path):
        files = _tree(tmp_path)
        return files.get

    def test_reversible_no_fails(self, read) -> None:
        assert {f.rule for f in _fails(_line(reversible="no"), read)} == {"not-reversible"}

    def test_decision_pointer(self, read) -> None:
        assert _fails(_line(), read) == []
        assert {f.rule for f in _fails(_line(precedent="the usual way"), read)} == {"pointer-missing"}
        assert {f.rule for f in _fails(_line(precedent="Decision 999"), read)} == {"pointer-unresolved"}
        assert "Decision 30" in _fails(_line(precedent="Decision 20"), read)[0].message
        assert "Decision 31" in _fails(_line(precedent="Decision 21"), read)[0].message

    def test_superseded_pointer_only_warns(self, read) -> None:
        findings = pfl.judge_line(_line(precedent="Decision 80"), plan_path="docs/plans/PLAN-self.yaml", read=read)
        assert [f.severity for f in findings] == ["warn"] and "Decision 81" in findings[0].message

    def test_missing_index_fails(self) -> None:
        assert {f.rule for f in _fails(_line())} == {"pointer-unresolved"}

    def test_contract_pointer(self, read) -> None:
        for ok in ("docs/contracts/my-c.yaml", "my-c.yaml#a-b.c.d", "docs/contracts/my-c.yaml#a-b.c)."):
            assert _fails(_line(precedent=ok, precedent_kind="contract"), read) == [], ok
        for bad, rule in (
            ("no pointer here", "pointer-missing"),
            ("docs/contracts/nope.yaml", "pointer-unresolved"),
            ("my-c.yaml#a-b.zzz", "pointer-unresolved"),
        ):
            assert {f.rule for f in _fails(_line(precedent=bad, precedent_kind="contract"), read)} == {rule}, bad

    def test_contract_pointer_ignores_other_paths(self, read) -> None:
        text = _line(precedent="docs/contracts/my-c.yaml and config/x.yaml", precedent_kind="contract")
        assert _fails(text, read) == []
        assert pfl._CONTRACT_RE.findall("docs/contracts/my-c.yaml and config/x.yaml") == [("my-c.yaml", "")]

    def test_answered_fork_pointer(self, read) -> None:
        def line(p: str) -> str:
            return _line(precedent=p, precedent_kind="answered-fork")

        assert _fails(line("PLAN-other fork 2"), read) == []
        assert {f.rule for f in _fails(line("PLAN-other fork 1"), read)} == {"pointer-not-asked"}
        assert {f.rule for f in _fails(line("PLAN-other fork 3"), read)} == {"pointer-unresolved"}
        assert {f.rule for f in _fails(line("PLAN-ghost fork 1"), read)} == {"pointer-unresolved"}
        assert {f.rule for f in _fails(line("PLAN-self fork 1"), read)} == {"pointer-self"}
        assert {f.rule for f in _fails(line("see the other plan"), read)} == {"pointer-missing"}

    def test_asked_lines_are_never_pointer_checked(self) -> None:
        assert _fails(_line(**{"class": "asked", "precedent": "nobody", "precedent_kind": "decision"})) == []
        assert _fails(ASKED) == []


class TestGrammarMatchesContract:
    @staticmethod
    def _grammar() -> str:
        grammar = pfl.load_record_grammar((ROOT / pfl.CONTRACT).read_text(encoding="utf-8"))
        assert grammar is not None
        return grammar

    def test_constants_equal_the_repo_template(self) -> None:
        grammar = self._grammar()
        assert pfl.template_from_record_grammar(grammar) is not None
        assert pfl.grammar_mismatches(grammar) == []

    @pytest.mark.parametrize(
        "old, new, name",
        [
            ("precedent_kind: decision|contract|answered-fork", "precedent_kind: decision|contract", "PRECEDENT_KIND"),
            ("reversible: yes|no", "reversible: yes|no|maybe", "REVERSIBLE"),
            ("class: consistency-only|asked", "class: asked", "CLASS"),
            ("persisted: <where>", "persisted: <where> | extra: <y>", "FIELDS"),
            ("precedent: none | precedent_kind: none is legal", "precedent: none is legal", "PRECEDENT_KIND"),
        ],
    )
    def test_mutated_template_fails(self, old: str, new: str, name: str) -> None:
        grammar = " ".join(self._grammar().split())
        assert old in grammar
        problems = pfl.grammar_mismatches(grammar.replace(old, new))
        assert any(p.startswith(name) for p in problems)

    def test_no_template_fails(self) -> None:
        assert pfl.grammar_mismatches("nothing quoted here") != []


class TestCli:
    def _plan(self, tmp_path: Path, monkeypatch, line: str) -> str:
        monkeypatch.setattr(pfl, "_worktree_reader", lambda root: lambda rel: (tmp_path / rel).read_text(encoding="utf-8"))
        rel = "docs/plans/PLAN-cli.yaml"
        (tmp_path / "docs/plans").mkdir(parents=True)
        (tmp_path / rel).write_text(yaml.safe_dump({"context": [line]}), encoding="utf-8")
        return rel

    def test_bad_plan_exits_one_with_named_finding(self, tmp_path, monkeypatch, capsys) -> None:
        rel = self._plan(tmp_path, monkeypatch, "fork: x | chosen: y | class: asked | precedent: none | persisted: z")
        assert pfl.main([rel]) == 1
        out = capsys.readouterr().out
        assert rel in out and "fork 1" in out and "missing-key" in out and "template:" in out

    def test_unreadable_plan_exits_one(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(pfl, "_worktree_reader", lambda root: lambda rel: None)
        assert pfl.main(["docs/plans/PLAN-nope.yaml"]) == 1
        assert "PLAN-nope.yaml" in capsys.readouterr().out

    def test_good_plan_exits_zero(self, tmp_path, monkeypatch) -> None:
        assert pfl.main([self._plan(tmp_path, monkeypatch, ASKED)]) == 0


class TestEdgeInputs:
    def test_malformed_template_is_a_mismatch(self) -> None:
        assert any("separated" in p for p in pfl.grammar_mismatches("'fork: a | no colon here'"))

    def test_load_record_grammar_tolerates_bad_input(self) -> None:
        assert pfl.load_record_grammar(None) is None
        assert pfl.load_record_grammar("{ not: [valid") is None
        assert pfl.load_record_grammar("autonomy_tiers: {}") is None
        assert pfl.load_record_grammar("autonomy_tiers: {plan_fork_classification: {record_grammar: 3}}") is None

    def test_fork_records_of_a_non_list_is_empty(self) -> None:
        assert pfl.fork_records("not a list") == []

    def test_unparseable_decisions_index_fails_loudly(self) -> None:
        read = {pfl.DECISIONS_INDEX: "{not json"}.get
        assert {f.rule for f in _fails(_line(), read)} == {"pointer-unresolved"}

    def test_unparseable_contract_with_an_anchor_fails(self) -> None:
        read = {"docs/contracts/bad.yaml": "{ not: [valid"}.get
        text = _line(precedent="bad.yaml#a.b", precedent_kind="contract")
        assert {f.rule for f in _fails(text, read)} == {"pointer-unresolved"}

    def test_anchor_deeper_than_a_scalar_fails(self, tmp_path: Path) -> None:
        read = {"docs/contracts/my-c.yaml": yaml.safe_dump({"top": 1})}.get
        text = _line(precedent="my-c.yaml#top.deeper", precedent_kind="contract")
        assert {f.rule for f in _fails(text, read)} == {"pointer-unresolved"}

    def test_successor_falls_back_to_the_supersedes_holder_then_to_nothing(self) -> None:
        def index(extra: list[dict]) -> object:
            rows = [{"number": 5, "currency": "superseded_compacted", "live": True, "status": "x"}, *extra]
            return {pfl.DECISIONS_INDEX: json.dumps({"decisions": rows})}.get

        text = _line(precedent="Decision 5")
        assert "Decision 6" in _fails(text, index([{"number": 6, "supersedes": [5]}]))[0].message
        assert "its successor" in _fails(text, index([]))[0].message

    def test_worktree_reader_reads_and_tolerates_missing(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        read = pfl._worktree_reader(tmp_path)
        assert read("a.txt") == "hi" and read("nope.txt") is None
