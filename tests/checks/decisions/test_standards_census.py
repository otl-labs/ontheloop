"""Mirror test for scripts/checks/decisions/standards_census.py (rec-5367), on tmp_path fixture roots.

TestCommittedCensus reads docs/DECISIONS.md, docs/decisions-index.json, docs/ROADMAP-PLATFORM.yaml,
docs/contracts/decision-entry.yaml, docs/contracts/decision-compaction.yaml, docs/contracts/contract-population.yaml,
config/structural_size_budgets.yaml and config/decision_options_exemptions.yaml through the module, so a change to one
of them selects this file.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.decisions import standards_census as sc

REPO = Path(__file__).resolve().parents[3]
FILLER = "x" * 400
EXEMPT_TITLE = "Exempt thing"


def _entry(number: int, title: str, body: str = "", *, envelope: bool = True, options: bool = False, stanza: str = "") -> str:
    parts = [f"## Decision {number}: {title} (Decided)\n"]
    if envelope:
        parts.append(f"```yaml\nnumber: {number}\nstatus: Decided\n```\n")
    parts.append(f"**Status:** Decided\n\n**Decision:**\n1. {body}\n")
    if options:
        parts.append("**Options considered:** none considered -- fixture\n")
    if stanza:
        parts.append(f"```yaml reversal-conditions\n{stanza}\n```\n")
    return "\n".join(parts) + "\n"


STANZA = (
    "decision: 2\nconditions:\n  - id: m1\n    kind: manual\n    description: d\n"
    "  - id: r1\n    kind: repo_state\n    description: d\n  - not-a-mapping"
)
STUB = "## Decision 4: Old (Superseded)\n\n**Status:** Superseded\n**Superseded by: Decision 2**\n\n"
ANNOTATED = _entry(3, "Annotated", "short", envelope=False) + "> **Note 2026-01-01:** " + FILLER + "\n\n"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _index(rows: list[dict[str, Any]]) -> str:
    return json.dumps({"decisions": rows, "metadata": {}})


def _row(number: int, live: bool = True, currency: str = "current", amends: list[int] | None = None) -> dict[str, Any]:
    return {"number": number, "live": live, "currency": currency, "amends": amends or []}


def _contract(klass: str, **body: Any) -> str:
    return yaml.safe_dump({"contract": {"id": "c", "class": klass}, **body})


def _log(*semantic: bool) -> list[dict[str, Any]]:
    return [{"date": "2026-01-01", "semantic_break": s, "change_class": "x", "summary": "s" * 200} for s in semantic]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    decisions = [
        _entry(1, "Big", FILLER),
        _entry(2, "Small", "tiny", options=True, stanza=STANZA),
        ANNOTATED,
        STUB,
        _entry(5, "Dead", FILLER),
        _entry(6, EXEMPT_TITLE, "tiny"),
    ]
    _write(tmp_path, "docs/DECISIONS.md", "# Decisions\n\n" + "".join(decisions))
    rows = [_row(1), _row(2, amends=[1]), _row(3, amends=[1]), _row(4), _row(5, live=False), _row(6)]
    _write(tmp_path, "docs/decisions-index.json", _index(rows))
    _write(
        tmp_path,
        "docs/contracts/decision-entry.yaml",
        yaml.safe_dump(
            {
                "size_governance": {
                    "per_entry_size_norm": {"cap_bytes": 5000},
                    "measured_size_norm": {"cap_bytes": 300},
                    "options_considered": {"marker": "**Options considered:**", "cap_bytes": 1024},
                }
            }
        ),
    )
    _write(tmp_path, "docs/contracts/decision-compaction.yaml", yaml.safe_dump({"consolidation": {"threshold": 2}}))
    _write(
        tmp_path,
        "docs/contracts/contract-population.yaml",
        yaml.safe_dump(
            {
                "amendment_log_retention": {"keep_full": 2, "exempt_files": ["docs/contracts/exempt.yaml"]},
                "evaluator_coverage": {
                    "covered.yaml": {"asserts": ["alpha"]},
                    "trig.yaml": {"triggers": ["see trig.yaml#beta"]},
                },
            }
        ),
    )
    _write(tmp_path, "docs/contracts/over.yaml", yaml.safe_dump({"amendment_log": _log(False, False, False)}))
    _write(tmp_path, "docs/contracts/ok.yaml", yaml.safe_dump({"amendment_log": _log(False, False, True)}))
    _write(tmp_path, "docs/contracts/exempt.yaml", yaml.safe_dump({"amendment_log": _log(False, False, False, False)}))
    _write(tmp_path, "docs/contracts/broken.yaml", "a: [unterminated\n")
    _write(tmp_path, "docs/contracts/zero.yaml", _contract("D", gamma=1))
    _write(tmp_path, "docs/contracts/covered.yaml", _contract("D", alpha=1))
    _write(tmp_path, "docs/contracts/trig.yaml", _contract("D", beta=1))
    _write(tmp_path, "docs/contracts/classa.yaml", _contract("A", delta=1))
    _write(tmp_path, "config/structural_size_budgets.yaml", yaml.safe_dump({"byte_budgets": {"b.yaml": 1, "a.yaml": 2}}))
    _write(
        tmp_path,
        "config/decision_options_exemptions.yaml",
        yaml.safe_dump({"exempt_sidecars": [{"slug": "s", "title": EXEMPT_TITLE}]}),
    )
    _write(
        tmp_path,
        "docs/ROADMAP-PLATFORM.yaml",
        yaml.safe_dump({"tier_items": [{"id": "T9.1", "status": "open"}, {"id": "T9.2", "status": "complete"}]}),
    )
    return tmp_path


class TestQueries:
    def test_measured_cap_over_excludes_annotations_and_stubs(self, root: Path) -> None:
        assert sc.measured_cap_over(root) == ["1"]

    def test_options_missing_skips_exempt_title_and_options_block(self, root: Path) -> None:
        assert sc.options_missing(root) == ["1", "3"]

    def test_envelope_missing(self, root: Path) -> None:
        assert sc.envelope_missing(root) == ["3"]

    def test_consolidation_candidates(self, root: Path) -> None:
        assert sc.consolidation_candidates(root) == ["1"]

    def test_manual_conditions_skips_non_manual_and_non_mapping(self, root: Path) -> None:
        assert sc.manual_conditions(root) == ["2:m1"]

    def test_manual_conditions_skips_malformed_stanza(self, root: Path) -> None:
        text = (root / "docs/DECISIONS.md").read_text(encoding="utf-8")
        _write(root, "docs/DECISIONS.md", text.replace("decision: 2\nconditions:", "decision: [2\nconditions:"))
        assert sc.manual_conditions(root) == []

    def test_byte_seeded_sorted(self, root: Path) -> None:
        assert sc.byte_seeded(root) == ["a.yaml", "b.yaml"]

    def test_malformed_budgets_file_reads_empty(self, root: Path) -> None:
        _write(root, "config/structural_size_budgets.yaml", "a: [unterminated\n")
        assert sc.byte_seeded(root) == []

    def test_byte_seeded_absent(self, tmp_path: Path) -> None:
        assert sc.byte_seeded(tmp_path) == []

    def test_amendment_rows_over_keep_skips_exempt_and_semantic_break(self, root: Path) -> None:
        assert sc.amendment_rows_over_keep(root) == ["docs/contracts/over.yaml"]

    def test_evaluator_zero_coverage(self, root: Path) -> None:
        assert sc.evaluator_zero_coverage(root) == ["docs/contracts/zero.yaml"]

    def test_missing_entries_file_reads_empty(self, tmp_path: Path) -> None:
        assert sc.envelope_missing(tmp_path) == []
        assert sc.amendment_rows_over_keep(tmp_path) != []

    @pytest.mark.parametrize(
        ("query", "rel", "text"),
        [
            ("measured_cap_over", "docs/contracts/decision-entry.yaml", "not: [valid"),
            (
                "measured_cap_over",
                "docs/contracts/decision-entry.yaml",
                yaml.safe_dump({"size_governance": {"per_entry_size_norm": {"cap_bytes": 9}}}),
            ),
            ("options_missing", "docs/contracts/decision-entry.yaml", ""),
            ("consolidation_candidates", "docs/contracts/decision-compaction.yaml", ""),
            ("amendment_rows_over_keep", "docs/contracts/contract-population.yaml", ""),
            (
                "evaluator_zero_coverage",
                "docs/contracts/contract-population.yaml",
                yaml.safe_dump({"evaluator_coverage": "bad"}),
            ),
        ],
    )
    def test_unreadable_threshold_is_result_not_raise(self, root: Path, query: str, rel: str, text: str) -> None:
        _write(root, rel, text)
        assert isinstance(sc.QUERIES[query](root), sc.Unreadable)


def _census(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "S1", "standard": "s", "source": "Decision 1", "query": "measured_cap_over", "measure": "query",
        "disposition": "exempt", "owner": "Decision 1", "guarded_by": "validate_x", "reason": "r",
    }  # fmt: skip
    exception = {"id": "S1-1", "class": "S1", "root": 1, "owner": "rec-1", "disposition": "consolidate", "reason": "r"}
    base = {"rows": [row], "exceptions": [exception]}
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        target, field = key.split("__")
        out[target][0][field] = value
    return out


@pytest.fixture(autouse=True)
def _checks(monkeypatch: pytest.MonkeyPatch) -> None:
    if "TestCommittedCensus" not in str(monkeypatch):
        monkeypatch.setattr(registry, "all_checks", lambda: {"validate_x": object()})


class TestVerify:
    def test_clean_census_passes_with_note_for_missing_exception(self, root: Path) -> None:
        defects, notes = sc.verify(_census(), root)
        assert defects == []
        assert notes == []

    def test_candidate_without_exception_row_is_a_note(self, root: Path) -> None:
        census = _census()
        census["exceptions"] = []
        defects, notes = sc.verify(census, root)
        assert defects == []
        assert any("candidate 1 has no exception row" in n for n in notes)

    def test_exception_root_no_longer_member_is_a_note(self, root: Path) -> None:
        census = _census(exceptions__root=99)
        defects, notes = sc.verify(census, root)
        assert defects == []
        assert any("99" in n for n in notes) and any("candidate 1" in n for n in notes)

    @pytest.mark.parametrize(
        ("override", "fragment"),
        [
            ({"rows__disposition": "pending"}, "disposition"),
            ({"rows__query": "nope"}, "not a named query"),
            ({"rows__measure": "gate"}, "null query"),
            ({"rows__measure": "bogus"}, "measure"),
            ({"rows__guarded_by": "validate_missing"}, "guarded_by"),
            ({"rows__owner": "someone"}, "owner"),
            ({"rows__owner": "Decision 999"}, "owner"),
            ({"rows__owner": "T9.2"}, "owner"),
            ({"exceptions__root": "one"}, "not an int"),
            ({"exceptions__class": "S9"}, "not a class row"),
            ({"exceptions__owner": "Decision 1"}, "not rec-NNNN"),
            ({"exceptions__disposition": "pending"}, "disposition"),
        ],
    )
    def test_each_malformation_is_a_defect(self, root: Path, override: dict[str, Any], fragment: str) -> None:
        defects, _ = sc.verify(_census(**override), root)
        assert len(defects) == 1 and fragment in defects[0]

    def test_valid_owner_forms(self, root: Path) -> None:
        for owner in ("Decision 1 cl.2", "T9.1", "rec-5"):
            assert (
                sc.verify(_census(rows__owner=owner, rows__guarded_by=owner if owner == "rec-5" else "validate_x"), root)[0]
                == []
            )

    def test_owner_may_guard_its_own_row(self, root: Path) -> None:
        assert sc.verify(_census(rows__owner="rec-7", rows__guarded_by="rec-7"), root)[0] == []

    def test_missing_field_and_duplicate_and_non_mapping(self, root: Path) -> None:
        census = _census()
        del census["rows"][0]["reason"]
        census["rows"].append(copy.deepcopy(census["rows"][0]))
        census["rows"].append("junk")
        del census["exceptions"][0]["owner"]
        census["exceptions"].append({"id": "S1"})
        census["exceptions"].append("junk")
        defects, _ = sc.verify(census, root)
        text = "\n".join(defects)
        assert "missing reason" in text and "duplicate id 'S1'" in text and "not a mapping" in text and "missing owner" in text

    def test_no_rows(self, root: Path) -> None:
        assert sc.verify({}, root)[0] == ["census has no rows list"]

    def test_unreadable_threshold_is_one_defect_per_reader(self, root: Path) -> None:
        _write(root, "docs/contracts/decision-compaction.yaml", "")
        defects, _ = sc.verify(_census(), root)
        assert len(defects) == 1 and "consolidation_candidates unreadable" in defects[0]

    def test_row_with_unreadable_query_is_a_defect(self, root: Path) -> None:
        _write(root, "docs/contracts/decision-entry.yaml", "")
        defects, _ = sc.verify(_census(), root)
        assert len(defects) == 1 and "measured_cap_over unreadable" in defects[0]

    def test_load_reads_mapping_only(self, tmp_path: Path) -> None:
        assert sc.load(tmp_path / "absent.yaml") == {}
        _write(tmp_path, "l.yaml", "- a\n")
        assert sc.load(tmp_path / "l.yaml") == {}
        _write(tmp_path, "m.yaml", "a: 1\n")
        assert sc.load(tmp_path / "m.yaml") == {"a": 1}


class _Reader:
    pass


def _fetch(statuses: dict[str, str]):
    def fetch(reader: Any, ids: Any) -> tuple[list[dict], list[str]]:
        return [{"id": i, "status": statuses[i]} for i in ids if i in statuses], [i for i in ids if i not in statuses]

    return fetch


class TestReadOwners:
    def _census(self, **kw: Any) -> dict[str, Any]:
        census = _census(rows__owner="rec-2", rows__guarded_by="validate_x", exceptions__owner="rec-1")
        census["rows"].append(
            {"id": "S4", "standard": "s", "source": "x", "query": None, "measure": "judgement", "disposition": "exempt",
             "owner": "rec-3", "guarded_by": "rec-3", "reason": "r"}
        )  # fmt: skip
        census["rows"].append(
            {"id": "S9", "standard": "s", "source": "x", "query": None, "measure": "gate", "disposition": "clean",
             "owner": "rec-4", "guarded_by": "validate_x", "reason": "r"}
        )  # fmt: skip
        return census

    def test_all_open_passes(self, root: Path) -> None:
        fetch = _fetch({"rec-1": "open", "rec-2": "open", "rec-3": "open"})
        assert sc.read_owner_defects(self._census(), root, _Reader(), fetch) == []

    def test_closed_and_declined_owners_of_standing_debt_are_defects(self, root: Path) -> None:
        fetch = _fetch({"rec-1": "closed", "rec-2": "declined", "rec-3": "failed"})
        defects = sc.read_owner_defects(self._census(), root, _Reader(), fetch)
        assert len(defects) == 3 and all("not open" in d for d in defects)

    def test_missing_owner_is_a_defect(self, root: Path) -> None:
        fetch = _fetch({"rec-1": "open", "rec-3": "open"})
        assert sc.read_owner_defects(self._census(), root, _Reader(), fetch) == ["owner rec-2 does not exist"]

    def test_closed_owner_without_members_is_not_a_defect(self, root: Path) -> None:
        census = self._census()
        census["exceptions"][0]["root"] = 99
        _write(root, "docs/DECISIONS.md", "# empty\n")
        fetch = _fetch({"rec-1": "closed", "rec-2": "closed", "rec-3": "open"})
        defects = sc.read_owner_defects(census, root, _Reader(), fetch)
        assert defects == []

    def test_unreadable_threshold_counts_as_standing(self, root: Path) -> None:
        _write(root, "docs/contracts/decision-compaction.yaml", "")
        fetch = _fetch({"rec-1": "closed", "rec-2": "open", "rec-3": "open"})
        assert len(sc.read_owner_defects(self._census(), root, _Reader(), fetch)) == 1

    def test_no_rec_owners_skips_the_reader(self, root: Path) -> None:
        census = {"rows": [{"id": "S9", "owner": "Decision 1", "disposition": "exempt", "query": None}], "exceptions": []}
        assert sc.read_owner_defects(census, root, None, lambda *_: pytest.fail("reader used")) == []


class TestCli:
    def _args(self, root: Path, *argv: str) -> list[str]:
        return [*argv, "--root", str(root)]

    def _census_file(self, root: Path, census: dict[str, Any]) -> None:
        _write(root, "audits/c.yaml", yaml.safe_dump(census))

    def test_list_members_and_unknown_query(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert sc.main(self._args(root, "--list", "measured_cap_over")) == 0
        assert capsys.readouterr().out == "1\n"
        assert sc.main(self._args(root, "--list", "nope")) == 1
        assert "nope unreadable" in capsys.readouterr().out

    def test_list_unreadable_query(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(root, "docs/contracts/decision-compaction.yaml", "")
        assert sc.main(self._args(root, "--list", "consolidation_candidates")) == 1
        assert "consolidation_candidates unreadable" in capsys.readouterr().out

    def test_summary_skips_null_query_rows(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        census = _census()
        census["rows"].append({"id": "S9", "query": None})
        self._census_file(root, census)
        assert sc.main(self._args(root, "--summary", "audits/c.yaml")) == 0
        assert capsys.readouterr().out == "S1 measured_cap_over 1\n"

    def test_summary_unreadable_exits_1(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        census = _census()
        census["rows"].append({"id": "S2", "query": "nope"})
        self._census_file(root, census)
        _write(root, "docs/contracts/decision-entry.yaml", "")
        assert sc.main(self._args(root, "--summary", "audits/c.yaml")) == 1
        out = capsys.readouterr().out
        assert "S1 measured_cap_over unreadable" in out and "S2 nope unreadable" in out

    def test_verify_exit_codes(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._census_file(root, _census())
        assert sc.main(self._args(root, "--verify", "audits/c.yaml")) == 0
        self._census_file(root, _census(rows__disposition="pending"))
        assert sc.main(self._args(root, "--verify", "audits/c.yaml")) == 1
        assert "defect:" in capsys.readouterr().out

    def test_read_owners_uses_injected_reader(
        self, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from src.common import ducklake_reader_client as client

        self._census_file(root, _census(exceptions__owner="rec-1", rows__owner="Decision 1"))
        monkeypatch.setattr(client, "make_reader", lambda profile=None: _Reader())
        monkeypatch.setattr(client, "fetch_recs_by_ids", _fetch({"rec-1": "closed"}))
        assert sc.main(self._args(root, "--verify", "audits/c.yaml", "--read-owners")) == 1
        assert "owner rec-1 is closed" in capsys.readouterr().out

    def test_no_mode_is_a_usage_error(self, root: Path) -> None:
        with pytest.raises(SystemExit):
            sc.main(self._args(root))


class TestCommittedCensus:
    @pytest.fixture(autouse=True)
    def _real_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.undo()

    def test_committed_census_verifies(self) -> None:
        defects, _ = sc.verify(sc.load(REPO / sc.CENSUS_REL), REPO)
        assert defects == []

    def test_no_pending_row(self) -> None:
        assert "disposition: pending" not in (REPO / sc.CENSUS_REL).read_text(encoding="utf-8")

    def test_every_named_query_runs_on_the_real_tree(self) -> None:
        census = sc.load(REPO / sc.CENSUS_REL)
        names = {r["query"] for r in census["rows"] if r["query"]}
        assert names <= set(sc.QUERIES)
        for name in names:
            assert not isinstance(sc.QUERIES[name](REPO), sc.Unreadable)
