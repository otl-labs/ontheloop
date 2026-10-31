from __future__ import annotations

import json
import runpy
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.ci_rca.fetch_logs import _failed_jobs, _run_log, fetch_run_log, main
from scripts.ci_rca.log_evidence import read_envelope
from tests.fixtures.ci_rca.oversized_validate_log import build_log


def _cp(code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_end_to_end_oversized_log_retains_tail_anchor_in_published_envelope(tmp_path: Path, monkeypatch) -> None:
    """VP17 / acceptance criterion 6: the FULL fetch_run_log -> publish_envelope path (not only
    bound_text_windowed in isolation) retains the "Failed checks:" tail anchor for an oversized
    log -- an injected runner cannot exercise this (it bypasses _run_log entirely), so this test
    mocks subprocess.run (metadata) and subprocess.Popen (the real streaming transport, redirected
    to dump the oversized fixture) instead."""
    fixture_file = tmp_path / "fixture.log"
    fixture_file.write_text(build_log())
    jobs_json = json.dumps(
        {"jobs": [{"databaseId": 5, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]}]}
    )
    monkeypatch.setattr("scripts.ci_rca.fetch_logs.subprocess.run", lambda *_a, **_k: _cp(stdout=jobs_json))

    real_popen = subprocess.Popen

    def fake_popen(_command, **kwargs):
        dump = "import sys,pathlib; sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())"
        return real_popen([sys.executable, "-c", dump, str(fixture_file)], **kwargs)

    monkeypatch.setattr("scripts.ci_rca.fetch_logs.subprocess.Popen", fake_popen)

    out = tmp_path / "evidence.json"
    result = fetch_run_log("999", "owner/repo", out)
    assert result.fetched
    envelope = read_envelope(out)
    assert "Failed checks:" in envelope["body"]
    assert "Coverage below 100%" in envelope["body"]
    assert envelope["limits"]["truncation_reason"] == "head_tail_window"


class Runner:
    def __init__(self, results: list[subprocess.CompletedProcess]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        return self.results.pop(0)


JOBS = json.dumps(
    {
        "jobs": [
            {
                "databaseId": 22,
                "conclusion": "failure",
                "name": "untrusted token=secret",
                "steps": [{"name": "setup", "conclusion": "success"}, {"name": "test", "conclusion": "failure"}],
            }
        ]
    }
)


def test_primary_creates_typed_envelope_and_preserves_http_body(tmp_path: Path) -> None:
    out = tmp_path / "evidence.json"
    runner = Runner([_cp(stdout=JOBS), _cp(stdout="HTTP 502 in genuine log body\n")])
    result = fetch_run_log("123", "owner/repo", out, runner=runner)
    envelope = read_envelope(out)
    assert result.fetched
    assert envelope["retrieval_path"] == "primary"
    assert envelope["failed_jobs"] == [
        {"job_id": 22, "conclusion": "failure", "failed_steps": [{"step_index": 2, "conclusion": "failure"}]}
    ]
    assert envelope["body"] == "HTTP 502 in genuine log body\n"
    assert "untrusted" not in out.read_text(encoding="utf-8")


def test_fallback_is_per_failed_job_and_uses_shared_cap(tmp_path: Path) -> None:
    jobs = json.dumps(
        {
            "jobs": [
                {"databaseId": 9, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]},
                {"databaseId": 2, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]},
            ]
        }
    )
    runner = Runner([_cp(stdout=jobs), _cp(code=1, stderr="failed to get jobs"), _cp(stdout="abc\n"), _cp(stdout="def\n")])
    out = tmp_path / "evidence.json"
    result = fetch_run_log("123", "owner/repo", out, runner=runner, max_bytes=5, max_lines=10)
    envelope = read_envelope(out)
    assert result.fetched
    assert envelope["body"] == "abc\n"
    assert envelope["limits"]["complete"] is False
    assert [call[-2] for call in runner.calls[2:]] == ["2", "9"]
    assert all("--job" in call and "--log" in call for call in runner.calls[2:])
    assert not any(call[-1] == "--log" and "--job" not in call for call in runner.calls)


def test_failed_metadata_retries_and_leaves_no_output(tmp_path: Path) -> None:
    runner = Runner([_cp(code=1, stderr="HTTP 503") for _ in range(3)])
    out = tmp_path / "evidence.json"
    sleeps: list[int] = []
    result = fetch_run_log("123", "owner/repo", out, runner=runner, sleep_fn=sleeps.append)
    assert not result.fetched
    assert sleeps == [10, 20]
    assert not out.exists()


