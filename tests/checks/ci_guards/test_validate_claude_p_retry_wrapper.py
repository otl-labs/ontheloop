"""Mirror for the REGISTERED validate_claude_p_retry_wrapper (its map_source_to_test path).

tests/test_ci_claude_p_retry.py drives only the pure helper _check_claude_p_raw_invocations and
the shell wrapper, so the registered check's own per-violation emission is executed by no test.
This mirror drives fn(failed) against a synthetic .github/workflows tree under a patched ROOT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.ci_guards import validate_claude_p_retry_wrapper as subject

_REPO_ROOT = Path(__file__).parents[3]
_UNIT = "claude_p_files"

_UNWRAPPED = """name: agent
jobs:
  run:
    steps: [{run: 'claude -p "do the thing"'}]
"""

_CLEAN = """# a comment naming claude -p, which the guard skips

name: agent
jobs:
  run:
    steps:
      - run: command -v claude
      - run: claude --version
      - run: scripts/ci/claude_p_retry.sh claude -p "wrapped"
      - uses: ./.github/actions/telemetry-ci-capture
        if: always()
        continue-on-error: true
        with: {manifests: out.sessions.jsonl}
"""


def _workflows(tmp_path: Path) -> Path:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    return workflows


def _listed(root: Path, *names: str) -> None:
    """The ci_workflows allow-list a paired workflow must appear in."""
    (root / "config" / "telemetry").mkdir(parents=True)
    entries = "".join(f"  - {{workflow: .github/workflows/{n}, agent: ci_rca, content_policy: full}}\n" for n in names)
    (root / "config" / "telemetry" / "capture.yaml").write_text("enabled: true\nci_workflows:\n" + entries, encoding="utf-8")


def _run(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[str]:
    monkeypatch.setattr(subject._common, "ROOT", root)
    failed: list[str] = []
    subject.validate_claude_p_retry_wrapper(failed)
    return failed


class TestClaudePRetryWrapperEmission:
    def test_unwrapped_invocation_appends_one_failure_per_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (_workflows(tmp_path) / "agent.yml").write_text(_UNWRAPPED, encoding="utf-8")

        failed = _run(monkeypatch, tmp_path)

        assert failed == ["claude_p_retry wrapper: agent.yml:4: unwrapped `claude -p` invocation"]
        assert "FAIL: agent.yml:4: unwrapped `claude -p` invocation" in capsys.readouterr().out

    def test_clean_tree_exercising_every_skip_arm_appends_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (_workflows(tmp_path) / "agent.yml").write_text(_CLEAN, encoding="utf-8")
        _listed(tmp_path, "agent.yml")

        failed = _run(monkeypatch, tmp_path)

        assert failed == []
        assert "PASS: all claude -p invocations route through scripts/ci/claude_p_retry.sh" in capsys.readouterr().out

    def test_unreadable_workflow_entry_is_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (_workflows(tmp_path) / "a-directory-not-a-file.yml").mkdir()

        failed = _run(monkeypatch, tmp_path)

        assert failed == []


def _declared(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[list[str], registry._Declaration | None]:
    registry.pop_declaration()
    failed = _run(monkeypatch, root)
    return failed, registry.pop_declaration()


class TestClaudePRetryWrapperAccountingDeclaration:
    """The guard declares how many workflow files it read, so a run records enforced with a count
    that tracks the readable .github/workflows/*.yml set -- not a constant and not the glob size. The unit is claude_p_files
    over the workflows and the composite actions."""

    def test_real_tree_declares_every_readable_workflow_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        expected = sum(1 for p in (_REPO_ROOT / ".github" / "workflows").glob("*.yml") if p.is_file())
        expected += sum(
            1 for p in (_REPO_ROOT / ".github" / "actions").rglob("*") if p.is_file() and p.suffix in (".yml", ".yaml", ".sh")
        )

        failed, declaration = _declared(monkeypatch, _REPO_ROOT)

        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", expected, _UNIT)

    def test_count_excludes_unreadable_and_non_yml_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        workflows = _workflows(tmp_path)
        (workflows / "a.yml").write_text(_CLEAN, encoding="utf-8")
        (workflows / "b.yml").write_text(_CLEAN, encoding="utf-8")
        (workflows / "c.yaml").write_text(_UNWRAPPED, encoding="utf-8")
        (workflows / "d-directory.yml").mkdir()
        _listed(tmp_path, "a.yml", "b.yml")

        failed, declaration = _declared(monkeypatch, tmp_path)

        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count) == ("examined", 2)

    def test_empty_workflows_dir_declares_vacuous_domain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _workflows(tmp_path)

        failed, declaration = _declared(monkeypatch, tmp_path)

        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 0, _UNIT)

    def test_violation_still_records_failed_over_examined(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (_workflows(tmp_path) / "agent.yml").write_text(_UNWRAPPED, encoding="utf-8")

        failed, declaration = _declared(monkeypatch, tmp_path)
        outcome = registry.build_outcome("validate_claude_p_retry_wrapper", "check", declaration, bool(failed))

        assert declaration is not None and declaration.count == 1
        assert outcome.status == "failed"

    def test_list_helper_returns_violations_without_read_count(self, tmp_path: Path) -> None:
        workflows = _workflows(tmp_path)
        (workflows / "agent.yml").write_text(_UNWRAPPED, encoding="utf-8")
        (workflows / "clean.yml").write_text(_CLEAN, encoding="utf-8")

        assert subject._check_claude_p_raw_invocations(workflows) == ["agent.yml:4: unwrapped `claude -p` invocation"]


_CAPTURE = ".github/actions/telemetry-ci-capture"
_SWITCH = (
    "enabled: true\nci_repository: o/r\nci_workflows:\n"
    "  - {workflow: .github/workflows/agent.yml, agent: ci_rca, content_policy: full}\n"
)


def _job(*steps: str) -> str:
    return "name: agent\njobs:\n  run:\n    steps:\n" + "".join(f"      - {step}\n" for step in steps)


_WRAPPED = "run: scripts/ci/claude_p_retry.sh out.json -- --max-turns 1"
_PAIRED = (
    f"uses: ./{_CAPTURE}\n        if: always()\n        continue-on-error: true\n"
    "        with: {manifests: out.json.sessions.jsonl}"
)


def _tree(
    tmp_path: Path, workflow: str | None = None, switch: str | None = _SWITCH, actions: dict[str, str] | None = None
) -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    if workflow is not None:
        (tmp_path / ".github" / "workflows" / "agent.yml").write_text(workflow, encoding="utf-8")
    for rel, body in (actions or {}).items():
        target = tmp_path / ".github" / "actions" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    if switch is not None:
        (tmp_path / "config" / "telemetry").mkdir(parents=True)
        (tmp_path / "config" / "telemetry" / "capture.yaml").write_text(switch, encoding="utf-8")
    return tmp_path


def _pairing(tmp_path: Path, **kw) -> list[str]:
    return subject._check_pairing(_tree(tmp_path, **kw))


class TestClaudePCapturePairing:
    def test_a_paired_job_in_a_listed_workflow_passes(self, tmp_path: Path) -> None:
        assert _pairing(tmp_path, workflow=_job(_WRAPPED, _PAIRED)) == []

    def test_an_unpaired_call_site_fails(self, tmp_path: Path) -> None:
        problems = _pairing(tmp_path, workflow=_job(_WRAPPED))
        assert problems == ["agent.yml: job run runs claude -p but holds 0 capture steps, expected exactly one"]

    def test_two_capture_steps_fail(self, tmp_path: Path) -> None:
        problems = _pairing(tmp_path, workflow=_job(_WRAPPED, _PAIRED, _PAIRED))
        assert problems == ["agent.yml: job run runs claude -p but holds 2 capture steps, expected exactly one"]

    def test_a_capture_step_missing_if_always_or_continue_on_error_fails(self, tmp_path: Path) -> None:
        no_always = _PAIRED.replace("        if: always()\n", "")
        no_continue = _PAIRED.replace("        continue-on-error: true\n", "")
        assert _pairing(tmp_path / "a", workflow=_job(_WRAPPED, no_always)) == [
            "agent.yml: job run runs claude -p but capture step lacks `if: always()`"
        ]
        assert _pairing(tmp_path / "b", workflow=_job(_WRAPPED, no_continue)) == [
            "agent.yml: job run runs claude -p but capture step lacks `continue-on-error: true`"
        ]

    def test_a_paired_job_in_an_unlisted_workflow_fails(self, tmp_path: Path) -> None:
        switch = "enabled: true\nci_repository: o/r\nci_workflows: []\n"
        problems = _pairing(tmp_path, workflow=_job(_WRAPPED, _PAIRED), switch=switch)
        assert any("not a ci_workflows entry" in p for p in problems)

    def test_a_listed_workflow_with_no_call_site_or_no_file_fails(self, tmp_path: Path) -> None:
        problems = _pairing(tmp_path / "a", workflow=_job("run: echo hi"))
        assert problems == ["agent.yml: ci_workflows entry has no claude_p_retry.sh call-site job"]
        problems = _pairing(tmp_path / "b", workflow=None)
        assert problems == ["ci_workflows entry names a workflow that does not exist: .github/workflows/agent.yml"]

    def test_claude_yml_listed_in_either_list_fails(self, tmp_path: Path) -> None:
        listed = (
            "enabled: true\nci_workflows:\n  - {workflow: .github/workflows/claude.yml, agent: ci_rca, content_policy: full}\n"
        )
        assert "claude.yml appears in ci_workflows" in _pairing(tmp_path / "a", switch=listed)
        (tmp_path / "b").mkdir()
        exempt = "ci_uncaptured:\n  - {call_site: .github/workflows/claude.yml, reason: r, owner: rec-1}\n"
        assert "claude.yml appears in ci_uncaptured" in _pairing(tmp_path / "b", switch=exempt)

    def test_a_pending_exemption_covers_an_action_only_job(self, tmp_path: Path) -> None:
        action = {"review/action.yml": "runs: {using: composite}\n", "review/run.sh": "scripts/ci/claude_p_retry.sh o -- x\n"}
        uses = _job("uses: ./.github/actions/review")
        exempt = _SWITCH + "ci_uncaptured:\n  - {call_site: .github/actions/review, reason: r, owner: rec-4420}\n"
        assert _pairing(tmp_path / "a", workflow=uses, switch=exempt, actions=action) == [
            "agent.yml: ci_workflows entry has no claude_p_retry.sh call-site job"
        ]
        bare = "enabled: true\nci_uncaptured:\n  - {call_site: .github/actions/review, reason: r, owner: rec-4420}\n"
        assert _pairing(tmp_path / "b", workflow=uses, switch=bare, actions=action) == []
        problems = _pairing(tmp_path / "c", workflow=uses, switch="enabled: true\n", actions=action)
        assert problems == ["agent.yml: job run runs claude -p but holds 0 capture steps, expected exactly one"]

    def test_a_stale_or_ownerless_exemption_fails(self, tmp_path: Path) -> None:
        stale = "enabled: true\nci_uncaptured:\n  - {call_site: .github/actions/gone, reason: r, owner: rec-4420}\n"
        assert _pairing(tmp_path / "a", switch=stale) == [
            "stale ci_uncaptured entry: .github/actions/gone is not a claude_p_retry.sh call site"
        ]
        action = {"review/run.sh": "scripts/ci/claude_p_retry.sh o -- x\n"}
        ownerless = "enabled: true\nci_uncaptured:\n  - {call_site: .github/actions/review, reason: r, owner: someone}\n"
        assert _pairing(tmp_path / "b", switch=ownerless, actions=action) == [
            "ci_uncaptured entry for .github/actions/review has no rec-NNNN owner"
        ]

    def test_an_unwrapped_call_in_an_action_is_flagged(self, tmp_path: Path) -> None:
        tree = _tree(
            tmp_path, workflow=_job("run: echo"), actions={"x/run.sh": "claude -p hi\n", "x/ok.sh": "# claude -p note\n"}
        )
        violations, read = subject._scan_claude_p_raw_invocations(tree / ".github" / "workflows", tree / ".github" / "actions")
        assert violations == ["x/run.sh:1: unwrapped `claude -p` invocation"] and read == 3

    def test_the_registered_check_emits_pairing_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        failed = _run(monkeypatch, _tree(tmp_path, workflow=_job(_WRAPPED)))
        assert failed == [
            "claude_p_retry capture pairing: agent.yml: job run runs claude -p but holds 0 capture steps, expected exactly one"
        ]

    def test_the_real_tree_passes(self) -> None:
        assert subject._check_pairing(_REPO_ROOT) == []
