"""CI-parity entry point: only _entrypoint() re-execs; --full-env/--ignore-budget print the not-predictive banner."""

import itertools
import sys
from unittest.mock import MagicMock, patch

import pytest

from scripts.checks import registry
from scripts.checks.deps import fast_tier_env
from scripts.checks.deps import module_cost_table as mct
from tests.fixtures.subprocess_stubs import _pre_mock_run
from tests.fixtures.validate_module import _validate


@pytest.fixture(autouse=True)
def _pin_cost_table_absent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mct, "read_at_base_ref", lambda root: mct.CostTableRead("absent", {}, 0.0))


def _drive_main(monkeypatch: pytest.MonkeyPatch, pre_sequence_stub, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setenv("_VALIDATE_DEPTH", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    with (
        patch("scripts.checks._common.get_changed_files", return_value=[]),
        patch("scripts.checks._common.run", side_effect=_pre_mock_run),
        patch.object(registry, "pre_sequence", return_value=pre_sequence_stub(checks=())),
        patch("validate._file_budget_bypass_rec"),
        patch("time.monotonic", side_effect=itertools.chain([0.0], itertools.repeat(60.0))),
        pytest.raises(SystemExit) as exc_info,
    ):
        _validate.main()
    return exc_info.value.code


class TestEntrypoint:
    def test_reexec_hook_runs_before_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = MagicMock()
        monkeypatch.setattr(fast_tier_env, "reexec_pre_if_needed", calls.reexec)
        monkeypatch.setattr(_validate, "main", calls.main)
        monkeypatch.setattr(sys, "argv", ["validate", "--pre"])

        _validate._entrypoint()

        assert [c[0] for c in calls.mock_calls] == ["reexec", "main"]
        assert calls.reexec.call_args.args[0] == ["validate", "--pre"]


class TestMainNeverReexecs:
    def test_main_under_pre_does_not_reach_the_hook(self, monkeypatch: pytest.MonkeyPatch, pre_sequence_stub) -> None:
        hook = MagicMock()
        monkeypatch.setattr(fast_tier_env, "reexec_pre_if_needed", hook)

        code = _drive_main(monkeypatch, pre_sequence_stub, ["validate", "--pre"])

        assert code == 0
        hook.assert_not_called()


class TestFullEnvBanner:
    @pytest.mark.parametrize("flag", ["--full-env", "--ignore-budget"])
    def test_bypass_flags_print_banner(
        self, monkeypatch: pytest.MonkeyPatch, pre_sequence_stub, capsys: pytest.CaptureFixture, flag: str
    ) -> None:
        _drive_main(monkeypatch, pre_sequence_stub, ["validate", "--pre", flag])

        assert fast_tier_env.FULL_ENV_BANNER in capsys.readouterr().out

    def test_plain_pre_prints_no_banner(
        self, monkeypatch: pytest.MonkeyPatch, pre_sequence_stub, capsys: pytest.CaptureFixture
    ) -> None:
        _drive_main(monkeypatch, pre_sequence_stub, ["validate", "--pre"])

        assert fast_tier_env.FULL_ENV_BANNER not in capsys.readouterr().out

    def test_abbreviated_flag_is_rejected(self, monkeypatch: pytest.MonkeyPatch, pre_sequence_stub) -> None:
        assert _drive_main(monkeypatch, pre_sequence_stub, ["validate", "--pr"]) == 2