def test_main_fails_closed_with_annotation(tmp_path: Path, monkeypatch, capsys) -> None:
    runner = Runner([_cp(code=1) for _ in range(3)])
    monkeypatch.setattr("scripts.ci_rca.fetch_logs.subprocess.run", runner)
    monkeypatch.setattr("scripts.ci_rca.fetch_logs.time.sleep", lambda _value: None)
    assert main(["--run-id", "1", "--repo", "owner/repo", "--out", str(tmp_path / "out")]) == 1
    assert "::error::" in capsys.readouterr().out


def test_main_returns_success_and_converts_validation_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.ci_rca.fetch_logs.fetch_run_log", lambda *_args, **_kwargs: type("Outcome", (), {"fetched": True})()
    )
    assert main(["--run-id", "1", "--repo", "owner/repo", "--out", str(tmp_path / "out")]) == 0
    monkeypatch.setattr(
        "scripts.ci_rca.fetch_logs.fetch_run_log", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad"))
    )
    assert main(["--run-id", "1", "--repo", "owner/repo", "--out", str(tmp_path / "out")]) == 1


@pytest.mark.parametrize("max_bytes,max_lines", [(0, 1), (1, 0), (-1, 1)])
def test_invalid_limits_fail_before_any_subprocess(tmp_path: Path, max_bytes: int, max_lines: int) -> None:
    runner = Runner([])
    with pytest.raises(ValueError, match="positive"):
        fetch_run_log("1", "owner/repo", tmp_path / "out", runner=runner, max_bytes=max_bytes, max_lines=max_lines)
    assert runner.calls == []


def test_partial_fallback_failure_fails_whole_attempt(tmp_path: Path) -> None:
    jobs = json.dumps(
        {
            "jobs": [
                {"databaseId": 2, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]},
                {"databaseId": 9, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]},
            ]
        }
    )
    runner = Runner([_cp(stdout=jobs), _cp(code=1), _cp(stdout="partial\n"), _cp(code=1, stderr="unavailable")])
    result = fetch_run_log("1", "owner/repo", tmp_path / "out", attempts=1, runner=runner)
    assert not result.fetched
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "payload, message",
    [
        ("[]", "no jobs array"),
        (json.dumps({"jobs": [{"databaseId": 1, "conclusion": "success"}]}), "no failed jobs"),
        (json.dumps({"jobs": [{"databaseId": None, "conclusion": "failure"}]}), "numeric database id"),
    ],
)
def test_failed_job_metadata_validation(payload: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _failed_jobs(payload)


def test_invalid_json_metadata_is_reported(tmp_path: Path) -> None:
    result = fetch_run_log("1", "owner/repo", tmp_path / "out", attempts=1, runner=Runner([_cp(stdout="{")]))
    assert not result.fetched
    assert result.diagnostic and "invalid failed-job metadata" in result.diagnostic


def test_fallback_stops_before_query_when_aggregate_cap_is_consumed(tmp_path: Path) -> None:
    jobs = json.dumps(
        {
            "jobs": [
                {"databaseId": 2, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]},
                {"databaseId": 9, "conclusion": "failure", "steps": [{"name": "s", "conclusion": "failure"}]},
            ]
        }
    )
    runner = Runner([_cp(stdout=jobs), _cp(code=1), _cp(stdout="x")])
    result = fetch_run_log("1", "owner/repo", tmp_path / "out", attempts=1, runner=runner, max_bytes=1)
    assert result.fetched
    assert len(runner.calls) == 3


class TestClassification:
    def test_successful_fetch_with_5xx_in_log_body_is_accepted(self, tmp_path: Path) -> None:
        test_primary_creates_typed_envelope_and_preserves_http_body(tmp_path)

    def test_successful_fetch_with_transient_signature_on_stderr_is_accepted(self, tmp_path: Path) -> None:
        out = tmp_path / "evidence.json"
        runner = Runner([_cp(stdout=JOBS), _cp(stdout="body\n", stderr="failed to get jobs: HTTP 503")])
        assert fetch_run_log("123", "owner/repo", out, runner=runner).fetched


class TestTransientRetry:
    def test_transient_stderr_failure_retries_then_fails_loud(self, tmp_path: Path) -> None:
        test_failed_metadata_retries_and_leaves_no_output(tmp_path)


