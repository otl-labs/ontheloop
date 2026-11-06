"""rec-4769 acceptance: the approved-words digest on real sidecar history, the provenance core, the plan-PR leg and
the two real seams (critique r5 B1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.roadmap import approved_text, approved_words
from scripts.roadmap import approved_text_provenance as prov
from tests.fixtures import merge_step_repos as fx

HISTORY = yaml.safe_load((Path(__file__).parent / "fixtures/approved_text/sidecar-history.yaml").read_text(encoding="utf-8"))
OWN = "approved-text-operator-approval@211f307c"
SLUG = "demo"
BLOCK = {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """pr-validate exports these three to the whole step that runs pytest and tests/conftest.py does not clear them."""
    monkeypatch.delenv("APPROVED_TEXT_GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture-owner/fixture-repo")


def _digest(name: str) -> str:
    return approved_words.approved_digest(approved_words.parse_sidecar(HISTORY[name]))


def _own() -> dict[str, Any]:
    return yaml.safe_load(HISTORY[OWN])


def _synthetic_digest(edit) -> str:
    data = _own()
    edit({b["id"]: b for b in data["blocks"]})
    return approved_words.approved_digest(approved_words.parse_sidecar(yaml.safe_dump(data)))


class TestSidecarProvenance:
    def test_anchor_only_pairs_keep_the_digest(self) -> None:
        assert _digest("retire-strategic@44d7d83d") == _digest("retire-strategic@40252d08")
        assert _digest("t2-47@a7fb3bc9~1") == _digest("t2-47@a7fb3bc9")

    def test_a_word_change_inside_main_edits_changes_the_digest(self) -> None:
        assert _digest("retire-strategic@4e62d4b3") != _digest("retire-strategic@44d7d83d")
        assert _digest("glossary-sweep@390379a1") != _digest("glossary-sweep@4b76d804")

    def test_a_widened_removal_changes_the_digest(self) -> None:
        def edit(b):
            b["planning-step-8b"]["before"] = "Extra leading words. " + b["planning-step-8b"]["before"]
            b["planning-step-8b"]["after"] = b["planning-step-8b"]["after"]

        assert _synthetic_digest(edit) != approved_words.approved_digest(approved_words.parse_sidecar(HISTORY[OWN]))

    def test_a_removed_word_kept_by_moving_it_into_the_anchor_changes_the_digest(self) -> None:
        def edit(b):
            b["planning-step-8b"]["after"] = b["planning-step-8b"]["after"].replace(
                "edits, then runs", "edits (`approved_text.verify`), then runs", 1
            )

        assert _synthetic_digest(edit) != _synthetic_digest(lambda b: None)

    def test_swapping_two_blocks_additions_changes_the_digest(self) -> None:
        def edit(b):
            b["git-ops-log"]["after"], b["git-ops-binding"]["after"] = b["git-ops-binding"]["after"], b["git-ops-log"]["after"]

        assert _synthetic_digest(edit) != _synthetic_digest(lambda b: None)

    def test_swapping_two_pure_insertions_keeps_the_digest_the_declared_residual(self) -> None:
        def edit(b):
            for key in ("before", "after"):
                b["decision-new"][key], b["decision-83-update"][key] = b["decision-83-update"][key], b["decision-new"][key]
            b["decision-new"]["file"], b["decision-83-update"]["file"] = "docs/DECISIONS.md", "docs/DECISIONS.md"

        assert _synthetic_digest(edit) == _synthetic_digest(lambda b: None)

    def test_a_replaced_number_changed_to_a_word_changes_the_digest(self) -> None:
        def edit(b):
            b["git-ops-version"]["before"] = "contract_version: twelve\n"

        assert _synthetic_digest(edit) != _synthetic_digest(lambda b: None)

    def test_a_replaced_number_moving_keeps_the_digest(self) -> None:
        def edit(b):
            b["git-ops-version"]["before"] = "contract_version: 13\n"

        assert _synthetic_digest(edit) == _synthetic_digest(lambda b: None)

    def test_record_prints_the_approved_digest_and_fingerprint(self, tmp_path, capsys) -> None:
        fx.write(tmp_path, approved_text.sidecar_rel(SLUG), HISTORY[OWN])
        assert approved_text.main(["record", "--slug", SLUG, "--root", str(tmp_path)]) == 0
        out = dict(line.split("=", 1) for line in capsys.readouterr().out.replace(" blocks", "\nblocks").splitlines())
        assert out["approved"] == _digest(OWN) and out["fingerprint"] == approved_words.fingerprint(_digest(OWN))
        assert out["digest"] == approved_words.digest(approved_words.parse_sidecar(HISTORY[OWN]))

    def test_the_moved_names_re_export_unchanged(self) -> None:
        for name in (
            "Sidecar",
            "Block",
            "ApprovedTextError",
            "find_all",
            "digest",
            "parse_sidecar",
            "_PLACEHOLDERS",
            "_token_matches",
        ):
            assert getattr(approved_text, name) is getattr(approved_words, name)


def _pinned_repo(tmp_path, monkeypatch, *, declared: bool = False, sidecar_blocks=None) -> tuple[Path, str]:
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    fx.write_plan(repo, SLUG, [], declared)
    fx.write(repo, "doc.md", "alpha beta gamma delta\n")
    fx.write_sidecar(repo, SLUG, sidecar_blocks or [BLOCK])
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "base")
    fx.push_main(repo)
    return repo, fx.git(repo, "rev-parse", "HEAD")


def _sidecar(blocks) -> approved_words.Sidecar:
    return approved_words.parse_sidecar(fx.sidecar_text(SLUG, blocks))


class TestGrandfather:
    def test_grandfathered_only_at_the_pinned_ref_with_the_same_digest_and_an_undeclared_plan(
        self, tmp_path, monkeypatch
    ) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        verdict = prov.grandfather(SLUG, _sidecar([BLOCK]), repo, ref)
        assert verdict is not None and verdict.kind == prov.GRANDFATHERED

    def test_a_reworded_sidecar_is_not_grandfathered(self, tmp_path, monkeypatch) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        assert prov.grandfather(SLUG, _sidecar([{**BLOCK, "after": "BETA GAMMA!"}]), repo, ref) is None

    def test_a_plan_declared_at_the_ref_is_not_grandfathered(self, tmp_path, monkeypatch) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch, declared=True)
        assert prov.grandfather(SLUG, _sidecar([BLOCK]), repo, ref) is None

    def test_a_sidecar_absent_at_the_ref_is_not_grandfathered(self, tmp_path, monkeypatch) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        assert prov.grandfather("other", _sidecar([BLOCK]), repo, ref) is None

    def test_a_ref_missing_from_the_clone_is_unverified_never_missing(self, tmp_path, monkeypatch) -> None:
        repo, _ = _pinned_repo(tmp_path, monkeypatch)
        verdict = prov.provenance(SLUG, _sidecar([BLOCK]), repo, lambda s, d: prov.Verdict(prov.MISSING), "f" * 40)
        assert verdict.kind == prov.UNVERIFIED

    def test_a_sidecar_that_is_not_grandfathered_takes_the_lookup_answer(self, tmp_path, monkeypatch) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        seen = []
        sidecar = _sidecar([{**BLOCK, "after": "BETA GAMMA!"}])
        verdict = prov.provenance(SLUG, sidecar, repo, lambda s, d: seen.append((s, d)) or prov.Verdict(prov.MISSING), ref)
        assert verdict.kind == prov.MISSING and seen == [(SLUG, approved_words.approved_digest(sidecar))]

    @pytest.mark.parametrize(
        "in_push,on_pr,expected",
        [(True, True, "skip"), (True, False, "skip"), (False, True, "enforce"), (False, False, "advisory")],
    )
    def test_mode_per_context(self, in_push, on_pr, expected) -> None:
        assert prov.mode(in_push, on_pr) == expected

    @pytest.mark.parametrize("kind", [prov.MISSING, prov.UNVERIFIED])
    @pytest.mark.parametrize("check_mode,fails", [("enforce", True), ("advisory", False), ("skip", False)])
    def test_only_enforce_fails_a_non_passing_answer(self, kind, check_mode, fails) -> None:
        assert prov.is_failure(prov.Verdict(kind), check_mode) is fails

    @pytest.mark.parametrize("kind", [prov.APPROVED, prov.GRANDFATHERED])
    def test_a_passing_answer_never_fails(self, kind) -> None:
        assert prov.is_failure(prov.Verdict(kind), "enforce") is False


class TestDeadline:
    def test_a_fresh_budget_has_time_left(self) -> None:
        deadline = prov.Deadline(30)
        assert not deadline.expired() and 0 < deadline.remaining() <= 30

    def test_a_spent_budget_is_expired(self, monkeypatch) -> None:
        clock = iter([100.0, 131.0])
        monkeypatch.setattr(prov.time, "monotonic", lambda: next(clock))
        deadline = prov.Deadline(30)
        assert deadline.expired()


class TestUnreadableRefCopies:
    def test_a_plan_missing_at_the_ref_counts_as_undeclared(self, tmp_path, monkeypatch) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        assert prov._plan_declared_at("docs/plans/PLAN-nothing.yaml", repo, ref) is False

    def test_an_unparsable_plan_at_the_ref_counts_as_undeclared(self, tmp_path, monkeypatch) -> None:
        repo, _ = _pinned_repo(tmp_path, monkeypatch)
        fx.write(repo, f"docs/plans/PLAN-{SLUG}.yaml", "a: [unclosed\n")
        fx.git(repo, "add", "-A")
        fx.git(repo, "commit", "-m", "bad plan")
        assert prov._plan_declared_at(f"docs/plans/PLAN-{SLUG}.yaml", repo, fx.git(repo, "rev-parse", "HEAD")) is False

    def test_an_unparsable_sidecar_at_the_ref_is_not_grandfathered(self, tmp_path, monkeypatch) -> None:
        repo, _ = _pinned_repo(tmp_path, monkeypatch)
        fx.write(repo, f"docs/plans/approved/{SLUG}.yaml", "blocks: [unclosed\n")
        fx.git(repo, "add", "-A")
        fx.git(repo, "commit", "-m", "bad sidecar")
        ref = fx.git(repo, "rev-parse", "HEAD")
        assert prov.grandfather(SLUG, _sidecar([BLOCK]), repo, ref) is None

    def test_the_default_ref_is_the_pinned_constant(self, tmp_path, monkeypatch) -> None:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(prov, "GRANDFATHER_REF", ref)
        verdict = prov.grandfather(SLUG, _sidecar([BLOCK]), repo)
        assert verdict is not None and verdict.kind == prov.GRANDFATHERED

    def test_a_verdict_prints_its_kind_and_detail(self) -> None:
        assert str(prov.Verdict(prov.MISSING, "why")) == "MISSING why"
        assert str(prov.Verdict(prov.APPROVED)) == "APPROVED"
