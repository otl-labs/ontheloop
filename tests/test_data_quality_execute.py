"""Tests for scripts/data_quality_execute.py -- the DuckLake-only DQ execution path."""

from unittest.mock import patch

import pytest

boto3 = pytest.importorskip("boto3")

from scripts.data_quality_execute import (  # noqa: E402
    _execute_check_ducklake,
    _is_reader_unavailable,
    run_checks,
)
from scripts.data_quality_models import Check, CheckResult, RunResult  # noqa: E402


def test_run_checks_dry_run():
    checks = [Check("t", "c", "type", "not_null", "desc")]
    res = run_checks(checks, dry_run=True)
    assert res.verdict == "SKIP"


def test_run_checks_with_issues():
    with patch("scripts.data_quality_execute._execute_check_ducklake") as mock_exec:
        mock_exec.side_effect = [
            CheckResult(Check("t", "c", "type", "not_null", "desc"), "PASS"),
            CheckResult(Check("t", "c", "type", "not_null", "desc"), "FAIL"),
            CheckResult(Check("t", "c", "type", "not_null", "desc"), "ERROR"),
        ]
        checks = [Check("t", "c", "type", "not_null", "desc")] * 3
        with patch("src.common.ducklake_reader_client.DuckLakeReader"):
            res = run_checks(checks)
    assert res.verdict == "FAIL"


def test_run_checks_profile_arg_reaches_the_reader():
    """profile_name is handed straight to the DuckLake reader client."""
    with (
        patch("src.common.ducklake_reader_client.DuckLakeReader") as mock_reader,
        patch("scripts.data_quality_execute._execute_check_ducklake") as mock_exec,
    ):
        mock_exec.return_value = CheckResult(Check("t", "c", "type", "not_null", "desc"), "PASS")
        run_checks([Check("t", "c", "type", "not_null", "desc")], profile_name="my-profile")
    mock_reader.assert_called_once_with(profile="my-profile")


def test_run_checks_profile_defaults_to_none():
    """With no profile_name, the reader resolves its own profile (None is passed through)."""
    with (
        patch("src.common.ducklake_reader_client.DuckLakeReader") as mock_reader,
        patch("scripts.data_quality_execute._execute_check_ducklake") as mock_exec,
    ):
        mock_exec.return_value = CheckResult(Check("t", "c", "type", "not_null", "desc"), "PASS")
        run_checks([Check("t", "c", "type", "not_null", "desc")])
    mock_reader.assert_called_once_with(profile=None)


def test_apply_backend_routing_pins_ops_tables_and_appends_clause8():
    """Every _OPS_TABLES check is pinned to the DuckLake reader and the clause-8 checks are appended."""
    import scripts.data_quality_runner as dq

    recs = Check("ops_recommendations", "file", "not_null", "not_null", "recs file not null", params={"column": "file"})
    other = Check("market_data", "trade_id", "not_null", "not_null", "x", backend="elsewhere")
    out = dq.apply_backend_routing([recs, other], "agent_platform")
    assert recs.backend == "ducklake" and other.backend == "elsewhere"
    assert out[:2] == [recs, other] and {c.test_type for c in out[2:]} >= {"ulid_history_unique", "current_merge_key_unique"}


def test_run_checks_no_reader_client():
    """An unimportable reader client degrades to SKIP rather than raising."""
    with patch.dict("sys.modules", {"src.common.ducklake_reader_client": None}):
        checks = [Check("t", "c", "type", "not_null", "desc")]
        res = run_checks(checks)
    assert res.verdict == "SKIP"


def test_run_checks_empty_list_returns_error():
    """Gap 1: run_checks with an empty check list must return verdict=ERROR, not PASS."""
    res = run_checks([])
    assert res.verdict == "ERROR"
    assert len(res.results) == 0


def test_run_checks_enforced_false_advisory():
    """enforced=False FAIL does not produce FAIL aggregate; enforced=True FAIL does."""
    enforced_check = Check("t", "c", "not_null", "not_null", "desc", enforced=True)
    unenforced_check = Check("t", "c", "unique", "not_null", "desc", enforced=False)

    with (
        patch("src.common.ducklake_reader_client.DuckLakeReader"),
        patch("scripts.data_quality_execute._execute_check_ducklake") as mock_exec,
    ):
        mock_exec.side_effect = [CheckResult(unenforced_check, "FAIL")]
        res = run_checks([unenforced_check])
    assert res.verdict == "PASS"

    with (
        patch("src.common.ducklake_reader_client.DuckLakeReader"),
        patch("scripts.data_quality_execute._execute_check_ducklake") as mock_exec,
    ):
        mock_exec.side_effect = [CheckResult(enforced_check, "FAIL")]
        res = run_checks([enforced_check])
    assert res.verdict == "FAIL"


