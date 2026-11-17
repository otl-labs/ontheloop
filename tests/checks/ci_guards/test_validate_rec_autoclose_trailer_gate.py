"""Tests for validate_rec_autoclose_trailer_gate() -- the Resolves: trailer gate's delegation-shape
pin (rec-3775 / rec-2922 / rec-3901 / Decision 201).

Pass-path fixture is the real committed repository state. Most removal negatives patch this
module's own `_read` binding to simulate ONE removal so each fails on exactly that removal and
nothing else. `test_read_returns_none_on_oserror` is deliberately NOT one of these: it patches
`pathlib.Path.read_text` instead, so the real `_read` helper's `except OSError: return None`
branch actually executes. Do NOT rewrite it onto the `_run(sources)` / patched-`_read` pattern the
others use -- doing so would silently drop coverage of that branch back to the state that filed
rec-3991, and the shard's test_selector and the coverage gate would both stay green while the
branch went untested again.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks.ci_guards.validate_rec_autoclose_trailer_gate import (
    _ALL_SOURCES,
    _AUTOCLOSE_WORKFLOW_PATH,
    _CI_RCA_LIFECYCLE_CONTRACT,
    _CI_WORKFLOW_PATH,
    _GIT_OPS_CONTRACT,
    _LIFECYCLE_MODULE_PATH,
    _TRAILER_MODULE_PATH,
    validate_rec_autoclose_trailer_gate,
)

_MODULE = "scripts.checks.ci_guards.validate_rec_autoclose_trailer_gate"


def _real_sources() -> dict[str, str]:
    from scripts.checks import _common

    return {rel: (_common.ROOT / rel).read_text(encoding="utf-8") for rel in _ALL_SOURCES}


def _run(sources: dict[str, str]) -> list[str]:
    def fake_read(root, rel):  # noqa: ANN001, ARG001
        return sources.get(rel)

    with patch(f"{_MODULE}._read", side_effect=fake_read):
        failed: list[str] = []
        validate_rec_autoclose_trailer_gate(failed)
    return failed


class TestPassPath:
    def test_passes_against_the_real_committed_repository(self) -> None:
        failed: list[str] = []
        validate_rec_autoclose_trailer_gate(failed)
        assert failed == []

    def test_registered_in_pre_sequence(self) -> None:
        names = {step.name for step in registry.pre_sequence()}
        assert "validate_rec_autoclose_trailer_gate" in names

    def test_examined_count_is_twelve_delegation_assertions(self) -> None:
        with registry.outcome_scope("validate_rec_autoclose_trailer_gate"):
            failed: list[str] = []
            validate_rec_autoclose_trailer_gate(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 12
        assert declaration.unit == "delegation_assertions"


class TestRemovalNegatives:
    def test_fails_when_workflow_stops_delegating(self) -> None:
        sources = _real_sources()
        sources[_CI_WORKFLOW_PATH] = sources[_CI_WORKFLOW_PATH].replace("scripts.rec_trailer_acceptance", "inline_loop")
        failed = _run(sources)
        assert any("no longer delegate closure" in item for item in failed), failed

    def test_fails_when_gate_unreferenced(self) -> None:
        sources = _real_sources()
        sources[_LIFECYCLE_MODULE_PATH] = sources[_LIFECYCLE_MODULE_PATH].replace("merge_leg_refuses", "unrelated_symbol")
        failed = _run(sources)
        assert any("no longer references the merge-leg gate" in item for item in failed), failed

    def test_fails_when_carrier_clause_absent(self) -> None:
        sources = _real_sources()
        sources[_GIT_OPS_CONTRACT] = sources[_GIT_OPS_CONTRACT].replace("IMPLEMENTATION merge", "the right merge")
        failed = _run(sources)
        assert any("carrier rule" in item for item in failed), failed

    def test_fails_when_placement_clause_absent(self) -> None:
        sources = _real_sources()
        sources[_GIT_OPS_CONTRACT] = sources[_GIT_OPS_CONTRACT].replace("Key: value", "properly shaped")
        failed = _run(sources)
        assert any("placement rule" in item for item in failed), failed

    def test_skips_when_a_source_is_unreadable(self) -> None:
        with (
            registry.outcome_scope("validate_rec_autoclose_trailer_gate"),
            patch(f"{_MODULE}._read", return_value=None),
        ):
            failed: list[str] = []
            validate_rec_autoclose_trailer_gate(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "skipped"
        assert failed  # unreadable sources are also a hard failure, never a silent pass

    def test_read_returns_none_on_oserror(self) -> None:
        with (
            registry.outcome_scope("validate_rec_autoclose_trailer_gate"),
            patch.object(pathlib.Path, "read_text", side_effect=OSError("unreadable")),
        ):
            failed: list[str] = []
            validate_rec_autoclose_trailer_gate(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "skipped"
        matching = [item for item in failed if "could not read" in item]
        assert matching, failed
        assert any(path in matching[0] for path in _ALL_SOURCES)


class TestAcceptanceGateAssertions:
    """Decision 201's five new assertions, each with a passing (via TestPassPath's real-repo
    fixture) and a failing limb here."""

    def test_retired_delegation_fails_when_close_recs_from_trailer_is_re_added(self) -> None:
        sources = _real_sources()
        sources[_AUTOCLOSE_WORKFLOW_PATH] = sources[_AUTOCLOSE_WORKFLOW_PATH].replace(
            "jobs:\n  autoclose:", "jobs:\n  autoclose:\n    # close_recs_from_trailer re-added\n"
        )
        failed = _run(sources)
        assert any("still names close_recs_from_trailer" in item for item in failed), failed

    def test_evaluator_job_fails_when_it_gains_id_token(self) -> None:
        sources = _real_sources()
        sources[_CI_WORKFLOW_PATH], count = re.subn(
            r"(timeout-minutes: 30\n    permissions:\n      contents: read\n)"
            r"(    steps:\n      - uses: actions/checkout@\S+[^\n]*\n"
            r"        with:\n          persist-credentials: false)",
            r"\1      id-token: write\n\2",
            sources[_CI_WORKFLOW_PATH],
        )
        assert count == 1
        failed = _run(sources)
        assert any("not credential-free" in item for item in failed), failed

    def test_evaluator_job_fails_when_it_gains_a_secret_reference(self) -> None:
        sources = _real_sources()
        sources[_CI_WORKFLOW_PATH] = sources[_CI_WORKFLOW_PATH].replace(
            "requirements.txt -r requirements-fast.txt\n\n      - name: Download census artifact",
            "requirements.txt -r requirements-fast.txt\n          echo ${{ secrets.DUCKLAKE_WRITER_URL }}\n\n"
            "      - name: Download census artifact",
        )
        failed = _run(sources)
        assert any("not credential-free" in item for item in failed), failed

    def test_structural_binding_fails_when_require_acceptance_verdict_is_dropped(self) -> None:
        sources = _real_sources()
        sources[_TRAILER_MODULE_PATH] = sources[_TRAILER_MODULE_PATH].replace("require_acceptance_verdict=True", "")
        failed = _run(sources)
        assert any("no longer passes acceptance_verdicts=" in item for item in failed), failed

    def test_git_ops_gate_key_fails_when_removed(self) -> None:
        sources = _real_sources()
        sources[_GIT_OPS_CONTRACT] = sources[_GIT_OPS_CONTRACT].replace("trailer_acceptance_gate", "renamed_gate_key")
        failed = _run(sources)
        assert any("no longer declares trailer_acceptance_gate" in item for item in failed), failed

    def test_ci_rca_lifecycle_contract_fails_when_ci_yml_unmentioned(self) -> None:
        sources = _real_sources()
        sources[_CI_RCA_LIFECYCLE_CONTRACT] = sources[_CI_RCA_LIFECYCLE_CONTRACT].replace("ci.yml", "the closure workflow")
        failed = _run(sources)
        assert any("no longer names ci.yml" in item for item in failed), failed

    def test_evaluator_job_fails_when_ci_yml_is_unparseable(self) -> None:
        sources = _real_sources()
        sources[_CI_WORKFLOW_PATH] = "not: valid: yaml: [unterminated"
        failed = _run(sources)
        assert any("not credential-free" in item for item in failed), failed

    def test_evaluator_job_fails_when_the_job_itself_is_missing(self) -> None:
        sources = _real_sources()
        sources[_CI_WORKFLOW_PATH] = "jobs:\n  some-other-job:\n    runs-on: ubuntu-latest\n"
        failed = _run(sources)
        assert any("not credential-free" in item for item in failed), failed

    def test_pre_globs_cover_every_read_input(self) -> None:
        """Recall guard (mirrors tests/checks/ci_guards/test__manifest.py's
        TestGatedEntryInputClosures): derives the read set from _ALL_SOURCES itself, never a
        hand-listed set, so a future read added to the check without a matching glob still reds
        here instead of silently skipping the check in --pre."""
        from fnmatch import fnmatch

        from scripts.checks.ci_guards import _manifest

        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_rec_autoclose_trailer_gate")
        globs = entry.pre_globs or ()
        for path in _ALL_SOURCES:
            assert any(fnmatch(path, glob) for glob in globs), path


_UPLOAD_STEP = """\
      - name: Upload pytest junit report
        uses: actions/upload-artifact@v7
        with:
          name: pytest-junit
          path: logs/debug/pytest-junit.xml
