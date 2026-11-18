"""Tests for scripts/verify_ci_workflow.py -- ci-rca-filter guard + canary guard concern
(VERBATIM split from tests/test_verify_ci_workflow.py, rec-2709 Wave 12).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from scripts.ci_rca.log_evidence import main as log_evidence_main
from scripts.verify_ci_workflow import _check_canary, _check_ci_rca_fetch_classification, _check_ci_rca_filter

_REAL_RCA_IF = (
    "github.event_name == 'workflow_dispatch' || "
    "(github.event.workflow_run.conclusion == 'failure' "
    "&& github.event.workflow_run.head_repository.full_name == github.repository "
    "&& github.event.workflow_run.head_branch == github.event.repository.default_branch)"
)

_COMPILED_CACHE_KEY = (
    "pip-${{ hashFiles('requirements.in', 'requirements-dev.in', 'requirements.txt', 'requirements-dev.txt') }}"
)

_RCA_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci-rca.yml"
_REQUIRED_WORKFLOWS = ["CI", "Main Canary", "terraform-apply-sandbox", "rec-autoclose", "deploy-ducklake-lambdas"]

_REAL_RCA_DATA = {
    "on": {"workflow_run": {"workflows": list(_REQUIRED_WORKFLOWS), "types": ["completed"]}},
    "jobs": {"rca": {"if": _REAL_RCA_IF, "runs-on": "ubuntu-latest", "steps": []}},
}

_REAL_CANARY_DATA = {"name": "Main Canary"}

_FILED_MARKER_CONTENT = "## Step 6: Report\n\nFILED: rec-NNN or FILED: none\n"


class TestCheckCiRcaFilterPassPath:
    def test_passes_with_real_workflow_files(self) -> None:
        _check_ci_rca_filter()


class TestCheckCiRcaFilterRequiredSet:
    """PLAN-ci-rca-ops-plane-coverage: the required-membership floor closes the pre-existing
    hole where terraform-apply-sandbox sat in the filter unguarded (rec-2849). One drop-one-entry
    case per required workflow, plus a duplicate-entry case."""

    @pytest.mark.parametrize("dropped", _REQUIRED_WORKFLOWS)
    def test_fails_when_required_entry_missing(self, dropped: str) -> None:
        workflows = [w for w in _REQUIRED_WORKFLOWS if w != dropped]
        rca_data = {
            "on": {"workflow_run": {"workflows": workflows}},
            "jobs": {"rca": {"if": _REAL_RCA_IF, "steps": []}},
        }
        with (
            patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load,
            patch("scripts.verify_ci_workflow._ci_rca.Path") as mock_path,
        ):
            mock_load.side_effect = lambda p: _REAL_CANARY_DATA if "canary" in p else rca_data
            mock_path.return_value.read_text.return_value = _FILED_MARKER_CONTENT
            with pytest.raises(AssertionError, match="missing required entries"):
                _check_ci_rca_filter()

    def test_fails_on_duplicate_entry(self) -> None:
        workflows = [*_REQUIRED_WORKFLOWS, "CI"]
        rca_data = {
            "on": {"workflow_run": {"workflows": workflows}},
            "jobs": {"rca": {"if": _REAL_RCA_IF, "steps": []}},
        }
        with (
            patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load,
            patch("scripts.verify_ci_workflow._ci_rca.Path") as mock_path,
        ):
            mock_load.side_effect = lambda p: _REAL_CANARY_DATA if "canary" in p else rca_data
            mock_path.return_value.read_text.return_value = _FILED_MARKER_CONTENT
            with pytest.raises(AssertionError, match="duplicate entries"):
                _check_ci_rca_filter()


class TestCheckCiRcaFilterMainBranchGate:
    def test_fails_when_head_branch_missing(self) -> None:
        rca_data_no_gate = {
            "on": {"workflow_run": {"workflows": list(_REQUIRED_WORKFLOWS)}},
            "jobs": {
                "rca": {
                    "if": (
                        "github.event_name == 'workflow_dispatch' || "
                        "(github.event.workflow_run.conclusion == 'failure' "
                        "&& github.event.workflow_run.head_repository.full_name == github.repository)"
                    ),
                    "steps": [],
                }
            },
        }
        with (
            patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load,
            patch("scripts.verify_ci_workflow._ci_rca.Path") as mock_path,
        ):
            mock_load.side_effect = lambda p: _REAL_CANARY_DATA if "canary" in p else rca_data_no_gate
            mock_path.return_value.read_text.return_value = _FILED_MARKER_CONTENT
            with pytest.raises(AssertionError, match="head_branch"):
                _check_ci_rca_filter()

    def test_fails_when_default_branch_missing(self) -> None:
        rca_data_partial_gate = {
            "on": {"workflow_run": {"workflows": list(_REQUIRED_WORKFLOWS)}},
            "jobs": {
                "rca": {
                    "if": (
                        "github.event_name == 'workflow_dispatch' || "
                        "(github.event.workflow_run.conclusion == 'failure' "
                        "&& github.event.workflow_run.head_branch == 'main')"
                    ),
                    "steps": [],
                }
            },
        }
        with (
            patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load,
            patch("scripts.verify_ci_workflow._ci_rca.Path") as mock_path,
        ):
            mock_load.side_effect = lambda p: _REAL_CANARY_DATA if "canary" in p else rca_data_partial_gate
            mock_path.return_value.read_text.return_value = _FILED_MARKER_CONTENT
            with pytest.raises(AssertionError, match="default_branch"):
                _check_ci_rca_filter()


class TestCheckCiRcaFilterFiledMarker:
    def test_fails_when_filed_marker_missing_from_agent_doc(self) -> None:
        with (
            patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load,
            patch("scripts.verify_ci_workflow._ci_rca.Path") as mock_path,
        ):
            mock_load.side_effect = lambda p: _REAL_CANARY_DATA if "canary" in p else _REAL_RCA_DATA
            mock_path.return_value.read_text.return_value = "## Step 6: Report\n\nPrint a brief summary.\n"
            with pytest.raises(AssertionError, match="FILED:"):
                _check_ci_rca_filter()

    def test_passes_when_filed_marker_present(self) -> None:
        with (
            patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load,
            patch("scripts.verify_ci_workflow._ci_rca.Path") as mock_path,
        ):
            mock_load.side_effect = lambda p: _REAL_CANARY_DATA if "canary" in p else _REAL_RCA_DATA
            mock_path.return_value.read_text.return_value = _FILED_MARKER_CONTENT
            _check_ci_rca_filter()


_VALID_CANARY_DATA: dict[str, Any] = {
    "name": "Main Canary",
    "on": {
        "schedule": [{"cron": "0 */3 * * *"}],
        "workflow_dispatch": {},
    },
    "jobs": {
        "canary": {
            "runs-on": "ubuntu-latest",
            "steps": [
                {
                    "uses": "actions/cache@v6",
                    "with": {"key": _COMPILED_CACHE_KEY},
                },
                {"run": "pip install -r requirements.txt\npip install -r requirements-dev.txt"},
                {"run": "bin/venv-python -m scripts.validate"},
            ],
        }
    },
}


# ---------------------------------------------------------------------------
# _check_canary (CD.21: assert ubuntu-latest, not self-hosted)
# ---------------------------------------------------------------------------


class TestCheckCanaryPassPath:
    def test_passes_with_ubuntu_latest(self) -> None:
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _VALID_CANARY_DATA
            _check_canary()


class TestCheckCanaryFailPath:
    def test_fails_when_canary_still_uses_self_hosted(self) -> None:
        data = {
            "name": "Main Canary",
            "on": {
                "schedule": [{"cron": "0 */3 * * *"}],
                "workflow_dispatch": {},
            },
            "jobs": {
                "canary": {
                    "runs-on": ["self-hosted", "linux"],
                    "steps": [{"run": "bin/venv-python -m scripts.validate"}],
                }
            },
        }
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = data
            with pytest.raises(AssertionError, match="ubuntu-latest"):
                _check_canary()

    def test_fails_when_canary_steps_reference_pre(self) -> None:
        data = {
            "name": "Main Canary",
            "on": {
                "schedule": [{"cron": "0 */3 * * *"}],
                "workflow_dispatch": {},
            },
            "jobs": {
                "canary": {
                    "runs-on": "ubuntu-latest",
                    "steps": [{"run": "bin/venv-python -m scripts.validate --pre"}],
                }
            },
        }
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = data
            with pytest.raises(AssertionError, match="--pre"):
                _check_canary()


# ---------------------------------------------------------------------------
# _check_ci_rca_fetch_classification (rec-2857 forward fix -- ALLOWLIST guard)
# ---------------------------------------------------------------------------

_PASSING_FETCH_STEP_BODY = (
    'RUN_ID="${{ steps.meta.outputs.run_id }}"\n'
    'bin/venv-python -m scripts.ci_rca.fetch_logs --run-id "$RUN_ID" '
    '--repo "${{ github.repository }}" --out /tmp/ci-rca-log-evidence.json\n'
    "bin/venv-python -m scripts.ci_rca.log_evidence --validate /tmp/ci-rca-log-evidence.json "
    "--extract-body /tmp/ci-rca-failed.log\n"
    "test -s /tmp/ci-rca-log-evidence.json\n"
    "test -s /tmp/ci-rca-failed.log\n"
)

_EXPECTED_FETCH_ENV = {"GH_TOKEN": "${{ github.token }}"}
_AUTHORITY_COMMENT = (
    "      # Decision 72 is provenance for workflow_run-triggered CI-RCA failed-run log retrieval. "
    "`test -s` below is a local\n"
    "      # YAML-side fail-closed guard.\n"
)
_AUTHORITY_WORKFLOW = _AUTHORITY_COMMENT + "      - name: Fetch failed run logs\n"
_DECISION_72_ENTRY = """## Decision 72: RCA-as-Plan-Source for CI Merge Gate Failures (Decided)

