"""rec-4769 acceptance: the approved-words digest on real sidecar history, the provenance core, the plan-PR leg and
the two real seams (critique r5 B1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.roadmap import validate_plan_documents as plan_documents
from scripts.checks.verification import validate_merge_step as merge_step
from scripts.roadmap import approved_text, approved_text_github, approved_words
from scripts.roadmap import approved_text_provenance as prov
from tests import test_approved_text_github as api
from tests.checks.roadmap import test_validate_plan_documents as plan_fx
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


class TestSidecarApprovalLeg:
    """validate_plan_documents' leg: every node patches the push context so a main checkout reads like a session."""

    _DIGEST = "d" * 64

    def _run(self, tmp_path, capsys, monkeypatch, triple, *, event="pull_request", push=None):
        module = plan_documents
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        monkeypatch.setattr(module._common, "push_context_base", lambda root=None: push)
        monkeypatch.setattr(module._common, "status_diff_base", lambda root=None: "basesha")
        monkeypatch.setattr(module, "_changed_sidecar_digest", lambda slug, base: self._DIGEST)
        monkeypatch.setattr(module, "_has_sidecar", lambda slug: True)
        calls: list[Any] = []
        monkeypatch.setattr(module, "_sidecar_provenance", lambda *a: calls.append(a) or triple)
        monkeypatch.setattr(approved_text_github, "remedy", lambda slug, sidecar, root: f"dispatch for {slug} at {'e' * 40}")
        monkeypatch.setattr(approved_text, "load_sidecar", lambda slug, root: object())
        data = plan_fx._plan_with_linked_obligation()
        data["schema_version"] = 5
        data["handoff_policy"] = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}
        row = {
            "gate": "wording-check",
            "round": 1,
            "verdict": "PASS",
            "digest": self._DIGEST,
            "base": "e" * 40,
            "selection": "complete",
        }
        data["gate_rounds"] = [*plan_fx._CRITIQUED, row]
        failed, out = plan_fx._validate(tmp_path, data, capsys, added=set())
        return failed, out, calls

    def _verdict(self, kind, mode="enforce"):
        return (prov.Verdict(kind, "why"), mode, None)

    @pytest.mark.parametrize("kind", [prov.MISSING, prov.UNVERIFIED])
    def test_a_changed_sidecar_without_approval_fails_on_the_plan_pr(self, tmp_path, capsys, monkeypatch, kind) -> None:
        failed, out, _ = self._run(tmp_path, capsys, monkeypatch, self._verdict(kind))
        assert failed == ["Plan document schema validation"] and f"approval {kind} (why)" in out and f"at {'e' * 40}" in out

    @pytest.mark.parametrize("kind", [prov.APPROVED, prov.GRANDFATHERED])
    def test_approved_and_grandfathered_pass(self, tmp_path, capsys, monkeypatch, kind) -> None:
        failed, out, calls = self._run(tmp_path, capsys, monkeypatch, self._verdict(kind))
        assert failed == [] and "PASS:" in out and len(calls) == 1

    @pytest.mark.parametrize("kind", [prov.MISSING, prov.UNVERIFIED])
    def test_an_advisory_answer_warns_without_a_pass_line(self, tmp_path, capsys, monkeypatch, kind) -> None:
        failed, out, _ = self._run(tmp_path, capsys, monkeypatch, self._verdict(kind, "advisory"), event="workflow_dispatch")
        assert failed == [] and f"WARN: {kind} (advisory)" in out and "PASS:" not in out

    def test_a_patched_push_context_skips_the_lookup_with_examined_kept(self, tmp_path, capsys, monkeypatch) -> None:
        from scripts.checks import registry

        declared: list[Any] = []
        monkeypatch.setattr(registry, "examined", lambda n, unit="": declared.append((n, unit)))
        failed, out, _ = self._run(tmp_path, capsys, monkeypatch, (None, "skip", None), push="abc123")
        assert failed == [] and "SKIP: approval lookup (on main)" in out and declared == [(1, "plan_documents")]
        assert "PASS:" in out


