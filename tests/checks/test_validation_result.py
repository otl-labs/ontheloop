from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.checks import registry, validation_result

_GIT_ENV = ["-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture(autouse=True)
def _reset_attributions():
    validation_result._ATTRIBUTIONS.clear()
    validation_result._OUTCOMES.clear()
    validation_result._SCAFFOLD_FAILURES.clear()
    validation_result._FAILURE_DETAILS.clear()
    validation_result._START_HEAD_TREE = None
    validation_result._START_TREE_CLEAN = False
    yield
    validation_result._ATTRIBUTIONS.clear()
    validation_result._OUTCOMES.clear()
    validation_result._SCAFFOLD_FAILURES.clear()
    validation_result._FAILURE_DETAILS.clear()
    validation_result._START_HEAD_TREE = None
    validation_result._START_TREE_CLEAN = False


def test_clear_removes_stale_result(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text("stale", encoding="utf-8")
    validation_result.clear(path)
    assert not path.exists()


def test_clear_resets_the_attribution_accumulator(tmp_path: Path) -> None:
    validation_result._ATTRIBUTIONS.append({"check": "stale_check", "label": "stale label"})
    validation_result.clear(tmp_path / "result.json")
    assert validation_result._ATTRIBUTIONS == []


def test_clear_default_resolves_at_call_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """clear(path=None) must resolve RESULT_PATH AT CALL TIME, not at def time -- a test that
    monkeypatches RESULT_PATH before calling clear() with no argument must be honoured."""
    redirected = tmp_path / "redirected.json"
    redirected.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(validation_result, "RESULT_PATH", redirected)
    validation_result.clear()
    assert not redirected.exists()


def test_clear_snapshots_start_tree_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(validation_result, "_head_tree", lambda: "tree-a")
    monkeypatch.setattr(validation_result, "_tree_clean", lambda: True)
    validation_result.clear(tmp_path / "result.json")
    assert validation_result._START_HEAD_TREE == "tree-a"
    assert validation_result._START_TREE_CLEAN is True


def test_result_path_is_anchored_at_root() -> None:
    assert validation_result.RESULT_PATH == validation_result._common.ROOT / "logs" / "debug" / "validation-result.json"


def test_git_head_resolves_from_invoking_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rec-3033's acceptance node: every git probe in this module resolves against
    scripts.checks._common.ROOT -- the invoking worktree -- never the process cwd or the real
    repository this test itself runs inside. Uses a genuine `git worktree add` against a throwaway
    tmp repository, never the real checkout (which would write its .git/worktrees)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(["init"], cwd=origin)
    (origin / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=origin)
    _git([*_GIT_ENV, "commit", "-m", "initial"], cwd=origin)
    expected_head = _git(["rev-parse", "HEAD"], cwd=origin).stdout.strip()

    worktree = tmp_path / "worktree"
    _git(["worktree", "add", str(worktree), "HEAD"], cwd=origin)

    monkeypatch.setattr(validation_result._common, "ROOT", worktree)
    assert validation_result.git_head() == expected_head


def test_dispatch_recording_attributes_each_newly_appended_label() -> None:
    def _two_failures(failed: list[str]) -> None:
        failed.append("first problem")
        failed.append("second problem")

    failed: list[str] = []
    validation_result.dispatch_recording("validate_two_things", failed, _two_failures)
    assert failed == ["first problem", "second problem"]
    assert validation_result._ATTRIBUTIONS == [
        {"check": "validate_two_things", "label": "first problem"},
        {"check": "validate_two_things", "label": "second problem"},
    ]


def test_record_scaffold_outcome_harvests_a_declared_outcome() -> None:
    """record_scaffold_outcome() is the scaffold-side counterpart to dispatch_recording()'s
    outcome harvest -- used by non-check scaffolds like ensure_fresh_dq_results."""
    with registry.outcome_scope("my_scaffold", kind="scaffold"):
        registry.examined(3, unit="items")
    validation_result.record_scaffold_outcome("my_scaffold", 0, [])
    row = validation_result._OUTCOMES[-1]
    assert row.check == "my_scaffold"
    assert row.kind == "scaffold"
    assert row.status == "enforced"


def test_record_scaffold_outcome_is_failed_when_failed_grew() -> None:
    with registry.outcome_scope("failing_scaffold", kind="scaffold"):
        registry.examined(1)
    failed = ["already there", "new problem"]
    validation_result.record_scaffold_outcome("failing_scaffold", before=1, failed=failed)
    row = validation_result._OUTCOMES[-1]
    assert row.status == "failed"


def test_dispatch_recording_attributes_nothing_on_a_passing_check() -> None:
    failed: list[str] = []
    validation_result.dispatch_recording("validate_ok", failed, lambda failed: None)
    assert failed == []
    assert validation_result._ATTRIBUTIONS == []


def test_dispatch_recording_does_not_attribute_pre_existing_entries() -> None:
    """Only labels newly appended by THIS check are attributed -- a pre-existing `failed` entry
    from an earlier check must not be re-attributed to the current one."""

    def _adds_one_more(failed: list[str]) -> None:
        failed.append("new problem")

    failed: list[str] = ["earlier problem"]
    validation_result.dispatch_recording("validate_second", failed, _adds_one_more)
    assert failed == ["earlier problem", "new problem"]
    assert validation_result._ATTRIBUTIONS == [{"check": "validate_second", "label": "new problem"}]


def test_dispatch_recording_calls_the_resolved_callable_at_call_time() -> None:
    """`fn` is whatever the CALLER resolved (scripts.checks.registry.resolve(name)) and is invoked
    directly -- late-bound by the caller, not by a namespace lookup inside this function."""
    calls: list[str] = []

    def _fn(failed: list[str]) -> None:
        calls.append("called")
        failed.append("patched")

    failed: list[str] = []
    validation_result.dispatch_recording("validate_x", failed, _fn)
    assert calls == ["called"]
    assert failed == ["patched"]
    assert validation_result._ATTRIBUTIONS == [{"check": "validate_x", "label": "patched"}]


def test_write_completed_is_bounded_atomic_and_secret_free(tmp_path: Path) -> None:
    path = tmp_path / "debug" / "result.json"
    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=1, failed_checks=["lint"], path=path
        )
    assert json.loads(path.read_text(encoding="utf-8")) == record
    assert record["git_head"] == "abc123"
    assert record["failed_checks"] == ["lint"]
    assert not list(path.parent.glob("*.tmp"))
    assert "secret" not in path.read_text(encoding="utf-8").lower()


def test_write_completed_emits_schema_v3_with_failed_check_attributions(tmp_path: Path) -> None:
    """failed_checks/failed_check_attributions keep their exact schema_version 2 shape
    byte-for-byte under the v4 bump (Decision 142 lineage -- scripts.ci_rca.taxonomy's
    Priority-0 attribution path is a live consumer)."""
    path = tmp_path / "debug" / "result.json"
    validation_result._ATTRIBUTIONS.append({"check": "validate_test_coverage", "label": "Coverage below 100%"})
    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00",
            exit_code=1,
            failed_checks=["validate_test_coverage"],
            path=path,
        )
    assert record["schema_version"] == 4
    assert record["failed_check_attributions"] == [{"check": "validate_test_coverage", "label": "Coverage below 100%"}]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 4
    assert on_disk["failed_check_attributions"] == record["failed_check_attributions"]


