"""Mirror tests for scripts/session/handoff_evidence.py. Real tmp repos with a bare origin;
evidence is written by validation_result's real clear()/write_completed()."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, validation_result
from scripts.session import handoff_evidence
from scripts.session.handoff_push_parse import ParsedPush, parse_canonical

_GIT_ENV = ["-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]

_GIT_OPS_YAML = (
    'branching_topology:\n  agent_branch_prefixes: ["claude/", "agent/"]\n'
    'handoff_evidence_gate:\n  exempt_path_globs: ["docs/plans/*"]\n'
    '  never_exempt_paths: ["docs/contracts/git-ops.yaml"]\n  extra_destination_prefixes: ["audit/"]\n'
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _git_ok(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _make_repo(tmp_path: Path, *, seed_gate_clause: bool = True) -> Path:
    """Bare origin + a clone with an initial, pushed commit seeding git-ops.yaml."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(["init", "--bare", "-b", "main"], cwd=origin)

    work = tmp_path / "work"
    work.mkdir()
    _git(["init", "-b", "main"], cwd=work)
    _git(["remote", "add", "origin", str(origin)], cwd=work)
    (work / "docs").mkdir()
    (work / "docs" / "contracts").mkdir()
    contents = _GIT_OPS_YAML if seed_gate_clause else "branching_topology:\n  agent_branch_prefixes: [claude/, agent/]\n"
    (work / "docs" / "contracts" / "git-ops.yaml").write_text(contents, encoding="utf-8")
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=work)
    _git([*_GIT_ENV, "commit", "-m", "initial"], cwd=work)
    _git(["push", "-u", "origin", "main"], cwd=work)
    return work


def _new_branch_worktree(work: Path, branch: str) -> Path:
    parent = work.parent / f"wt-{branch.replace('/', '-')}"
    _git(["worktree", "add", "-b", branch, str(parent), "main"], cwd=work)
    return parent


def _commit_file(worktree: Path, rel_path: str, content: str, message: str = "change") -> str:
    target = worktree / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(["add", "-A"], cwd=worktree)
    _git([*_GIT_ENV, "commit", "-m", message], cwd=worktree)
    return _git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def _write_real_evidence(worktree: Path, *, ok: bool = True) -> None:
    """Use 2a's REAL clear()/write_completed() against `worktree`'s own evidence path."""
    path = validation_result.evidence_path_for(worktree)
    orig_root = validation_result._common.ROOT
    try:
        validation_result._common.ROOT = worktree
        validation_result.clear(path)
        validation_result.write_completed_visible(
            started_at=validation_result.utc_now(),
            exit_code=0 if ok else 1,
            failed_checks=[] if ok else ["some_check"],
            path=path,
        )
    finally:
        validation_result._common.ROOT = orig_root


