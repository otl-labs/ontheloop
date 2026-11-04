"""Check tests for scripts/checks/roadmap/validate_plan_fork_lines.py (rec-3530), served through the seams."""

from __future__ import annotations

import builtins
import glob
import io
import json
import os
import pathlib
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.roadmap import validate_plan_fork_lines as check
from scripts.roadmap import plan_fork_lines as engine

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_TEXT = (ROOT / engine.CONTRACT).read_text(encoding="utf-8")


def _line(**over: str) -> str:
    base = {
        "fork": "which store",
        "chosen": "the index",
        "class": "consistency-only",
        "precedent": "Decision 10",
        "precedent_kind": "decision",
        "reversible": "yes",
        "persisted": "the plan",
    }
    base.update(over)
    return " | ".join(f"{k}: {v}" for k, v in base.items())


GOOD = _line()
BAD = "fork: only | chosen: two | class: asked"
ASKED = _line(**{"class": "asked", "precedent": "none", "precedent_kind": "none"})
INDEX = json.dumps({"decisions": [{"number": 10, "currency": "current", "live": True, "status": "Decided"}]})


def _plan(*lines: str) -> str:
    return yaml.safe_dump({"context": list(lines)})


def _files(**extra: str) -> dict[str, str]:
    return {engine.CONTRACT: CONTRACT_TEXT, engine.DECISIONS_INDEX: INDEX, **extra}


def _run(changed, base: dict[str, str], head: dict[str, str]) -> list[str]:
    failed: list[str] = []
    check.run_check(failed, root=Path("/unused"), base="BASE", changed=changed, read_base=base.get, read_head=head.get)
    return failed


P = "docs/plans/PLAN-diff.yaml"


class TestOnlyNewLinesAreJudged:
    def test_bad_line_already_on_base_passes(self) -> None:
        assert _run([("M", P)], {P: _plan(BAD)}, _files(**{P: _plan(BAD, GOOD)})) == []

    def test_same_bad_line_added_fails(self) -> None:
        failed = _run([("M", P)], {P: _plan(GOOD)}, _files(**{P: _plan(GOOD, BAD)}))
        assert any(f.startswith(f"{P} fork 2: missing-key") for f in failed)
        assert failed[-1] == "Plan fork-line validation"

    def test_edited_base_line_fails(self) -> None:
        edited = GOOD.replace("the index", "the other index")
        assert _run([("M", P)], {P: _plan(GOOD)}, _files(**{P: _plan(edited)})) == []
        broken = GOOD.replace(" | persisted: the plan", "")
        failed = _run([("M", P)], {P: _plan(GOOD)}, _files(**{P: _plan(broken)}))
        assert any("missing-key" in f for f in failed)

    def test_untracked_plan_judges_all_lines(self) -> None:
        failed = _run([("??", P)], {P: _plan(BAD)}, _files(**{P: _plan(BAD)}))
        assert any("missing-key" in f for f in failed)

    def test_added_plan_judges_all_lines(self) -> None:
        failed = _run([("A", P)], {P: _plan(BAD)}, _files(**{P: _plan(BAD, BAD)}))
        assert len([f for f in failed if "missing-key" in f]) == 2

    def test_non_plan_paths_and_deleted_plans_are_ignored(self) -> None:
        head = _files(
            **{"docs/plans/approved/x.yaml": _plan(BAD), "docs/plans/PLAN-gone.yaml": _plan(BAD), "notes.yaml": _plan(BAD)}
        )
        changed = [("M", "docs/plans/approved/x.yaml"), ("D", "docs/plans/PLAN-gone.yaml"), ("M", "notes.yaml")]
        assert _run(changed, {}, head) == []

    def test_duplicate_line_added_beyond_base_count_is_judged(self) -> None:
        assert _run([("M", P)], {P: _plan(BAD)}, _files(**{P: _plan(BAD, BAD)}))

    def test_warn_does_not_fail(self, capsys) -> None:
        rows = [{"number": 10, "currency": "superseded_pointer", "live": True, "status": "x", "superseded_by": 11}]
        head = _files(**{P: _plan(GOOD), engine.DECISIONS_INDEX: json.dumps({"decisions": rows})})
        assert _run([("A", P)], {}, head) == []
        assert "WARN" in capsys.readouterr().out


