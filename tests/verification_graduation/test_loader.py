"""New loader classes (Decision 176 verification-registry-sharding): TestBaselineReaderSpansLayouts,
TestDeprecatedSubtreeExcluded, TestLoaderOrdering, TestConcurrentGraduationsMergeClean.

Covers scripts.verification_graduation.load_entries / entries_at_ref -- the sole read path for
the sharded config/agent/verification_registry/entries/<check_id>.yaml registry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from scripts import verification_graduation as vg

from .conftest import _commit_all, _git, _init_repo


class TestFastLoader:
    """Both registry loaders parse through scripts.contract_rules.parse_yaml."""

    def test_load_entries_uses_parse_yaml(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "a-check.yaml").write_text("check_id: a-check\n", encoding="utf-8")
        with mock.patch.object(vg._contract_rules, "parse_yaml", wraps=vg._contract_rules.parse_yaml) as spy:
            rows = vg.load_entries(repo_root=tmp_path)
        assert rows == [{"check_id": "a-check"}]
        assert spy.call_count == 1

    def test_entries_at_ref_uses_parse_yaml(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "a-check.yaml").write_text("check_id: a-check\n", encoding="utf-8")
        sha = _commit_all(repo, "shard")
        with mock.patch.object(vg._contract_rules, "parse_yaml", wraps=vg._contract_rules.parse_yaml) as spy:
            rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows == [{"check_id": "a-check"}]
        assert spy.call_count == 1


class TestBaselineReaderSpansLayouts:
    """VP step 4: entries_at_ref spans both the sharded and legacy-flat layouts, and never
    silently returns an empty baseline when a resolving ref carries neither (Decision 55) --
    the ~56-minute melt guard (every record misread as newly added)."""

    def test_shard_directory_layout(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "a-check.yaml").write_text("check_id: a-check\nprimitive_slot: command_exit_zero\n", encoding="utf-8")
        (entries_dir / "b-check.yaml").write_text("check_id: b-check\nprimitive_slot: command_exit_zero\n", encoding="utf-8")
        sha = _commit_all(repo, "shards")
        rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows is not None
        assert {r["check_id"] for r in rows} == {"a-check", "b-check"}

    def test_legacy_flat_layout(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        flat = repo / vg.REGISTRY_DIR_REL / vg.LEGACY_FLAT_BASENAME
        flat.parent.mkdir(parents=True)
        flat.write_text("entries:\n  - check_id: legacy-check\n    primitive_slot: command_exit_zero\n", encoding="utf-8")
        sha = _commit_all(repo, "legacy flat")
        rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows is not None
        assert [r["check_id"] for r in rows] == ["legacy-check"]

    def test_ref_resolves_neither_layout_present_fails_loud(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("x\n", encoding="utf-8")
        sha = _commit_all(repo, "no registry at all")
        with pytest.raises(vg.GraduationError, match="refusing to return an empty baseline"):
            vg.entries_at_ref(sha, repo_root=repo)

    def test_ref_does_not_resolve_returns_none(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("x\n", encoding="utf-8")
        _commit_all(repo, "base")
        assert vg.entries_at_ref("refs/heads/does-not-exist", repo_root=repo) is None

    def test_malformed_legacy_flat_is_empty(self, tmp_path: Path) -> None:
        """Kept from the incumbent TestEntriesAtRef: a malformed legacy-flat ref is a lenient
        empty baseline, not a crash -- distinct from branch (iii)'s fail-loud (both layouts
        genuinely ABSENT, not merely unparseable)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        flat = repo / vg.REGISTRY_DIR_REL / vg.LEGACY_FLAT_BASENAME
        flat.parent.mkdir(parents=True)
        flat.write_text("entries: [\n  - broken: yaml: :", encoding="utf-8")
        sha = _commit_all(repo, "malformed legacy")
        assert vg.entries_at_ref(sha, repo_root=repo) == []

    def test_non_dict_legacy_flat_is_empty(self, tmp_path: Path) -> None:
        """Kept from the incumbent TestEntriesAtRef: a non-dict document at the legacy-flat ref
        is a lenient empty baseline."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        flat = repo / vg.REGISTRY_DIR_REL / vg.LEGACY_FLAT_BASENAME
        flat.parent.mkdir(parents=True)
        flat.write_text("just-a-string\n", encoding="utf-8")
        sha = _commit_all(repo, "non-dict legacy")
        assert vg.entries_at_ref(sha, repo_root=repo) == []


class TestDeprecatedSubtreeExcluded:
    """VP step 7: entries/deprecated/ is excluded from the live set by a LOADER GLOB rule
    (single-level, non-recursive Path.glob), never a documented convention -- so retirement by
    `git mv` actually retires, for both the live-tree and at-a-ref readers."""

    def test_load_entries_excludes_deprecated(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "live-check.yaml").write_text("check_id: live-check\n", encoding="utf-8")
        deprecated = entries_dir / "deprecated"
        deprecated.mkdir()
        (deprecated / "dead-check.yaml").write_text("check_id: dead-check\n", encoding="utf-8")
        rows = vg.load_entries(repo_root=tmp_path)
        assert {r["check_id"] for r in rows} == {"live-check"}

    def test_entries_at_ref_excludes_deprecated(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "live-check.yaml").write_text("check_id: live-check\n", encoding="utf-8")
        deprecated = entries_dir / "deprecated"
        deprecated.mkdir()
        (deprecated / "dead-check.yaml").write_text("check_id: dead-check\n", encoding="utf-8")
        sha = _commit_all(repo, "with deprecated subtree")
        rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows is not None
        assert {r["check_id"] for r in rows} == {"live-check"}


class TestLoaderOrdering:
    """VP step 8: load_entries() returns records sorted by filename, stable across repeated
    calls -- no consumer may depend on directory-listing order instead."""

    def test_returns_sorted_by_filename(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        for cid in ["zebra-check", "alpha-check", "mid-check"]:
            (entries_dir / f"{cid}.yaml").write_text(f"check_id: {cid}\n", encoding="utf-8")
        rows = vg.load_entries(repo_root=tmp_path)
        assert [r["check_id"] for r in rows] == ["alpha-check", "mid-check", "zebra-check"]

    def test_ordering_stable_across_repeated_calls(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        for cid in ["c-check", "a-check", "b-check"]:
            (entries_dir / f"{cid}.yaml").write_text(f"check_id: {cid}\n", encoding="utf-8")
        first = [r["check_id"] for r in vg.load_entries(repo_root=tmp_path)]
        second = [r["check_id"] for r in vg.load_entries(repo_root=tmp_path)]
        assert first == second == ["a-check", "b-check", "c-check"]


class TestConcurrentGraduationsMergeClean:
    """rec-3231 acceptance: two branches cut from the same base, each adding one graduated
    record, merge with no conflict in the sharded layout. The discriminating half: the identical
    scenario against a single flat sequence DOES conflict. The one conflict that must survive:
    two branches minting the SAME check_id still produce an add/add conflict."""

    def _base_repo(self, tmp_path: Path, sharded: bool) -> Path:
        repo = tmp_path / "repo"
        _init_repo(repo)
        if sharded:
            (repo / "config" / "agent" / "verification_registry" / "entries").mkdir(parents=True)
            (repo / "config" / "agent" / "verification_registry" / "entries" / ".gitkeep").write_text("", encoding="utf-8")
        else:
            flat = repo / vg.REGISTRY_DIR_REL / vg.LEGACY_FLAT_BASENAME
            flat.parent.mkdir(parents=True)
            flat.write_text("entries:\n  - check_id: pre-existing\n    primitive_slot: command_exit_zero\n", encoding="utf-8")
        _commit_all(repo, "base")
        _git(repo, ["branch", "-M", "main"])
        return repo

    def test_sharded_layout_merges_clean(self, tmp_path: Path) -> None:
        repo = self._base_repo(tmp_path, sharded=True)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"

        _git(repo, ["checkout", "-q", "-b", "branch-a"])
        (entries_dir / "check-a.yaml").write_text("check_id: check-a\nprimitive_slot: command_exit_zero\n", encoding="utf-8")
        _commit_all(repo, "graduate check-a")

        _git(repo, ["checkout", "-q", "main"])
        _git(repo, ["checkout", "-q", "-b", "branch-b"])
        (entries_dir / "check-b.yaml").write_text("check_id: check-b\nprimitive_slot: command_exit_zero\n", encoding="utf-8")
        _commit_all(repo, "graduate check-b")

        _git(repo, ["checkout", "-q", "main"])
        _git(repo, ["merge", "-q", "--no-ff", "-m", "merge branch-a", "branch-a"])
        result = _git(repo, ["merge", "--no-ff", "-m", "merge branch-b", "branch-b"])
        assert result.returncode == 0, result.stderr
        assert (entries_dir / "check-a.yaml").exists()
        assert (entries_dir / "check-b.yaml").exists()

    def test_flat_layout_conflicts_the_discriminating_half(self, tmp_path: Path) -> None:
        repo = self._base_repo(tmp_path, sharded=False)
        flat = repo / vg.REGISTRY_DIR_REL / vg.LEGACY_FLAT_BASENAME

        _git(repo, ["checkout", "-q", "-b", "branch-a"])
        flat.write_text(
            "entries:\n  - check_id: pre-existing\n    primitive_slot: command_exit_zero\n"
            "  - check_id: check-a\n    primitive_slot: command_exit_zero\n",
            encoding="utf-8",
        )
        _commit_all(repo, "graduate check-a")

        _git(repo, ["checkout", "-q", "main"])
        _git(repo, ["checkout", "-q", "-b", "branch-b"])
        flat.write_text(
            "entries:\n  - check_id: pre-existing\n    primitive_slot: command_exit_zero\n"
            "  - check_id: check-b\n    primitive_slot: command_exit_zero\n",
            encoding="utf-8",
        )
        _commit_all(repo, "graduate check-b")

        _git(repo, ["checkout", "-q", "main"])
        _git(repo, ["merge", "-q", "--no-ff", "-m", "merge branch-a", "branch-a"])
        result = subprocess.run(
            ["git", "merge", "--no-ff", "-m", "merge branch-b", "branch-b"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode != 0, "the flat list should conflict -- this is the discriminating half"
        _git(repo, ["merge", "--abort"])

    def test_same_check_id_is_a_legitimate_add_add_conflict(self, tmp_path: Path) -> None:
        repo = self._base_repo(tmp_path, sharded=True)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"

        _git(repo, ["checkout", "-q", "-b", "branch-a"])
        (entries_dir / "same-check.yaml").write_text(
            "check_id: same-check\nprimitive_slot: command_exit_zero\nplan_slug: a\n", encoding="utf-8"
        )
        _commit_all(repo, "branch-a graduates same-check")

        _git(repo, ["checkout", "-q", "main"])
        _git(repo, ["checkout", "-q", "-b", "branch-b"])
        (entries_dir / "same-check.yaml").write_text(
            "check_id: same-check\nprimitive_slot: command_exit_zero\nplan_slug: b\n", encoding="utf-8"
        )
        _commit_all(repo, "branch-b graduates same-check")

        _git(repo, ["checkout", "-q", "main"])
        _git(repo, ["merge", "-q", "--no-ff", "-m", "merge branch-a", "branch-a"])
        result = subprocess.run(
            ["git", "merge", "--no-ff", "-m", "merge branch-b", "branch-b"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode != 0, "minting the same check_id from two branches must still conflict"
        _git(repo, ["merge", "--abort"])


class TestLoaderEdgeCases:
    """Direct coverage for shard_path_for and the load_entries/entries_at_ref branches not
    exercised by the VP-named classes above: the missing-directory short-circuit, a malformed
    or non-mapping live shard (fail-loud, Decision 55), and entries_at_ref's shard-branch
    per-file git-show failure / malformed-YAML-at-ref tolerance."""

    def test_shard_path_for_default_root(self) -> None:
        path = vg.shard_path_for("some-check")
        assert path == vg.ROOT / "config" / "agent" / "verification_registry" / "entries" / "some-check.yaml"

    def test_shard_path_for_explicit_root(self, tmp_path: Path) -> None:
        path = vg.shard_path_for("some-check", repo_root=tmp_path)
        assert path == tmp_path / "config" / "agent" / "verification_registry" / "entries" / "some-check.yaml"

    def test_load_entries_missing_directory_is_empty(self, tmp_path: Path) -> None:
        assert vg.load_entries(repo_root=tmp_path) == []

    def test_load_entries_malformed_shard_raises(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "bad.yaml").write_text("{broken: [", encoding="utf-8")
        with pytest.raises(vg.GraduationError, match="malformed shard"):
            vg.load_entries(repo_root=tmp_path)

    def test_load_entries_non_mapping_shard_raises(self, tmp_path: Path) -> None:
        entries_dir = tmp_path / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "bad.yaml").write_text("just-a-string\n", encoding="utf-8")
        with pytest.raises(vg.GraduationError, match="is not a mapping"):
            vg.load_entries(repo_root=tmp_path)

    def test_entries_at_ref_shard_branch_tolerates_show_failure(self, tmp_path: Path) -> None:
        """A shard path ls-tree lists but git show subsequently fails for is skipped, not
        crashed (defensive -- ls-tree/show run against the same immutable ref so this should
        not occur in practice, but the reader must not assume it can't)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "a-check.yaml").write_text("check_id: a-check\n", encoding="utf-8")
        (entries_dir / "b-check.yaml").write_text("check_id: b-check\n", encoding="utf-8")
        sha = _commit_all(repo, "two shards")

        from scripts.checks.verification import _graduation_git

        real_read = _graduation_git.read_blobs_at_ref

        def fake_read(ref, paths, root):
            blobs = real_read(ref, paths, root)
            return {p: t for p, t in blobs.items() if "a-check.yaml" not in p}

        with mock.patch.object(_graduation_git, "read_blobs_at_ref", fake_read):
            rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows is not None
        assert [r["check_id"] for r in rows] == ["b-check"]

    def test_entries_at_ref_shard_branch_tolerates_malformed_yaml(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        entries_dir = repo / "config" / "agent" / "verification_registry" / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "good-check.yaml").write_text("check_id: good-check\n", encoding="utf-8")
        (entries_dir / "bad-check.yaml").write_text("{broken: [", encoding="utf-8")
        sha = _commit_all(repo, "one good, one malformed")
        rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows is not None
        assert [r["check_id"] for r in rows] == ["good-check"]

    def test_shard_paths_at_ref_genuine_git_failure_falls_back(self, tmp_path: Path) -> None:
        """A genuine ls-tree failure (distinct from "path absent, exit 0") is tolerated by
        _shard_paths_at_ref (returns []), which then routes entries_at_ref to try the legacy
        flat-file branch next rather than crashing."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        flat = repo / vg.REGISTRY_DIR_REL / vg.LEGACY_FLAT_BASENAME
        flat.parent.mkdir(parents=True)
        flat.write_text("entries:\n  - check_id: legacy-check\n    primitive_slot: command_exit_zero\n", encoding="utf-8")
        sha = _commit_all(repo, "legacy flat only")

        real_run_git = vg._run_git

        def fake_run_git(args, cwd):
            if args[:1] == ["ls-tree"]:
                return subprocess.CompletedProcess(args=["git", *args], returncode=128, stdout="", stderr="simulated failure")
            return real_run_git(args, cwd)

        with mock.patch.object(vg, "_run_git", fake_run_git):
            rows = vg.entries_at_ref(sha, repo_root=repo)
        assert rows is not None
        assert [r["check_id"] for r in rows] == ["legacy-check"]