class TestPushDecision:
    def test_denies_evidence_for_a_different_tree(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _write_real_evidence(wt, ok=True)
        # A second, later commit changes the tree -- the evidence still attests the FIRST one.
        second_sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        verdict = handoff_evidence.decide_push(wt, second_sha)
        assert verdict.kind == "deny"
        assert verdict.rule == "tree_mismatch"

    def test_pass_on_clean_matching_evidence(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)
        verdict = handoff_evidence.decide_push(wt, sha)
        assert verdict.kind == "pass"
        assert verdict.rule == "ok"

    def test_warn_on_failed_run(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=False)
        verdict = handoff_evidence.decide_push(wt, sha)
        assert verdict.kind == "warn"
        assert verdict.rule == "failed_run"

    def test_deny_on_missing_evidence(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        verdict = handoff_evidence.decide_push(wt, sha)
        assert verdict.kind == "deny"
        assert verdict.rule == "missing"

    def test_deny_on_unreadable_evidence(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        path = validation_result.evidence_path_for(wt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        verdict = handoff_evidence.decide_push(wt, sha)
        assert verdict.kind == "deny"
        assert verdict.rule == "unreadable"

    def test_deny_on_unresolvable_sha(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        verdict = handoff_evidence.decide_push(wt, "0" * 40)
        assert verdict.kind == "deny"
        assert verdict.rule == "unresolvable_sha"

    def test_deny_on_dirty_start(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        (wt / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        path = validation_result.evidence_path_for(wt)
        orig_root = validation_result._common.ROOT
        try:
            validation_result._common.ROOT = wt
            validation_result.clear(path)
            (wt / "uncommitted.txt").unlink()
            validation_result.write_completed_visible(
                started_at=validation_result.utc_now(), exit_code=0, failed_checks=[], path=path
            )
        finally:
            validation_result._common.ROOT = orig_root
        verdict = handoff_evidence.decide_push(wt, sha)
        assert verdict.kind == "deny"
        assert verdict.rule == "dirty_start"


class TestBaseRefReadUnknowns:
    def test_load_base_ref_yaml_no_origin_remote(self, tmp_path: Path) -> None:
        repo = tmp_path / "solo"
        repo.mkdir()
        _git(["init", "-b", "main"], cwd=repo)
        (repo / "README.md").write_text("x\n", encoding="utf-8")
        _git(["add", "-A"], cwd=repo)
        _git([*_GIT_ENV, "commit", "-m", "x"], cwd=repo)
        assert handoff_evidence._load_base_ref_yaml(repo) is None

    def test_base_ref_clause_none_on_malformed_yaml(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        (work / "docs" / "contracts" / "git-ops.yaml").write_text("not: {valid", encoding="utf-8")
        _git([*_GIT_ENV, "commit", "-am", "break it"], cwd=work)
        _git(["push", "origin", "main"], cwd=work)
        assert handoff_evidence._base_ref_clause(work) is None

    @pytest.mark.parametrize(
        "yaml_text",
        ["branching_topology: not-a-dict\n", "branching_topology:\n  agent_branch_prefixes: [1, 2]\n"],
    )
    def test_malformed_agent_branch_prefixes(self, tmp_path: Path, yaml_text: str) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        (work / "docs" / "contracts" / "git-ops.yaml").write_text(yaml_text, encoding="utf-8")
        _git([*_GIT_ENV, "commit", "-am", "break it"], cwd=work)
        _git(["push", "origin", "main"], cwd=work)
        assert handoff_evidence._base_ref_agent_branch_prefixes(work) is None

    def test_extra_destination_prefixes_not_a_list_falls_back_to_empty(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        (work / "docs" / "contracts" / "git-ops.yaml").write_text(
            "branching_topology:\n  agent_branch_prefixes: [claude/]\n"
            "handoff_evidence_gate:\n  extra_destination_prefixes: not-a-list\n",
            encoding="utf-8",
        )
        _git([*_GIT_ENV, "commit", "-am", "break it"], cwd=work)
        _git(["push", "origin", "main"], cwd=work)
        assert handoff_evidence.destination_prefixes(work) == ["claude/"]

    def test_changed_paths_none_on_diff_failure(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        assert handoff_evidence._changed_paths(work, "0" * 40, "HEAD") is None

    def test_is_exempt_false_on_non_list_globs(self) -> None:
        clause = {"exempt_path_globs": "not-a-list", "never_exempt_paths": []}
        assert handoff_evidence._is_exempt(["docs/plans/x.yaml"], clause) is False

    def test_is_exempt_false_on_empty_paths(self) -> None:
        """A zero-diff push must never be vacuously exempt -- fail closed to the evidence check."""
        clause = {"exempt_path_globs": ["*"], "never_exempt_paths": []}
        assert handoff_evidence._is_exempt([], clause) is False

    def test_decide_push_exempt_pass_on_real_diff(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "docs/plans/PLAN-x.yaml", "probe\n")
        verdict = handoff_evidence.decide_push(wt, sha)
        assert verdict.kind == "pass"
        assert verdict.rule == "exempt"

    def test_decide_bash_denies_unreadable_prefixes(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        (work / "docs" / "contracts" / "git-ops.yaml").write_text("not: {valid", encoding="utf-8")
        _git([*_GIT_ENV, "commit", "-am", "break it"], cwd=work)
        _git(["push", "origin", "main"], cwd=work)

        verdict = handoff_evidence.decide_bash(parse_canonical("git push -u origin HEAD"), work)
        assert verdict.kind == "deny"
        assert verdict.rule == "unreadable_agent_branch_prefixes"

    def test_decide_pr_denies_unreadable_prefixes(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        (work / "docs" / "contracts" / "git-ops.yaml").write_text("not: {valid", encoding="utf-8")
        _git([*_GIT_ENV, "commit", "-am", "break it"], cwd=work)
        _git(["push", "origin", "main"], cwd=work)
        verdict = handoff_evidence.decide_pr(work, "claude/feature", work)
        assert verdict.kind == "deny"
        assert verdict.rule == "unreadable_agent_branch_prefixes"

    def test_bare_push_destination_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "not-an-agent-branch")
        _git(["push", "-u", "origin", "not-an-agent-branch"], cwd=wt)
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "destination"

    def test_bare_push_unresolvable_head(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _git(["push", "-u", "origin", "claude/feature"], cwd=wt)

        real_run = handoff_evidence._run

        def _fake_run(args, cwd):
            if args[:2] == ["rev-parse", "HEAD"]:
                return False, ""
            return real_run(args, cwd)

        monkeypatch.setattr(handoff_evidence, "_run", _fake_run)
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "unresolvable_sha"

    def test_push_with_explicit_branch_src_no_dst(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)

        parsed = parse_canonical("git push origin claude/feature")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "pass"

    def test_push_head_detached_denies_unresolvable_branch(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _git(["checkout", "--detach"], cwd=wt)

        parsed = parse_canonical("git push -u origin HEAD")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "unresolvable_branch"

    def test_push_unresolvable_src_sha(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")

        parsed = parse_canonical("git push origin claude/no-such-branch-anywhere")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "unresolvable_sha"


class TestExemptSet:
    def test_exempt_set_is_read_from_base_ref_not_worktree(self, tmp_path: Path) -> None:
        """The exempt clause comes from `git show origin/main:...`, never the working tree."""
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        # Widen the WORKING TREE's own clause to exempt everything -- must have no effect.
        (wt / "docs" / "contracts" / "git-ops.yaml").write_text(
            "branching_topology:\n  agent_branch_prefixes: [claude/]\n"
            "handoff_evidence_gate:\n  exempt_path_globs: ['*']\n  never_exempt_paths: []\n",
            encoding="utf-8",
        )
        sha = _commit_file(wt, "docs/contracts/git-ops.yaml", (wt / "docs/contracts/git-ops.yaml").read_text())
        verdict = handoff_evidence.decide_push(wt, sha)
        # No evidence written -> denies on missing evidence, NOT exempt (proves the widened
        # working-tree clause granted nothing).
        assert verdict.kind == "deny"
        assert verdict.rule == "missing"

    def test_real_contract_exempt_clause_parses(self) -> None:
        """The real working-tree git-ops.yaml clause: docs/plans/x exempt, git-ops.yaml never-exempt."""
        text = (_common.ROOT / "docs" / "contracts" / "git-ops.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        gate_clause = data.get("handoff_evidence_gate")
        assert gate_clause is not None, "docs/contracts/git-ops.yaml must carry handoff_evidence_gate"
        assert handoff_evidence._is_exempt(["docs/plans/x.yaml"], gate_clause) is True
        assert handoff_evidence._is_exempt(["docs/contracts/git-ops.yaml"], gate_clause) is False

    def test_rename_out_of_non_exempt_path_is_not_exempt(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        clause = {"exempt_path_globs": ["docs/plans/*"], "never_exempt_paths": ["docs/contracts/git-ops.yaml"]}
        assert handoff_evidence._is_exempt(["scripts/x.py", "docs/plans/x.py"], clause) is False

    def test_never_exempt_source_rename_is_not_exempt(self) -> None:
        clause = {"exempt_path_globs": ["docs/plans/*"], "never_exempt_paths": ["docs/contracts/git-ops.yaml"]}
        assert handoff_evidence._is_exempt(["docs/contracts/git-ops.yaml", "docs/plans/x.py"], clause) is False

    def test_never_exempt_defeats_widened_glob(self) -> None:
        clause = {"exempt_path_globs": ["*"], "never_exempt_paths": ["docs/contracts/git-ops.yaml"]}
        assert handoff_evidence._is_exempt(["docs/contracts/git-ops.yaml"], clause) is False
        assert handoff_evidence._is_exempt(["anything/else.py"], clause) is True

    def test_no_clause_means_no_exemptions(self) -> None:
        assert handoff_evidence._is_exempt(["docs/plans/x.py"], None) is False

    def test_unknown_merge_base_means_no_exemptions(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "docs/plans/x.yaml", "probe\n")
        merge_base = handoff_evidence._merge_base(wt, "0" * 40)
        assert merge_base is None


class TestDestination:
    def test_main_and_non_agent_destinations_denied(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        assert handoff_evidence._dst_allowed("main", ["claude/", "agent/"]) is False
        assert handoff_evidence._dst_allowed("master", ["claude/", "agent/"]) is False
        assert handoff_evidence._dst_allowed("refs/heads/main", ["claude/", "agent/"]) is False
        assert handoff_evidence._dst_allowed("random-branch", ["claude/", "agent/"]) is False
        assert handoff_evidence._dst_allowed("claude/feature", ["claude/", "agent/"]) is True
        assert handoff_evidence._dst_allowed("refs/heads/claude/feature", ["claude/", "agent/"]) is True
        assert handoff_evidence._dst_allowed("audit/x-abc123", ["claude/", "agent/", "audit/"]) is True

    def test_base_ref_without_gate_clause_admits_agent_prefix(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)

        parsed = parse_canonical("git push -u origin HEAD")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "pass"


def test_a_ccr_harness_session_branch_is_an_allowed_destination(tmp_path: Path) -> None:
    # Seeds the REAL working-tree contract as origin/main, so the declared prefix set is the shipped one.
    work = _make_repo(tmp_path)
    real = (_common.ROOT / "docs" / "contracts" / "git-ops.yaml").read_text(encoding="utf-8")
    _commit_file(work, "docs/contracts/git-ops.yaml", real, "real contract")
    _git(["push", "origin", "main"], cwd=work)
    wt = _new_branch_worktree(work, "ccr-679d754f-m99ugp")
    _commit_file(wt, "docs/plans/PLAN-probe.yaml", "probe\n")
    verdict = handoff_evidence.decide_bash(parse_canonical("git push -u origin HEAD"), wt)
    assert (verdict.kind, verdict.rule) == ("pass", "exempt")
    assert handoff_evidence._dst_allowed("random-branch", handoff_evidence.destination_prefixes(wt)) is False


class TestBareAndDelete:
    def test_delete_single_agent_branch_passes(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        parsed = ParsedPush(kind="delete", worktree_hint=None, remote="origin", src=None, dst="claude/feature")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "pass"

    def test_delete_main_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        parsed = ParsedPush(kind="delete", worktree_hint=None, remote="origin", src=None, dst="main")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "delete_main"

    def test_delete_non_agent_branch_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        parsed = ParsedPush(kind="delete", worktree_hint=None, remote="origin", src=None, dst="some-random-branch")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "delete_non_agent_branch"

    def test_bare_push_denied_when_push_default_simple_is_not_matched(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _git(["config", "push.default", "matching"], cwd=wt)
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "bare_push_default"

    def test_bare_push_denied_when_remote_push_is_set(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _git(["config", "remote.origin.push", "+refs/heads/*:refs/heads/*"], cwd=wt)
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "bare_push_remote_push_set"

    def test_bare_push_denied_when_remote_mirror_true(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _git(["config", "remote.origin.mirror", "true"], cwd=wt)
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "bare_push_remote_mirror"

    def test_bare_push_denied_when_no_upstream(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        # A freshly created local branch (never pushed with -u) has no @{push} upstream yet.
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "bare_push_no_upstream"

    def test_bare_push_passes_with_clean_config_and_evidence(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _git(["push", "-u", "origin", "claude/feature"], cwd=wt)
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)
        parsed = ParsedPush(kind="bare_push", worktree_hint=None, remote="origin", src=None, dst=None)
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "pass"
        assert verdict.rule == "ok"


class TestPrResolution:
    def test_resolves_worktree_via_worktree_list(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _git(["push", "origin", "claude/feature"], cwd=wt)
        _write_real_evidence(wt, ok=True)
        verdict = handoff_evidence.decide_pr(work, "claude/feature", work)
        assert verdict.kind == "pass"

    def test_cross_repo_head_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        verdict = handoff_evidence.decide_pr(work, "someone:claude/feature", work)
        assert verdict.kind == "deny"
        assert verdict.rule == "cross_repo_head"

    def test_non_agent_head_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        verdict = handoff_evidence.decide_pr(work, "some-random-branch", work)
        assert verdict.kind == "deny"
        assert verdict.rule == "destination"

    def test_unpushed_head_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        _new_branch_worktree(work, "claude/feature")
        verdict = handoff_evidence.decide_pr(work, "claude/feature", work)
        assert verdict.kind == "deny"
        assert verdict.rule == "unpushed_head"


class TestRemoteWrites:
    def test_branch_main_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        verdict = handoff_evidence.decide_remote_write(work, "main", ["docs/plans/x.yaml"])
        assert verdict.kind == "deny"
        assert verdict.rule == "destination"

    def test_non_agent_branch_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        verdict = handoff_evidence.decide_remote_write(work, "some-random-branch", ["docs/plans/x.yaml"])
        assert verdict.kind == "deny"
        assert verdict.rule == "destination"

    def test_exempt_paths_on_agent_branch_pass(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        verdict = handoff_evidence.decide_remote_write(work, "claude/feature", ["docs/plans/x.yaml"])
        assert verdict.kind == "pass"
        assert verdict.rule == "exempt"

    def test_non_exempt_paths_on_agent_branch_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        verdict = handoff_evidence.decide_remote_write(work, "claude/feature", ["scripts/x.py"])
        assert verdict.kind == "deny"
        assert verdict.rule == "remote_write_not_exempt"

    def test_unreadable_agent_branch_prefixes_denies(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path, seed_gate_clause=False)
        (work / "docs" / "contracts" / "git-ops.yaml").write_text("not: {valid", encoding="utf-8")
        _git([*_GIT_ENV, "commit", "-am", "break it"], cwd=work)
        _git(["push", "origin", "main"], cwd=work)
        verdict = handoff_evidence.decide_remote_write(work, "claude/feature", ["docs/plans/x.yaml"])
        assert verdict.kind == "deny"
        assert verdict.rule == "unreadable_agent_branch_prefixes"


class TestBashNonCanonical:
    def test_non_canonical_denies_with_reason(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)

        parsed = parse_canonical("git push --force origin main")
        verdict = handoff_evidence.decide_bash(parsed, work)
        assert verdict.kind == "deny"
        assert verdict.rule == "non_canonical"

    def test_dry_run_passes(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)

        parsed = parse_canonical("git push --dry-run origin main")
        verdict = handoff_evidence.decide_bash(parsed, work)
        assert verdict.kind == "pass"
        assert verdict.rule == "dry_run"

    def test_worktree_hint_resolves_relative_to_payload_cwd(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)

        parsed = parse_canonical(f"git -C {wt} push -u origin HEAD")
        verdict = handoff_evidence.decide_bash(parsed, tmp_path)
        assert verdict.kind == "pass"

    def test_explicit_src_and_dst_refspec_uses_dst_directly(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)

        parsed = parse_canonical("git push origin claude/feature:claude/feature")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "pass"

    def test_no_hint_push_checks_payload_cwd_not_some_other_worktree(self, tmp_path: Path) -> None:
        """A no-hint push checks payload_cwd, never a fixed default (EnterWorktree isolation)."""
        work = _make_repo(tmp_path)
        wt_a = _new_branch_worktree(work, "claude/feature-a")
        _commit_file(wt_a, "src/a.py", "print('a')\n")
        _write_real_evidence(wt_a, ok=True)
        wt_b = _new_branch_worktree(work, "claude/feature-b")
        _commit_file(wt_b, "src/b.py", "print('b')\n")
        # wt_b has no evidence at all.

        parsed = parse_canonical("git push -u origin HEAD")
        assert handoff_evidence.decide_bash(parsed, wt_a).kind == "pass"
        verdict_b = handoff_evidence.decide_bash(parsed, wt_b)
        assert verdict_b.kind == "deny"
        assert verdict_b.rule == "missing"

    def test_destination_outside_prefixes_denied(self, tmp_path: Path) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "not-an-agent-branch")

        parsed = parse_canonical("git push -u origin HEAD")
        verdict = handoff_evidence.decide_bash(parsed, wt)
        assert verdict.kind == "deny"
        assert verdict.rule == "destination"


class TestCli:
    def test_check_push_exit_zero_on_pass(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        _write_real_evidence(wt, ok=True)
        rc = handoff_evidence.main(["--check-push", "--sha", sha, "--worktree", str(wt)])
        assert rc == 0

    def test_check_push_exit_one_on_deny(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        work = _make_repo(tmp_path)
        wt = _new_branch_worktree(work, "claude/feature")
        sha = _commit_file(wt, "src/thing.py", "print(1)\n")
        rc = handoff_evidence.main(["--check-push", "--sha", sha, "--worktree", str(wt)])
        assert rc == 1

    def test_default_args_print_help_and_exit_one(self, capsys: pytest.CaptureFixture) -> None:
        rc = handoff_evidence.main([])
        assert rc == 1

    def test_main_module_defaults_to_cwd_and_head(self) -> None:
        # exercises the argparse default path (no --worktree/--sha) without requiring --check-push
        rc = handoff_evidence.main(["--check-push"])
        assert rc in (0, 1)
