"""Each envelope rule over synthetic inputs (PLAN-decision-serves-kind-grammar, Decision 243 clause 8)."""

from __future__ import annotations

import pytest

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


_KEY = "docs/contracts/x.yaml#a.b"
_RULES = ["contract_amendment", "operator_approval"]


def _item(**over: object) -> dict:
    return {"key": _KEY, "change_rule": "contract_amendment", "bounds": {"min": 1, "max": 2}, **over}


class TestDelegatedKeyHelpers:
    @pytest.mark.parametrize("key", [_KEY, "a/b.yml#one", "config/c.yaml#a.b-c_d"])
    def test_a_well_formed_key_splits(self, key: str) -> None:
        path, dotted = r.split_delegated_key(key) or ("", "")
        assert f"{path}#{dotted}" == key

    @pytest.mark.parametrize("key", ["x.yaml", "x.json#a", "x.yaml#", "x.yaml#a..b", "x y.yaml#a", 5, None])
    def test_a_malformed_key_does_not(self, key: object) -> None:
        assert r.split_delegated_key(key) is None

    def test_dotted_value_walks_a_mapping(self) -> None:
        parsed = {"a": {"b": 3, "c": {"d": None}}}
        assert r.dotted_value(parsed, "a.b") == (True, 3)
        assert r.dotted_value(parsed, "a.c.d") == (True, None)
        assert r.dotted_value(parsed, "a.x") == (False, None)
        assert r.dotted_value(parsed, "a.b.c") == (False, None)
        assert r.dotted_value(None, "a") == (False, None)


class TestDelegatesIssues:
    def _issues(self, delegates: object, resolves: bool = True) -> list[str]:
        return r.delegates_issues({"delegates": delegates}, _RULES, lambda _k: resolves)

    def test_absent_field_is_clean(self) -> None:
        assert r.delegates_issues({}, _RULES, lambda _k: False) == []

    @pytest.mark.parametrize("bounds", [{"min": 1, "max": 2}, {"one_of": ["a"]}, {"text": "a one-line rule"}])
    def test_each_bounds_shape_passes(self, bounds: dict) -> None:
        assert self._issues([_item(bounds=bounds)]) == []

    @pytest.mark.parametrize("delegates", [None, [], "x", {}])
    def test_not_a_non_empty_list_fails(self, delegates: object) -> None:
        assert "non-empty list" in self._issues(delegates)[0]

    @pytest.mark.parametrize("item", ["x", {"key": _KEY}, {**_item(), "extra": 1}])
    def test_an_item_with_the_wrong_keys_fails(self, item: object) -> None:
        assert "exactly key, change_rule and bounds" in self._issues([item])[0]

    def test_a_malformed_key_an_unresolved_key_and_a_bad_rule_each_fail(self) -> None:
        assert "does not match" in self._issues([_item(key="nope")])[0]
        assert "does not resolve" in self._issues([_item()], resolves=False)[0]
        assert "change_rule" in self._issues([_item(change_rule="vibes")])[0]

    @pytest.mark.parametrize(
        "bounds",
        [
            "x",
            {},
            {"min": 1},
            {"min": 3, "max": 2},
            {"min": True, "max": 2},
            {"min": "1", "max": 2},
            {"one_of": []},
            {"one_of": "ab"},
            {"text": ""},
            {"text": "two\nlines"},
            {"text": 5},
            {"min": 1, "max": 2, "text": "x"},
        ],
    )
    def test_malformed_bounds_fail(self, bounds: object) -> None:
        assert "bounds" in self._issues([_item(bounds=bounds)])[0]


class TestPlanField:
    def test_absent_and_well_formed_are_clean(self) -> None:
        assert r.plan_issues({}) == [] and r.plan_issues({"plan": "a-b1"}) == []
        assert r.plan_warnings({}, lambda _s: False) == []

    @pytest.mark.parametrize("slug", ["", "-a", "A", "a b", 5])
    def test_a_non_slug_fails_and_does_not_warn(self, slug: object) -> None:
        assert "slug" in r.plan_issues({"plan": slug})[0]
        assert r.plan_warnings({"plan": slug}, lambda _s: False) == []

    def test_an_unknown_plan_warns_and_a_known_one_does_not(self) -> None:
        assert "has no docs/plans/PLAN-gone.yaml" in r.plan_warnings({"plan": "gone"}, lambda _s: False)[0]
        assert r.plan_warnings({"plan": "here"}, lambda _s: True) == []


class TestShapeDetailNamesNewFields:
    def test_delegates_and_plan_shapes_are_named(self) -> None:
        assert "delegates" in r._shape_detail({"delegates": "x"})
        assert "plan" in r._shape_detail({"plan": 3})
