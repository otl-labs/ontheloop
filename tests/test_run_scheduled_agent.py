"""Unit tests for scripts/run_scheduled_agent.py."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

import scripts.run_scheduled_agent as sched_mod
from scripts.run_scheduled_agent import (
    _match_cron_field,
    _smoke_test,
    _trigger_lambda,
    is_agent_due,
    load_manifest,
    main,
    parse_findings,
    run_agent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_MANIFEST = {
    "agents": [
        {
            "name": "doc-freshness",
            "cron": "0 6 * * 1",
            "model": "gpt-4.1-mini",
            "prompt_path": ".github/prompts/scheduled/doc-freshness.prompt.md",
            "description": "Freshness check",
        },
        {
            "name": "orphan-code",
            "cron": "0 6 * * 2",
            "model": "gpt-4.1-mini",
            "prompt_path": ".github/prompts/scheduled/orphan-code.prompt.md",
            "description": "Orphan code check",
        },
    ]
}


@pytest.fixture()
def manifest_file(tmp_path: Path) -> Path:
    """Write a valid manifest YAML to a temp file."""
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(VALID_MANIFEST), encoding="utf-8")
    return p


@pytest.fixture()
def prompt_file(tmp_path: Path) -> Path:
    """Create a minimal prompt file."""
    p = tmp_path / "agent.prompt.md"
    p.write_text("# Test Agent\nDo something.", encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _hermetic_logging_and_spawn(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # An earlier test on the same worker can leave logging.disable(CRITICAL) set, which empties caplog (rec-4553).
    previous = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    # pytest.fail raises a BaseException, so the product's broad `except Exception` cannot swallow an unmocked spawn.
    monkeypatch.setattr(sched_mod.subprocess, "Popen", MagicMock(side_effect=lambda *a, **k: pytest.fail("unmocked Popen")))
    yield
    logging.disable(previous)


def _invoke_writing(body: str | None) -> Callable[..., MagicMock]:
    """subprocess.run stand-in for `aws lambda invoke`: writes *body* to the outfile, or deletes it when None."""

    def _run(cmd: list[str], **_kwargs: Any) -> MagicMock:
        if body is None:
            os.unlink(cmd[-1])
        else:
            Path(cmd[-1]).write_text(body, encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    return _run


def _smoke_with_s3(stdout: str, returncode: int = 0) -> int:
    deploy = MagicMock(returncode=0, stdout="", stderr="")
    s3 = MagicMock(returncode=returncode, stdout=stdout, stderr="s3 error" if returncode else "")
    with (
        patch.object(sched_mod.subprocess, "run", side_effect=[deploy, s3]) as mock_run,
        patch.object(sched_mod, "_trigger_lambda", return_value=0) as mock_trigger,
    ):
        rc = _smoke_test("doc-freshness")
    mock_trigger.assert_called_once_with("doc-freshness")
    assert "list-objects-v2" in mock_run.call_args.args[0]
    return rc


# ---------------------------------------------------------------------------
# TestMatchCronField
# ---------------------------------------------------------------------------


class TestMatchCronField:
    def test_wildcard_always_matches(self) -> None:
        assert _match_cron_field("*", 0, 0, 59) is True
        assert _match_cron_field("*", 59, 0, 59) is True

    def test_exact_match(self) -> None:
        assert _match_cron_field("6", 6, 0, 23) is True
        assert _match_cron_field("6", 7, 0, 23) is False

    def test_comma_separated(self) -> None:
        assert _match_cron_field("1,3,5", 3, 1, 7) is True
        assert _match_cron_field("1,3,5", 2, 1, 7) is False

    def test_invalid_field_returns_false(self) -> None:
        assert _match_cron_field("abc", 6, 0, 23) is False


# ---------------------------------------------------------------------------
# TestIsAgentDue
# ---------------------------------------------------------------------------


class TestIsAgentDue:
    def _make_agent(self, cron: str) -> dict:
        return {"name": "test", "cron": cron}

    def test_matches_exact_time(self) -> None:
        # Monday (isoweekday=1), 06:00 UTC
        now = datetime(2026, 4, 6, 6, 0, tzinfo=timezone.utc)  # April 6 2026 = Monday
        agent = self._make_agent("0 6 * * 1")
        assert is_agent_due(agent, now) is True

    def test_no_match_wrong_hour(self) -> None:
        now = datetime(2026, 4, 6, 7, 0, tzinfo=timezone.utc)  # 07:00, not 06:00
        agent = self._make_agent("0 6 * * 1")
        assert is_agent_due(agent, now) is False

    def test_no_match_wrong_day(self) -> None:
        now = datetime(2026, 4, 7, 6, 0, tzinfo=timezone.utc)  # Tuesday, not Monday
        agent = self._make_agent("0 6 * * 1")
        assert is_agent_due(agent, now) is False

    def test_wildcard_day_matches_any_day(self) -> None:
        now = datetime(2026, 4, 6, 6, 0, tzinfo=timezone.utc)
        agent = self._make_agent("0 6 * * *")
        assert is_agent_due(agent, now) is True

    def test_malformed_cron_returns_false(self) -> None:
        now = datetime(2026, 4, 6, 6, 0, tzinfo=timezone.utc)
        agent = self._make_agent("0 6 * *")  # only 4 fields
        assert is_agent_due(agent, now) is False


# ---------------------------------------------------------------------------
# TestLoadManifest
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_loads_valid_manifest(self, manifest_file: Path) -> None:
        agents = load_manifest(manifest_file)
        assert len(agents) == 2
        assert agents[0]["name"] == "doc-freshness"

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        agents = load_manifest(tmp_path / "missing.yaml")
        assert agents == []

    def test_returns_empty_for_empty_agents_key(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text(yaml.dump({"agents": []}), encoding="utf-8")
        agents = load_manifest(p)
        assert agents == []


# ---------------------------------------------------------------------------
# TestRunAgent
# ---------------------------------------------------------------------------


class TestRunAgent:
    def _make_agent(self, prompt_path: str) -> dict:
        return {
            "name": "test-agent",
            "cron": "0 6 * * 1",
            "model": "gpt-4.1-mini",
            "prompt_path": prompt_path,
            "description": "Test agent",
        }

    def test_dry_run_returns_true(self, prompt_file: Path) -> None:
        agent = self._make_agent(prompt_file.name)
        with patch.object(sched_mod, "_REPO_ROOT", prompt_file.parent):
            result = run_agent(agent, dry_run=True)
        assert result is True

    def test_missing_prompt_returns_false(self, tmp_path: Path) -> None:
        agent = self._make_agent("nonexistent/path.md")
        with patch.object(sched_mod, "_REPO_ROOT", tmp_path):
            result = run_agent(agent)
        assert result is False

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        agent = self._make_agent("../../../etc/passwd")
        with patch.object(sched_mod, "_REPO_ROOT", tmp_path):
            result = run_agent(agent)
        assert result is False

    def test_live_invocation_retired_returns_false(
        self, tmp_path: Path, prompt_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # CD.28 retired the local direct (Bedrock) inference path; live
        # invocation must fail loudly and point at --trigger-lambda.
        agent = self._make_agent(prompt_file.name)

        with (
            patch.object(sched_mod, "_REPO_ROOT", prompt_file.parent),
            patch("scripts.ops_data_portal.enqueue_findings") as mock_enqueue,
        ):
            result = run_agent(agent)

        assert result is False
        assert mock_enqueue.call_count == 0
        assert "retired per CD.28" in caplog.text
        assert "--trigger-lambda" in caplog.text

    def test_model_override_still_resolves_before_retirement_error(self, tmp_path: Path, prompt_file: Path) -> None:
        # The override is resolved during validation (dry-run shows it);
        # the live path still returns False under retirement.
        agent = self._make_agent(prompt_file.name)
        with (
            patch.object(sched_mod, "_REPO_ROOT", prompt_file.parent),
            patch.object(sched_mod, "_MODEL_OVERRIDE", "override-model"),
        ):
            assert run_agent(agent, dry_run=True) is True
            assert run_agent(agent) is False

    def test_disabled_agents_skipped(self) -> None:
        agent = self._make_agent("whatever.md")
        agent["enabled"] = False
        result = run_agent(agent)
        assert result is True


# ---------------------------------------------------------------------------
# TestMain
# ---------------------------------------------------------------------------


class TestMain:
    def test_list_flag_returns_zero(self, manifest_file: Path) -> None:
        with patch("scripts.run_scheduled_agent.load_manifest", return_value=VALID_MANIFEST["agents"]):
            exit_code = main(["--list"])
        assert exit_code == 0

    def test_list_empty_manifest_returns_zero(self) -> None:
        with patch("scripts.run_scheduled_agent.load_manifest", return_value=[]):
            exit_code = main(["--list"])
        assert exit_code == 0

    def test_agent_not_found_returns_one(self) -> None:
        with patch("scripts.run_scheduled_agent.load_manifest", return_value=VALID_MANIFEST["agents"]):
            exit_code = main(["--agent", "nonexistent"])
        assert exit_code == 1

    def test_agent_dry_run_returns_zero(self, prompt_file: Path) -> None:
        agents = [
            {
                "name": "doc-freshness",
                "cron": "0 6 * * 1",
                "model": "gpt-4.1-mini",
                "prompt_path": prompt_file.name,
                "description": "Test",
            }
        ]
        with (
            patch("scripts.run_scheduled_agent.load_manifest", return_value=agents),
            patch.object(sched_mod, "_REPO_ROOT", prompt_file.parent),
        ):
            exit_code = main(["--agent", "doc-freshness", "--dry-run"])
        assert exit_code == 0

    def test_due_no_agents_returns_zero(self) -> None:
        now = datetime(2026, 4, 6, 12, 30, tzinfo=timezone.utc)  # No agents due
        with (
            patch("scripts.run_scheduled_agent.load_manifest", return_value=VALID_MANIFEST["agents"]),
            patch("scripts.run_scheduled_agent.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exit_code = main(["--due"])
        assert exit_code == 0

    def test_no_flags_returns_one(self) -> None:
        with patch("scripts.run_scheduled_agent.load_manifest", return_value=[]):
            exit_code = main([])
        assert exit_code == 1

    def test_agent_invalid_name_returns_one(self) -> None:
        with patch("scripts.run_scheduled_agent.load_manifest", return_value=VALID_MANIFEST["agents"]):
            exit_code = main(["--agent", "../etc/passwd"])
        assert exit_code == 1

    def test_agent_invalid_name_with_spaces_returns_one(self) -> None:
        with patch("scripts.run_scheduled_agent.load_manifest", return_value=VALID_MANIFEST["agents"]):
            exit_code = main(["--agent", "my agent"])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# TestCronRangeValidation
# ---------------------------------------------------------------------------


class TestCronRangeValidation:
    def test_value_below_min_returns_false(self) -> None:
        assert _match_cron_field("*", -1, 0, 59) is False

    def test_value_above_max_returns_false(self) -> None:
        assert _match_cron_field("*", 60, 0, 59) is False

    def test_exact_value_above_max_returns_false(self) -> None:
        assert _match_cron_field("99", 99, 0, 59) is False

    def test_boundary_min_matches(self) -> None:
        assert _match_cron_field("0", 0, 0, 59) is True

    def test_boundary_max_matches(self) -> None:
        assert _match_cron_field("59", 59, 0, 59) is True


# ---------------------------------------------------------------------------
# TestRealManifest
# ---------------------------------------------------------------------------


class TestRealManifest:
    def test_real_manifest_loads_and_has_required_keys(self) -> None:
        """Integration test: real schedule.yaml must load and have all required agent keys."""
        agents = load_manifest()
        assert len(agents) >= 4, f"Expected at least 4 agents, got {len(agents)}"
        required_keys = {"name", "cron", "model", "prompt_path", "description"}
        for agent in agents:
            missing = required_keys - set(agent.keys())
            assert not missing, f"Agent {agent.get('name', '?')} is missing keys: {missing}"

    def test_real_manifest_agent_names_are_valid(self) -> None:
        """Agent names must match [a-z0-9-]+ pattern used in name validation."""
        import re

        agents = load_manifest()
        for agent in agents:
            name = agent.get("name", "")
            assert re.match(r"^[a-z0-9-]+$", name), f"Invalid agent name: {name!r}"


class TestRunAgentFindings:
    """The findings-write path left run_agent with the CD.28 retirement."""

    def test_rec_curator_live_path_retired(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "test.prompt.md"
        prompt_file.write_text("Hello", encoding="utf-8")

        agent_def = {
            "name": "rec-curator",
            "model": "gpt-4.1-mini",
            "prompt_path": prompt_file.name,
        }

        with patch("scripts.run_scheduled_agent._REPO_ROOT", tmp_path):
            assert run_agent(agent_def) is False


class TestTriggerLambda:
    """Tests for the --trigger-lambda flag and _trigger_lambda()."""

    def test_trigger_lambda_constructs_correct_subprocess_command(self) -> None:
        """--trigger-lambda passes the correct aws lambda invoke command."""
        with patch.object(sched_mod.subprocess, "run", side_effect=_invoke_writing('{"StatusCode": 200}')) as mock_run:
            exit_code = _trigger_lambda("rec-curator")

        assert exit_code == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "aws" in cmd
        assert "lambda" in cmd
        assert "invoke" in cmd
        assert "--function-name" in cmd
        assert "agent-platform-scheduled-agent-dispatcher" in cmd
        assert "--payload" in cmd
        payload_idx = cmd.index("--payload") + 1
        payload = json.loads(cmd[payload_idx])
        assert payload == {"force_agent": "rec-curator"}

    def test_trigger_lambda_returns_1_on_subprocess_failure(self) -> None:
        """Returns exit code 1 when the aws CLI invocation fails."""
        failed = MagicMock(returncode=1, stdout="", stderr="An error occurred")
        with patch.object(sched_mod.subprocess, "run", return_value=failed):
            exit_code = _trigger_lambda("rec-curator")

        assert exit_code == 1


# ---------------------------------------------------------------------------
# TestSmokeTest
# ---------------------------------------------------------------------------


class TestSmokeTest:
    """Tests for the --smoke-test flag and _smoke_test()."""

    def test_smoke_test_success_full_flow(self) -> None:
        """Returns 0 when deploy, invoke, and S3 verification all succeed."""
        # depth-first subprocess call tree for _smoke_test():
        #   1. sys.executable -m scripts.build_lambda --deploy  (subprocess.run)
        #   2. aws lambda invoke ... (via _trigger_lambda)      (subprocess.run)
        #   3. aws s3api list-objects-v2 ...                    (subprocess.run)
        # Total subprocess.run count: 3
        now = datetime.now(timezone.utc).isoformat()
        s3_response = json.dumps(
            {
                "Key": "agents/doc-freshness/2026-04-19.jsonl",
                "LastModified": now,
            }
        )

        deploy_result = MagicMock(returncode=0, stdout="", stderr="")
        s3_result = MagicMock(returncode=0, stdout=s3_response, stderr="")
        steps = iter([deploy_result, _invoke_writing('{"StatusCode": 200}'), s3_result])

        def _run(cmd: list[str], **kwargs: Any) -> MagicMock:
            step = next(steps)
            return step if isinstance(step, MagicMock) else step(cmd, **kwargs)

        with patch.object(sched_mod.subprocess, "run", side_effect=_run):
            exit_code = _smoke_test("doc-freshness")

        assert exit_code == 0

    def test_smoke_test_deploy_failure_returns_1(self) -> None:
        """Returns 1 when build/deploy fails."""
        deploy_result = MagicMock(returncode=1, stdout="", stderr="build error")

        with patch(
            "scripts.run_scheduled_agent.subprocess.run",
            return_value=deploy_result,
        ):
            exit_code = _smoke_test("doc-freshness")

        assert exit_code == 1

    def test_smoke_test_invoke_failure_returns_2(self) -> None:
        """Returns 2 when Lambda invocation fails."""
        deploy_result = MagicMock(returncode=0, stdout="", stderr="")
        invoke_result = MagicMock(returncode=1, stdout="", stderr="invoke err")

        with patch.object(sched_mod.subprocess, "run", side_effect=[deploy_result, invoke_result]):
            exit_code = _smoke_test("doc-freshness")

        assert exit_code == 2

    def test_smoke_test_stale_object_returns_3(self) -> None:
        """Returns 3 when the latest S3 object is older than 60 seconds."""
        stale_time = "2020-01-01T00:00:00+00:00"
        s3_response = json.dumps(
            {
                "Key": "agents/doc-freshness/old.jsonl",
                "LastModified": stale_time,
            }
        )

        exit_code = _smoke_with_s3(s3_response)

        assert exit_code == 3

    def test_smoke_test_no_objects_returns_3(self) -> None:
        """Returns 3 when no objects found in S3."""
        exit_code = _smoke_with_s3("null")

        assert exit_code == 3

    def test_smoke_test_s3_command_failure_returns_3(self) -> None:
        """Returns 3 when the S3 list-objects-v2 command fails."""
        exit_code = _smoke_with_s3("", returncode=1)

        assert exit_code == 3

    def test_main_smoke_test_invalid_name(self) -> None:
        """--smoke-test rejects invalid agent names."""
        with patch(
            "scripts.run_scheduled_agent.load_manifest",
            return_value=VALID_MANIFEST["agents"],
        ):
            exit_code = main(["--smoke-test", "../bad-name"])
        assert exit_code == 1

    def test_main_smoke_test_dispatches_to_function(self) -> None:
        """--smoke-test routes to _smoke_test with valid name."""
        with (
            patch(
                "scripts.run_scheduled_agent.load_manifest",
                return_value=VALID_MANIFEST["agents"],
            ),
            patch(
                "scripts.run_scheduled_agent._smoke_test",
                return_value=0,
            ) as mock_st,
        ):
            exit_code = main(["--smoke-test", "doc-freshness"])

        assert exit_code == 0
        mock_st.assert_called_once_with("doc-freshness")


def test_parse_findings_empty_output() -> None:
    assert parse_findings("  \n") == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [('[{"id": 1}]', [{"id": 1}]), ('{"id": 1}', [{"raw": '{"id": 1}'}]), ("not json", [{"raw": "not json"}])],
)
def test_parse_findings_non_empty_output(output: str, expected: list[dict]) -> None:
    assert parse_findings(output) == expected


def test_trigger_lambda_json_parse_failure(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sched_mod.subprocess, "run", side_effect=_invoke_writing("plain text body")):
        assert _trigger_lambda("doc-freshness") == 0
    assert capsys.readouterr().out == "plain text body\n"


@pytest.mark.parametrize(
    ("run_effect", "exit_code", "stdout", "log"),
    [
        (_invoke_writing(""), 0, "(empty Lambda response body)\n", None),
        (_invoke_writing(None), 0, "", "Could not read Lambda response file"),
        (FileNotFoundError("aws"), 1, "", "Lambda invocation error"),
    ],
    ids=["empty-body", "unreadable-outfile", "invoke-raises"],
)
def test_trigger_lambda_response_and_error_paths(
    run_effect: Any,
    exit_code: int,
    stdout: str,
    log: str | None,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(sched_mod.subprocess, "run", side_effect=run_effect):
        assert _trigger_lambda("doc-freshness") == exit_code
    assert capsys.readouterr().out == stdout
    problems = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert problems == [] if log is None else any(log in m for m in problems)


def test_smoke_test_s3_missing_last_modified(caplog: pytest.LogCaptureFixture) -> None:
    assert _smoke_with_s3(json.dumps({"Key": "agents/doc-freshness/a.jsonl"})) == 3
    assert "LastModified missing" in caplog.text


@pytest.mark.parametrize(
    ("stdout", "message"),
    [("not json", "could not parse S3 response"), ('{"LastModified": "yesterday"}', "could not parse LastModified")],
)
def test_smoke_test_s3_unparseable_returns_3(stdout: str, message: str, caplog: pytest.LogCaptureFixture) -> None:
    assert _smoke_with_s3(stdout) == 3
    assert message in caplog.text


@pytest.mark.parametrize(("name", "exit_code", "calls"), [("Bad_Name", 1, []), ("doc-freshness", 0, [call("doc-freshness")])])
def test_main_trigger_lambda_validates_then_dispatches(name: str, exit_code: int, calls: list[Any]) -> None:
    with (
        patch.object(sched_mod, "load_manifest", return_value=VALID_MANIFEST["agents"]),
        patch.object(sched_mod, "_trigger_lambda", return_value=0) as mock_trigger,
    ):
        assert main(["--trigger-lambda", name]) == exit_code
    assert mock_trigger.call_args_list == calls


def test_main_due_branch_runs_due_agents(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(sched_mod, "load_manifest", return_value=VALID_MANIFEST["agents"]),
        patch.object(sched_mod, "is_agent_due", return_value=True) as mock_due,
        patch.object(sched_mod, "run_agent", side_effect=[True, False, True, True]) as mock_run,
    ):
        assert main(["--due"]) == 1
        assert main(["--due", "--dry-run"]) == 0
    ran = [(c.args[0]["name"], c.kwargs["dry_run"]) for c in mock_run.call_args_list]
    assert ran == [("doc-freshness", False), ("orphan-code", False), ("doc-freshness", True), ("orphan-code", True)]
    assert all(c.args[1].tzinfo is timezone.utc for c in mock_due.call_args_list)
    assert "Running 2 agent(s) due" in capsys.readouterr().out
