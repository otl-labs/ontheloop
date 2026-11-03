"""validate.main() summary wiring: one gate marker per failed check and scaffold step (rec-4172)."""

from __future__ import annotations

import itertools
import sys
from unittest.mock import patch

import pytest

from scripts.checks import registry, validation_result
from scripts.ci_rca.taxonomy import _parse_failed_checks_block
from scripts.executor.run_summary import _extract_validation_failed_checks
from src.turn_capture.markers import parse_markers
from tests.fixtures.subprocess_stubs import _pre_mock_run
from tests.fixtures.validate_module import _validate

_CHECK = "synthetic_gate_check"


@pytest.fixture(autouse=True)
def _reset_accumulators():
    validation_result._ATTRIBUTIONS.clear()
    validation_result._SCAFFOLD_FAILURES.clear()
    yield
    validation_result._ATTRIBUTIONS.clear()
    validation_result._SCAFFOLD_FAILURES.clear()


@pytest.fixture(autouse=True)
def _refuse_unstubbed_subprocess():
    def _refuse(cmd, *args, **kwargs):
        raise AssertionError(f"unstubbed scripts.checks._common.run({cmd!r}): stub it with _pre_mock_run")

    with patch("scripts.checks._common.run", side_effect=_refuse):
        yield


def test_unstubbed_subprocess_is_refused() -> None:
    from scripts.checks import _common

    with pytest.raises(AssertionError, match="show-current"):
        _common.run(["git", "branch", "--show-current"])


def _failing_check(failed: list[str]) -> None:
    failed.append("synthetic failure label")


def _failing_precommit(failed: list[str], **_: object) -> None:
    failed.append("synthetic precommit label")


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str], steps: list[registry.Step], *, fail: bool) -> int:
    monkeypatch.setattr(sys, "argv", ["validate", *argv])
    monkeypatch.setenv("_VALIDATE_DEPTH", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    check = _failing_check if fail else (lambda failed: None)
    precommit = _failing_precommit if fail else (lambda failed, **_: None)
    patches = [
        patch.object(registry, "pre_sequence", return_value=steps),
        patch.object(registry, "full_sequence", return_value=steps),
        patch.object(registry, "resolve", return_value=check),
        patch("validate.run_precommit_checks", side_effect=precommit),
        patch("scripts.checks._common.get_changed_files", return_value=[]),
        patch("scripts.checks._common.run", side_effect=_pre_mock_run),
        patch("time.monotonic", side_effect=itertools.chain([0.0], itertools.repeat(1.0))),
        patch("validate.validation_result.clear"),
        patch("validate.validation_result.write_completed_visible"),
    ]
    for p in patches:
        p.start()
    try:
        with pytest.raises(SystemExit) as exc:
            _validate.main()
    finally:
        for p in reversed(patches):
            p.stop()
    return exc.value.code


def _steps(scaffold: str = "precommit_changed") -> list[registry.Step]:
    return [registry.Step("check", _CHECK), registry.Step("scaffold", scaffold)]


def _assert_labels_then_markers(out: str, scaffold: str) -> None:
    lines = out.splitlines()
    start = lines.index("Failed checks:")
    assert lines[start + 1 : start + 3] == ["  - synthetic failure label", "  - synthetic precommit label"]
    assert lines[start + 3 : start + 5] == [
        f"GATE-FAILURE v1 source=gate check={_CHECK}",
        f"GATE-FAILURE v1 source=gate check={scaffold}",
    ]
    assert [(m.source, m.signature) for m in parse_markers(out)] == [("gate", _CHECK), ("gate", scaffold)]


def test_pre_tier_summary_prints_markers_after_labels(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    assert _run_main(monkeypatch, ["--pre"], _steps(), fail=True) == 1
    _assert_labels_then_markers(capsys.readouterr().out, "precommit_changed")


def test_full_tier_summary_prints_markers_after_labels(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    assert _run_main(monkeypatch, [], _steps("precommit_all_files"), fail=True) == 1
    _assert_labels_then_markers(capsys.readouterr().out, "precommit_all_files")


def test_clean_run_and_terraform_only_print_no_marker(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    assert _run_main(monkeypatch, ["--pre"], _steps(), fail=False) == 0
    assert "GATE-FAILURE" not in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["validate", "--terraform-only"])
    monkeypatch.setenv("_VALIDATE_DEPTH", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    def fail_terraform(failed: list[str]) -> None:
        failed.append("terraform label")

    with (
        patch("validate.run_terraform_creds_free", side_effect=fail_terraform),
        patch("scripts.checks._common.run", side_effect=_pre_mock_run),
        pytest.raises(SystemExit) as exc,
    ):
        _validate.main()
    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "  - terraform label" in out
    assert "GATE-FAILURE" not in out


def test_stdout_consumers_still_read_only_the_labels(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _run_main(monkeypatch, ["--pre"], _steps(), fail=True)
    out = capsys.readouterr().out
    expected = ["synthetic failure label", "synthetic precommit label"]
    assert _parse_failed_checks_block(out) == expected
    assert _extract_validation_failed_checks(out) == expected