def test_write_completed_records_head_tree_and_clean_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "debug" / "result.json"
    monkeypatch.setattr(validation_result, "_START_HEAD_TREE", "tree-a")
    monkeypatch.setattr(validation_result, "_START_TREE_CLEAN", True)
    monkeypatch.setattr(validation_result, "_head_tree", lambda: "tree-a")
    monkeypatch.setattr(validation_result, "_tree_clean", lambda: True)
    monkeypatch.setattr(validation_result, "_worktree_toplevel", lambda: "/repo")
    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=0, failed_checks=[], path=path
        )
    assert record["schema_version"] == 4
    assert record["head_tree_at_start"] == "tree-a"
    assert record["head_tree_at_end"] == "tree-a"
    assert record["tree_clean_at_start"] is True
    assert record["tree_clean_at_end"] is True
    assert record["worktree_toplevel"] == "/repo"
    assert record["failed_checks"] == []
    assert record["failed_check_attributions"] == []
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == record


def test_write_completed_defaults_head_tree_fields_when_clear_never_ran(tmp_path: Path) -> None:
    path = tmp_path / "debug" / "result.json"
    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=0, failed_checks=[], path=path
        )
    assert record["head_tree_at_start"] is None
    assert record["tree_clean_at_start"] is False


def test_failed_check_details_emitted_additively(tmp_path: Path) -> None:
    """failed_check_details (this plan, coverage-failure-attribution) appears under the NEW
    top-level key only when a check actually declared detail this run; failed_checks and
    failed_check_attributions stay byte-identical to today either way (Decision 170 clause 1 /
    check-accounting.yaml's validation_result_schema_v3 pin)."""
    path = tmp_path / "debug" / "result.json"
    git = MagicMock(returncode=0, stdout="abc123\n")

    def _fn(failed: list[str]) -> None:
        registry.failure_detail(["scripts/checks/hygiene/validate_vacuity_justified.py: 95.8% (expected 100%)"])
        failed.append("Coverage below 100%")

    failed_list: list[str] = []
    validation_result.dispatch_recording("validate_test_coverage", failed_list, _fn)
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=1, failed_checks=failed_list, path=path
        )
    assert record["failed_check_details"] == {
        "validate_test_coverage": ["scripts/checks/hygiene/validate_vacuity_justified.py: 95.8% (expected 100%)"]
    }
    assert record["failed_checks"] == ["Coverage below 100%"]
    assert record["failed_check_attributions"] == [{"check": "validate_test_coverage", "label": "Coverage below 100%"}]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["failed_check_details"] == record["failed_check_details"]

    validation_result.clear(path)
    failed_list2: list[str] = []
    validation_result.dispatch_recording("validate_ok", failed_list2, lambda failed: None)
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record2 = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=0, failed_checks=failed_list2, path=path
        )
    assert "failed_check_details" not in record2
    on_disk2 = json.loads(path.read_text(encoding="utf-8"))
    assert "failed_check_details" not in on_disk2


