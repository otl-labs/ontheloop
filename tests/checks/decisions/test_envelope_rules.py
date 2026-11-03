"""Each envelope rule over synthetic inputs (PLAN-decision-serves-kind-grammar, Decision 243 clause 8)."""

from __future__ import annotations

from scripts.checks.decisions import envelope_rules as r

_ROUTING = {"numbered_decision": "A commitment.", "operational_fact": "A statement. Never a numbered Decision."}


class TestRequiredAndSignificance:
    def test_required_field_issues_excludes_significance_and_kind(self) -> None:
        assert r.required_field_issues({"number": 1}, ["number", "significance", "kind"]) == []
        assert r.required_field_issues({}, ["number", "significance", "kind"]) == [
            "envelope is missing required field(s): ['number']"
        ]

    def test_significance_cases(self) -> None:
        ok = {"significance": {"value": "numbered_decision", "justification": "j"}}
        assert r.significance_issues(ok, _ROUTING) == []
        assert "missing" in r.significance_issues({}, _ROUTING)[0]
        assert "routing token" in r.significance_issues({"significance": {"value": "x", "justification": "j"}}, _ROUTING)[0]
        never = {"significance": {"value": "operational_fact", "justification": "j"}}
        assert "never a numbered Decision" in r.significance_issues(never, _ROUTING)[0]
        empty = {"significance": {"value": "numbered_decision", "justification": " "}}
        assert "justification" in r.significance_issues(empty, _ROUTING)[0]


class TestKind:
    def test_missing_kind_fails(self) -> None:
        assert "kind element is missing" in r.kind_issues({}, ["direction", "mechanism"])[0]

    def test_value_outside_the_passed_values_fails(self) -> None:
        issues = r.kind_issues({"kind": {"value": "north_star", "justification": "j"}}, ["direction", "mechanism"])
        assert "north_star" in issues[0]

    def test_a_value_added_to_the_passed_values_is_admitted(self) -> None:
        assert r.kind_issues({"kind": {"value": "policy", "justification": "j"}}, ["direction", "mechanism", "policy"]) == []

    def test_empty_justification_fails(self) -> None:
        assert "kind.justification" in r.kind_issues({"kind": {"value": "direction", "justification": ""}}, ["direction"])[0]


class TestServes:
    _IDS = {"NS.1", "NS.2"}

    def test_absent_is_fine(self) -> None:
        assert r.serves_issues({}, self._IDS) == []

    def test_valid_list_passes(self) -> None:
        assert r.serves_issues({"serves": ["NS.1", "NS.2"]}, self._IDS) == []

    def test_empty_list_fails(self) -> None:
        assert r.serves_issues({"serves": []}, self._IDS)

    def test_bad_shape_duplicate_and_unresolved_fail(self) -> None:
        assert "match" in r.serves_issues({"serves": ["NS1"]}, self._IDS)[0]
        assert "duplicate" in r.serves_issues({"serves": ["NS.1", "NS.1"]}, self._IDS)[0]
        assert "resolve" in r.serves_issues({"serves": ["NS.9"]}, self._IDS)[0]


class TestKeysAndNumber:
    def test_unknown_key_fails(self) -> None:
        assert "bogus" in r.unknown_key_issues({"number": 1, "bogus": 1}, ["number"])[0]
        assert r.unknown_key_issues({"number": 1}, ["number"]) == []

    def test_number_must_exceed_baseline_max(self) -> None:
        assert r.number_issues(5, 5)
        assert r.number_issues(6, 5) == []

    def test_unfilled_placeholder_fails_unless_draft(self) -> None:
        assert r.number_issues("{{N}}", 5)
        assert r.number_issues("{{N}}", None, draft=True) == []

    def test_draft_without_baseline_skips_the_maximum_rule(self) -> None:
        assert r.number_issues(1, None, draft=True) == []


class TestWarnings:
    def test_dangling_serves_names_the_lawful_remedy(self) -> None:
        out = r.dangling_serves_warnings(7, {"serves": ["NS.9", "NS.1"]}, {"NS.1"})
        assert len(out) == 1 and "retired" in out[0] and "Decision 177" in out[0]

    def test_dangling_serves_ignores_a_non_list(self) -> None:
        assert r.dangling_serves_warnings(7, {"serves": "x"}, set()) == []

    def test_carried_by_mechanism_names_the_remedy_and_skips_direction(self) -> None:
        out = r.carried_by_mechanism_warnings({"NS.1": [1, 2]}, {1: "mechanism", 2: "direction"})
        assert len(out) == 1 and "Decision 1" in out[0] and "supersede" in out[0] and "Decision 177" in out[0]


class TestMalformedDetail:
    def test_folded_scalar_both_shapes(self) -> None:
        no_blank = "## Decision {{N}}: T ```yaml number: {{N}} kind: x ```\n"
        blank = "## Decision {{N}}: T\n```yaml number: {{N}} kind: x ```\n"
        for block in (no_blank, blank):
            assert (
                r.malformed_envelope_detail(block, placeholder=True) == "folded scalar: put the draft in a literal (|) block"
            )

    def test_a_later_reversal_conditions_stanza_is_not_judged(self) -> None:
        block = "## Decision 1: T\n\nbody\n\n```yaml reversal-conditions\nx: 1\n```\n"
        assert "no bare" in r.malformed_envelope_detail(block)

    def test_names_the_offending_field(self) -> None:
        def detail(env: str) -> str:
            return r.malformed_envelope_detail(f"## Decision 1: T\n\n```yaml\n{env}\n```\n")

        assert "number" in detail("number: abc")
        assert "amends" in detail("amends: 150")
        assert "kind" in detail("kind: direction")
        assert "serves" in detail("serves: NS.1")
        assert "not valid YAML" in detail("a: [")
        assert "mapping" in detail("- a")

    def test_unclosed_fence_and_unknown_shape(self) -> None:
        assert "never closed" in r.malformed_envelope_detail("## Decision 1: T\n\n```yaml\nnumber: 1\n")
        assert (
            r.malformed_envelope_detail("## Decision 1: T\n\n```yaml\nnumber: 1\n```\n") == "the envelope shape is malformed"
        )


class TestPlaceholderDiagnostics:
    def test_placeholder_tokens_are_neutralised_before_the_shape_is_judged(self) -> None:
        block = '## Decision {{N}}: T\n\n```yaml\nnumber: {{N}}\ndecided_date: "{{DATE}}"\nkind: direction\n```\n'
        assert "kind" in r.malformed_envelope_detail(block, placeholder=True)
