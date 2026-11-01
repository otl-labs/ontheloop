"""run_auto full-sequence concern: tests/session/postflight/test_auto.py (rec-2709 Wave 10).

Split from the former tests/test_session_postflight.py monolith: TestAutoMode,
TestRetiredDrainBlocks, TestRunAutoCompactAll. Imports MODULE_PATH from the shared loader for the
source-text assertion.

PLAN-handoff-validates-committed-tree: run_auto's order is now close -> evidence -> metrics ->
commit -> rebase -> validate -> verify-head -> push -> portal sync. The package conftest's
autouse `_fail_closed_real_git_seams` fixture stubs session_postflight.run_rebase and
.run_verify_head to RAISE unless a test overrides them, and wraps scripts.postflight._common._run
so a mutating git verb (add/commit/fetch/rebase/push) also raises if reached for real -- every
test below that drives run_auto past the commit leg patches run_rebase/run_verify_head itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.postflight import _common
from tests.fixtures.session_postflight_module import MODULE_PATH as _MODULE_PATH
from tests.fixtures.session_postflight_module import postflight as _postflight


@pytest.fixture(autouse=True)
def _valid_evidence(tmp_path, monkeypatch):
    from scripts.session import postflight_evidence

    path = tmp_path / "implementation-retrospective.json"
    path.write_text(json.dumps({category: [] for category in postflight_evidence.CATEGORIES}), encoding="utf-8")
    monkeypatch.setattr(postflight_evidence, "EVIDENCE_PATH", path)


class TestAutoMode:
    """Tests for run_auto(): the full session-close sequence in one call."""

    def _close_output(self, sanity_status: str = "PASS") -> str:
        return json.dumps(
            {
                "sanity_status": sanity_status,
                "details": {},
            }
        )

    def _push_output(self, status: str = "merged") -> str:
        return json.dumps({"status": status, "pr_url": "https://github.com/pr/1"})

    def test_happy_path_returns_merged(self, capsys: pytest.CaptureFixture) -> None:
        """All steps succeed -> rc=0 and status merged in output."""
        close_out = self._close_output("PASS")
        push_out = self._push_output("merged")

        def fake_close() -> int:
            print(close_out)
            return 0

        def fake_push() -> int:
            print(push_out)
            return 0

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", return_value={"pulled": {}}),
            patch("scripts.postflight.housekeeping._stage_document_derived_tables"),
        ):
            rc = _postflight.run_auto("feat: test", 5, 1)

        assert rc == 0
        captured = capsys.readouterr()
        assert '"merged"' in captured.out

    def test_auto_commits_before_full_tier_and_pushes_only_after_pass(self, capsys: pytest.CaptureFixture) -> None:
        """VP step 2 (graduated): commit < rebase < validate < verify-head < push, and the retired
        post-push log-housekeeping leg is never called."""
        calls: list[str] = []
        push_out = self._push_output("merged")

        def fake_close() -> int:
            print(self._close_output("PASS"))
            return 0

        def fake_commit(message: str) -> int:
            calls.append("commit")
            return 0

        def fake_rebase() -> str:
            calls.append("rebase")
            return "ok"

        def fake_validate() -> int:
            calls.append("validate")
            return 0

        def fake_verify_head() -> int:
            calls.append("verify_head")
            return 0

        def fake_push() -> int:
            calls.append("push")
            print(push_out)
            return 0

        def fake_log_housekeeping() -> int:
            calls.append("log_housekeeping")
            return 0

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", side_effect=fake_commit),
            patch("session_postflight.run_rebase", side_effect=fake_rebase),
            patch("session_postflight.run_validate", side_effect=fake_validate),
            patch("session_postflight.run_verify_head", side_effect=fake_verify_head),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.postflight.housekeeping.run_log_housekeeping", side_effect=fake_log_housekeeping),
            patch("scripts.ops_data_portal.sync", return_value={"pulled": {}}),
            patch("scripts.postflight.housekeeping._stage_document_derived_tables"),
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc == 0
        assert calls == ["commit", "rebase", "validate", "verify_head", "push"]
        assert "log_housekeeping" not in calls

    def test_push_never_called_when_validate_fails(self) -> None:
        with (
            patch("session_postflight.run_close", side_effect=lambda: print(self._close_output()) or 0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=1),
            patch("session_postflight.run_verify_head") as verify_head,
            patch("scripts.postflight.remote.run_push") as push,
        ):
            rc = _postflight.run_auto("feat: test")
        assert rc == 1
        verify_head.assert_not_called()
        push.assert_not_called()

    def test_push_never_called_when_verify_head_fails(self) -> None:
        with (
            patch("session_postflight.run_close", side_effect=lambda: print(self._close_output()) or 0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=1),
            patch("scripts.postflight.remote.run_push") as push,
        ):
            rc = _postflight.run_auto("feat: test")
        assert rc == 1
        push.assert_not_called()

    def test_evidence_mode_writes_and_renders_record(self, tmp_path, monkeypatch, capsys) -> None:
        from scripts.session.postflight_evidence import CATEGORIES

        source = tmp_path / "evidence.json"
        source.write_text(json.dumps({category: ["observed fact"] for category in CATEGORIES}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["postflight", "--evidence", str(source)])
        with patch("scripts.session.postflight_evidence.write") as write:
            assert _postflight.main() == 0
        write.assert_called_once()
        assert "Observed Fact" not in capsys.readouterr().out

    def test_validate_failure_stops_early(self, capsys: pytest.CaptureFixture) -> None:
        """If --validate fails, auto stops and returns validate_failed. Close now precedes
        validate (close/evidence/metrics/commit/rebase all run first), so this asserts push is
        never reached rather than that close was skipped."""
        with (
            patch("session_postflight.run_close", return_value=0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=1),
            patch("scripts.postflight.remote.run_push") as mock_push,
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc == 1
        mock_push.assert_not_called()
        captured = capsys.readouterr()
        assert '"validate_failed"' in captured.out

    def test_rebase_conflict_returns_rebase_failed_without_validating(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("session_postflight.run_close", side_effect=lambda: print(self._close_output()) or 0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="rebase_failed"),
            patch("session_postflight.run_validate") as validate,
        ):
            rc = _postflight.run_auto("feat: test")
        assert rc == 1
        validate.assert_not_called()
        assert '"rebase_failed"' in capsys.readouterr().out

    def test_fetch_failure_returns_fetch_failed_without_validating(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("session_postflight.run_close", side_effect=lambda: print(self._close_output()) or 0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="fetch_failed"),
            patch("session_postflight.run_validate") as validate,
        ):
            rc = _postflight.run_auto("feat: test")
        assert rc == 1
        validate.assert_not_called()
        assert '"fetch_failed"' in capsys.readouterr().out

    def test_missing_evidence_stops_before_commit(self, monkeypatch, capsys) -> None:
        from scripts.session import postflight_evidence

        monkeypatch.setattr(postflight_evidence, "EVIDENCE_PATH", Path("/missing/evidence.json"))
        with (
            patch("session_postflight.run_close", side_effect=lambda: print(self._close_output()) or 0),
            patch("session_postflight.run_commit") as commit,
        ):
            assert _postflight.run_auto("feat: test") == 1
        commit.assert_not_called()
        assert '"evidence_failed"' in capsys.readouterr().out

    def test_sanity_fail_stops_before_commit(self, capsys: pytest.CaptureFixture) -> None:
        """If close returns sanity_status FAIL, auto should stop and not commit."""
        close_out = self._close_output("FAIL")

        def fake_close() -> int:
            print(close_out)
            return 0

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("session_postflight.run_commit") as mock_commit,
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc == 1
        mock_commit.assert_not_called()
        captured = capsys.readouterr()
        assert '"sanity_failed"' in captured.out

    def test_ci_failed_propagated(self, capsys: pytest.CaptureFixture) -> None:
        """If push returns ci_failed, run_auto propagates that status."""
        close_out = self._close_output("PASS")
        push_out = self._push_output("ci_failed")

        def fake_close() -> int:
            print(close_out)
            return 0

        def fake_push() -> int:
            print(push_out)
            return 1  # ci_failed exits non-zero in production

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", return_value={"pulled": {}}),
            patch("scripts.postflight.housekeeping._stage_document_derived_tables"),
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc != 0
        captured = capsys.readouterr()
        assert '"ci_failed"' in captured.out

    def test_auto_rejects_empty_commit_message(self, capsys: pytest.CaptureFixture) -> None:
        """run_auto returns rc=1 with clear error when commit message is empty."""
        with (
            patch("session_postflight.run_close", return_value=0),
        ):
            rc = _postflight.run_auto("")

        assert rc == 1
        captured = capsys.readouterr()
        assert '"commit_failed"' in captured.out

    def test_auto_refreshes_read_cache_via_portal_sync(self, capsys: pytest.CaptureFixture) -> None:
        """Step 9: the portal sync (cache pull) runs during run_auto; merged status appears in output."""
        close_out = self._close_output("PASS")
        push_out = self._push_output("merged")

        def fake_close() -> int:
            print(close_out)
            return 0

        def fake_push() -> int:
            print(push_out)
            return 0

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", return_value={"pulled": {"ops_recommendations": 3}}) as mock_sync,
            patch("scripts.postflight.housekeeping._stage_document_derived_tables"),
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc == 0
        mock_sync.assert_called_once()
        captured = capsys.readouterr()
        assert '"merged"' in captured.out


class TestRebaseSeam:
    """Direct tests of the ORIGINAL run_rebase() body, reached via the postflight_seams fixture
    (the package's autouse fail-closed fixture stubs session_postflight.run_rebase itself)."""

    def test_fetch_failure_returns_fetch_failed_and_skips_rebase(self, postflight_seams) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            return MagicMock(returncode=1, stdout="", stderr="network unreachable")

        with patch("scripts.postflight._common._run", side_effect=mock_run):
            status = postflight_seams["run_rebase"]()

        assert status == "fetch_failed"
        assert calls == [["git", "fetch", "origin", "main"]]

    def test_conflict_aborts_and_returns_rebase_failed(self, postflight_seams) -> None:
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            if cmd[:2] == ["git", "fetch"]:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "rebase"] and "--abort" not in cmd:
                return MagicMock(returncode=1, stdout="CONFLICT (content)", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("scripts.postflight._common._run", side_effect=mock_run):
            status = postflight_seams["run_rebase"]()

        assert status == "rebase_failed"
        assert ["git", "rebase", "--abort"] in calls

    def test_clean_rebase_returns_ok(self, postflight_seams) -> None:
        result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("scripts.postflight._common._run", return_value=result):
            status = postflight_seams["run_rebase"]()
        assert status == "ok"


class TestVerifyHeadSeam:
    """Direct tests of the ORIGINAL run_verify_head() body, reached via postflight_seams."""

    def test_passes_through_zero(self, postflight_seams) -> None:
        result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("scripts.postflight._common._run", return_value=result) as mock_run:
            rc = postflight_seams["run_verify_head"]()
        assert rc == 0
        mock_run.assert_called_once_with([_common.PYTHON, "-m", "scripts.checks.validation_result", "--verify-head"])

    def test_passes_through_nonzero(self, postflight_seams) -> None:
        result = MagicMock(returncode=1, stdout="tree_mismatch", stderr="")
        with patch("scripts.postflight._common._run", return_value=result):
            rc = postflight_seams["run_verify_head"]()
        assert rc == 1


class TestMutatingGitGuard:
    """Guard self-test (conftest._guarded_run): a mis-targeted patch must not leave the
    fail-closed _common._run guard silently inert."""

    def test_guard_raises_on_mutating_git_verb(self) -> None:
        with pytest.raises(AssertionError):
            _common._run(["git", "commit", "-m", "x"])

    def test_guard_delegates_non_mutating_git_verb(self) -> None:
        result = _common._run(["git", "status", "--porcelain"])
        assert result.returncode == 0


class TestRetiredDrainBlocks:
    """Decision 84 I-4: the pending-outbox drain blocks are gone from run_auto().

    The static-key invariant survives the retirement: run_auto() must still never
    spawn an 'aws sso login' subprocess.
    """

    def _close_output(self, sanity_status: str = "PASS") -> str:
        return json.dumps(
            {
                "sanity_status": sanity_status,
                "details": {},
            }
        )

    def _push_output(self, status: str = "merged") -> str:
        return json.dumps({"status": status, "pr_url": "https://github.com/pr/1"})

    def test_no_sso_login_subprocess_spawned(self, capsys: pytest.CaptureFixture) -> None:
        """run_auto() must never call subprocess.run(['aws', 'sso', 'login', ...])."""
        close_out = self._close_output()
        push_out = self._push_output()

        def fake_close() -> int:
            print(close_out)
            return 0

        def fake_push() -> int:
            print(push_out)
            return 0

        with (
            patch("session_postflight.run_close", side_effect=fake_close),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", return_value={"pulled": {}}),
            patch("scripts.postflight.housekeeping._stage_document_derived_tables"),
            patch("subprocess.run") as mock_subprocess,
        ):
            _postflight.run_auto("feat: test")

        # Confirm no 'aws sso login' call was made
        for call_args in mock_subprocess.call_args_list:
            cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("args", [])
            assert not ("sso" in cmd and "login" in cmd), f"Unexpected aws sso login call: {cmd}"

    def test_run_auto_source_has_no_drain_blocks(self) -> None:
        """The drain_pending / drain_pending_decisions blocks are deleted; step 7b is a retirement comment."""
        source = _MODULE_PATH.read_text(encoding="utf-8")
        assert "drain_pending" not in source, "postflight must not reference the retired outbox drain"
        assert "retired" in source and "7b" in source  # the step-7b retirement comment documents the removal


# ---------------------------------------------------------------------------
# compact_all() integration in run_auto (Decision 50)
# ---------------------------------------------------------------------------


class TestRunAutoCompactAll:
    """Tests for ops_data_portal.sync() integration in run_auto (Decision 50 / Decision 84 step 8)."""

    def test_compact_all_called_in_run_auto(self, capsys: pytest.CaptureFixture) -> None:
        """ops_data_portal.sync() is called as the last step of run_auto."""
        mock_sync = MagicMock(return_value=None)

        with (
            patch("session_postflight.run_close", return_value=0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", return_value=0),
            patch("scripts.ops_data_portal.sync", mock_sync),
        ):
            _postflight.run_auto("feat: test")

        mock_sync.assert_called_once()

    def test_compact_all_exception_does_not_fail_run_auto(self, capsys: pytest.CaptureFixture) -> None:
        """ops_data_portal.sync() exception is caught and does not cause run_auto to fail."""
        push_out = json.dumps({"status": "merged", "pr_url": "https://github.com/test/pr/1"})

        def fake_push() -> int:
            print(push_out)
            return 0

        with (
            patch("session_postflight.run_close", return_value=0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", side_effect=RuntimeError("sync failed!")),
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc == 0

    def test_ops_writer_import_failure_handled_gracefully(self, capsys: pytest.CaptureFixture) -> None:
        """ops_data_portal.sync() ImportError is caught and does not fail run_auto."""
        push_out = json.dumps({"status": "merged", "pr_url": "https://github.com/test/pr/1"})

        def fake_push() -> int:
            print(push_out)
            return 0

        with (
            patch("session_postflight.run_close", return_value=0),
            patch("scripts.postflight.housekeeping.run_metrics", return_value=0),
            patch("session_postflight.run_commit", return_value=0),
            patch("session_postflight.run_rebase", return_value="ok"),
            patch("session_postflight.run_validate", return_value=0),
            patch("session_postflight.run_verify_head", return_value=0),
            patch("scripts.postflight.remote.run_push", side_effect=fake_push),
            patch("scripts.ops_data_portal.sync", side_effect=ImportError("ops_data_portal not found")),
        ):
            rc = _postflight.run_auto("feat: test")

        assert rc == 0
