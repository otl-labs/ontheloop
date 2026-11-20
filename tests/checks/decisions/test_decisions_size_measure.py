"""Mirror test for scripts/checks/decisions/decisions_size_measure.py (Decision 276 clauses 4-6)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks.decisions import decisions_size_measure as m
from scripts.decisions_md import extract_entry_envelope, iter_decision_sections

REPO = Path(__file__).resolve().parents[3]
KEY = m.MEASURED_CAP_KEY
PER_ENTRY = 6 * 1024


def _contract(measured: object = 100, options: object = 80, per_entry: object = PER_ENTRY) -> str:
    section: dict = {"per_entry_size_norm": {"cap_bytes": per_entry}}
    if measured is not None:
        section["measured_size_norm"] = {"cap_bytes": measured}
    if options is not None:
        section["options_considered"] = {"marker": "**Options considered:**", "cap_bytes": options}
    return yaml.safe_dump({"size_governance": section})


GOV = m.size_governance(_contract())
assert isinstance(GOV, m.Governance)


def _entry(
    number: int = 9, title: str = "T (Decided)", options: str = "**Options considered:** a -- rejected: b.\n\n", body: str = ""
) -> str:
    return (
        f"## Decision {number}: {title}\n\n```yaml\nnumber: {number}\nkind:\n  value: direction\n  justification: j\n"
        f"significance:\n  value: numbered_decision\n  justification: j\n```\n\n**Status:** Decided\n\n{body}{options}"
        f"**Related:** none\n\n---\n\n"
    )


class TestSizeGovernance:
    def test_parsed_with_every_cap(self) -> None:
        assert GOV == m.Governance(PER_ENTRY, 100, 80)

    def test_older_contract_without_the_new_keys_reads_as_none(self) -> None:
        gov = m.size_governance(_contract(measured=None, options=None))
        assert gov == m.Governance(PER_ENTRY, None, None)

    @pytest.mark.parametrize("text", ["a: [", "size_governance: {}", "- x", "size_governance:\n  per_entry_size_norm: {}"])
    def test_unparseable_or_missing_per_entry_cap_is_unreadable(self, text: str) -> None:
        assert isinstance(m.size_governance(text), str)

    @pytest.mark.parametrize("bad", [None, 0, -1, "5120", True, 1.5])
    def test_a_present_cap_without_a_positive_integer_is_unreadable(self, bad: object) -> None:
        for kwargs in ({"measured": bad, "options": 80}, {"measured": 100, "options": bad}):
            text = yaml.safe_dump(
                {
                    "size_governance": {
                        "per_entry_size_norm": {"cap_bytes": PER_ENTRY},
                        "measured_size_norm": {"cap_bytes": kwargs["measured"]},
                        "options_considered": {"cap_bytes": kwargs["options"]},
                    }
                }
            )
            assert isinstance(m.size_governance(text), str)

    def test_per_entry_cap_must_be_a_positive_integer(self) -> None:
        assert isinstance(m.size_governance(_contract(per_entry=True)), str)

    def test_a_present_key_that_is_not_a_mapping_is_unreadable(self) -> None:
        text = "size_governance:\n  per_entry_size_norm: {cap_bytes: 1}\n  measured_size_norm: 5\n"
        assert isinstance(m.size_governance(text), str)


class TestTitleKey:
    @pytest.mark.parametrize("number", ["12", "{{N}}"])
    @pytest.mark.parametrize("suffix", ["", " (Decided)"])
    def test_filled_and_placeholder_headings_with_and_without_status(self, number: str, suffix: str) -> None:
        assert m.title_key(f"## Decision {number}: A title (amends Decision 3){suffix}") == "A title (amends Decision 3)"

    def test_a_non_heading_has_no_key(self) -> None:
        assert m.title_key("text") == ""


class TestOptionsRule:
    def test_conforming_block_passes(self) -> None:
        assert m.options_issues(_entry(), GOV, set()) == []

    def test_missing_marker_fails(self) -> None:
        assert "carries no" in m.options_issues(_entry(options=""), GOV, set())[0]

    def test_empty_block_fails(self) -> None:
        assert "empty" in m.options_issues(_entry(options="**Options considered:**\n\n"), GOV, set())[0]

    def test_none_considered_needs_a_reason(self) -> None:
        bare = m.options_issues(_entry(options="**Options considered:** none considered\n\n"), GOV, set())
        assert "needs" in bare[0]
        assert (
            m.options_issues(_entry(options="**Options considered:** none considered -- a one-line fix.\n\n"), GOV, set())
            == []
        )

    def test_over_the_block_cap_fails(self) -> None:
        issues = m.options_issues(_entry(options="**Options considered:** " + "x" * 90 + "\n\n"), GOV, set())
        assert "over its 80-byte cap" in issues[0]

    def test_an_exempt_title_passes_without_a_block(self) -> None:
        assert m.options_issues(_entry(options=""), GOV, {"T"}) == []

    def test_the_rule_is_off_without_the_contract_key(self) -> None:
        assert m.options_issues(_entry(options=""), m.Governance(PER_ENTRY, 100), set()) == []


class TestMeasuredCap:
    def test_an_entry_over_the_whole_span_cap_but_under_the_measured_one_passes(self) -> None:
        gov = m.Governance(300, 6000, 5000)
        assert m.measured_cap_issues(_entry(), gov) == []

    def test_an_entry_over_the_measured_cap_fails_and_names_the_clause(self) -> None:
        issue = m.measured_cap_issues(_entry(body="x" * 400 + "\n\n"), GOV)[0]
        assert "clause 4" in issue and "measured_size_norm" in issue

    def test_the_excluded_blocks_do_not_count(self) -> None:
        big_options = "**Options considered:** " + "y" * 900 + "\n\n"
        assert m.measured_cap_issues(_entry(options=big_options), m.Governance(PER_ENTRY, 200, 2000)) == []

    def test_no_measured_cap_means_no_finding(self) -> None:
        assert m.measured_cap_issues(_entry(body="x" * 9000), m.Governance(PER_ENTRY)) == []


class TestExemptionLegs:
    def test_rows_parse_and_bad_text_reads_as_none(self) -> None:
        assert m.exemption_rows("exempt_sidecars:\n- {slug: a, title: T}\n") == [("a", "T")]
        assert m.exemption_rows(None) == []
        assert m.exemption_rows("a: [") == []
        assert m.exemption_rows("exempt_sidecars:\n- {slug: a}\n") == []

    def test_a_head_row_absent_from_the_base_list_fails(self) -> None:
        fails, _ = m.exemption_findings([("a", "T"), ("b", "U")], [("a", "T")], True, {"a", "b"})
        assert len(fails) == 1 and "'b'" not in fails[0] and "(b, U)" in fails[0]

    def test_the_list_appearing_after_the_rule_landed_fails(self) -> None:
        fails, _ = m.exemption_findings([("a", "T")], None, True, {"a"})
        assert "only the build PR creates it" in fails[0]

    def test_the_build_prs_own_case_passes(self) -> None:
        assert m.exemption_findings([("a", "T")], None, False, {"a"}) == ([], [])

    def test_a_slug_without_a_sidecar_warns_with_the_open_pr_wording(self) -> None:
        _, warns = m.exemption_findings([("a", "T")], [("a", "T")], True, set())
        assert "expected while that slug's PR is open" in warns[0]

    def test_a_shrinking_list_passes(self) -> None:
        assert m.exemption_findings([], [("a", "T")], True, set()) == ([], [])


def _delegator(extra: str = "") -> str:
    return (
        f"## Decision 5: Delegator\n\n```yaml\nnumber: 5\nkind:\n  value: direction\n  justification: j\n"
        f"delegates:\n  - key: {KEY}\n    change_rule: contract_amendment\n    bounds: {{min: 1, max: 9}}\n"
        f"{extra}```\n\n---\n\n"
    )


def _amender(kind: str = "direction", field: str = "amends") -> str:
    head = f"## Decision 9: Amender\n\n```yaml\nnumber: 9\nkind:\n  value: {kind}\n  justification: j\n"
    return f"{head}{field}: [5]\n```\n\n---\n\n"


def _entries(text: str) -> dict[int, str]:
    return {int(mt.group(1)): block for mt, block in iter_decision_sections(text)}


class TestFailClosedLeg:
    BASE = _contract()

    @pytest.mark.parametrize(
        "head",
        [
            _contract(measured=None),
            _contract(options=None),
            yaml.safe_dump({"size_governance": {"per_entry_size_norm": {"cap_bytes": PER_ENTRY}, "measured_size_norm": {}}}),
        ],
    )
    def test_dropping_a_key_fails_without_an_amender(self, head: str) -> None:
        issues = m.fail_closed_issues(self.BASE, head, _entries(_delegator()), {})
        assert issues or m.carried_keys(head) == m.carried_keys(self.BASE)

    def test_each_dropped_key_is_named(self) -> None:
        issues = m.fail_closed_issues(self.BASE, _contract(measured=None, options=None), _entries(_delegator()), {})
        assert len(issues) == 2 and "Decision 128" in issues[0]

    def test_a_new_direction_entry_amending_the_delegator_passes(self) -> None:
        new = _entries(_amender())
        assert m.fail_closed_issues(self.BASE, _contract(measured=None), _entries(_delegator()), new) == []

    def test_a_direction_entry_that_supersedes_it_passes(self) -> None:
        new = _entries(_amender(field="supersedes"))
        assert m.fail_closed_issues(self.BASE, _contract(measured=None), _entries(_delegator()), new) == []

    def test_a_mechanism_entry_does_not_waive_it(self) -> None:
        new = _entries(_amender(kind="mechanism"))
        assert m.fail_closed_issues(self.BASE, _contract(measured=None), _entries(_delegator()), new)

    def test_an_unrelated_direction_entry_does_not_waive_it(self) -> None:
        other = _amender().replace("[5]", "[6]")
        assert m.fail_closed_issues(self.BASE, _contract(measured=None), _entries(_delegator()), _entries(other))

    def test_nothing_dropped_or_no_base_is_clean(self) -> None:
        assert m.fail_closed_issues(self.BASE, self.BASE, {}, {}) == []
        assert m.fail_closed_issues(None, "x: 1", {}, {}) == []
        assert m.carried_keys("a: [") == set()


class TestInPlaceGrowth:
    def _grow(self, base: str, head: str, gov: m.Governance = GOV) -> tuple[list[str], list[str]]:
        return m.in_place_growth(9, "docs/DECISIONS.md", head, base, gov)

    def test_body_growth_past_the_cap_fails_and_names_the_remedies(self) -> None:
        fails, warns = self._grow(_entry(body="x" * 300 + "\n\n"), _entry(body="x" * 320 + "\n\n"))
        assert warns == [] and "clause 6" in fails[0] and "compacted stub" in fails[0] and "archival" in fails[0]

    @pytest.mark.parametrize(
        "note",
        ["> **Amended by Decision 9 (2026-01-01):** x\n\n", "> **Note (2026-01-01):** a\n> b\n\n"],
    )
    def test_a_dated_annotation_alone_passes(self, note: str) -> None:
        base = _entry(body="x" * 300 + "\n\n")
        assert self._grow(base, _entry(body="x" * 300 + "\n\n" + note)) == ([], [])

    def test_shrinking_or_growing_under_the_cap_passes(self) -> None:
        assert self._grow(_entry(body="x" * 320 + "\n\n"), _entry(body="x" * 300 + "\n\n")) == ([], [])
        assert self._grow(_entry(), _entry(body="y" * 10 + "\n\n")) == ([], [])

    def test_a_bare_superseded_by_line_on_an_over_cap_entry_fails(self) -> None:
        base = _entry(body="x" * 300 + "\n\n")
        head = base.replace("**Related:** none", "**Superseded by: Decision 12**\n\n**Related:** none")
        assert self._grow(base, head)[0]

    def test_an_entry_absent_at_base_in_this_file_is_never_growth(self) -> None:
        assert m.in_place_growth(9, "docs/DECISIONS_ARCHIVE.md", _entry(body="x" * 900 + "\n\n"), None, GOV) == ([], [])

    def test_growth_only_through_an_undated_label_paragraph_warns(self) -> None:
        fails, warns = self._grow(
            _entry(body="x" * 60 + "\n\n"),
            _entry(body="x" * 60 + "\n\n").replace("---", "> **Supersession note:** " + "z" * 80 + "\n\n---"),
        )
        assert fails == [] and "undated annotation" in warns[0] and "Supersession note" in warns[0]

    def test_the_leg_is_off_without_the_measured_cap(self) -> None:
        assert self._grow(_entry(), _entry(body="x" * 9000), m.Governance(PER_ENTRY)) == ([], [])


class TestLiveContractSwitch:
    def test_the_live_contract_carries_both_keys_and_the_delegated_values_sit_in_bounds(self) -> None:
        contract = (REPO / "docs/contracts/decision-entry.yaml").read_text(encoding="utf-8")
        gov = m.size_governance(contract)
        assert isinstance(gov, m.Governance) and gov.measured_cap is not None and gov.options_cap is not None
        parsed = yaml.safe_load(contract)
        decisions = (REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")
        delegators = [
            envelope
            for _, block in iter_decision_sections(decisions)
            if (envelope := extract_entry_envelope(block))
            and any(d.get("key") == KEY for d in envelope.get("delegates") or [])
        ]
        assert len(delegators) == 1
        for item in delegators[0]["delegates"]:
            path, dotted = item["key"].split("#")
            node = parsed
            for part in dotted.split("."):
                node = node[part]
            assert item["bounds"]["min"] <= node <= item["bounds"]["max"]
