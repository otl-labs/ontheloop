"""Mirror home for scripts/rec_trailer_acceptance_junit.py (Decision 201, slice B).

Covers the total JUNIT_ARMS -> ACCEPTANCE_VERDICTS map (positive evidence only), the node-id
inversion against a fixture report carrying no `file=` attribute (class-based, parametrized,
class-level-selector expansion, absent-report), and the total GRAMMAR_ARMS classification
(every declared arm's route and reason, the parse-failure fail-closed arm, and totality over a
corpus of real acceptance shapes).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from scripts.ops_portal.closure_gate import ACCEPTANCE_VERDICTS, FAILS, HOLDS, UNMEASURABLE
from scripts.rec_trailer_acceptance_junit import (
    GRAMMAR_ARMS,
    GRAMMAR_MIXED_CONJUNCTION,
    GRAMMAR_MIXED_OTHER_OPERATOR,
    GRAMMAR_NEGATED_SEGMENT,
    GRAMMAR_NO_NODE_ID,
    GRAMMAR_SELECTOR_FLAG,
    GRAMMAR_SINGLE_NODE_CHAIN,
    GRAMMAR_UNPARSEABLE,
    JUNIT_ARMS,
    ROUTE_JUNIT,
    ROUTE_REFUSE,
    ROUTE_STATIC,
    ROUTES,
    classify_grammar,
    index_report,
    junit_verdict,
    node_key,
    outcome_for,
    verdict_for_junit,
)
from scripts.rec_trailer_acceptance_junit import main as junit_main


def _write_report(tmp_path: Path, testcases_xml: str) -> Path:
    path = tmp_path / "pytest-junit.xml"
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite>{testcases_xml}</testsuite></testsuites>', encoding="utf-8")
    return path


class TestJunitVerdict:
    def test_arm_map_is_total(self) -> None:
        for arm in JUNIT_ARMS:
            assert verdict_for_junit(arm) in ACCEPTANCE_VERDICTS

    def test_only_passed_yields_holds(self) -> None:
        for arm in JUNIT_ARMS:
            if arm == "passed":
                assert verdict_for_junit(arm) == HOLDS
            else:
                assert verdict_for_junit(arm) != HOLDS

    def test_failed_and_errored_yield_fails(self) -> None:
        assert verdict_for_junit("failed") == FAILS
        assert verdict_for_junit("errored") == FAILS

    def test_skipped_absent_and_report_unavailable_yield_unmeasurable(self) -> None:
        assert verdict_for_junit("skipped") == UNMEASURABLE
        assert verdict_for_junit("absent") == UNMEASURABLE
        assert verdict_for_junit("report_unavailable") == UNMEASURABLE

    def test_node_key_module_level(self) -> None:
        assert node_key("tests/x.py::test_y") == "tests.x.test_y"

    def test_node_key_bare_path_with_no_node_id(self) -> None:
        assert node_key("tests/x.py") == "tests.x"

    def test_node_key_class_based(self) -> None:
        assert node_key("tests/x.py::TestKlass::test_y") == "tests.x.TestKlass.test_y"

    def test_node_key_class_level_selector(self) -> None:
        assert node_key("tests/x.py::TestKlass") == "tests.x.TestKlass"

    def test_class_based_node_id_resolves_without_file_attribute(self, tmp_path: Path) -> None:
        """The target tests/x.py::TestKlass::test_y matches classname="tests.x.TestKlass"
        name="test_y" -- the inversion, not fingerprint.py's classname-to-path reconstruction."""
        report = _write_report(tmp_path, '<testcase classname="tests.x.TestKlass" name="test_y"/>')
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::TestKlass::test_y") == "passed"

    def test_class_level_selector_matches_every_method_in_the_class(self, tmp_path: Path) -> None:
        report = _write_report(
            tmp_path,
            '<testcase classname="tests.x.TestKlass" name="test_a"/>'
            '<testcase classname="tests.x.TestKlass" name="test_b"><failure message="boom"/></testcase>',
        )
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::TestKlass") == "failed"

    def test_class_level_selector_all_pass(self, tmp_path: Path) -> None:
        report = _write_report(
            tmp_path,
            '<testcase classname="tests.x.TestKlass" name="test_a"/><testcase classname="tests.x.TestKlass" name="test_b"/>',
        )
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::TestKlass") == "passed"

    def test_class_level_selector_never_matches_a_differently_named_class(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path, '<testcase classname="tests.x.TestKlassExtra" name="test_a"/>')
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::TestKlass") == "absent"

    def test_parametrized_target_without_bracket_matches_every_variant(self, tmp_path: Path) -> None:
        report = _write_report(
            tmp_path,
            '<testcase classname="tests.x" name="test_p[1]"/>'
            '<testcase classname="tests.x" name="test_p[2]"><error message="boom"/></testcase>',
        )
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::test_p") == "errored"

    def test_skipped_testcase_resolves_skipped(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path, '<testcase classname="tests.x" name="test_a"><skipped message="why"/></testcase>')
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::test_a") == "skipped"

    def test_no_matching_entry_resolves_absent(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path, '<testcase classname="tests.x" name="test_other"/>')
        index = index_report(report)
        assert outcome_for(index, "tests/x.py::test_a") == "absent"

    def test_outcome_for_defensive_fallback_when_priority_tuple_is_broken(self, tmp_path: Path) -> None:
        """_ARM_PRIORITY covers every value index_report can emit, so the trailing `return PASSED`
        is unreachable under normal operation. Force the invariant to break (an empty priority
        tuple) to prove the defensive fallback itself behaves sanely rather than raising."""
        import scripts.rec_trailer_acceptance_junit as junit_mod

        report = _write_report(tmp_path, '<testcase classname="tests.x" name="test_a"/>')
        index = index_report(report)
        original = junit_mod._ARM_PRIORITY
        junit_mod._ARM_PRIORITY = ()
        try:
            assert outcome_for(index, "tests/x.py::test_a") == "passed"
        finally:
            junit_mod._ARM_PRIORITY = original

    def test_combine_defensive_fallback_when_priority_tuple_is_broken(self) -> None:
        import scripts.rec_trailer_acceptance_junit as junit_mod

        original = junit_mod._ARM_PRIORITY
        junit_mod._ARM_PRIORITY = ()
        try:
            assert junit_mod._combine(["passed"]) == "report_unavailable"
        finally:
            junit_mod._ARM_PRIORITY = original

    def test_absent_report_resolves_every_entry_to_unmeasurable(self, tmp_path: Path) -> None:
        census_doc = {
            "entries": [
                {"rec_id": "rec-1", "acceptance": "pytest tests/x.py::test_a", "acceptance_sha256": "a", "route": ROUTE_JUNIT}
            ]
        }
        doc = junit_verdict(census_doc, tmp_path / "missing.xml", "deadbeef")
        assert doc["report_available"] is False
        assert doc["source"] == "junit"
        assert len(doc["records"]) == 1
        record = doc["records"][0]
        assert record["verdict"] == UNMEASURABLE
        assert record["arm"] == "report_unavailable"

    def test_malformed_report_also_resolves_unavailable(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.xml"
        path.write_text("not xml", encoding="utf-8")
        assert index_report(path) is None

    def test_junit_verdict_only_processes_junit_routed_entries(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path, '<testcase classname="tests.x" name="test_a"/>')
        census_doc = {
            "entries": [
                {"rec_id": "rec-1", "acceptance": "pytest tests/x.py::test_a", "acceptance_sha256": "a", "route": ROUTE_JUNIT},
                {"rec_id": "rec-2", "acceptance": "grep -q X f.py", "acceptance_sha256": "b", "route": ROUTE_STATIC},
            ]
        }
        doc = junit_verdict(census_doc, report, "deadbeef")
        assert [r["rec_id"] for r in doc["records"]] == ["rec-1"]
        assert doc["records"][0]["verdict"] == HOLDS

    def test_multi_node_conjunction_requires_all_to_pass(self, tmp_path: Path) -> None:
        report = _write_report(
            tmp_path,
            '<testcase classname="tests.x" name="test_a"/>'
            '<testcase classname="tests.y" name="test_b"><failure message="boom"/></testcase>',
        )
        census_doc = {
            "entries": [
                {
                    "rec_id": "rec-1",
                    "acceptance": "pytest tests/x.py::test_a tests/y.py::test_b",
                    "acceptance_sha256": "a",
                    "route": ROUTE_JUNIT,
                }
            ]
        }
        doc = junit_verdict(census_doc, report, "deadbeef")
        assert doc["records"][0]["verdict"] == FAILS
        assert doc["records"][0]["arm"] == "failed"


class TestJunitGrammar:
    def test_every_arm_declares_a_reason_and_a_route(self) -> None:
        reasons = []
        for route, reason in GRAMMAR_ARMS.values():
            assert route in ROUTES
            assert reason
            reasons.append(reason)
        assert len(reasons) == len(set(reasons))

    def test_single_segment_multi_node_command_is_junit(self) -> None:
        result = classify_grammar("pytest tests/x.py::test_a tests/x.py::test_b -q")
        assert result.arm == GRAMMAR_SINGLE_NODE_CHAIN
        assert result.route == ROUTE_JUNIT
        assert result.node_ids == ("tests/x.py::test_a", "tests/x.py::test_b")

    def test_bash_valid_trailing_backslash_is_unparseable_and_refused(self) -> None:
        result = classify_grammar("pytest tests/x.py \\")
        assert result.arm == GRAMMAR_UNPARSEABLE
        assert result.route == ROUTE_REFUSE

    def test_unbalanced_quote_is_unparseable_and_refused(self) -> None:
        result = classify_grammar("pytest tests/x.py::test_a'")
        assert result.arm == GRAMMAR_UNPARSEABLE
        assert result.route == ROUTE_REFUSE

    def test_and_only_chain_is_mixed_conjunction_and_stays_static(self) -> None:
        result = classify_grammar("grep -q X tests/f.py && pytest tests/f.py::Node")
        assert result.arm == GRAMMAR_MIXED_CONJUNCTION
        assert result.route == ROUTE_STATIC

    def test_pipe_chain_is_mixed_other_operator_and_stays_static(self) -> None:
        result = classify_grammar("pytest tests/x.py::test_a | tee out.log")
        assert result.arm == GRAMMAR_MIXED_OTHER_OPERATOR
        assert result.route == ROUTE_STATIC

    def test_semicolon_chain_is_mixed_other_operator(self) -> None:
        result = classify_grammar("pytest tests/x.py::test_a ; echo done")
        assert result.arm == GRAMMAR_MIXED_OTHER_OPERATOR
        assert result.route == ROUTE_STATIC

    def test_negated_segment_stays_static(self) -> None:
        result = classify_grammar("! pytest tests/x.py::test_a")
        assert result.arm == GRAMMAR_NEGATED_SEGMENT
        assert result.route == ROUTE_STATIC

    def test_selector_flag_stays_static(self) -> None:
        result = classify_grammar("pytest -k foo tests/x.py::test_a")
        assert result.arm == GRAMMAR_SELECTOR_FLAG
        assert result.route == ROUTE_STATIC

    def test_interpreter_dash_m_pytest_is_not_a_selector_flag(self) -> None:
        for prefix in ("bin/venv-python -m pytest", "python -m pytest", ".venv/bin/python -m pytest", ".venv/bin/pytest"):
            result = classify_grammar(f"{prefix} tests/x.py::test_a tests/x.py::test_b -q")
            assert result.arm == GRAMMAR_SINGLE_NODE_CHAIN, prefix
            assert result.route == ROUTE_JUNIT, prefix
            assert result.node_ids == ("tests/x.py::test_a", "tests/x.py::test_b"), prefix

    def test_real_marker_selector_still_refused(self) -> None:
        for cmd in (
            "bin/venv-python -m pytest -m slow tests/x.py::test_a",
            "bin/venv-python -m pytest tests/x.py::test_a -k foo",
            "bin/venv-python -m pytest --deselect tests/x.py::test_b tests/x.py::test_a",
            "pytest -m=slow tests/x.py::test_a",
        ):
            result = classify_grammar(cmd)
            assert result.arm == GRAMMAR_SELECTOR_FLAG, cmd
            assert result.route == ROUTE_STATIC, cmd

    def test_attached_or_combined_short_selector_is_refused(self) -> None:
        for cmd in (
            "pytest -mslow tests/x.py::test_a",
            "bin/venv-python -m pytest -kfoo tests/x.py::test_a",
            "bin/venv-python -m pytest -vk foo tests/x.py::test_a",
            "bin/venv-python -m pytest -xm slow tests/x.py::test_a",
            "pytest -qk foo tests/x.py::test_a",
        ):
            result = classify_grammar(cmd)
            assert result.arm == GRAMMAR_SELECTOR_FLAG, cmd
            assert result.route == ROUTE_STATIC, cmd

    def test_without_an_anchored_invocation_the_whole_segment_is_scanned(self) -> None:
        for cmd in (
            "py.test -k foo tests/x.py::test_a tests/pytest",
            "bin/venv-python -mpytest -k foo tests/x.py::test_a --rootdir=a/pytest",
            "pytest-3 -k foo tests/x.py::test_a",
            "timeout -k 5 60 pytest tests/x.py::test_a",
            "pytest-3 -k foo -m pytest tests/x.py::test_a",
            "uv run --with pytest pytest -k foo tests/x.py::test_a",
        ):
            result = classify_grammar(cmd)
            assert result.arm == GRAMMAR_SELECTOR_FLAG, cmd
            assert result.route == ROUTE_STATIC, cmd

    def test_bare_path_with_no_node_id_is_residual_and_stays_static(self) -> None:
        result = classify_grammar("pytest tests/x.py")
        assert result.arm == GRAMMAR_NO_NODE_ID
        assert result.route == ROUTE_STATIC

    def test_parse_failure_routes_to_refusal_not_to_the_probe(self) -> None:
        result = classify_grammar("pytest tests/x.py::test_a \\")
        assert result.route == ROUTE_REFUSE
        assert result.route != ROUTE_STATIC

    def test_classification_is_total_over_a_shape_corpus(self) -> None:
        corpus = [
            "pytest tests/x.py::test_a",
            "pytest tests/x.py::test_a tests/x.py::test_b",
            "pytest tests/x.py::TestKlass::test_y",
            "pytest tests/x.py",
            "pytest tests/x.py::test_a && pytest tests/y.py::test_b",
            "pytest tests/x.py::test_a || true",
            "pytest tests/x.py::test_a ; true",
            "! pytest tests/x.py::test_a",
            "pytest -k foo tests/x.py::test_a",
            "pytest -m slow tests/x.py::test_a",
            "bin/venv-python -m pytest tests/x.py::test_a -q",
            "bin/venv-python -m pytest -m slow tests/x.py::test_a",
            "pytest --deselect tests/x.py::test_b tests/x.py::test_a",
            "pytest tests/x.py::test_a'",
        ]
        for cmd in corpus:
            result = classify_grammar(cmd)
            assert result.arm in GRAMMAR_ARMS
            assert result.route in ROUTES


def test_junit_arms_and_grammar_arms_are_disjoint_vocabularies() -> None:
    assert set(JUNIT_ARMS).isdisjoint(set(GRAMMAR_ARMS))


def test_verdict_and_grammar_totality_cross_product() -> None:
    for arm in itertools.chain(JUNIT_ARMS, GRAMMAR_ARMS):
        assert isinstance(arm, str) and arm


class TestJunitVerdictCli:
    def test_missing_census_artifact_fails_loud_and_writes_no_artifact(self, tmp_path: Path) -> None:
        rc = junit_main(["--artifact-dir", str(tmp_path)])
        assert rc == 1
        assert not (tmp_path / "junit_verdict.json").exists()

    def test_present_census_and_report_writes_verdict_artifact(self, tmp_path: Path) -> None:
        (tmp_path / "trailer_census.json").write_text(
            json.dumps(
                {
                    "ids": ["rec-1"],
                    "entries": [
                        {
                            "rec_id": "rec-1",
                            "acceptance": "pytest tests/x.py::test_a",
                            "acceptance_sha256": "a",
                            "bucket": "probeable",
                            "route": ROUTE_JUNIT,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        report = tmp_path / "pytest-junit.xml"
        report.write_text(
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase classname="tests.x" name="test_a"/>'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )
        rc = junit_main(["--artifact-dir", str(tmp_path), "--junit-report", str(report)])
        assert rc == 0
        written = json.loads((tmp_path / "junit_verdict.json").read_text(encoding="utf-8"))
        assert written["report_available"] is True
        assert written["records"][0]["verdict"] == HOLDS

    def test_default_junit_report_path_is_artifact_dir_relative(self, tmp_path: Path) -> None:
        (tmp_path / "trailer_census.json").write_text(json.dumps({"ids": [], "entries": []}), encoding="utf-8")
        rc = junit_main(["--artifact-dir", str(tmp_path)])
        assert rc == 0
        written = json.loads((tmp_path / "junit_verdict.json").read_text(encoding="utf-8"))
        assert written["report_available"] is False