class TestBoundedReads:
    PLANS = {
        "docs/plans/PLAN-ref.yaml": _plan(ASKED),
        "docs/plans/PLAN-idle-a.yaml": _plan(GOOD),
        "docs/plans/PLAN-idle-b.yaml": "{ not: [valid",
        "docs/plans/PLAN-only-base-cites.yaml": _plan(ASKED),
    }
    DIFF_PLAN = _plan(
        _line(precedent="PLAN-ref fork 1", precedent_kind="answered-fork"),
        _line(precedent="docs/contracts/overseer-dispatch.yaml#autonomy_tiers", precedent_kind="contract"),
        GOOD,
    )
    BASE_PLAN = _plan(_line(precedent="PLAN-only-base-cites fork 1", precedent_kind="answered-fork"))
    EXPECTED = {P, engine.CONTRACT, engine.DECISIONS_INDEX, "docs/plans/PLAN-ref.yaml"}

    def _tree(self) -> dict[str, str]:
        return _files(**{P: self.DIFF_PLAN, **self.PLANS})

    def _patch_listing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a, **_k):
            raise AssertionError("direct filesystem or process access")

        for owner, name in (
            (builtins, "open"),
            (io, "open"),
            (pathlib.Path, "open"),
            (pathlib.Path, "read_text"),
            (pathlib.Path, "read_bytes"),
            (pathlib.Path, "glob"),
            (pathlib.Path, "rglob"),
            (pathlib.Path, "iterdir"),
            (os, "listdir"),
            (os, "scandir"),
            (glob, "glob"),
            (subprocess, "run"),
            (subprocess, "Popen"),
            (_common, "run"),
        ):
            monkeypatch.setattr(owner, name, boom)

    def test_run_check_reads_only_the_expected_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tree, requested = self._tree(), []

        def read(rel: str) -> str | None:
            requested.append(rel)
            return tree.get(rel)

        failed: list[str] = []
        self._patch_listing(monkeypatch)
        check.run_check(
            failed,
            root=Path("/unused"),
            base="BASE",
            changed=[("M", P)],
            read_base=lambda rel: (requested.append(rel), {P: self.BASE_PLAN}.get(rel))[1],
            read_head=read,
        )
        assert failed == []
        assert set(requested) == self.EXPECTED
        assert len(requested) == len(set(requested)) + 1  # the diff plan is read at base and at head

    def test_registered_entrypoint_enumerates_only_through_the_diff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tree, head_reads, git_calls = self._tree(), [], []

        def recorder(cmd, **_k):
            assert cmd[:2] == ["git", "show"] and cmd[2] == f"BASE:{P}", cmd
            git_calls.append(cmd[2])
            return type("R", (), {"returncode": 0, "stdout": self.BASE_PLAN})()

        def worktree(root, rel):
            head_reads.append(rel)
            return tree.get(rel)

        monkeypatch.setattr(check, "_read_worktree", worktree)
        self._patch_listing(monkeypatch)
        monkeypatch.setattr(_common, "status_diff_base", lambda root=None: "BASE")
        monkeypatch.setattr(_common, "get_status_aware_diff", lambda root=None: [("M", P)])
        monkeypatch.setattr(_common, "run", recorder)
        failed: list[str] = []
        check.validate_plan_fork_lines(failed, Path("/unused"))
        assert failed == []
        assert set(head_reads) | {rel.split(":", 1)[1] for rel in git_calls} == self.EXPECTED
        assert "docs/plans/PLAN-idle-a.yaml" not in head_reads and "docs/plans/PLAN-idle-b.yaml" not in head_reads