def test_write_completed_attributions_is_a_snapshot_not_a_live_reference(tmp_path: Path) -> None:
    """write_completed must copy the accumulator -- a later clear() must not mutate the
    already-written record's in-memory dict."""
    path = tmp_path / "debug" / "result.json"
    validation_result._ATTRIBUTIONS.append({"check": "validate_x", "label": "problem"})
    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=1, failed_checks=["validate_x"], path=path
        )
    validation_result.clear(path)
    assert record["failed_check_attributions"] == [{"check": "validate_x", "label": "problem"}]


def test_write_completed_emits_check_outcomes_and_consistent_rollups(tmp_path: Path) -> None:
    path = tmp_path / "debug" / "result.json"
    validation_result.dispatch_recording("vacuous_check", [], lambda f: registry.examined(0))
    validation_result.dispatch_recording("enforced_check", [], lambda f: registry.examined(4))
    validation_result.dispatch_recording("skipped_check", [], lambda f: registry.skipped("no input"))
    validation_result.dispatch_recording("undeclared_check", [], lambda f: None)
    failed_list: list[str] = []
    validation_result.dispatch_recording("failing_check", failed_list, lambda f: f.append("bad"))

    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=1, failed_checks=failed_list, path=path
        )

    statuses = {row["check"]: row["status"] for row in record["check_outcomes"]}  # type: ignore[attr-defined]
    assert statuses == {
        "vacuous_check": "vacuous",
        "enforced_check": "enforced",
        "skipped_check": "skipped",
        "undeclared_check": "undeclared",
        "failing_check": "failed",
    }
    # Rollups agree with check_outcomes -- each non-"failed" status appears in exactly one bucket,
    # and the bucket totals sum to the non-failed row count.
    assert record["ran_checks"] == 1
    assert record["skipped_checks"] == 1
    assert record["vacuous_checks"] == 1
    assert record["undeclared_checks"] == 1
    non_failed_rows = sum(1 for status in statuses.values() if status != "failed")
    assert record["ran_checks"] + record["skipped_checks"] + record["vacuous_checks"] + record["undeclared_checks"] == (
        non_failed_rows
    )
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["check_outcomes"] == record["check_outcomes"]


def test_write_completed_check_outcomes_is_a_snapshot_not_a_live_reference(tmp_path: Path) -> None:
    """A later clear() must not mutate an already-written record's check_outcomes list."""
    path = tmp_path / "debug" / "result.json"
    validation_result.dispatch_recording("check_x", [], lambda f: registry.examined(1))
    git = MagicMock(returncode=0, stdout="abc123\n")
    with patch("scripts.checks.validation_result.subprocess.run", return_value=git):
        record = validation_result.write_completed(
            started_at="2026-01-01T00:00:00+00:00", exit_code=0, failed_checks=[], path=path
        )
    validation_result.clear(path)
    assert record["check_outcomes"] == [
        {
            "check": "check_x",
            "kind": "check",
            "status": "enforced",
            "examined_count": 1,
            "examined_unit": "items",
            "skipped_reason": None,
        }
    ]


