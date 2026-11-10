"""Restore-rehearsal CLI flag dispatch (rec-5023)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts import ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import restore_rehearsal as rr

pytestmark = pytest.mark.unit

RUN = "20261109t193000_a1b2c3"


# ---------------------------------------------------------------- CLI flags


@pytest.mark.parametrize(
    "flags,subcommand",
    [
        (["--restore-rehearsal"], "run"),
        (["--restore-rehearsal-version-recovery"], "version-recovery"),
    ],
)
def test_the_two_cli_flags_dispatch_and_pass_values_through(flags, subcommand):
    argv = [
        *flags,
        "--profile",
        "admin",
        "--dev-profile",
        "dev",
        "--run-id",
        RUN,
        "--project-id",
        "proj-1",
        "--evidence-out",
        "/tmp/e.json",
        "--dry-run",
        "--restore-final",
    ]
    with patch.object(rr, "main", return_value=0) as main:
        assert smoke.main(argv) == 0
    passed = main.call_args.args[0]
    assert passed[0] == subcommand
    for pair in (
        ("--profile", "admin"),
        ("--dev-profile", "dev"),
        ("--run-id", RUN),
        ("--project-id", "proj-1"),
        ("--evidence-out", "/tmp/e.json"),
    ):
        assert passed[passed.index(pair[0]) + 1] == pair[1]
    assert "--dry-run" in passed and "--restore-final" in passed


def test_cli_without_optional_values_passes_none_of_them():
    with patch.object(rr, "main", return_value=0) as main:
        smoke.main(["--restore-rehearsal-version-recovery"])
    passed = main.call_args.args[0]
    assert "--profile" not in passed and "--run-id" not in passed and "--dry-run" not in passed
    assert passed[:3] == ["version-recovery", "--dev-profile", "agent_platform"]
