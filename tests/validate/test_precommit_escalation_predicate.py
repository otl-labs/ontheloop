"""Direct tests of scripts.checks._scaffolding.precommit_escalates (Decision 208 VP step 7):
the shared escalation predicate `_run_precommit_body` delegates to, and that
`run_precommit_checks` returns the decision it actually applied. tests/validate/ is
map_source_to_test's home for _scaffolding.py (Decision 131); no cross-test imports."""

from __future__ import annotations

from unittest.mock import patch

from scripts.checks._scaffolding import precommit_escalates, run_precommit_checks
from tests.fixtures.subprocess_stubs import _mock_completed

_CONFIG_YAML = (
    "repos:\n"
    "- repo: https://github.com/Yelp/detect-secrets\n"
    "  rev: v1.4.0\n"
    "  hooks:\n"
    "  - id: detect-secrets\n"
    "    args: ['--baseline', '.secrets.baseline']\n"
)


class TestPrecommitEscalatesPredicate:
    def test_escalates_on_the_config_path_itself(self) -> None:
        with patch("scripts.checks._common.run") as mock_run:
            mock_run.return_value = _mock_completed(0, stdout=_CONFIG_YAML)
            assert precommit_escalates([".pre-commit-config.yaml"]) is True

    def test_escalates_on_a_hook_baseline_arg(self) -> None:
        with patch("scripts.checks._common.run") as mock_run:
            mock_run.return_value = _mock_completed(0, stdout=_CONFIG_YAML)
            assert precommit_escalates([".secrets.baseline"]) is True

    def test_does_not_escalate_on_an_ordinary_file(self) -> None:
        with patch("scripts.checks._common.run") as mock_run:
            mock_run.return_value = _mock_completed(0, stdout=_CONFIG_YAML)
            assert precommit_escalates(["scripts/foo.py"]) is False

    def test_unreadable_base_ref_config_fails_closed(self) -> None:
        with patch("scripts.checks._common.run") as mock_run:
            mock_run.return_value = _mock_completed(1, stderr="fatal: path does not exist")
            assert precommit_escalates(["scripts/foo.py"]) is True

    def test_unparseable_base_ref_config_fails_closed(self) -> None:
        with patch("scripts.checks._common.run") as mock_run:
            mock_run.return_value = _mock_completed(0, stdout="not: valid: yaml: [")
            assert precommit_escalates(["scripts/foo.py"]) is True


class TestRunPrecommitBodyDelegatesToPredicate:
    def test_patching_the_predicate_flips_the_all_files_flag(self) -> None:
        with (
            patch("scripts.checks._scaffolding.precommit_escalates", return_value=True) as mock_pred,
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = _mock_completed(0)
            failed: list[str] = []
            run_precommit_checks(failed, all_files=False, files=["scripts/foo.py"])
        mock_pred.assert_called_once_with(["scripts/foo.py"], "origin/main")
        cmd = mock_run.call_args.args[0]
        assert "--all-files" in cmd


class TestPushContextBase:
    def test_push_context_uses_push_base(self) -> None:
        for base, expected in (("abc123", "abc123"), (None, "origin/main")):
            with (
                patch("scripts.checks._scaffolding.precommit_escalates", return_value=False) as mock_pred,
                patch("scripts.checks._common.push_context_base", return_value=base),
                patch("scripts.checks._common.run") as mock_run,
            ):
                mock_run.return_value = _mock_completed(0)
                run_precommit_checks([], all_files=False, files=["scripts/foo.py"])
            mock_pred.assert_called_once_with(["scripts/foo.py"], expected)


class TestRunPrecommitChecksReturnsAppliedDecision:
    def test_returns_true_when_escalated(self) -> None:
        with (
            patch("scripts.checks._scaffolding.precommit_escalates", return_value=True),
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = _mock_completed(0)
            failed: list[str] = []
            assert run_precommit_checks(failed, all_files=False, files=["scripts/foo.py"]) is True

    def test_returns_false_when_not_escalated(self) -> None:
        with (
            patch("scripts.checks._scaffolding.precommit_escalates", return_value=False),
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = _mock_completed(0)
            failed: list[str] = []
            assert run_precommit_checks(failed, all_files=False, files=["scripts/foo.py"]) is False

    def test_returns_false_when_caller_already_requested_all_files(self) -> None:
        with patch("scripts.checks._common.run") as mock_run:
            mock_run.return_value = _mock_completed(0)
            failed: list[str] = []
            assert run_precommit_checks(failed, all_files=True) is False

    def test_returns_false_when_pre_commit_not_installed(self) -> None:
        with patch("scripts.checks._scaffolding.importlib.util.find_spec", return_value=None):
            failed: list[str] = []
            assert run_precommit_checks(failed, all_files=False, files=["scripts/foo.py"]) is False

    def test_returns_false_when_no_changed_files(self) -> None:
        with patch("scripts.checks._common.get_changed_files", return_value=[]):
            failed: list[str] = []
            assert run_precommit_checks(failed, all_files=False) is False