class TestStreamingTransport:
    def test_exact_byte_cap_is_complete_but_cap_plus_one_is_truncated(self) -> None:
        """ci-rca-evidence-fidelity: genuine truncation is "more was drained than head
        retained", not merely "head reached its cap" -- content ending EXACTLY at the cap is
        complete. The over-cap case now also carries a rolling `tail` sample."""
        exact = _run_log([sys.executable, "-c", "import sys;sys.stdout.write('abcd')"], 4, 10)
        over = _run_log([sys.executable, "-c", "import sys;sys.stdout.write('abcde')"], 4, 10)
        assert exact.stdout == over.stdout == "abcd"
        assert exact.truncated is False
        assert exact.truncation_reason is None
        assert exact.tail == ""
        assert over.truncated is True and over.truncation_reason == "head_tail_window"
        assert over.tail == "e"

    def test_line_cap_terminates_on_first_byte_of_next_line(self) -> None:
        result = _run_log([sys.executable, "-c", "import sys;sys.stdout.write('one\\ntwo\\nthree')"], 100, 2)
        assert result.stdout == "one\ntwo\n"
        assert result.truncated is True and result.truncation_reason == "head_tail_window"
        assert result.tail == "three"

    def test_large_stderr_is_drained_concurrently(self) -> None:
        program = "import sys;sys.stderr.write('x'*200000);sys.stderr.flush();sys.stdout.write('ok')"
        result = _run_log([sys.executable, "-c", program], 100, 10)
        assert result.returncode == 0
        assert result.stdout == "ok"
        assert len(result.stderr) == 8192

    def test_sigterm_ignoring_child_is_killed_after_the_drain_ceiling(self) -> None:
        """A child that ignores SIGTERM and produces no further output after filling the head
        buffer must still be bounded -- by the EXPLICIT drain_timeout_s ceiling (test-injected
        small value), not by an indefinite wait for more data that will never come."""
        program = (
            "import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "sys.stdout.write('xx');sys.stdout.flush();time.sleep(10)"
        )
        started_at = time.monotonic()
        result = _run_log([sys.executable, "-c", program], 1, 10, drain_timeout_s=0.3)
        elapsed = time.monotonic() - started_at
        assert result.stdout == "x"
        assert result.truncated is True
        assert result.truncation_reason == "drain_ceiling"
        assert result.returncode == 0
        assert result.tail == ""
        # Regression guard: a buffered stream.read(n) blocks trying to fill n bytes even after
        # select() reports the fd merely ready, silently defeating drain_timeout_s against a
        # child that writes a little then goes quiet -- this must return near drain_timeout_s
        # (~0.3s) plus kill overhead, not wait out the child's full 10s sleep.
        assert elapsed < 5.0, f"drain_timeout_s=0.3 should bound this well under 5s, took {elapsed:.2f}s"

    def test_drain_max_bytes_ceiling_kills_a_runaway_producer(self) -> None:
        """A child producing MORE than drain_max_bytes (even though it would eventually finish
        on its own) is killed rather than drained to completion -- the bounded-memory contract."""
        program = "import sys;sys.stdout.write('y' * 5000)"
        result = _run_log([sys.executable, "-c", program], 100, 10, drain_max_bytes=10, drain_timeout_s=5.0)
        assert result.truncated is True
        assert result.truncation_reason == "drain_ceiling"
        assert result.returncode == 0

    def test_timeout_kills_child_escalates_from_sigterm_to_sigkill(self, monkeypatch) -> None:
        """The wait()-escalation path: a killed child that does not exit within the 1s
        process.wait() grace period is force-killed. Independent of drain_timeout_s -- this
        exercises the post-loop cleanup, not the drain ceiling itself."""

        class Process:
            stdout = object()
            stderr = object()
            returncode = 1

            def __init__(self) -> None:
                self.waits = 0
                self.killed = False

            def wait(self, *, timeout: int) -> None:
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("gh", timeout)

            def kill(self) -> None:
                self.killed = True

        process = Process()
        selector = type(
            "Selector",
            (),
            {
                "register": lambda *_args: None,
                "get_map": lambda _self: {},
                "close": lambda _self: None,
            },
        )()
        monkeypatch.setattr("scripts.ci_rca.fetch_logs.subprocess.Popen", lambda *_args, **_kwargs: process)
        monkeypatch.setattr("scripts.ci_rca.fetch_logs.selectors.DefaultSelector", lambda: selector)
        result = _run_log(["gh"], 1, 1)
        assert process.killed
        assert result.truncated is False
        assert result.stdout == ""