On CI failure, a `workflow_run`-triggered GitHub Actions workflow
(`.github/workflows/ci-rca.yml`) reads the failed run logs via `gh run view`.

"""
_AUTHORITY_DECISIONS = _DECISION_72_ENTRY + "## Decision 70: Next boundary (Decided)\n"
_REAL_AUTHORITY_SOURCES = (
    Path(".github/workflows/ci-rca.yml").read_text(encoding="utf-8"),
    Path("docs/DECISIONS.md").read_text(encoding="utf-8"),
)


def _rca_data_with_fetch_step(run_body: str) -> dict[str, Any]:
    return {
        "jobs": {
            "rca": {
                "steps": [
                    {"name": "Resolve run metadata"},
                    {"name": "Fetch failed run logs", "run": run_body},
                    {"name": "Fetch jobs JSON"},
                    {
                        "name": "Generate evidence bundle",
                        "run": "bin/venv-python -m scripts.ci_rca.evidence --retrieval-envelope /tmp/ci-rca-log-evidence.json",
                    },
                ]
            }
        }
    }


class TestCiRcaAuthorityAnchor:
    def _check(self, workflow: str = _AUTHORITY_WORKFLOW, decisions: str = _AUTHORITY_DECISIONS) -> None:
        with (
            patch("scripts.verify_ci_workflow._ci_rca._read_ci_rca_authority_sources", return_value=(workflow, decisions)),
            patch(
                "scripts.verify_ci_workflow._ci_rca._load", return_value=_rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY)
            ),
        ):
            _check_ci_rca_fetch_classification()

    def test_production_step_runtime_is_unchanged(self) -> None:
        data = yaml.safe_load(Path(".github/workflows/ci-rca.yml").read_text(encoding="utf-8"))
        step = next(step for step in data["jobs"]["rca"]["steps"] if step.get("name") == "Fetch failed run logs")
        assert step["env"] == _EXPECTED_FETCH_ENV
        assert step["run"] == _PASSING_FETCH_STEP_BODY
        _check_ci_rca_fetch_classification()

    def test_accepts_adjacent_retrieval_provenance_and_bounded_decision(self) -> None:
        self._check()

    @pytest.mark.parametrize(
        ("workflow", "match"),
        [
            (_AUTHORITY_WORKFLOW.replace("Decision 72", "Decision 143"), "Decision 72 only"),
            (_AUTHORITY_WORKFLOW.replace("Decision 72 is provenance", "retrieval provenance"), "Decision 72 only"),
            (_AUTHORITY_WORKFLOW.replace("a local\n      # YAML-side", "a\n      # YAML-side"), "local YAML-side"),
            (_AUTHORITY_WORKFLOW.replace("is provenance", "mitigation"), "Decision 72 only"),
            (_AUTHORITY_WORKFLOW.replace("is provenance", "guard"), "Decision 72 only"),
            (
                "      # Decision 72 is provenance for workflow_run-triggered CI-RCA failed-run log retrieval.\n\n"
                + _AUTHORITY_WORKFLOW.split("\n", 2)[2],
                "immediately adjacent",
            ),
            (_AUTHORITY_WORKFLOW + "      - name: Fetch failed run logs\n", "missing or duplicated"),
        ],
    )
    def test_rejects_invalid_or_nonlocal_workflow_authority(self, workflow: str, match: str) -> None:
        with pytest.raises(AssertionError, match=match):
            self._check(workflow=workflow)

    def test_remote_matching_comment_cannot_satisfy_locality(self) -> None:
        workflow = (
            _AUTHORITY_COMMENT
            + "      - name: Other step\n\n      # local unrelated comment\n      - name: Fetch failed run logs\n"
        )
        with pytest.raises(AssertionError, match="Decision 72 only"):
            self._check(workflow=workflow)

    @pytest.mark.parametrize(
        ("decisions", "match"),
        [
            (_AUTHORITY_DECISIONS.replace("## Decision 72:", "## Decision seventy-two:"), "exactly one"),
            (_AUTHORITY_DECISIONS + _DECISION_72_ENTRY, "exactly one"),
            (_DECISION_72_ENTRY, "no following"),
            (_AUTHORITY_DECISIONS.replace("failed run logs", "diagnostic material"), "does not establish"),
            (
                "## Decision 72: unrelated\n\nNo retrieval authority.\n\n## Decision 71: boundary\n"
                + _DECISION_72_ENTRY.replace("## Decision 72:", "## Decision 70:")
                + "## Decision 69: boundary\n",
                "does not establish",
            ),
        ],
    )
    def test_rejects_missing_duplicate_malformed_or_wrong_decision_entry(self, decisions: str, match: str) -> None:
        with pytest.raises(AssertionError, match=match):
            self._check(decisions=decisions)


class TestCheckCiRcaFetchClassification:
    def test_passes_on_real_post_change_step_body(self) -> None:
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY)
            _check_ci_rca_fetch_classification()

    def test_rejects_literal_historical_grep(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + (
            "TRANSIENT_ERROR_RE='failed to get jobs|failed to get run|HTTP 5[0-9][0-9]|Service Unavailable'\n"
            'grep -qiE "$TRANSIENT_ERROR_RE" /tmp/ci-rca-failed.log\n'
        )
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="pattern-matching construct"):
                _check_ci_rca_fetch_classification()

    def test_rejects_renamed_pattern_variable(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + ("PATTERN_X='HTTP 5[0-9][0-9]'\ngrep -qiE \"$PATTERN_X\" /tmp/ci-rca-failed.log\n")
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="pattern-matching construct"):
                _check_ci_rca_fetch_classification()

    def test_rejects_renamed_log_path_variable(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + ('LOGFILE=/tmp/ci-rca-failed.log\ngrep -qiE "HTTP 5" "$LOGFILE"\n')
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="pattern-matching construct"):
                _check_ci_rca_fetch_classification()

    def test_rejects_case_statement_shape_a_blocklist_would_miss(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + ('case "$(cat /tmp/ci-rca-failed.log)" in\n  *"HTTP 5"*) exit 1 ;;\nesac\n')
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="pattern-matching construct"):
                _check_ci_rca_fetch_classification()

    def test_rejects_awk_shape_a_blocklist_would_miss(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + ("awk '/HTTP 5/{exit 1}' /tmp/ci-rca-failed.log\n")
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="pattern-matching construct"):
                _check_ci_rca_fetch_classification()

    def test_rejects_inline_python_c_shape_a_blocklist_would_miss(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + (
            "python3 -c \"import re,sys; sys.exit(1 if re.search('HTTP 5', open('/tmp/ci-rca-failed.log').read()) else 0)\"\n"
        )
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="pattern-matching construct"):
                _check_ci_rca_fetch_classification()

    def test_rejects_missing_module_invocation(self) -> None:
        body = (
            'RUN_ID="${{ steps.meta.outputs.run_id }}"\n'
            'echo "no module call here" > /tmp/ci-rca-failed.log\n'
            "test -s /tmp/ci-rca-failed.log\n"
        )
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="no longer invokes scripts.ci_rca.fetch_logs"):
                _check_ci_rca_fetch_classification()

    @pytest.mark.parametrize(
        "mutation,match",
        [
            ("scripts.ci_rca.log_evidence --validate", "bounded retrieval envelope"),
            ("--extract-body /tmp/ci-rca-failed.log", "not extracted"),
            ("test -s /tmp/ci-rca-log-evidence.json", "envelope false-green"),
            ("test -s /tmp/ci-rca-failed.log", "body false-green"),
        ],
    )
    def test_rejects_removed_bounded_retrieval_backstop(self, mutation: str, match: str) -> None:
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY.replace(mutation, "removed"))
            with pytest.raises(AssertionError, match=match):
                _check_ci_rca_fetch_classification()

    def test_rejects_historical_uncapped_whole_run_fallback(self) -> None:
        body = _PASSING_FETCH_STEP_BODY + 'gh run view "$RUN_ID" --log > /tmp/ci-rca-failed.log\n'
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = _rca_data_with_fetch_step(body)
            with pytest.raises(AssertionError, match="uncapped direct log retrieval"):
                _check_ci_rca_fetch_classification()

    def test_rejects_evidence_generation_without_retrieval_envelope(self) -> None:
        data = _rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY)
        data["jobs"]["rca"]["steps"][-1]["run"] = "bin/venv-python -m scripts.ci_rca.evidence"
        with patch("scripts.verify_ci_workflow._ci_rca._load", return_value=data):
            with pytest.raises(AssertionError, match="durable evidence generation"):
                _check_ci_rca_fetch_classification()


class TestCiRcaBoundedRetrievalCounterfactuals:
    def test_plan_fast_replay_enumerates_non_live_evidence_modules(self) -> None:
        plan = yaml.safe_load(Path("docs/plans/PLAN-bounded-ci-rca-evidence.yaml").read_text(encoding="utf-8"))
        command = next(step["command"] for step in plan["verification_plan"] if step["step"] == 3)
        arguments = shlex.split(command)
        evidence_modules = {
            f"tests/ci_rca/evidence/{path.name}"
            for path in Path("tests/ci_rca/evidence").glob("test_*.py")
            if path.name != "test_live_s3.py"
        }

        assert "tests/ci_rca/evidence" not in arguments
        assert "tests/ci_rca/evidence/test_live_s3.py" not in arguments
        assert evidence_modules <= set(arguments)
        assert "boto3" not in Path("requirements-fast.txt").read_text(encoding="utf-8").splitlines()

    @pytest.mark.parametrize(
        "fragment",
        [
            "_run_log(primary_command, max_bytes, max_lines)",
            'remaining = max_bytes - len("".join(fragments).encode("utf-8"))',
            "remaining_lines = max_lines -",
            "_run_log(command, remaining, remaining_lines)",
            'selected.sort(key=lambda item: item["job_id"])',
        ],
    )
    def test_rejects_retrieval_budget_and_ordering_mutations(self, fragment: str) -> None:
        source = Path("scripts/ci_rca/fetch_logs.py").read_text(encoding="utf-8")
        with (
            patch(
                "scripts.verify_ci_workflow._ci_rca._load", return_value=_rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY)
            ),
            patch("scripts.verify_ci_workflow._ci_rca._read_ci_rca_authority_sources", return_value=_REAL_AUTHORITY_SOURCES),
            patch("pathlib.Path.read_text", return_value=source.replace(fragment, "removed", 1)),
            pytest.raises(AssertionError, match="bounded retrieval invariant"),
        ):
            _check_ci_rca_fetch_classification()

    def test_rejects_per_job_budget_reset_to_configured_maxima(self) -> None:
        source = Path("scripts/ci_rca/fetch_logs.py").read_text(encoding="utf-8")
        reset = source.replace("_run_log(command, remaining, remaining_lines)", "_run_log(command, max_bytes, max_lines)", 1)
        with (
            patch(
                "scripts.verify_ci_workflow._ci_rca._load", return_value=_rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY)
            ),
            patch("scripts.verify_ci_workflow._ci_rca._read_ci_rca_authority_sources", return_value=_REAL_AUTHORITY_SOURCES),
            patch("pathlib.Path.read_text", return_value=reset),
            pytest.raises(AssertionError, match="bounded retrieval invariant"),
        ):
            _check_ci_rca_fetch_classification()

    @pytest.mark.parametrize(
        "fragment",
        [
            "scripts.ci_rca.log_evidence --validate",
            "--extract-body /tmp/ci-rca-failed.log",
        ],
    )
    def test_rejects_malformed_envelope_body_boundary(self, fragment: str) -> None:
        with patch(
            "scripts.verify_ci_workflow._ci_rca._load",
            return_value=_rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY.replace(fragment, "removed")),
        ):
            with pytest.raises(AssertionError):
                _check_ci_rca_fetch_classification()

    def test_rejects_missing_durable_evidence_argument(self) -> None:
        data = _rca_data_with_fetch_step(_PASSING_FETCH_STEP_BODY)
        data["jobs"]["rca"]["steps"][-1]["run"] = "bin/venv-python -m scripts.ci_rca.evidence"
        with patch("scripts.verify_ci_workflow._ci_rca._load", return_value=data):
            with pytest.raises(AssertionError, match="durable evidence generation"):
                _check_ci_rca_fetch_classification()

    def test_real_boundary_rejects_nonempty_malformed_envelope(self, tmp_path: Path) -> None:
        envelope = tmp_path / "malformed.json"
        extracted = tmp_path / "body.log"
        envelope.write_text(json.dumps({"schema": "ci-rca-log-evidence/v1", "body": "nonempty"}), encoding="utf-8")
        assert log_evidence_main(["--validate", str(envelope), "--extract-body", str(extracted)]) == 1
        assert not extracted.exists()

    def test_fails_when_step_not_found(self) -> None:
        rca_data = {"jobs": {"rca": {"steps": [{"name": "Some other step", "run": "echo hi"}]}}}
        with patch("scripts.verify_ci_workflow._ci_rca._load") as mock_load:
            mock_load.return_value = rca_data
            with pytest.raises(AssertionError, match="not found in job"):
                _check_ci_rca_fetch_classification()


def test_rejected_deployment_is_classified_before_the_log_fetch_and_fails_closed() -> None:
    doc = yaml.safe_load(_RCA_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["rca"]["steps"]
    names = [s.get("name") for s in steps]
    fetch, jobs, rej, refusal = (
        names.index(n)
        for n in (
            "Fetch failed run logs",
            "Fetch jobs JSON",
            "Rejected-deployment check",
            "Designed-refusal check (fail-closed skip predicate)",
        )
    )
    assert jobs < rej < fetch < refusal
    assert steps[fetch]["if"] == "steps.rejected_deployment.outputs.skip != 'true'"
    assert '|| OUTPUT="skip=false"' in steps[rej]["run"] and "--rejected-deployment-only" in steps[rej]["run"]
    assert "continue-on-error" not in steps[rej] and "--rejected-deployment-only" not in steps[refusal]["run"]
    assert "deploy-telemetry-content" in doc.get("on", doc.get(True))["workflow_run"]["workflows"]
