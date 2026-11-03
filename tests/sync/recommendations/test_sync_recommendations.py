"""Tests for scripts/sync/recommendations.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "sync" / "recommendations.py"
_spec = importlib.util.spec_from_file_location("sync_recommendations", _MODULE_PATH)
assert _spec and _spec.loader
_sync = importlib.util.module_from_spec(_spec)
sys.modules["sync_recommendations"] = _sync
_spec.loader.exec_module(_sync)  # type: ignore[union-attr]

# Import functions that still exist
next_id = _sync.next_id
seed_counters = _sync.seed_counters
main_fn = _sync.main


class TestRemovedCLIFlags:
    """Confirm that --merge and --push-closures flags are no longer accepted.

    These flags relied on the agent-* S3 sync pattern which was removed.
    All writes now go through scripts.ops_data_portal.
    """

    def test_merge_flag_rejected(self) -> None:
        """--merge is not a recognised argument; main() should fail or print help."""

        with pytest.raises(SystemExit) as exc_info:
            mfn = _sync.main
            # argparse raises SystemExit(2) for unrecognised arguments
            mfn(["--merge"])
        assert exc_info.value.code != 0

    def test_push_closures_flag_rejected(self) -> None:
        """--push-closures is not a recognised argument; main() should fail or print help."""
        with pytest.raises(SystemExit) as exc_info:
            _sync.main(["--push-closures"])
        assert exc_info.value.code != 0

    def test_merge_from_s3_function_removed(self) -> None:
        """merge_from_s3 function no longer exists in sync_recommendations."""
        assert not hasattr(_sync, "merge_from_s3")

    def test_push_closures_to_s3_function_removed(self) -> None:
        """push_closures_to_s3 function no longer exists in sync_recommendations."""
        assert not hasattr(_sync, "push_closures_to_s3")


class TestNextId:
    """Tests for next_id(): atomic DynamoDB counter allocation."""

    def _mock_ddb_response(self, value: int) -> dict:
        return {"Attributes": {"current_value": {"N": str(value)}}}

    def test_recommendations_returns_rec_nnn_format(self, capsys: pytest.CaptureFixture) -> None:
        """next_id('recommendations') returns 'rec-NNN' string."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_session = mock_boto3.Session.return_value
            mock_ddb = mock_session.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(522)
            result = next_id("recommendations", profile="test-profile")

        assert result == "rec-522"

    def test_recommendations_zero_pads_to_three_digits(self) -> None:
        """rec IDs are zero-padded to at least 3 digits."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(7)
            result = next_id("recommendations")

        assert result == "rec-007"

    def test_decisions_returns_int(self) -> None:
        """next_id('decisions') returns an integer."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(56)
            result = next_id("decisions")

        assert result == 56
        assert isinstance(result, int)

    def test_generic_counter_returns_int(self) -> None:
        """next_id with an arbitrary name returns an integer."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(3)
            result = next_id("sessions")

        assert result == 3

    def test_raises_runtime_error_when_boto3_unavailable(self) -> None:
        """RuntimeError raised when boto3 is not installed."""
        import pytest as _pytest  # noqa: PLC0415

        with patch("sync_recommendations._BOTO3_AVAILABLE", False):
            with _pytest.raises(RuntimeError, match="boto3 not available"):
                next_id("recommendations")

    def test_uses_profile_arg_over_env(self) -> None:
        """profile argument is forwarded to boto3.Session."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(100)
            next_id("recommendations", profile="my-profile")

        mock_boto3.Session.assert_called_once_with(profile_name="my-profile", region_name="eu-west-2")

    def test_cli_next_id_recommendations_prints_rec_nnn(self, capsys: pytest.CaptureFixture) -> None:
        """CLI --next-id recommendations prints rec-NNN to stdout."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(523)
            rc = main_fn(["--next-id", "recommendations"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "rec-523" in captured.out

    def test_uses_sso_profile_fallback_when_no_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """next_id() uses _SSO_PROFILE when no profile arg and AWS_PROFILE is unset."""
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.return_value = self._mock_ddb_response(1)
            next_id("recommendations")

        mock_boto3.Session.assert_called_once_with(profile_name=_sync._SSO_PROFILE, region_name="eu-west-2")

    def test_cli_next_id_error_returns_nonzero(self, capsys: pytest.CaptureFixture) -> None:
        """CLI --next-id returns exit code 1 when DynamoDB unreachable."""
        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations.boto3") as mock_boto3,
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            mock_ddb.update_item.side_effect = Exception("Connection refused")
            rc = main_fn(["--next-id", "recommendations"])

        assert rc == 1


class TestSeedCounters:
    """Tests for seed_counters(): seed DynamoDB from local state."""

    def test_seeds_from_local_recs_and_decisions(self, capsys: pytest.CaptureFixture) -> None:
        """seed_counters reads max ID from recs log and decisions, puts both items."""
        local_recs = [
            {"id": "rec-010", "status": "open"},
            {"id": "rec-007", "status": "closed"},
        ]
        decisions = [{"decision_id": 42}, {"decision_id": 38}]

        with (
            patch("sync_recommendations._BOTO3_AVAILABLE", True),
            patch("sync_recommendations._read_local_recs", return_value=local_recs),
            patch("sync_recommendations.boto3") as mock_boto3,
            patch("scripts.decisions_md.parse_decisions_md", return_value=decisions),
        ):
            mock_ddb = mock_boto3.Session.return_value.client.return_value
            seed_counters(profile="test-profile")

            put_calls = mock_ddb.put_item.call_args_list

        assert len(put_calls) == 2
        # Verify recommendations seeded with max_rec_id=10
        rec_call = next(c for c in put_calls if c[1]["Item"]["counter_name"]["S"] == "recommendations")
        assert rec_call[1]["Item"]["current_value"]["N"] == "10"
        # Verify decisions seeded with max_decision_id=42
        dec_call = next(c for c in put_calls if c[1]["Item"]["counter_name"]["S"] == "decisions")
        assert dec_call[1]["Item"]["current_value"]["N"] == "42"

    def test_raises_when_boto3_unavailable(self) -> None:
        """RuntimeError raised when boto3 is not installed."""
        import pytest as _pytest  # noqa: PLC0415

        with patch("sync_recommendations._BOTO3_AVAILABLE", False):
            with _pytest.raises(RuntimeError, match="boto3 not available"):
                seed_counters()
