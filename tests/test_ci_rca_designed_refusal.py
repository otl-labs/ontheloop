"""Unit tests for scripts.ci_rca.designed_refusal (100% coverage).

Covers the fail-closed predicate's four behaviours (PLAN-ci-rca-ops-plane-coverage VP step 1):
skip when only a declared designed-refusal job failed; do NOT skip when an undeclared job also
failed; do NOT skip on an empty job set; do NOT skip on a `{}` or unparseable jobs file.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from scripts.ci_rca.designed_refusal import (
    DESIGNED_REFUSAL_JOBS,
    PERSONA_GATE_STEP_PREFIX,
    _load_jobs_payload,
    is_designed_refusal,
    is_persona_gate_refusal,
    main,
    resolve_failing_jobs,
)


class TestResolveFailingJobs:
    def test_single_failing_job(self) -> None:
        payload = {"jobs": [{"name": "reconcile-gate", "conclusion": "failure"}]}
        assert resolve_failing_jobs(payload) == {"reconcile-gate"}

    def test_ignores_non_failing_jobs(self) -> None:
        payload = {
            "jobs": [
                {"name": "reconcile-gate", "conclusion": "failure"},
                {"name": "deploy-maintenance", "conclusion": "success"},
            ]
        }
        assert resolve_failing_jobs(payload) == {"reconcile-gate"}

    def test_empty_dict_yields_empty_set(self) -> None:
        assert resolve_failing_jobs({}) == set()

    def test_non_dict_payload_yields_empty_set(self) -> None:
        assert resolve_failing_jobs([]) == set()  # type: ignore[arg-type]

    def test_non_list_jobs_key_yields_empty_set(self) -> None:
        assert resolve_failing_jobs({"jobs": "not-a-list"}) == set()

    def test_non_dict_job_entry_ignored(self) -> None:
        payload = {"jobs": ["not-a-dict", {"name": "reconcile-gate", "conclusion": "failure"}]}
        assert resolve_failing_jobs(payload) == {"reconcile-gate"}

    def test_missing_name_ignored(self) -> None:
        payload = {"jobs": [{"conclusion": "failure"}]}
        assert resolve_failing_jobs(payload) == set()


class TestIsDesignedRefusal:
    def test_skips_when_only_declared_job_failed(self) -> None:
        payload = {"jobs": [{"name": "reconcile-gate", "conclusion": "failure"}]}
        assert is_designed_refusal("deploy-ducklake-lambdas", payload) is True

    def test_does_not_skip_when_undeclared_job_also_failed(self) -> None:
        payload = {
            "jobs": [
                {"name": "reconcile-gate", "conclusion": "failure"},
                {"name": "deploy-maintenance", "conclusion": "failure"},
            ]
        }
        assert is_designed_refusal("deploy-ducklake-lambdas", payload) is False

    def test_does_not_skip_on_empty_job_set(self) -> None:
        payload = {"jobs": []}
        assert is_designed_refusal("deploy-ducklake-lambdas", payload) is False

    def test_does_not_skip_on_empty_payload(self) -> None:
        assert is_designed_refusal("deploy-ducklake-lambdas", {}) is False

    def test_does_not_skip_for_undeclared_slug(self) -> None:
        payload = {"jobs": [{"name": "reconcile-gate", "conclusion": "failure"}]}
        assert is_designed_refusal("rec-autoclose", payload) is False

    def test_does_not_skip_when_only_undeclared_job_failed(self) -> None:
        payload = {"jobs": [{"name": "some-other-job", "conclusion": "failure"}]}
        assert is_designed_refusal("deploy-ducklake-lambdas", payload) is False


class TestLoadJobsPayload:
    def test_reads_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps({"jobs": [{"name": "x", "conclusion": "failure"}]}), encoding="utf-8")
        assert _load_jobs_payload(p) == {"jobs": [{"name": "x", "conclusion": "failure"}]}

    def test_empty_object_file(self, tmp_path: Path) -> None:
        p = tmp_path / "jobs.json"
        p.write_text("{}", encoding="utf-8")
        assert _load_jobs_payload(p) == {}

    def test_unparseable_file_returns_empty_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "jobs.json"
        p.write_text("not json at all {{{", encoding="utf-8")
        assert _load_jobs_payload(p) == {}

    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert _load_jobs_payload(tmp_path / "does-not-exist.json") == {}

    def test_non_dict_json_returns_empty_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "jobs.json"
        p.write_text("[]", encoding="utf-8")
        assert _load_jobs_payload(p) == {}


class TestMain:
    def test_prints_skip_true_for_declared_job_only(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps({"jobs": [{"name": "reconcile-gate", "conclusion": "failure"}]}), encoding="utf-8")
        rc = main(["--jobs-json", str(p), "--workflow-slug", "deploy-ducklake-lambdas"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "skip=true"

    def test_prints_skip_false_when_undeclared_job_also_failed(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(
            json.dumps(
                {
                    "jobs": [
                        {"name": "reconcile-gate", "conclusion": "failure"},
                        {"name": "deploy-maintenance", "conclusion": "failure"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc = main(["--jobs-json", str(p), "--workflow-slug", "deploy-ducklake-lambdas"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "skip=false"

    def test_prints_skip_false_on_empty_job_set(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps({"jobs": []}), encoding="utf-8")
        rc = main(["--jobs-json", str(p), "--workflow-slug", "deploy-ducklake-lambdas"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "skip=false"

    def test_prints_skip_false_on_unparseable_jobs_file(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "jobs.json"
        p.write_text("{{{not json", encoding="utf-8")
        rc = main(["--jobs-json", str(p), "--workflow-slug", "deploy-ducklake-lambdas"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "skip=false"

    def test_prints_skip_false_on_empty_object_jobs_file(self, tmp_path: Path, capsys) -> None:
        p = tmp_path / "jobs.json"
        p.write_text("{}", encoding="utf-8")
        rc = main(["--jobs-json", str(p), "--workflow-slug", "deploy-ducklake-lambdas"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "skip=false"


class TestDeclaredJobsMapping:
    def test_only_deploy_ducklake_lambdas_declared(self) -> None:
        assert dict(DESIGNED_REFUSAL_JOBS) == {"deploy-ducklake-lambdas": frozenset({"reconcile-gate"})}


class TestCliMain:
    # This module is already imported at collection time (for the direct-call tests above), so
    # it is already in sys.modules by the time runpy re-executes it below -- the standard, benign
    # runpy caveat for this pattern (docs.python.org/3/library/runpy.html).
    @pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
    def test_runpy_main_prints_skip_and_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps({"jobs": [{"name": "reconcile-gate", "conclusion": "failure"}]}), encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            ["designed_refusal", "--jobs-json", str(p), "--workflow-slug", "deploy-ducklake-lambdas"],
        )
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("scripts.ci_rca.designed_refusal", run_name="__main__")
        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == "skip=true"


_GATE_STEP = {"name": f"{PERSONA_GATE_STEP_PREFIX} (Decision 205 cl.1)", "conclusion": "failure"}
_MARKER_LOG = "x\nPERSONA_DISPATCH_REFUSED: dispatch by sender.type=Bot\n"


def _gate_jobs(steps: object = None, **job: object) -> dict:
    job_obj = {"name": "apply-sandbox", "conclusion": "failure", "steps": [_GATE_STEP] if steps is None else steps}
    job_obj.update(job)
    return {"jobs": [job_obj]}


class TestPersonaGateRefusal:
    def test_sole_failing_gate_step_with_marker_skips(self) -> None:
        assert is_persona_gate_refusal(_gate_jobs(), _MARKER_LOG)

    def test_in_progress_job_with_failed_gate_step_skips(self) -> None:
        assert is_persona_gate_refusal(_gate_jobs(conclusion=None, status="in_progress"), _MARKER_LOG)

    def test_gate_error_without_marker_files(self) -> None:
        assert not is_persona_gate_refusal(_gate_jobs(), "PERSONA_DISPATCH_GATE_ERROR: RuntimeError")

    def test_unreadable_log_files(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps(_gate_jobs()), encoding="utf-8")
        argv = ["--jobs-json", str(p), "--workflow-slug", "terraform-apply-sandbox"]
        assert main([*argv, "--failed-log", str(tmp_path / "missing.log")]) == 0
        assert capsys.readouterr().out.strip() == "skip=false"

    def test_cli_skips_with_marker_log(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps(_gate_jobs()), encoding="utf-8")
        log = tmp_path / "failed.log"
        log.write_text(_MARKER_LOG, encoding="utf-8")
        assert main(["--jobs-json", str(p), "--workflow-slug", "terraform-apply-sandbox", "--failed-log", str(log)]) == 0
        assert capsys.readouterr().out.strip() == "skip=true"

    def test_gate_step_plus_another_failed_step_files(self) -> None:
        steps = [_GATE_STEP, {"name": "Apply", "conclusion": "failure"}]
        assert not is_persona_gate_refusal(_gate_jobs(steps), _MARKER_LOG)

    def test_missing_steps_array_files(self) -> None:
        payload = {"jobs": [{"name": "apply-sandbox", "conclusion": "failure"}]}
        assert not is_persona_gate_refusal(payload, _MARKER_LOG)

    def test_differently_named_failed_step_files(self) -> None:
        assert not is_persona_gate_refusal(_gate_jobs([{"name": "Apply", "conclusion": "failure"}]), _MARKER_LOG)

    def test_empty_failing_set_files(self) -> None:
        assert not is_persona_gate_refusal({"jobs": [{"name": "a", "conclusion": "success", "steps": []}]}, _MARKER_LOG)
        assert not is_persona_gate_refusal({}, _MARKER_LOG)


def test_live_designed_refusal_command_uses_only_declared_arguments() -> None:
    import re

    text = Path(".github/workflows/ci-rca.yml").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "scripts.ci_rca.designed_refusal" in ln)
    main_text = Path("scripts/ci_rca/designed_refusal.py").read_text(encoding="utf-8")
    declared = {"--jobs-json", "--workflow-slug"}
    assert set(re.findall(r"--[a-z-]+", line)) <= declared
    for arg in declared:
        assert f'"{arg}"' in main_text


def test_persona_gate_refusal_ignores_non_dict_job_entries() -> None:
    payload = _gate_jobs()
    payload["jobs"].insert(0, "not-a-job")
    assert is_persona_gate_refusal(payload, _MARKER_LOG)