def test_ops_backend_unconditionally_ducklake(monkeypatch):
    import scripts.data_quality_runner as dq

    # Decision 84 I-1: DuckLake is the sole ops backend; the env rollback flag is retired.
    monkeypatch.delenv("OPS_STORAGE_BACKEND", raising=False)
    assert dq._ops_backend() == "ducklake"
    monkeypatch.setenv("OPS_STORAGE_BACKEND", "anything")  # ignored: no env read remains
    assert dq._ops_backend() == "ducklake"


def test_ducklake_ops_tables_set():
    import scripts.data_quality_runner as dq

    assert dq._OPS_TABLES == frozenset({"ops_recommendations", "ops_decisions", "ops_priority_queue", "ops_execution_plans"})


def test_verdict_for_pass_fail_unenforced_hardgate():
    import scripts.data_quality_runner as dq

    c_pass = dq.Check("t", "x", "not_null", "sql", "d")
    assert dq._verdict_for(c_pass, 0, 0.1).verdict == "PASS"
    assert dq._verdict_for(c_pass, 3, 0.1).verdict == "FAIL"
    c_unenf = dq.Check("t", "x", "not_null", "sql", "d", enforced=False)
    assert dq._verdict_for(c_unenf, 1, 0.1).verdict == "UNENFORCED_FAIL"
    c_tomb = dq.Check("t", "id", "tombstone_resurrection", "sql", "d")
    assert dq._verdict_for(c_tomb, 1, 0.1).verdict == "HARD_GATE"


def _reply(**over):
    from src.common.ducklake_dq_checks import check_version

    body = {"ok": True, "check": "not_null", "table": "ops_recommendations", "violation_count": 0}
    body["check_version"] = check_version("not_null")
    body.update(over)
    return body


class _Reader:
    def __init__(self, body=None, exc=None):
        self.body, self.exc, self.calls = body if body is not None else _reply(), exc, []

    def dq_check(self, check, table, params=None):
        self.calls.append((check, table, params))
        if self.exc:
            raise self.exc
        return self.body


def _check(**kw):
    return Check("ops_recommendations", "id", "not_null", "not_null", "d", params={"column": "id"}, **kw)


def test_checks_run_through_dq_check():
    reader = _Reader()
    assert _execute_check_ducklake(_check(), reader).verdict == "PASS"
    assert reader.calls == [("not_null", "ops_recommendations", {"column": "id"})]
    failing = _Reader(_reply(violation_count=3))
    result = _execute_check_ducklake(_check(), failing)
    assert result.verdict == "FAIL" and result.violation_count == 3


def test_execute_check_ducklake_relationships_skipped():
    check = Check("ops_priority_queue", "rec_id", "relationships", None, "d", backend="ducklake")
    assert _execute_check_ducklake(check, object()).verdict == "SKIP"


def test_check_version_skew_is_unavailable():
    result = _execute_check_ducklake(_check(), _Reader(_reply(check_version="0" * 16)))
    assert result.verdict == "UNAVAILABLE" and "not at this code" in result.detail
    assert result.violation_count == 0


def test_history_kind_is_unavailable_on_a_transient_reader_error():
    check = Check("ops_recommendations", "ulid", "ulid_history_unique", "unique", "d", params={"columns": ["ulid"]})
    result = _execute_check_ducklake(check, _Reader(exc=RuntimeError("ducklake reader failed (HTTP 503)")))
    assert result.verdict == "UNAVAILABLE"


def test_missing_violation_count_is_error():
    body = _reply()
    del body["violation_count"]
    assert _execute_check_ducklake(_check(), _Reader(body)).verdict == "ERROR"


def test_run_checks_routes_ducklake(monkeypatch):
    monkeypatch.setattr("src.common.ducklake_reader_client.DuckLakeReader", lambda profile=None: _Reader())
    assert run_checks([_check(backend="ducklake")], dry_run=False).verdict == "PASS"


# ---------------------------------------------------------------------------
# UNAVAILABLE/DEGRADED: _is_reader_unavailable classification
# ---------------------------------------------------------------------------