def test_module_entrypoint(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["fetch_logs", "--run-id", "1", "--repo", "owner/repo", "--out", str(tmp_path / "out"), "--max-bytes", "0"],
    )
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("scripts.ci_rca.fetch_logs", run_name="__main__")
    assert exc_info.value.code == 1


class TestScopeStepTableTotality:
    """AC1/AC3/AC5/AC17: producer-side step-table totality, both-path log_retrieved derivation,
    multi-job pair keying, and no step names in the envelope."""

    def test_scope_lists_every_executed_step_including_late_non_failed_steps(self, tmp_path: Path) -> None:
        """run 30615075007 shape: step 15 fails, steps 16 (skipped) and 17 (success) still ran
        after it -- both must appear in scope with log_retrieved=false (AC1)."""
        conclusions = ["success"] * 14 + ["failure", "skipped", "success"]
        steps = [{"name": f"step-{i}", "conclusion": c} for i, c in enumerate(conclusions, 1)]
        jobs = json.dumps({"jobs": [{"databaseId": 999, "conclusion": "failure", "steps": steps}]})
        runner = Runner([_cp(stdout=jobs), _cp(stdout="log body\n")])
        out = tmp_path / "evidence.json"
        result = fetch_run_log("30615075007", "owner/repo", out, runner=runner)
        envelope = read_envelope(out)
        assert result.fetched
        job_scope = next(entry for entry in envelope["scope"] if entry["job_id"] == 999)
        assert [step["step_index"] for step in job_scope["steps"]] == list(range(1, 18))
        by_index = {step["step_index"]: step for step in job_scope["steps"]}
        assert by_index[15] == {"step_index": 15, "conclusion": "failure", "log_retrieved": True}
        assert by_index[16] == {"step_index": 16, "conclusion": "skipped", "log_retrieved": False}
        assert by_index[17] == {"step_index": 17, "conclusion": "success", "log_retrieved": False}

    def test_fallback_path_log_retrieved_true_for_queried_false_for_unqueried(self, tmp_path: Path) -> None:
        jobs = json.dumps(
            {
                "jobs": [
                    {
                        "databaseId": 2,
                        "conclusion": "failure",
                        "steps": [{"name": "a", "conclusion": "success"}, {"name": "b", "conclusion": "failure"}],
                    },
                    {"databaseId": 9, "conclusion": "failure", "steps": [{"name": "c", "conclusion": "failure"}]},
                ]
            }
        )
        runner = Runner([_cp(stdout=jobs), _cp(code=1), _cp(stdout="x")])
        out = tmp_path / "evidence.json"
        result = fetch_run_log("1", "owner/repo", out, attempts=1, runner=runner, max_bytes=1)
        envelope = read_envelope(out)
        assert result.fetched
        assert envelope["retrieval_path"] == "fallback"
        by_job = {entry["job_id"]: entry for entry in envelope["scope"]}
        assert all(step["log_retrieved"] is True for step in by_job[2]["steps"])
        assert all(step["log_retrieved"] is False for step in by_job[9]["steps"])

    def test_two_job_scope_keeps_shared_step_index_distinct_per_job(self, tmp_path: Path) -> None:
        """Job A step 3 is unretrieved and job B step 3 is retrieved -- pair keying must not
        collide the two just because they share a positional step_index."""
        steps_a = [
            {"name": "a1", "conclusion": "failure"},
            {"name": "a2", "conclusion": "success"},
            {"name": "a3", "conclusion": "success"},
        ]
        steps_b = [{"name": "b1", "conclusion": "success"}] * 2 + [{"name": "b3", "conclusion": "failure"}]
        jobs = json.dumps(
            {
                "jobs": [
                    {"databaseId": 2, "conclusion": "failure", "steps": steps_a},
                    {"databaseId": 9, "conclusion": "failure", "steps": steps_b},
                ]
            }
        )
        runner = Runner([_cp(stdout=jobs), _cp(stdout="log body\n")])
        out = tmp_path / "evidence.json"
        result = fetch_run_log("1", "owner/repo", out, runner=runner)
        envelope = read_envelope(out)
        assert result.fetched
        by_job = {entry["job_id"]: entry for entry in envelope["scope"]}
        step3_a = next(step for step in by_job[2]["steps"] if step["step_index"] == 3)
        step3_b = next(step for step in by_job[9]["steps"] if step["step_index"] == 3)
        assert step3_a["log_retrieved"] is False
        assert step3_b["log_retrieved"] is True

    def test_succeeded_sibling_job_is_excluded_from_scope(self, tmp_path: Path) -> None:
        """A job that succeeded (never selected by _failed_jobs) must not appear in scope at
        all, even though it is present in the raw `gh run view --json jobs` payload."""
        jobs = json.dumps(
            {
                "jobs": [
                    {"databaseId": 5, "conclusion": "failure", "steps": [{"name": "x", "conclusion": "failure"}]},
                    {"databaseId": 6, "conclusion": "success", "steps": [{"name": "y", "conclusion": "success"}]},
                ]
            }
        )
        runner = Runner([_cp(stdout=jobs), _cp(stdout="log body\n")])
        out = tmp_path / "evidence.json"
        result = fetch_run_log("1", "owner/repo", out, runner=runner)
        envelope = read_envelope(out)
        assert result.fetched
        assert [entry["job_id"] for entry in envelope["scope"]] == [5]

    def test_hostile_step_name_never_appears_in_envelope(self, tmp_path: Path) -> None:
        steps = [{"name": "untrusted token=deadbeef12345678", "conclusion": "failure"}]
        jobs = json.dumps({"jobs": [{"databaseId": 5, "conclusion": "failure", "steps": steps}]})
        runner = Runner([_cp(stdout=jobs), _cp(stdout="log body\n")])
        out = tmp_path / "evidence.json"
        result = fetch_run_log("1", "owner/repo", out, runner=runner)
        assert result.fetched
        assert "untrusted" not in out.read_text(encoding="utf-8")