def test_visible_writer_warns_without_raising(capsys) -> None:
    with patch("scripts.checks.validation_result.write_completed", side_effect=OSError("disk full")):
        validation_result.write_completed_visible(started_at="now", exit_code=0, failed_checks=[])
    assert "WARNING: validation evidence could not be written: OSError" in capsys.readouterr().out


def test_load_record_missing(tmp_path: Path) -> None:
    record, status = validation_result.load_record(tmp_path / "nope.json")
    assert record is None
    assert status == "missing"


def test_load_record_unreadable_on_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    record, status = validation_result.load_record(path)
    assert record is None
    assert status == "unreadable"


def test_load_record_unreadable_on_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    record, status = validation_result.load_record(path)
    assert record is None
    assert status == "unreadable"


def test_load_record_ok(tmp_path: Path) -> None:
    path = tmp_path / "good.json"
    path.write_text(json.dumps({"schema_version": 4}), encoding="utf-8")
    record, status = validation_result.load_record(path)
    assert record == {"schema_version": 4}
    assert status == "ok"


def _good_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 4,
        "scope": "all",
        "worktree_toplevel": "/repo",
        "tree_clean_at_start": True,
        "tree_clean_at_end": True,
        "head_tree_at_start": "tree-a",
        "head_tree_at_end": "tree-a",
        "exit_code": 0,
        "failed_checks": [],
    }
    record.update(overrides)
    return record


