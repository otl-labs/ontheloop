"""egress_fail_closed_verdict: passes only on the hooked cut-off shape with the control's visible failure (network faked)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from scripts.personas import egress_fail_closed_verdict as V


def _job(name: str, conclusion: str | None, **steps: str | None) -> dict[str, Any]:
    return {
        "name": name,
        "conclusion": conclusion,
        "steps": [{"name": k.replace("_", "-"), "conclusion": v} for k, v in steps.items()],
    }


def _hooked(**over: str | None) -> dict[str, Any]:
    steps: dict[str, str | None] = {
        "drill_floor": "success",
        "drill_self_check": "success",
        "drill_fail_closed": None,
        "drill_final_check": None,
    }
    steps.update(over)
    return _job("egress-fail-closed", "failure", **steps)


def _control(**over: str | None) -> dict[str, Any]:
    steps: dict[str, str | None] = {
        "drill_floor": "success",
        "drill_self_check": "success",
        "drill_fail_closed": "failure",
        "drill_final_check": "success",
    }
    steps.update(over)
    return _job("egress-fail-closed-control", "failure", **steps)


def _run(monkeypatch: pytest.MonkeyPatch, jobs: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    monkeypatch.setattr(V, "fetch_jobs", lambda: jobs)
    code = V.main([])
    return code, capsys.readouterr().out


def test_passes_on_the_cut_off_hooked_job_and_the_visibly_failed_control(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(monkeypatch, [_hooked(), _control()], capsys)
    assert code == 0 and out.rstrip().endswith("EGRESS-FAIL-CLOSED-VERDICT PASS")
    assert out.count("EGRESS-FAIL-CLOSED-VERDICT observed job=") == 2 and "drill-fail-closed=failure" in out


@pytest.mark.parametrize(
    ("jobs", "text"),
    [
        ([_hooked(drill_fail_closed="success"), _control()], "egress-fail-closed drill-fail-closed concluded success"),
        ([_hooked(drill_fail_closed="failure"), _control()], "drill-fail-closed concluded failure"),
        ([_hooked(drill_final_check="success"), _control()], "kept reporting"),
        ([_hooked(drill_floor="failure"), _control()], "drill-floor not success"),
        ([_hooked(), _control(drill_fail_closed="success")], "did not fail visibly"),
        ([_hooked(), _control(drill_final_check=None)], "drill-final-check never concluded"),
        ([_hooked(), _control(drill_self_check="failure")], "drill-self-check not success"),
        ([_control()], "egress-fail-closed missing"),
        ([_hooked()], "egress-fail-closed-control missing"),
    ],
)
def test_refuses_each_other_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], jobs: list[dict[str, Any]], text: str
) -> None:
    code, out = _run(monkeypatch, jobs, capsys)
    assert code == 1 and "EGRESS-FAIL-CLOSED-VERDICT FAIL" in out and text in out


def test_a_green_hooked_job_is_refused(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    hooked = _hooked()
    hooked["conclusion"] = "success"
    code, out = _run(monkeypatch, [hooked, _control()], capsys)
    assert code == 1 and "concluded success" in out


def test_fetch_jobs_reads_the_attempt_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {"GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "2", "GH_TOKEN": "t"}.items():
        monkeypatch.setenv(key, value)
    seen: list[Any] = []

    def opener(request: Any, timeout: int) -> io.BytesIO:
        seen.append(request)
        return io.BytesIO(json.dumps({"jobs": [{"name": "a"}, "junk"]}).encode())

    monkeypatch.setattr(V.urllib.request, "urlopen", opener)
    assert V.fetch_jobs() == [{"name": "a"}]
    assert seen[0].full_url == "https://api.github.com/repos/o/r/actions/runs/7/attempts/2/jobs?per_page=100"
    assert seen[0].get_header("Authorization") == "Bearer t"
