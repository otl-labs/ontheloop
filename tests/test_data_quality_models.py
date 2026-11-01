from scripts.data_quality_models import Check, CheckResult, RunResult, dq_request


def test_check_dataclass():
    c = Check("t", "c", "type", "not_null", "desc", "error")
    assert c.table == "t"
    assert c.column == "c"
    assert (c.kind, c.params) == ("not_null", {})
    assert not hasattr(c, "sql")
    assert Check("t", "c", "type", "unique", "desc", params={"columns": ["c"]}).params == {"columns": ["c"]}


def test_check_enforced_default():
    c = Check("t", "c", "type", "not_null", "desc", "error")
    assert c.enforced is True


def test_check_enforced_false():
    c = Check("t", "c", "type", "not_null", "desc", "error", enforced=False)
    assert c.enforced is False


def test_check_exclude_before_default():
    c = Check("t", "c", "type", "not_null", "desc")
    assert c.exclude_before is None


def test_run_result_properties():
    c = Check("t", "c", "type", "not_null", "desc", "error")
    results = [
        CheckResult(c, "PASS"),
        CheckResult(c, "FAIL"),
        CheckResult(c, "WARN"),
        CheckResult(c, "ERROR"),
        CheckResult(c, "SKIP"),
    ]
    rr = RunResult(results=results, verdict="FAIL", duration_seconds=10.0)
    assert rr.passed == 1
    assert rr.failed == 1
    assert rr.warned == 1
    assert rr.errored == 1
    assert rr.skipped == 1


def test_run_result_unenforced_fail_property():
    """RunResult.unenforced_fail counts only UNENFORCED_FAIL verdicts."""
    c = Check("t", "c", "type", "not_null", "desc", "error", enforced=False)
    results = [
        CheckResult(c, "UNENFORCED_FAIL"),
        CheckResult(c, "UNENFORCED_FAIL"),
        CheckResult(c, "FAIL"),
        CheckResult(c, "PASS"),
    ]
    rr = RunResult(results=results, verdict="FAIL", duration_seconds=1.0)
    assert rr.unenforced_fail == 2
    assert rr.failed == 1
    assert rr.passed == 1


def test_run_result_hard_gated_property():
    """RunResult.hard_gated counts only HARD_GATE verdicts -- exercised independently of _save_latest_result."""
    c = Check("ops_recommendations", "id", "tombstone_resurrection", "not_null", "desc")
    results = [
        CheckResult(c, "HARD_GATE"),
        CheckResult(c, "PASS"),
        CheckResult(c, "FAIL"),
    ]
    rr = RunResult(results=results, verdict="HARD_GATE", duration_seconds=1.0)
    assert rr.hard_gated == 1
    assert rr.passed == 1
    assert rr.failed == 1


def test_graduation_guard_unenforced_fail_is_not_pass():
    """UNENFORCED_FAIL is not PASS -- graduation guard condition (verdict != 'PASS') is satisfied."""
    c = Check("ops_recommendations", "file", "not_null", "not_null", "desc", "error", enforced=False)
    result = CheckResult(check=c, verdict="UNENFORCED_FAIL")
    assert result.verdict != "PASS"


def test_graduation_guard_unenforced_fail_not_in_passed_count():
    """UNENFORCED_FAIL checks are excluded from passed count -- graduation readiness requires PASS."""
    c = Check("ops_recommendations", "file", "not_null", "not_null", "desc", "error", enforced=False)
    rr = RunResult(results=[CheckResult(c, "UNENFORCED_FAIL")], verdict="PASS")
    assert rr.passed == 0
    assert rr.unenforced_fail == 1


def test_dq_request_is_the_closed_request_a_check_sends():
    c = Check("ops_recommendations", "title", "not_null", "not_null", "desc", params={"column": "title"})
    assert dq_request(c) == {
        "action": "dq_check",
        "check": "not_null",
        "table": "ops_recommendations",
        "params": {"column": "title"},
    }