def _jobs_json(*jobs: tuple[int, str, list[str]]) -> str:
    return json.dumps(
        {
            "jobs": [
                {"databaseId": job_id, "conclusion": conclusion, "steps": [{"name": "s", "conclusion": c} for c in steps]}
                for job_id, conclusion, steps in jobs
            ]
        }
    )


_UNFINALIZED = ("success", "success", "")
_RAW_CMD = ["gh", "api", "repos/owner/repo/actions/jobs/7/logs"]


class TestUnfinalizedStepMetadata:
    """rec-4776: a failed job GitHub has not finalized reads its raw job log, never --log-failed."""

    def test_empty_conclusion_is_null_and_raw_log_is_read(self, tmp_path: Path) -> None:
        runner = Runner([_cp(stdout=_jobs_json((7, "failure", list(_UNFINALIZED)))), _cp(stdout="raw job log\n")])
        out = tmp_path / "evidence.json"
        assert fetch_run_log("1", "owner/repo", out, runner=runner).fetched
        envelope = read_envelope(out)
        assert runner.calls[1] == _RAW_CMD
        assert not any("--log-failed" in call for call in runner.calls)
        assert envelope["retrieval_path"] == "fallback"
        assert envelope["body"] == "raw job log\n"
        steps = envelope["scope"][0]["steps"]
        assert [s["conclusion"] for s in steps] == ["success", "success", None]
        assert all(s["log_retrieved"] is True for s in steps)

    def test_cancelled_job_with_unstarted_steps_takes_raw_route(self, tmp_path: Path) -> None:
        jobs = _jobs_json((7, "cancelled", ["success", "cancelled", ""]))
        runner = Runner([_cp(stdout=jobs), _cp(stdout="cancelled log\n")])
        assert fetch_run_log("1", "owner/repo", tmp_path / "e.json", runner=runner).fetched
        assert runner.calls[1] == _RAW_CMD

    def test_mixed_finalized_and_unfinalized_jobs_use_their_own_commands(self, tmp_path: Path) -> None:
        jobs = _jobs_json((7, "failure", list(_UNFINALIZED)), (9, "failure", ["success", "failure"]))
        runner = Runner([_cp(stdout=jobs), _cp(stdout="raw\n"), _cp(stdout="split\n")])
        out = tmp_path / "evidence.json"
        assert fetch_run_log("1", "owner/repo", out, runner=runner).fetched
        assert runner.calls[1] == _RAW_CMD
        assert runner.calls[2] == ["gh", "run", "view", "1", "--repo", "owner/repo", "--job", "9", "--log"]
        envelope = read_envelope(out)
        assert envelope["body"] == "raw\nsplit\n"
        assert all(s["log_retrieved"] for entry in envelope["scope"] for s in entry["steps"])

    def test_failed_job_with_no_steps_takes_raw_route(self, tmp_path: Path) -> None:
        runner = Runner([_cp(stdout=_jobs_json((7, "failure", []))), _cp(stdout="runner log\n")])
        assert fetch_run_log("1", "owner/repo", tmp_path / "e.json", runner=runner).fetched
        assert runner.calls[1] == _RAW_CMD

    def test_finalized_run_keeps_primary_routing(self, tmp_path: Path) -> None:
        runner = Runner([_cp(stdout=_jobs_json((7, "failure", ["success", "failure"]))), _cp(stdout="primary\n")])
        assert fetch_run_log("1", "owner/repo", tmp_path / "e.json", runner=runner).fetched
        assert runner.calls[1][-1] == "--log-failed"

    @pytest.mark.parametrize("result", [_cp(code=1, stderr="HTTP 404"), _cp(stdout="")])
    def test_failed_or_empty_raw_fetch_fails_loud(self, tmp_path: Path, result) -> None:
        runner = Runner([_cp(stdout=_jobs_json((7, "failure", list(_UNFINALIZED)))), result])
        out = tmp_path / "evidence.json"
        assert not fetch_run_log("1", "owner/repo", out, attempts=1, runner=runner).fetched
        assert not out.exists()

    def test_fragment_without_transport_tail_is_appended_unwindowed(self, tmp_path: Path) -> None:
        runner = Runner([_cp(stdout=_jobs_json((7, "failure", list(_UNFINALIZED)))), _cp(stdout="x" * 50 + "\n")])
        out = tmp_path / "evidence.json"
        assert fetch_run_log("1", "owner/repo", out, runner=runner, max_bytes=1000).fetched
        assert read_envelope(out)["body"] == "x" * 50 + "\n"

    def _stream_raw_log(self, tmp_path: Path, monkeypatch, text: str, **limits) -> dict:
        source = tmp_path / "raw.log"
        source.write_text(text)
        jobs = _jobs_json((7, "failure", list(_UNFINALIZED)))
        monkeypatch.setattr("scripts.ci_rca.fetch_logs.subprocess.run", lambda *_a, **_k: _cp(stdout=jobs))
        real_popen = subprocess.Popen
        dump = "import sys,pathlib; sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())"
        monkeypatch.setattr(
            "scripts.ci_rca.fetch_logs.subprocess.Popen",
            lambda _c, **kw: real_popen([sys.executable, "-c", dump, str(source)], **kw),
        )
        out = tmp_path / "evidence.json"
        assert fetch_run_log("1", "owner/repo", out, **limits).fetched
        return read_envelope(out)

    def test_oversized_raw_log_keeps_tail_anchor_incomplete(self, tmp_path: Path, monkeypatch) -> None:
        envelope = self._stream_raw_log(tmp_path, monkeypatch, build_log())
        assert "Failed checks:" in envelope["body"]
        assert envelope["limits"]["complete"] is False

    def test_fitting_raw_log_is_complete(self, tmp_path: Path, monkeypatch) -> None:
        envelope = self._stream_raw_log(tmp_path, monkeypatch, "short raw log\nFailed checks:\n")
        assert envelope["body"] == "short raw log\nFailed checks:\n"
        assert envelope["limits"]["complete"] is True