class TestIsReaderUnavailable:
    """_is_reader_unavailable must classify transient infra outages vs structured handler errors."""

    def test_requests_connection_error_is_unavailable(self):
        import requests

        assert _is_reader_unavailable(requests.ConnectionError("connection refused")) is True

    def test_requests_timeout_is_unavailable(self):
        import requests

        assert _is_reader_unavailable(requests.Timeout("timed out")) is True

    def test_runtime_error_502_no_error_type_is_unavailable(self):
        assert _is_reader_unavailable(RuntimeError("ducklake reader failed (HTTP 502)")) is True

    def test_runtime_error_503_is_unavailable(self):
        assert _is_reader_unavailable(RuntimeError("failed (HTTP 503)")) is True

    def test_runtime_error_504_is_unavailable(self):
        assert _is_reader_unavailable(RuntimeError("failed (HTTP 504)")) is True

    def test_runtime_error_502_with_error_type_is_not_unavailable(self):
        assert _is_reader_unavailable(RuntimeError("failed (HTTP 502) error_type=runtime")) is False

    def test_runtime_error_500_with_error_type_is_not_unavailable(self):
        assert _is_reader_unavailable(RuntimeError("failed (HTTP 500) error_type=version_mismatch")) is False

    def test_runtime_error_500_no_error_type_is_not_unavailable(self):
        """HTTP 500 is NOT in the transient set {502,503,504} -- must gate as ERROR."""
        assert _is_reader_unavailable(RuntimeError("failed (HTTP 500)")) is False

    def test_runtime_error_4xx_is_not_unavailable(self):
        assert _is_reader_unavailable(RuntimeError("failed (HTTP 403)")) is False

    def test_generic_exception_is_not_unavailable(self):
        assert _is_reader_unavailable(Exception("some error")) is False

    def test_value_error_is_not_unavailable(self):
        assert _is_reader_unavailable(ValueError("bad value")) is False


# ---------------------------------------------------------------------------
# UNAVAILABLE/DEGRADED: _execute_check_ducklake exception classification
# ---------------------------------------------------------------------------


class TestExecuteCheckDucklakeUnavailable:
    """_execute_check_ducklake must classify transient dq_check raises as UNAVAILABLE."""

    def _verdict(self, exc):
        return _execute_check_ducklake(_check(), _Reader(exc=exc)).verdict

    def test_connection_error_returns_unavailable(self):
        import requests

        assert self._verdict(requests.ConnectionError("connection refused")) == "UNAVAILABLE"

    def test_runtime_error_502_returns_unavailable(self):
        assert self._verdict(RuntimeError("ducklake reader failed (HTTP 502)")) == "UNAVAILABLE"

    def test_runtime_error_503_returns_unavailable(self):
        assert self._verdict(RuntimeError("failed (HTTP 503)")) == "UNAVAILABLE"

    def test_structured_500_with_error_type_returns_error(self):
        assert self._verdict(RuntimeError("ducklake reader failed (HTTP 500) error_type=runtime")) == "ERROR"

    def test_typed_400_input_refusal_returns_error(self):
        assert self._verdict(RuntimeError("ducklake reader failed (HTTP 400) error_type=dq_check_input")) == "ERROR"

    def test_runtime_error_4xx_returns_error(self):
        assert self._verdict(RuntimeError("ducklake reader failed (HTTP 403)")) == "ERROR"

    def test_semantic_error_returns_error(self):
        assert self._verdict(ValueError("bad value")) == "ERROR"


# ---------------------------------------------------------------------------
# UNAVAILABLE/DEGRADED: run_checks aggregate
# ---------------------------------------------------------------------------


class TestRunChecksDegradedAggregate:
    """run_checks must aggregate DEGRADED when the only non-PASS results are UNAVAILABLE."""

    def test_only_unavailable_aggregates_to_degraded(self, monkeypatch):
        reader = _Reader(exc=RuntimeError("failed (HTTP 503)"))
        monkeypatch.setattr("src.common.ducklake_reader_client.DuckLakeReader", lambda profile=None: reader)
        result = run_checks([_check(backend="ducklake")], dry_run=False)
        assert result.verdict == "DEGRADED"
        assert result.unavailable == 1

    def test_mixed_unavailable_and_violation_aggregates_to_fail(self, monkeypatch):
        replies = [RuntimeError("failed (HTTP 503)"), _reply(violation_count=5)]

        class _Seq:
            def dq_check(self, check, table, params=None):
                nxt = replies.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt

        monkeypatch.setattr("src.common.ducklake_reader_client.DuckLakeReader", lambda profile=None: _Seq())
        result = run_checks([_check(backend="ducklake"), _check(backend="ducklake")], dry_run=False)
        assert result.verdict == "FAIL"

    def test_unavailable_property_counts_correctly(self):
        c = Check("t", "c", "type", "sql", "d")
        rr = RunResult(
            results=[
                CheckResult(c, "UNAVAILABLE"),
                CheckResult(c, "UNAVAILABLE"),
                CheckResult(c, "PASS"),
            ],
            verdict="DEGRADED",
        )
        assert rr.unavailable == 2