class TestContractDrift:
    def _fail(self, contract_text: str | None) -> list[str]:
        head = {engine.CONTRACT: contract_text} if contract_text is not None else {}
        return _run([], {}, head)

    def test_disagreeing_template_names_the_field(self) -> None:
        mutated = CONTRACT_TEXT.replace("reversible: yes|no", "reversible: yes|no|maybe", 1)
        assert mutated != CONTRACT_TEXT
        failed = self._fail(mutated)
        assert failed and "REVERSIBLE" in failed[0]

    def test_missing_contract_or_grammar_fails(self) -> None:
        assert self._fail(None)
        assert self._fail(yaml.safe_dump({"autonomy_tiers": {}}))

    def test_grammar_without_template_fails(self) -> None:
        doc = {"autonomy_tiers": {"plan_fork_classification": {"record_grammar": "no quoted template"}}}
        assert "no extractable" in self._fail(yaml.safe_dump(doc))[0]


class TestAccounting:
    def test_base_none_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reasons: list[str] = []
        monkeypatch.setattr(_common, "status_diff_base", lambda root=None: None)
        monkeypatch.setattr(registry, "skipped", reasons.append)
        failed: list[str] = []
        check.validate_plan_fork_lines(failed, Path("/unused"))
        assert failed == [] and reasons == ["diff base unreachable"]

    def test_no_plan_in_diff_is_examined_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[tuple[int, str]] = []
        monkeypatch.setattr(registry, "examined", lambda n, *, unit="items": seen.append((n, unit)))
        assert _run([("M", "scripts/x.py")], {}, _files()) == []
        assert seen == [(0, "fork_lines")]

    def test_counts_judged_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[int] = []
        monkeypatch.setattr(registry, "examined", lambda n, *, unit="items": seen.append(n))
        _run([("A", P)], {}, _files(**{P: _plan(GOOD, ASKED)}))
        assert seen == [2]

    def test_unparseable_head_plan_warns_and_is_skipped(self, capsys) -> None:
        assert _run([("A", P)], {}, _files(**{P: "{ not: [valid"})) == []
        assert "WARN" in capsys.readouterr().out

    def test_main_replays_through_git_show(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        shown: list[str] = []
        tree = {f"HEAD1:{engine.CONTRACT}": CONTRACT_TEXT, f"HEAD1:{P}": _plan(BAD), f"HEAD1:{engine.DECISIONS_INDEX}": INDEX}

        def fake_run(cmd, **_k):
            if cmd[:2] == ["git", "diff"]:
                assert cmd[-1] == "BASE..HEAD1"
                return type("R", (), {"returncode": 0, "stdout": f"A\t{P}\n"})()
            shown.append(cmd[2])
            text = tree.get(cmd[2])
            return type("R", (), {"returncode": 0 if text is not None else 1, "stdout": text or ""})()

        monkeypatch.setattr(_common, "run", fake_run)
        assert check.main(["--base", "BASE", "--head", "HEAD1"]) == 1
        assert f"HEAD1:{P}" in shown and "FAILED" in capsys.readouterr().out


class TestSeamsAndEntrypoints:
    def test_read_worktree_reads_and_tolerates_missing(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        assert check._read_worktree(tmp_path, "a.txt") == "hi"
        assert check._read_worktree(tmp_path, "nope.txt") is None

    def test_read_at_ref_returns_none_on_git_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_common, "run", lambda cmd, **_k: type("R", (), {"returncode": 128, "stdout": ""})())
        assert check._read_at_ref(Path("/unused"), "BASE", "x") is None

    def test_context_of_non_plan_text_is_none_or_empty(self) -> None:
        assert check._context(None) is None
        assert check._context("[1, 2]") is None
        assert check._context("a: 1") == []

    def test_main_skips_when_the_base_is_unreachable(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.setattr(_common, "status_diff_base", lambda root=None: None)
        assert check.main([]) == 0
        assert "SKIP" in capsys.readouterr().out

    def test_main_on_the_working_tree_passes_a_clean_diff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_common, "status_diff_base", lambda root=None: "BASE")
        monkeypatch.setattr(_common, "get_status_aware_diff", lambda root=None: [])
        monkeypatch.setattr(check, "_read_worktree", lambda root, rel: _files().get(rel))
        assert check.main([]) == 0