class TestRunLogTailOverlap:
    """rec-3002: _run_log's head plus tail never repeats or drops a character."""

    @staticmethod
    def _emit(text: str, max_bytes: int, max_lines: int = 100):
        program = f"import sys;sys.stdout.buffer.write({text.encode('utf-8')!r})"
        return _run_log([sys.executable, "-c", program], max_bytes, max_lines)

    @pytest.mark.parametrize("text, max_bytes", [("abcdefgh", 5), ("abc\u00e9", 4), ("\u20ac\u20ac", 4), ("\u00e9\u00e9", 3)])
    def test_fitting_stream_is_reconstructed_exactly(self, text: str, max_bytes: int) -> None:
        result = self._emit(text, max_bytes)
        assert result.truncated is True
        assert result.stdout + result.tail == text

    def test_invalid_byte_in_head_does_not_repeat_bytes(self) -> None:
        program = "import sys;sys.stdout.buffer.write(b'a\\xffbc\\xc3\\xa9d')"
        result = _run_log([sys.executable, "-c", program], 5, 100)
        assert result.stdout + result.tail == "abc\u00e9d"

    def test_gap_keeps_last_max_bytes(self) -> None:
        text = "abcdefghijklmnopqrstuvwxyz"
        result = self._emit(text, 4)
        assert result.stdout == "abcd" and result.tail == "wxyz"
