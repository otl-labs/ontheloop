"""Tests for validate_prose_allowlist() (Decision 127 sanctioned-prose gate).

VP step 1: positive (all-listed passes), negative (an unlisted .md path fails), and
fail-open (missing prose_allowlist key -> warning, no failure) cases.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.checks.hygiene.validate_prose_allowlist import path_allowed, validate_prose_allowlist


def _mock_ls_files(tracked_paths: list[str]):
    def _run(cmd: list, **kwargs: object) -> MagicMock:
        result = MagicMock()
        result.returncode = 0
        result.stdout = "".join(f"{p}\n" for p in tracked_paths)
        result.stderr = ""
        return result

    return _run


def _router(tmp_path: Path, content: str) -> Path:
    router = tmp_path / "file-router.yaml"
    router.write_text(content, encoding="utf-8")
    return router


class TestPathAllowed:
    """Unit coverage of the recursive-** matcher independent of the check body."""

    def test_bare_root_filename_matches_exact(self) -> None:
        assert path_allowed("CLAUDE.md", ["CLAUDE.md"])
        assert not path_allowed("terraform/CLAUDE.md", ["CLAUDE.md"])

    def test_double_star_matches_zero_or_more_segments(self) -> None:
        globs = ["**/CLAUDE.md"]
        assert path_allowed("CLAUDE.md", globs)
        assert path_allowed("terraform/CLAUDE.md", globs)
        assert path_allowed("a/b/CLAUDE.md", globs)
        assert not path_allowed("terraform/NOTCLAUDE.md", globs)

    def test_middle_double_star_matches_direct_child_and_nested(self) -> None:
        globs = ["docs/contracts/**/*.md"]
        assert path_allowed("docs/contracts/delegate-cli.md", globs)
        assert path_allowed("docs/contracts/sub/delegate-cli.md", globs)
        assert not path_allowed("docs/other/delegate-cli.md", globs)


def test_persona_guidance_case_files_are_sanctioned() -> None:
    import yaml

    router = Path(__file__).resolve().parents[3] / "docs/contracts/file-router.yaml"
    globs = yaml.safe_load(router.read_text(encoding="utf-8"))["prose_allowlist"]["allowed_globs"]
    base = "tests/fixtures/persona_verdict"
    assert path_allowed(f"{base}/guidance_cases/canary_persona/benign_add.md", globs)
    assert not path_allowed(f"{base}/fixture_pr/notes.md", globs)
    assert not path_allowed("tests/fixtures/other/x.md", globs)
    assert not path_allowed(f"{base}/guidance_cases/x.md", globs)
    assert not path_allowed(f"{base}/guidance_cases/canary_persona/sub/x.md", globs)


class TestValidateProseAllowlist:
    def test_all_listed_files_pass(self, tmp_path: Path) -> None:
        router = _router(
            tmp_path,
            "schema_version: 1\n"
            "prose_allowlist:\n"
            "  allowed_globs:\n"
            "    - CLAUDE.md\n"
            "    - '**/CLAUDE.md'\n"
            "  grandfathered_globs:\n"
            "    - docs/DECISIONS.md\n",
        )
        with patch(
            "scripts.checks._common.run",
            side_effect=_mock_ls_files(["CLAUDE.md", "terraform/CLAUDE.md", "docs/DECISIONS.md"]),
        ):
            failed: list[str] = []
            validate_prose_allowlist(failed, router_path=router)
        assert failed == []

    def test_unlisted_path_fails(self, tmp_path: Path) -> None:
        router = _router(
            tmp_path,
            "schema_version: 1\nprose_allowlist:\n  allowed_globs:\n    - CLAUDE.md\n  grandfathered_globs: []\n",
        )
        with patch(
            "scripts.checks._common.run",
            side_effect=_mock_ls_files(["CLAUDE.md", "docs/rogue-prose.md"]),
        ):
            failed: list[str] = []
            validate_prose_allowlist(failed, router_path=router)
        assert len(failed) == 1
        assert "docs/rogue-prose.md" in failed[0]

    def test_missing_key_fails_open(self, tmp_path: Path) -> None:
        router = _router(tmp_path, "schema_version: 1\nroutes: []\n")
        with patch("scripts.checks._common.run", side_effect=_mock_ls_files(["anything.md"])):
            failed: list[str] = []
            validate_prose_allowlist(failed, router_path=router)
        assert failed == []

    def test_missing_router_file_fails_open(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_prose_allowlist(failed, router_path=tmp_path / "does-not-exist.yaml")
        assert failed == []

    def test_malformed_allowed_globs_fails(self, tmp_path: Path) -> None:
        router = _router(
            tmp_path,
            "schema_version: 1\nprose_allowlist:\n  allowed_globs: not-a-list\n  grandfathered_globs: []\n",
        )
        failed: list[str] = []
        validate_prose_allowlist(failed, router_path=router)
        assert len(failed) == 1
        assert "allowed_globs" in failed[0]


def test_marketing_prose_is_not_sanctioned() -> None:
    import yaml

    router = Path(__file__).resolve().parents[3] / "docs/contracts/file-router.yaml"
    globs = yaml.safe_load(router.read_text(encoding="utf-8"))["prose_allowlist"]["allowed_globs"]
    assert not path_allowed("marketing/blog/launch.md", globs)
    assert not path_allowed("marketing/index.md", globs)
