"""Tests for validate_actions_sha_pinned() (rec-5020, rec-5289): tmp_path trees with ROOT patched."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks import registry
from scripts.checks.ci_guards.validate_actions_sha_pinned import validate_actions_sha_pinned

_REPO = Path(__file__).resolve().parents[3]
_SHA = "a" * 40
_WF = ".github/workflows/w.yml"
_PINNED = f"actions/checkout@{_SHA}"
_OLD_BASELINE = "config/actions_sha_pin_baseline.yaml"


def _wf(uses: str, comment: str = "") -> str:
    tail = f"  {comment}" if comment else ""
    return f"name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: {uses}{tail}\n"


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(root: Path) -> list[str]:
    with patch("scripts.checks._common.ROOT", root):
        failed: list[str] = []
        validate_actions_sha_pinned(failed)
    return failed


def _tree(root: Path, uses: str, comment: str = "") -> None:
    _put(root, _WF, _wf(uses, comment))


def test_refuses_tag_pinned_uses_reference(tmp_path: Path) -> None:
    _tree(tmp_path, "actions/checkout@v7")
    failed = _run(tmp_path)
    assert any("actions/checkout@v7" in f and "not pinned to a 40-hex" in f for f in failed)


class TestRefusals:
    def test_refuses_branch_ref(self, tmp_path: Path) -> None:
        _tree(tmp_path, "actions/checkout@main")
        assert _run(tmp_path)

    def test_refuses_39_hex_ref(self, tmp_path: Path) -> None:
        _tree(tmp_path, f"actions/checkout@{'a' * 39}", "# v7.0.1")
        assert _run(tmp_path)

    def test_refuses_pinned_ref_without_comment(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED)
        assert any("exact version comment" in f for f in _run(tmp_path))

    def test_refuses_pinned_ref_with_non_version_comment(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# latest")
        assert _run(tmp_path)

    def test_refuses_major_only_and_two_part_comments(self, tmp_path: Path) -> None:
        for comment in ("# v7", "# v7.1", "# 7"):
            _tree(tmp_path, _PINNED, comment)
            assert any("exact version comment" in f for f in _run(tmp_path)), comment

    def test_refuses_docker_tag(self, tmp_path: Path) -> None:
        _tree(tmp_path, "docker://alpine:3.20", "# v3.20.0")
        assert any("sha256 digest" in f for f in _run(tmp_path))

    def test_refuses_job_level_reusable_workflow_tag(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "name: t\non: push\njobs:\n  a:\n    uses: o/r/.github/workflows/x.yml@v1\n")
        assert any("o/r/.github/workflows/x.yml@v1" in f for f in _run(tmp_path))

    def test_refuses_composite_action_step(self, tmp_path: Path) -> None:
        _put(tmp_path, ".github/actions/c/action.yml", "runs:\n  using: composite\n  steps:\n    - uses: o/r@v1\n")
        assert any(".github/actions/c/action.yml::o/r@v1" in f for f in _run(tmp_path))

    def test_flow_style_step_fails_closed(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "name: t\non: push\njobs:\n  a:\n    steps: [{uses: o/r@v1}]\n")
        assert any("line scan cannot read" in f for f in _run(tmp_path))

    def test_each_floating_reference_fails_with_its_count(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, _wf("o/a@v1") + "      - uses: o/a@v1\n      - uses: o/b@v1\n")
        failed = _run(tmp_path)
        assert len(failed) == 2 and any("o/a@v1" in f and "(x2)" in f for f in failed)

    def test_leftover_baseline_grandfathers_nothing(self, tmp_path: Path) -> None:
        _tree(tmp_path, "o/a@v1")
        _put(tmp_path, _OLD_BASELINE, yaml.safe_dump({"drains": {"rec-5289": {f"{_WF}::o/a@v1": 1}}}))
        assert any("o/a@v1" in f for f in _run(tmp_path))


class TestUnreadableWorkflow:
    def test_unparseable_workflow_fails_closed(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "jobs: [\n")
        assert any("unreadable" in f for f in _run(tmp_path))

    def test_unparseable_workflow_with_baselined_key_reports_one_failure(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "jobs: [\n  uses: o/a@v1\n")
        _put(tmp_path, _OLD_BASELINE, yaml.safe_dump({"drains": {"rec-5289": {f"{_WF}::o/a@v1": 1}}}))
        failed = _run(tmp_path)
        assert len(failed) == 1 and "unreadable" in failed[0], failed

    def test_non_mapping_workflow_and_non_mapping_job_are_ignored(self, tmp_path: Path) -> None:
        _put(tmp_path, _WF, "- a\n")
        _put(tmp_path, ".github/workflows/j.yml", "jobs:\n  a: not-a-mapping\n")
        assert _run(tmp_path) == []


class TestCommentForms:
    def test_comment_without_leading_space_has_a_defined_outcome(self, tmp_path: Path) -> None:
        _tree(tmp_path, f"{_PINNED}# v7.0.1")
        failed = _run(tmp_path)
        assert len(failed) == 1 and "no space before #" in failed[0], failed


class TestGreen:
    def test_local_reference_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, "./.github/actions/x")
        assert _run(tmp_path) == []

    def test_version_comment_forms_pass(self, tmp_path: Path) -> None:
        for comment in ("# v7.0.1", "# 7.1.0", "#v1.14.3", "# v2.0.0-rc.1"):
            _tree(tmp_path, _PINNED, comment)
            assert _run(tmp_path) == [], comment

    def test_docker_digest_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, f"docker://alpine@sha256:{'b' * 64}")
        assert _run(tmp_path) == []

    def test_quoted_value_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path, f'"{_PINNED}"', "# v7.0.1")
        assert _run(tmp_path) == []


class TestAccountingDeclaration:
    def _declared(self, root: Path):
        with patch("scripts.checks._common.ROOT", root), registry.outcome_scope("validate_actions_sha_pinned"):
            validate_actions_sha_pinned([])
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined" and declaration.unit == "uses_references"
        return declaration

    def test_pass_path(self, tmp_path: Path) -> None:
        _tree(tmp_path, _PINNED, "# v7.0.1")
        assert self._declared(tmp_path).count == 1

    def test_failure_path(self, tmp_path: Path) -> None:
        _tree(tmp_path, "o/a@v1")
        assert self._declared(tmp_path).count == 1

    def test_empty_tree_path(self, tmp_path: Path) -> None:
        assert self._declared(tmp_path).count == 0


def test_real_tree_passes() -> None:
    failed: list[str] = []
    validate_actions_sha_pinned(failed)
    assert failed == []


def test_taxonomy_row_exists() -> None:
    taxonomy = yaml.safe_load((_REPO / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    assert taxonomy["function_to_category"]["validate_actions_sha_pinned"] == "code_regression"
