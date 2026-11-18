"""Tests for scripts/checks/ci_guards/_shared.py (rec-5289): the one SHA-pin predicate and the root-path helper."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.ci_guards import _shared

_SHA = "a" * 40


@pytest.mark.parametrize(
    "value", [f"actions/checkout@{_SHA}", f"github/codeql-action/init@{_SHA}", f"o/r/.github/workflows/x.yml@{_SHA}"]
)
def test_pinned_values_pass(value: str) -> None:
    assert _shared.is_sha_pinned(value)


@pytest.mark.parametrize(
    "value",
    [
        "actions/checkout@v7",
        f"actions/checkout@{_SHA[:39]}",
        f"actions/checkout@{_SHA.upper()}",
        f"actions/checkout@{_SHA} # v7.0.1",
        f"actions/checkout@{_SHA}x",
        f"@{_SHA}",
        f"a b@{_SHA}",
        f"o/r@x@{_SHA}",
        "./.github/actions/x",
        f"docker://alpine@sha256:{'b' * 64}",
    ],
)
def test_unpinned_values_fail(value: str) -> None:
    assert not _shared.is_sha_pinned(value)


def test_ensure_root_on_path_injects_once(tmp_path: Path) -> None:
    with patch("scripts.checks._common.ROOT", tmp_path), patch.object(sys, "path", list(sys.path)):
        assert _shared._ensure_root_on_path() is True
        assert sys.path[0] == str(tmp_path)
        assert _shared._ensure_root_on_path() is False
