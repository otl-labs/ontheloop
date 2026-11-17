"""Tests for validate_actions_sha_pinned() (rec-5020): tmp_path trees, ROOT patched, git fixture repos for the base leg."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks import registry
from scripts.checks.ci_guards.validate_actions_sha_pinned import (
    BASELINE_REL,
    render_baseline,
    validate_actions_sha_pinned,
)

_REPO = Path(__file__).resolve().parents[3]
_SHA = "a" * 40
_WF = ".github/workflows/w.yml"
_PINNED = f"actions/checkout@{_SHA}"


def _wf(uses: str, comment: str = "") -> str:
    tail = f"  {comment}" if comment else ""
    return f"name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: {uses}{tail}\n"


def _baseline(**buckets: dict[str, int] | None) -> str:
    return yaml.safe_dump({"drains": {k.replace("_", "-"): v for k, v in buckets.items()}})


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(root: Path) -> list[str]:
    with patch("scripts.checks._common.ROOT", root):
        failed: list[str] = []
        validate_actions_sha_pinned(failed)
    return failed


def _tree(root: Path, uses: str, comment: str = "", baseline: str | None = None) -> None:
    _put(root, _WF, _wf(uses, comment))
    if baseline is not None:
        _put(root, BASELINE_REL, baseline)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _repo_with_base(root: Path, base_baseline: str | None) -> None:
    _git(root, "init", "-q", "-b", "main")
    _put(root, "README", "x")
    if base_baseline is not None:
        _put(root, BASELINE_REL, base_baseline)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")


def test_refuses_tag_pinned_uses_reference(tmp_path: Path) -> None:
    _tree(tmp_path, "actions/checkout@v7")
    failed = _run(tmp_path)
    assert any("actions/checkout@v7" in f and "unlisted" in f for f in failed)


class TestRefusals:
    def test_refuses_branch_ref(self, tmp_path: Path) -> None:
        _tree(tmp_path, "actions/checkout@main")
        assert _run(tmp_path)

    def test_refuses_39_hex_ref(self, tmp_path: Path) -> None:
        _tree(tmp_path, f"actions/checkout@{'a' * 39}", "# v7")
        assert _run(tmp_path)

    def test_refuses_pinned_ref_without_comment(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED)
        assert _run(tmp_path)

    def test_refuses_pinned_ref_with_non_version_comment(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# latest")
        assert _run(tmp_path)

    def test_refuses_docker_tag(self, tmp_path: Path) -> None:
        _tree(tmp_path, "docker://alpine:3.20", "# v3")
        assert _run(tmp_path)

    def test_refuses_job_level_reusable_workflow_tag(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "name: t\non: push\njobs:\n  a:\n    uses: o/r/.github/workflows/x.yml@v1\n")
        assert any("o/r/.github/workflows/x.yml@v1" in f for f in _run(tmp_path))

    def test_refuses_composite_action_step(self, tmp_path: Path) -> None:
        _put(tmp_path, ".github/actions/c/action.yml", "runs:\n  using: composite\n  steps:\n    - uses: o/r@v1\n")
        assert any(".github/actions/c/action.yml::o/r@v1" in f for f in _run(tmp_path))

    def test_flow_style_step_fails_closed(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "name: t\non: push\njobs:\n  a:\n    steps: [{uses: o/r@v1}]\n")
        assert any("line scan cannot read" in f for f in _run(tmp_path))

    def test_unlisted_key_beside_a_baselined_one_fails(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, _wf("o/a@v1") + "      - uses: o/b@v1\n")
        _put(tmp_path, BASELINE_REL, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        failed = _run(tmp_path)
        assert len(failed) == 1 and "o/b@v1" in failed[0]

    def test_count_above_baseline_fails(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, _wf("o/a@v1") + "      - uses: o/a@v1\n")
        _put(tmp_path, BASELINE_REL, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        assert any("above baseline" in f for f in _run(tmp_path))

    def test_stale_entry_fails(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# v7", _baseline(rec_5288={f"{_WF}::o/gone@v1": 1}))
        assert any("stale" in f for f in _run(tmp_path))

    def test_key_under_both_drain_recs_fails(self, tmp_path: Path) -> None:
        key = {f"{_WF}::o/a@v1": 1}
        _tree(tmp_path, "o/a@v1", "", _baseline(rec_5288=key, rec_5289=key))
        assert any("both" in f for f in _run(tmp_path))

    def test_failure_names_the_dependabot_route(self, tmp_path: Path) -> None:
        _tree(tmp_path, "o/a@v1")
        assert any("Dependabot" in f and "rec-5288" in f for f in _run(tmp_path))


class TestMalformedBaseline:
    def _bad(self, tmp_path: Path, text: str) -> list[str]:
        _tree(tmp_path, _PINNED, "# v7", text)
        return _run(tmp_path)

    def test_not_a_mapping(self, tmp_path: Path) -> None:
        assert self._bad(tmp_path, "- a\n")

    def test_bad_bucket_key(self, tmp_path: Path) -> None:
        assert self._bad(tmp_path, "drains:\n  nope: {}\n")

    def test_bad_entry_key(self, tmp_path: Path) -> None:
        assert self._bad(tmp_path, "drains:\n  rec-5288:\n    nocolons: 1\n")

    def test_zero_count(self, tmp_path: Path) -> None:
        assert self._bad(tmp_path, f'drains:\n  rec-5288:\n    "{_WF}::o/a@v1": 0\n')

    def test_non_int_count(self, tmp_path: Path) -> None:
        assert self._bad(tmp_path, f'drains:\n  rec-5288:\n    "{_WF}::o/a@v1": "1"\n')

    def test_drains_not_a_mapping(self, tmp_path: Path) -> None:
        assert any("drains is not a mapping" in f for f in self._bad(tmp_path, "drains: [a]\n"))

    def test_bucket_not_a_mapping(self, tmp_path: Path) -> None:
        assert any("is not a mapping" in f for f in self._bad(tmp_path, "drains:\n  rec-5288: [a]\n"))

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        assert any("not valid YAML" in f for f in self._bad(tmp_path, "drains: [\n"))

    def test_unparseable_workflow_fails_closed(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "jobs: [\n")
        assert any("unreadable" in f for f in _run(tmp_path))

    def test_non_mapping_workflow_and_non_mapping_job_are_ignored(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "- a\n")
        _put(tmp_path, ".github/workflows/j.yml", "jobs:\n  a: not-a-mapping\n")
        assert _run(tmp_path) == []

    def test_malformed_at_the_base_fails(self, tmp_path: Path) -> None:
        _repo_with_base(tmp_path, "- a\n")
        _tree(tmp_path, _PINNED, "# v7", _baseline(rec_5288=None))
        assert any("base baseline" in f for f in _run(tmp_path))


class TestGreen:
    def test_local_reference_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, "./.github/actions/x")
        assert _run(tmp_path) == []

    def test_version_comment_forms_pass(self, tmp_path: Path) -> None:
        for comment in ("# v7", "# v7.0.1", "# 7.1.0"):
            _tree(tmp_path, _PINNED, comment)
            assert _run(tmp_path) == [], comment

    def test_docker_digest_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, f"docker://alpine@sha256:{'b' * 64}")
        assert _run(tmp_path) == []

    def test_quoted_value_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, f'"{_PINNED}"', "# v7")
        assert _run(tmp_path) == []

    def test_exact_baseline_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, "o/a@v1", "", _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        assert _run(tmp_path) == []

    def test_empty_end_states_pass_on_a_clean_tree(self, tmp_path: Path) -> None:
        for text in ("drains: {}\n", "drains:\n  rec-5288:\n", "other: 1\n", None):
            _tree(tmp_path, _PINNED, "# v7")
            if text is not None:
                _put(tmp_path, BASELINE_REL, text)
            assert _run(tmp_path) == [], text

    def test_baseline_absent_with_live_violations_fails(self, tmp_path: Path) -> None:
        _tree(tmp_path, "o/a@v1")
        assert _run(tmp_path)


class TestNoRaiseLeg:
    def test_key_added_relative_to_base_fails(self, tmp_path: Path) -> None:
        _repo_with_base(tmp_path, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        _put(tmp_path, _WF, _wf("o/a@v1") + "      - uses: o/b@v1\n")
        _put(tmp_path, BASELINE_REL, _baseline(rec_5288={f"{_WF}::o/a@v1": 1, f"{_WF}::o/b@v1": 1}))
        assert any("key added" in f for f in _run(tmp_path))

    def test_count_raised_relative_to_base_fails(self, tmp_path: Path) -> None:
        _repo_with_base(tmp_path, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        _put(tmp_path, _WF, _wf("o/a@v1") + "      - uses: o/a@v1\n")
        _put(tmp_path, BASELINE_REL, _baseline(rec_5288={f"{_WF}::o/a@v1": 2}))
        assert any("raised" in f for f in _run(tmp_path))

    def test_shrink_relative_to_base_passes(self, tmp_path: Path) -> None:
        _repo_with_base(tmp_path, _baseline(rec_5288={f"{_WF}::o/a@v1": 2}))
        _put(tmp_path, _WF, _wf("o/a@v1"))
        _put(tmp_path, BASELINE_REL, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        assert _run(tmp_path) == []

    def test_merge_base_failing_falls_back_to_origin_main(self, tmp_path: Path) -> None:
        _repo_with_base(tmp_path, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        _git(tmp_path, "checkout", "-q", "--orphan", "other")
        _put(tmp_path, _WF, _wf("o/a@v1") + "      - uses: o/b@v1\n")
        _put(tmp_path, BASELINE_REL, _baseline(rec_5288={f"{_WF}::o/a@v1": 1, f"{_WF}::o/b@v1": 1}))
        assert any("key added" in f for f in _run(tmp_path))

    def test_baseline_absent_at_resolvable_base_is_skipped(self, tmp_path: Path, capsys) -> None:
        _repo_with_base(tmp_path, None)
        _tree(tmp_path, "o/a@v1", "", _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        assert _run(tmp_path) == []
        assert "SKIP" in capsys.readouterr().out

    def test_baseline_absent_at_head_with_live_violations_fails_against_base(self, tmp_path: Path) -> None:
        _repo_with_base(tmp_path, _baseline(rec_5288={f"{_WF}::o/a@v1": 1}))
        _tree(tmp_path, "o/a@v1")
        (tmp_path / BASELINE_REL).unlink()
        assert _run(tmp_path)

    def test_no_base_prints_skip_and_declares_examined(self, tmp_path: Path, capsys) -> None:
        _tree(tmp_path, _PINNED, "# v7")
        with patch("scripts.checks._common.ROOT", tmp_path), registry.outcome_scope("validate_actions_sha_pinned"):
            validate_actions_sha_pinned([])
        out = capsys.readouterr().out
        assert "SKIP" in out and "no-raise leg skipped" in out
        declaration = registry.pop_declaration()
        assert declaration is not None and declaration.kind == "examined"


class TestAccountingDeclaration:
    def _declared(self, root: Path):
        with patch("scripts.checks._common.ROOT", root), registry.outcome_scope("validate_actions_sha_pinned"):
            validate_actions_sha_pinned([])
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined" and declaration.unit == "uses_references"
        return declaration

    def test_pass_path(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# v7")
        assert self._declared(tmp_path).count == 1

    def test_ratchet_failure_path(self, tmp_path: Path) -> None:
        _tree(tmp_path, "o/a@v1")
        assert self._declared(tmp_path).count == 1

    def test_malformed_baseline_path(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# v7", "- a\n")
        assert self._declared(tmp_path).count == 1

    def test_empty_tree_path(self, tmp_path: Path) -> None:
        assert self._declared(tmp_path).count == 0


class TestRenderBaseline:
    def test_partitions_by_codeowners_and_round_trips(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, _wf("o/a@v1"))
        _put(tmp_path, ".github/workflows/terraform-x.yml", _wf("o/b@v1"))
        _put(tmp_path, ".github/actions/aws-region/action.yml", "runs:\n  using: composite\n  steps:\n    - uses: o/c@v1\n")
        _put(tmp_path, ".github/CODEOWNERS", "# c\n/.github/workflows/terraform-*.yml @o\n/.github/actions/aws-region/ @o\n")
        text = render_baseline(tmp_path)
        drains = yaml.safe_load(text)["drains"]
        assert drains["rec-5288"] == {f"{_WF}::o/a@v1": 1}
        assert set(drains["rec-5289"]) == {
            ".github/workflows/terraform-x.yml::o/b@v1",
            ".github/actions/aws-region/action.yml::o/c@v1",
        }
        _put(tmp_path, BASELINE_REL, text)
        assert _run(tmp_path) == []

    def test_clean_tree_renders_empty_buckets(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# v7")
        assert yaml.safe_load(render_baseline(tmp_path))["drains"] == {"rec-5288": {}, "rec-5289": {}}


def test_real_tree_passes() -> None:
    failed: list[str] = []
    validate_actions_sha_pinned(failed)
    assert failed == []


def test_taxonomy_row_exists() -> None:
    taxonomy = yaml.safe_load((_REPO / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    assert taxonomy["function_to_category"]["validate_actions_sha_pinned"] == "code_regression"