"""


def _minimal_workflow(*extra_jobs: str) -> str:
    return (
        "jobs:\n"
        "  main-validate:\n"
        "    permissions:\n"
        "      id-token: write\n"
        "      contents: read\n"
        "    steps:\n" + _UPLOAD_STEP + "".join(extra_jobs)
    )


def _download_job(name: str, *, id_token: bool = False, run_id: bool = False) -> str:
    permissions = "      id-token: write\n      contents: read\n" if id_token else "      contents: read\n"
    run_id_line = "          run-id: ${{ github.event.workflow_run.id }}\n" if run_id else ""
    return (
        f"  {name}:\n"
        f"    permissions:\n{permissions}"
        "    steps:\n"
        "      - name: Download pytest-junit report\n"
        "        uses: actions/download-artifact@v8\n"
        "        with:\n"
        "          name: pytest-junit\n" + run_id_line
    )


class TestJunitJobAssertions:
    """Decision 201 slice B's three new derived assertions (10-12), each with a passing (via
    TestPassPath's real-repo fixture) and a failing limb here."""

    def test_passes_against_the_real_committed_repository(self) -> None:
        failed: list[str] = []
        validate_rec_autoclose_trailer_gate(failed)
        assert failed == []

    def test_fails_when_a_verdict_consuming_job_declares_id_token(self) -> None:
        """The assertion is DERIVED, not a job-name literal (Decision 187 point 1): a SECOND,
        differently-named verdict-consuming job declaring id-token is caught, whatever it's
        named -- a name-literal assertion pinned to trailer-junit-verdict would miss it."""
        sources = _real_sources()
        workflow = _minimal_workflow(
            _download_job("trailer-junit-verdict"),
            _download_job("some-other-verdict-job", id_token=True),
        )
        sources[_CI_WORKFLOW_PATH] = workflow
        failed = _run(sources)
        assert any("some-other-verdict-job" in item and "declare id-token or a secret" in item for item in failed), failed

    def test_run_id_assertion_fails_when_a_cross_run_download_is_introduced(self) -> None:
        sources = _real_sources()
        workflow = _minimal_workflow(_download_job("trailer-junit-verdict", run_id=True))
        sources[_CI_WORKFLOW_PATH] = workflow
        failed = _run(sources)
        assert any("download by run-id" in item for item in failed), failed

    def test_derived_set_empty_fails_never_passes_vacuously(self) -> None:
        sources = _real_sources()
        sources[_CI_WORKFLOW_PATH] = "jobs:\n  some-job:\n    runs-on: ubuntu-latest\n"
        failed = _run(sources)
        assert any("derived verdict-consuming-job set is empty" in item for item in failed), failed

    def test_source_admission_clause_fails_when_removed(self) -> None:
        sources = _real_sources()
        sources[_GIT_OPS_CONTRACT] = sources[_GIT_OPS_CONTRACT].replace("source_admission", "source-admission")
        failed = _run(sources)
        assert any("does not declare the source_admission clause" in item for item in failed), failed

    def test_examined_and_skipped_declared_on_derived_set_failure_path(self) -> None:
        with registry.outcome_scope("validate_rec_autoclose_trailer_gate"):
            failed: list[str] = []
            sources = _real_sources()
            sources[_CI_WORKFLOW_PATH] = "jobs:\n  some-job:\n    runs-on: ubuntu-latest\n"

            def fake_read(root, rel):  # noqa: ANN001, ARG001
                return sources.get(rel)

            with patch(f"{_MODULE}._read", side_effect=fake_read):
                validate_rec_autoclose_trailer_gate(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"

    def test_derive_artifact_name_skips_a_non_dict_job_value(self) -> None:
        from scripts.checks.ci_guards.validate_rec_autoclose_trailer_gate import _derive_pytest_junit_artifact_name

        jobs = {"bad-job": "not-a-dict", "main-validate": {"steps": [{"uses": "actions/upload-artifact@v7"}]}}
        assert _derive_pytest_junit_artifact_name(jobs) is None

    def test_download_step_for_artifact_skips_a_non_dict_with_block(self) -> None:
        from scripts.checks.ci_guards.validate_rec_autoclose_trailer_gate import _download_step_for_artifact

        job = {"steps": [{"uses": "actions/download-artifact@v8", "with": "not-a-dict"}]}
        assert _download_step_for_artifact(job, "pytest-junit") is None

    def test_verdict_consuming_jobs_returns_none_when_jobs_key_is_not_a_dict(self) -> None:
        from scripts.checks.ci_guards.validate_rec_autoclose_trailer_gate import _verdict_consuming_jobs

        assert _verdict_consuming_jobs("jobs: not-a-dict\n") is None