class TestSeams:
    """Both real `_sidecar_provenance` seams in a tmp git repository; only http_get and GRANDFATHER_REF are patched."""

    def _setup(self, tmp_path, monkeypatch, runs_for) -> tuple[Path, api.Api, str]:
        repo, ref = _pinned_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(prov, "GRANDFATHER_REF", ref)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        double = api.Api([api.approval_run(SLUG, d, ref) for d in runs_for])
        monkeypatch.setattr(approved_text_github, "http_get", double)
        return repo, double, ref

    def test_plan_documents_seam_evaluates_the_head_copy(self, tmp_path, monkeypatch) -> None:
        old = approved_words.approved_digest(_sidecar([BLOCK]))
        repo, _, _ = self._setup(tmp_path, monkeypatch, [old])
        fx.write_sidecar(repo, SLUG, [{**BLOCK, "after": "BETA GAMMA!"}])
        verdict, mode, info = plan_documents._sidecar_provenance(SLUG, repo, False, prov.Deadline(30))
        assert (verdict.kind, mode, info) == (prov.MISSING, "enforce", None)
        fx.write_sidecar(repo, SLUG, [BLOCK])
        verdict, mode, info = plan_documents._sidecar_provenance(SLUG, repo, False, prov.Deadline(30))
        assert (verdict.kind, mode, info) == (prov.GRANDFATHERED, "enforce", None)

    def test_merge_step_seam_skips_on_a_push_and_never_calls_the_api(self, tmp_path, monkeypatch) -> None:
        repo, double, _ = self._setup(tmp_path, monkeypatch, [])
        triple = merge_step._sidecar_provenance(SLUG, _sidecar([BLOCK]), repo, True, prov.Deadline(30))
        assert triple == (None, "skip", None) and double.calls == []

    def test_merge_step_seam_reads_approved_for_the_base_sidecar_digest(self, tmp_path, monkeypatch) -> None:
        sidecar = _sidecar([{**BLOCK, "after": "BETA GAMMA!"}])
        repo, _, _ = self._setup(tmp_path, monkeypatch, [approved_words.approved_digest(sidecar)])
        verdict, mode, info = merge_step._sidecar_provenance(SLUG, sidecar, repo, False, prov.Deadline(30))
        assert (verdict.kind, mode, info) == (prov.APPROVED, "enforce", None)

    def test_merge_step_seam_returns_the_informational_line_for_a_grandfathered_sidecar(self, tmp_path, monkeypatch) -> None:
        repo, double, _ = self._setup(tmp_path, monkeypatch, [])
        verdict, mode, info = merge_step._sidecar_provenance(SLUG, _sidecar([BLOCK]), repo, False, prov.Deadline(30))
        assert (verdict.kind, mode) == (prov.GRANDFATHERED, "enforce")
        assert info == "lookup (informational): MISSING auth=anonymous" and len(double.run_list_calls()) == 1

    def test_the_informational_line_carries_the_token_path(self, tmp_path, monkeypatch) -> None:
        repo, _, _ = self._setup(tmp_path, monkeypatch, [])
        monkeypatch.setenv("APPROVED_TEXT_GH_TOKEN", "t")
        _, _, info = merge_step._sidecar_provenance(SLUG, _sidecar([BLOCK]), repo, False, prov.Deadline(30))
        assert info == "lookup (informational): MISSING auth=token"

    def test_the_remedy_names_the_event_payload_head_sha(self, tmp_path, monkeypatch) -> None:
        repo, _, _ = self._setup(tmp_path, monkeypatch, [])
        payload = tmp_path / "event.json"
        payload.write_text(json.dumps({"pull_request": {"head": {"sha": "9" * 40}}}), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
        text = approved_text_github.remedy(SLUG, _sidecar([BLOCK]), repo)
        assert f"sha={'9' * 40}" in text and f"slug={SLUG}" in text and "approved-text-approval.yml" in text
