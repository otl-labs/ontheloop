"""PRE-DECLARED decomposition target (Decision 186 / PLAN-escape-closure-obligation): hosts the
new close_recs_from_trailer threading tests from the start, keeping tests/test_ci_rca_lifecycle.py
(431 SLOC pre-plan, 69 headroom) clear of its 500-SLOC ceiling. The refusal-contract mirror
(catch-skip-mark, exit-code behaviour, the workflow-delegation shape pin) lives in
tests/test_ci_rca_lifecycle.py::TestCloseRecsFromTrailerRefusalContract.

PLAN-closure-stamp-applicability: the seam contract cases below drive close_recs_from_trailer
against the REAL update_rec with only the warehouse boundary stubbed (the shape
tests/test_ci_rca_inactivity_sweep.py::test_sweep_closes_escape_classified_rec_end_to_end already
established) -- #1133's mocked tests all patched update_rec with a bare MagicMock, so none of them
could catch the production defect where a blob-less rec's closure hit update_rec's real
closure-stamp precondition and reddened main. RecNotFound is resolved LAZILY (inside a test body)
so this module stays importable on origin/main, where the symbol does not exist yet.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

from tests.fixtures.ops_rec_rows import reader_rec_row

_FIX_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


@pytest.fixture(autouse=True)
def _code_bearing_changed_set():
    """Give every test in this module a code-bearing merge diff so rec-3775's merge-leg gate
    never refuses on this module's deliberately-unresolvable `_FIX_SHA` -- these tests exercise
    the CLOSURE seam, not the merge-leg gate itself (covered separately in
    tests/ops_data_portal/test_trailer_closure_gate.py and
    tests/checks/ci_guards/test_rec_autoclose_merge_leg.py)."""
    with patch("scripts.ops_portal.ci_rca_lifecycle.changed_files", return_value={"scripts/example.py"}):
        yield


def test_closes_rec_lacking_context_v2_json(tmp_path) -> None:
    """rec-3738's own acceptance predicate: a trailer-named rec carrying no context_v2_json
    closes cleanly through the REAL update_rec (only the warehouse boundary stubbed) -- exit code
    0, exactly one warehouse write. Red before this plan's fix: update_rec's own closure-stamp
    precondition raised ValueError for the unconditionally-threaded closure_fix_sha kwarg, which
    the broad `except Exception` in close_recs_from_trailer caught and turned into exit_code=1."""
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    existing = reader_rec_row(id="rec-1")  # complete rule-clean row, no context_v2_json key at all
    recs_file = tmp_path / "recs.jsonl"
    with (
        patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
        patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
        patch("scripts.ops_data_portal._sync_table"),
        patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
    ):
        rc = close_recs_from_trailer(["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "open"}})

    assert rc == 0
    mock_write.assert_called_once()


def test_trailer_closure_with_blob_passes_commit_sha_into_the_gated_write() -> None:
    """A trailer-named rec whose cached row carries a stampable context_v2_json blob gets
    closure_fix_sha=commit_sha threaded into the closing update_rec call, so the fix-commit
    binding is satisfiable on the automated route without a second write -- rec-autoclose is not
    wedged by its own gate."""
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    with patch("scripts.ops_data_portal.update_rec", return_value=True) as mock_update:
        rc = close_recs_from_trailer(
            ["rec-1"],
            _FIX_SHA,
            "https://x/runs/1",
            {"rec-1": {"status": "open", "context_v2_json": json.dumps({"escape_class": "no-edge"})}},
        )

    assert rc == 0
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "rec-1"
    assert kwargs.get("closure_fix_sha") == _FIX_SHA


def test_trailer_closure_without_blob_omits_closure_fix_sha() -> None:
    """A trailer-named rec whose cached row carries NO context_v2_json omits closure_fix_sha
    entirely from the update_rec call -- update_rec's own closure-stamp precondition is never
    even reached, since the kwarg is never threaded for an inapplicable blob."""
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    with patch("scripts.ops_data_portal.update_rec", return_value=True) as mock_update:
        rc = close_recs_from_trailer(["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "open"}})

    assert rc == 0
    mock_update.assert_called_once()
    args, kwargs = mock_update.call_args
    assert args[0] == "rec-1"
    assert "closure_fix_sha" not in kwargs


def test_already_closed_rec_is_skipped_idempotently() -> None:
    """A rec the cache already shows closed is skipped entirely -- no update_rec call at all."""
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    with patch("scripts.ops_data_portal.update_rec") as mock_update:
        rc = close_recs_from_trailer(["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "closed"}})

    assert rc == 0
    mock_update.assert_not_called()


def test_ci_rca_rec_still_gets_stamp_fixed_by_sha_call() -> None:
    """The existing post-close stamp_fixed_by_sha call is kept for ci_rca recs and runs
    alongside closure_fix_sha -- idempotent (both write the same key), never removed."""
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    with (
        patch("scripts.ops_data_portal.update_rec", return_value=True),
        patch("scripts.ops_portal.ci_rca_lifecycle.stamp_fixed_by_sha") as mock_stamp,
    ):
        rc = close_recs_from_trailer(
            ["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "open", "source": "ci_rca"}}
        )

    assert rc == 0
    mock_stamp.assert_called_once_with("rec-1", _FIX_SHA, profile=None)


def test_writer_transport_failure_sets_exit_code() -> None:
    """A writer-transport RuntimeError (reader-unreachable, a referential 409, an HTTP failure --
    anything that is not RecNotFound) during closure sets exit_code=1. Never discriminated by
    message substring -- the fixture's message deliberately does not resemble a 'not found'
    phrase, so a substring-matching implementation would fail this test."""
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    with patch(
        "scripts.ops_data_portal.update_rec",
        side_effect=RuntimeError("writer transport: 502 Bad Gateway"),
    ):
        rc = close_recs_from_trailer(["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "open"}})

    assert rc == 1


def test_absent_rec_skips_without_exit_code() -> None:
    """RecNotFound (an absent rec) is the ONLY RuntimeError treated as a skip -- exit_code stays
    0, distinguished by TYPE from every other RuntimeError, never by message substring. Resolved
    lazily (function-local import) so this module still imports cleanly on origin/main, where
    RecNotFound does not exist yet."""
    from scripts.ops_data_portal import RecNotFound
    from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

    with patch("scripts.ops_data_portal.update_rec", side_effect=RecNotFound("rec-1 does not exist")):
        rc = close_recs_from_trailer(["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "open"}})

    assert rc == 0


class TestAcceptanceVerdictRefusal:
    """Decision 201: the whole trailer-closure acceptance-verdict refusal surface -- an
    acceptance refusal and the pre-existing ClosureArtifactRequired refusal both leave the rec
    OPEN, print their own marker, and return 0; a holds verdict closes; the STRUCTURAL binding
    (require_acceptance_verdict=True) keeps an absent verdict out of update_rec entirely."""

    # sha256 of the acceptance every complete mocked `existing` row in this class carries (reader_rec_row).
    _ACCEPTANCE_SHA256 = hashlib.sha256(reader_rec_row()["acceptance"].encode("utf-8")).hexdigest()

    def _holds_record(self, **overrides) -> dict:
        record = {
            "rec_id": "rec-1",
            "sha": _FIX_SHA,
            "acceptance_sha256": self._ACCEPTANCE_SHA256,
            "verdict": "holds",
        }
        record.update(overrides)
        return record

    def test_holds_verdict_closes(self) -> None:
        from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

        with patch("scripts.ops_data_portal.update_rec", return_value=True) as mock_update:
            rc = close_recs_from_trailer(
                ["rec-1"],
                _FIX_SHA,
                "https://x/runs/1",
                {"rec-1": {"status": "open"}},
                acceptance_verdicts={"rec-1": self._holds_record()},
                require_acceptance_verdict=True,
            )

        assert rc == 0
        mock_update.assert_called_once()
        _args, kwargs = mock_update.call_args
        assert kwargs.get("acceptance_verdict") == self._holds_record()
        assert kwargs.get("closing_sha") == _FIX_SHA

    def test_non_holds_verdict_refused_by_the_real_gate_leaves_rec_open(self, capsys) -> None:
        """Drives close_recs_from_trailer against the REAL update_rec (only the warehouse
        boundary stubbed), so the assertion exercises the actual AcceptanceVerdictRefused catch,
        not a mocked stand-in."""
        from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

        existing = reader_rec_row(id="rec-1")
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal._ducklake_write") as mock_write,
        ):
            rc = close_recs_from_trailer(
                ["rec-1"],
                _FIX_SHA,
                "https://x/runs/1",
                {"rec-1": {"status": "open"}},
                acceptance_verdicts={"rec-1": self._holds_record(verdict="fails")},
                require_acceptance_verdict=True,
            )

        assert rc == 0
        mock_write.assert_not_called()
        out = capsys.readouterr().out
        assert "Decision 201" in out
        assert "rec-1" in out
        assert "left OPEN" in out

    def test_absent_verdict_is_refused_when_required(self) -> None:
        from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

        with patch("scripts.ops_data_portal.update_rec") as mock_update:
            rc = close_recs_from_trailer(
                ["rec-1"],
                _FIX_SHA,
                "https://x/runs/1",
                {"rec-1": {"status": "open"}},
                acceptance_verdicts={},
                require_acceptance_verdict=True,
            )

        assert rc == 0
        mock_update.assert_not_called()

    def test_absent_verdict_with_default_false_still_closes(self) -> None:
        from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

        with patch("scripts.ops_data_portal.update_rec", return_value=True) as mock_update:
            rc = close_recs_from_trailer(["rec-1"], _FIX_SHA, "https://x/runs/1", {"rec-1": {"status": "open"}})

        assert rc == 0
        mock_update.assert_called_once()

    def test_closes_blobless_rec_on_holds(self) -> None:
        """acceptance_verdict is a gate INPUT, never a context_v2_json stamp: a blob-less rec
        (no context_v2_json at all) still closes on a holds verdict -- closure_stamps_applicable
        is never consulted for the acceptance verdict or its closing sha."""
        from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

        existing = reader_rec_row(id="rec-1")  # complete rule-clean row, no context_v2_json key at all
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
        ):
            rc = close_recs_from_trailer(
                ["rec-1"],
                _FIX_SHA,
                "https://x/runs/1",
                {"rec-1": {"status": "open"}},
                acceptance_verdicts={"rec-1": self._holds_record()},
                require_acceptance_verdict=True,
            )

        assert rc == 0
        mock_write.assert_called_once()

    def test_closure_artifact_required_still_leaves_rec_open_and_exits_zero(self) -> None:
        """Decision 186 point 7 regression arm: the pre-existing ClosureArtifactRequired catch
        survives the relocation byte-for-behaviour -- nothing else asserts this once the closure
        step leaves rec-autoclose.yml."""
        from scripts.ops_data_portal import ClosureArtifactRequired
        from scripts.ops_portal.ci_rca_lifecycle import close_recs_from_trailer

        with patch("scripts.ops_data_portal.update_rec", side_effect=ClosureArtifactRequired("no artifact")):
            rc = close_recs_from_trailer(
                ["rec-1"],
                _FIX_SHA,
                "https://x/runs/1",
                {"rec-1": {"status": "open"}},
                acceptance_verdicts={"rec-1": self._holds_record()},
                require_acceptance_verdict=True,
            )

        assert rc == 0
