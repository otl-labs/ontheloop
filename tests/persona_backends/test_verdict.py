from src.persona_backends.verdict import expected, verdict_errors

FIELD = {
    "cases": [{"verdict": "REVISE", "any": [{"array": "findings", "field": "severity", "in": ["blocking"]}]}],
    "otherwise": "PROCEED",
}
THREE = {
    "cases": [
        {
            "verdict": "BLOCK",
            "any": [
                {"array": "findings", "field": "severity", "in": ["BLOCK"]},
                {"array": "roadmap.rows", "field": "status", "in": ["CONFLICT"]},
            ],
        },
        {"verdict": "NOTE", "any": [{"array": "spirit"}]},
    ],
    "otherwise": "CLEAR",
}


def test_field_condition_and_first_match():
    assert expected(FIELD, {"findings": [{"severity": "nit"}, {"severity": "blocking"}]}) == (
        "REVISE",
        "findings[1].severity is blocking",
    )
    assert expected(FIELD, {"findings": [{"severity": "nit"}]}) == ("PROCEED", "no case holds")


def test_ordered_cases_array_only_and_dotted_path():
    both = {"findings": [{"severity": "BLOCK"}], "roadmap": {"rows": [{"status": "CONFLICT"}]}}
    assert expected(THREE, both)[0] == "BLOCK"
    assert expected(THREE, {"spirit": ["x"]}) == ("NOTE", "spirit[0] is present")
    assert expected(THREE, {"roadmap": {"rows": [{"status": "OK"}]}}) == ("CLEAR", "no case holds")
    assert expected(THREE, {"roadmap": {"rows": [{"status": "CONFLICT"}]}})[1] == "roadmap.rows[0].status is CONFLICT"


def test_broken_paths_never_raise():
    for value in [{}, {"findings": 3}, {"findings": None}, {"roadmap": [1]}, {"roadmap": {"rows": "x"}}, "text", None, [1]]:
        assert expected(THREE, value)[0] == "CLEAR"


def test_non_mapping_or_fieldless_elements_never_match():
    assert expected(FIELD, {"findings": ["blocking", 5, None, {}, {"other": "blocking"}]}) == ("PROCEED", "no case holds")


def test_verdict_errors():
    good = {"verdict": "PROCEED", "findings": []}
    bad = {"verdict": "PROCEED", "findings": [{"severity": "blocking"}]}
    assert verdict_errors(FIELD, good) == []
    (error,) = verdict_errors(FIELD, bad)
    assert "is PROCEED, but the verdict rule gives REVISE (findings[0].severity is blocking)" in error
    assert error.endswith("change the verdict, never the findings, to follow the rule")
    assert "is absent" in verdict_errors(FIELD, {"findings": [{"severity": "blocking"}]})[0]
    assert len(verdict_errors(FIELD, "text")) == 1