class TestAttestsHead:
    """Every ATTEST_RULES code, exercised in the module's evaluation order."""

    def test_missing_when_record_is_none(self) -> None:
        ok, rule = validation_result.attests_head(None, head_tree="tree-a", toplevel=Path("/repo"))
        assert (ok, rule) == (False, "missing")

    def test_unreadable_when_record_lacks_schema_version(self) -> None:
        ok, rule = validation_result.attests_head({"scope": "all"}, head_tree="tree-a", toplevel=Path("/repo"))
        assert (ok, rule) == (False, "unreadable")

    def test_schema_mismatch(self) -> None:
        ok, rule = validation_result.attests_head(_good_record(schema_version=3), head_tree="tree-a", toplevel=Path("/repo"))
        assert (ok, rule) == (False, "schema")

    def test_scope_mismatch(self) -> None:
        ok, rule = validation_result.attests_head(_good_record(scope="pre"), head_tree="tree-a", toplevel=Path("/repo"))
        assert (ok, rule) == (False, "scope")

    def test_worktree_mismatch(self) -> None:
        ok, rule = validation_result.attests_head(
            _good_record(worktree_toplevel="/elsewhere"), head_tree="tree-a", toplevel=Path("/repo")
        )
        assert (ok, rule) == (False, "worktree")

    def test_dirty_start(self) -> None:
        ok, rule = validation_result.attests_head(
            _good_record(tree_clean_at_start=False), head_tree="tree-a", toplevel=Path("/repo")
        )
        assert (ok, rule) == (False, "dirty_start")

    def test_dirty_end(self) -> None:
        ok, rule = validation_result.attests_head(
            _good_record(tree_clean_at_end=False), head_tree="tree-a", toplevel=Path("/repo")
        )
        assert (ok, rule) == (False, "dirty_end")

    def test_head_moved_when_start_and_end_trees_differ(self) -> None:
        ok, rule = validation_result.attests_head(
            _good_record(head_tree_at_end="tree-b"), head_tree="tree-a", toplevel=Path("/repo")
        )
        assert (ok, rule) == (False, "head_moved")

    def test_rejects_evidence_for_a_different_tree(self, tmp_path: Path) -> None:
        """VP step 1 -- the graduated invariant: evidence internally consistent (start == end)
        but recorded for a different, REAL tree must never attest an unrelated HEAD. Built from
        two genuinely distinct trees via real commits, never a mocked/fabricated hash."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init"], cwd=repo)
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        _git(["add", "a.txt"], cwd=repo)
        _git([*_GIT_ENV, "commit", "-m", "first"], cwd=repo)
        tree_a = _git(["rev-parse", "HEAD^{tree}"], cwd=repo).stdout.strip()

        (repo / "a.txt").write_text("two\n", encoding="utf-8")
        _git(["add", "a.txt"], cwd=repo)
        _git([*_GIT_ENV, "commit", "-m", "second"], cwd=repo)
        tree_b = _git(["rev-parse", "HEAD^{tree}"], cwd=repo).stdout.strip()

        assert tree_a != tree_b

        record = _good_record(head_tree_at_start=tree_a, head_tree_at_end=tree_a, worktree_toplevel=str(repo))
        ok, rule = validation_result.attests_head(record, head_tree=tree_b, toplevel=repo)
        assert (ok, rule) == (False, "tree_mismatch")

    def test_failed_run_when_exit_code_nonzero(self) -> None:
        ok, rule = validation_result.attests_head(_good_record(exit_code=1), head_tree="tree-a", toplevel=Path("/repo"))
        assert (ok, rule) == (False, "failed_run")

    def test_failed_run_when_failed_checks_nonempty(self) -> None:
        ok, rule = validation_result.attests_head(
            _good_record(failed_checks=["lint"]), head_tree="tree-a", toplevel=Path("/repo")
        )
        assert (ok, rule) == (False, "failed_run")

    def test_ok_when_every_rule_holds(self) -> None:
        ok, rule = validation_result.attests_head(_good_record(), head_tree="tree-a", toplevel=Path("/repo"))
        assert (ok, rule) == (True, "ok")


class TestVerifyHead:
    def test_missing_evidence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validation_result, "_worktree_toplevel", lambda: str(tmp_path))
        monkeypatch.setattr(validation_result, "_head_tree", lambda: "tree-a")
        ok, rule = validation_result.verify_head()
        assert (ok, rule) == (False, "missing")

    def test_ok_when_evidence_attests_current_head(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validation_result, "_worktree_toplevel", lambda: str(tmp_path))
        monkeypatch.setattr(validation_result, "_head_tree", lambda: "tree-a")
        evidence = validation_result.evidence_path_for(tmp_path)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(_good_record(worktree_toplevel=str(tmp_path))),
            encoding="utf-8",
        )
        ok, rule = validation_result.verify_head()
        assert (ok, rule) == (True, "ok")


class TestVerifyHeadCli:
    def test_exits_zero_on_ok(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr(validation_result, "verify_head", lambda: (True, "ok"))
        rc = validation_result.main(["--verify-head"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "ok"

    def test_exits_one_on_failure_and_names_the_rule(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(validation_result, "verify_head", lambda: (False, "tree_mismatch"))
        rc = validation_result.main(["--verify-head"])
        assert rc == 1
        assert capsys.readouterr().out.strip() == "tree_mismatch"

    def test_no_flag_prints_help_and_exits_one(self, capsys: pytest.CaptureFixture) -> None:
        rc = validation_result.main([])
        assert rc == 1


def test_run_scaffold_records_only_scaffolds_that_append_labels() -> None:
    failed = ["earlier"]
    validation_result.run_scaffold("quiet", failed, lambda: None)
    validation_result.run_scaffold("noisy", failed, lambda: failed.append("label"))
    assert validation_result._SCAFFOLD_FAILURES == ["noisy"]


def test_failure_markers_are_ordered_unique_and_checks_first() -> None:
    validation_result._SCAFFOLD_FAILURES.extend(["scaf_b", "scaf_b", "scaf_a"])
    validation_result._ATTRIBUTIONS.extend(
        [{"check": "check_z", "label": "1"}, {"check": "check_y", "label": "2"}, {"check": "check_z", "label": "3"}]
    )
    assert validation_result.failure_markers() == [
        "GATE-FAILURE v1 source=gate check=check_z",
        "GATE-FAILURE v1 source=gate check=check_y",
        "GATE-FAILURE v1 source=gate check=scaf_b",
        "GATE-FAILURE v1 source=gate check=scaf_a",
    ]


def test_clear_resets_scaffold_failures(tmp_path: Path) -> None:
    validation_result._SCAFFOLD_FAILURES.append("scaf")
    validation_result.clear(tmp_path / "result.json")
    assert validation_result._SCAFFOLD_FAILURES == []


def test_print_failure_summary_prints_labels_then_markers(capsys: pytest.CaptureFixture[str]) -> None:
    validation_result._ATTRIBUTIONS.append({"check": "check_z", "label": "lbl"})
    validation_result.print_failure_summary(["lbl"])
    assert capsys.readouterr().out == "Failed checks:\n  - lbl\nGATE-FAILURE v1 source=gate check=check_z\n"


def test_print_failure_summary_without_markers_prints_labels_only(capsys: pytest.CaptureFixture[str]) -> None:
    validation_result.print_failure_summary(["lbl"])
    assert capsys.readouterr().out == "Failed checks:\n  - lbl\n"
