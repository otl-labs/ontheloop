"""Tests for scripts.repo_identity: GITHUB_REPOSITORY wins when well-formed, else the fallback; never raises."""

from __future__ import annotations

import pytest

from scripts import repo_identity


def test_ci_repository_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", " example-org/example-repo\n")
    assert repo_identity.full_name() == "example-org/example-repo"
    assert repo_identity.owner_and_name() == ("example-org", "example-repo")


def test_unset_environment_falls_back_to_the_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert repo_identity.full_name() == "benjamin-blake/theseus"
    assert repo_identity.FALLBACK_FULL_NAME == "benjamin-blake/theseus"


@pytest.mark.parametrize("value", ["", "   ", "example-org", "/example-repo", "example-org/", "a/b/c", "example-org//x"])
def test_malformed_environment_falls_back_to_the_constant(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", value)
    assert repo_identity.full_name() == "benjamin-blake/theseus"


def test_resolve_fills_only_missing_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/example-repo")
    assert repo_identity.resolve(None, None) == ("example-org", "example-repo")
    assert repo_identity.resolve("other-org", None) == ("other-org", "example-repo")
    assert repo_identity.resolve(None, "other-repo") == ("example-org", "other-repo")
    assert repo_identity.resolve("other-org", "other-repo") == ("other-org", "other-repo")
