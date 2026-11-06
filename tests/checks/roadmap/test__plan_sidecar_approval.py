"""The plan-PR approval leg and its seam (rec-4769): fixture repos only; the GitHub lookup is the one patched edge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.checks.roadmap import _plan_sidecar_approval as module
from scripts.roadmap import approved_text_github, approved_words, review_page
from scripts.roadmap import approved_text_provenance as prov
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
BLOCK = {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}
EDITED = {**BLOCK, "after": "BETA GAMMA!"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("APPROVED_TEXT_GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "fixture-owner/fixture-repo")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """main holds the sidecar (the grandfather pin); the working tree then edits it, as a plan PR does."""
    fx.clear_ci_env(monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    root = fx.make_repo(tmp_path)
    fx.write_plan(root, SLUG, [], False)
    fx.write(root, "doc.md", "alpha beta gamma delta\n")
    fx.write_sidecar(root, SLUG, [BLOCK])
    fx.git(root, "add", "-A")
    fx.git(root, "commit", "-m", "base")
    fx.push_main(root)
    monkeypatch.setattr(prov, "GRANDFATHER_REF", fx.git(root, "rev-parse", "HEAD"))
    return root


def _lookup(monkeypatch, verdict: prov.Verdict) -> list[str]:
    seen: list[str] = []

    def fake(slug, approved, root, deadline, repo=None):
        seen.append(approved)
        return verdict

    monkeypatch.setattr(approved_text_github, "lookup", fake)
    return seen


def _digest(block) -> str:
    return approved_words.approved_digest(approved_words.parse_sidecar(fx.sidecar_text(SLUG, [block])))


def _findings(repo, *, changed="digest", in_push=False):
    return module.approval_findings("PLAN-demo.yaml", SLUG, repo, lambda: changed, in_push, prov.Deadline(30))


class TestSeam:
    def test_the_working_tree_copy_is_the_one_looked_up(self, repo, monkeypatch) -> None:
        seen = _lookup(monkeypatch, prov.Verdict(prov.APPROVED, "run 1"))
        fx.write_sidecar(repo, SLUG, [EDITED])
        verdict, mode, info = module.sidecar_provenance(SLUG, repo, False, prov.Deadline(30))
        assert (verdict.kind, mode, info) == (prov.APPROVED, "enforce", None) and seen == [_digest(EDITED)]

    def test_an_unchanged_sidecar_is_grandfathered_without_an_informational_line(self, repo, monkeypatch) -> None:
        _lookup(monkeypatch, prov.Verdict(prov.MISSING))
        verdict, mode, info = module.sidecar_provenance(SLUG, repo, False, prov.Deadline(30))
        assert (verdict.kind, mode, info) == (prov.GRANDFATHERED, "enforce", None)

    def test_a_push_skips_the_lookup(self, repo, monkeypatch) -> None:
        seen = _lookup(monkeypatch, prov.Verdict(prov.APPROVED))
        assert module.sidecar_provenance(SLUG, repo, True, prov.Deadline(30)) == (None, "skip", None) and seen == []


class TestHeadSha:
    def test_the_event_payload_head_wins(self, repo, tmp_path, monkeypatch) -> None:
        payload = tmp_path / "event.json"
        payload.write_text(json.dumps({"pull_request": {"head": {"sha": "9" * 40}}}), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
        assert module._head_sha(repo) == "9" * 40

    @pytest.mark.parametrize("body", ["not json", '{"pull_request": {}}', '{"pull_request": {"head": {"sha": ""}}}'])
    def test_an_unusable_payload_falls_back_to_git(self, repo, tmp_path, monkeypatch, body) -> None:
        payload = tmp_path / "event.json"
        payload.write_text(body, encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
        assert module._head_sha(repo) == fx.git(repo, "rev-parse", "HEAD")

    def test_a_missing_payload_file_falls_back_to_git(self, repo, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "absent.json"))
        assert module._head_sha(repo) == fx.git(repo, "rev-parse", "HEAD")

    def test_outside_a_repository_it_is_the_literal_head(self, tmp_path) -> None:
        assert module._head_sha(tmp_path) == "HEAD"


def test_the_remedy_names_the_dispatch_inputs(repo) -> None:
    fx.write_sidecar(repo, SLUG, [EDITED])
    text = module._remedy(SLUG, repo)
    approved = _digest(EDITED)
    assert f"slug={SLUG}" in text and f"digest={approved}" in text and f"sha={fx.git(repo, 'rev-parse', 'HEAD')}" in text
    assert f"fingerprint={review_page.fingerprint(approved)}" in text and "approved-text-approval.yml" in text


class TestApprovalFindings:
    def test_a_diff_that_leaves_the_sidecar_alone_has_nothing_to_approve(self, repo, monkeypatch) -> None:
        seen = _lookup(monkeypatch, prov.Verdict(prov.MISSING))
        assert _findings(repo, changed=None) == ([], []) and seen == []

    def test_an_unreadable_sidecar_stays_the_wording_checks_gap(self, repo, monkeypatch) -> None:
        _lookup(monkeypatch, prov.Verdict(prov.MISSING))
        fx.write(repo, f"docs/plans/approved/{SLUG}.yaml", "schema_version: 9\n")
        assert _findings(repo) == ([], [])

    def test_a_push_prints_a_skip_line(self, repo, monkeypatch, capsys) -> None:
        _lookup(monkeypatch, prov.Verdict(prov.MISSING))
        assert _findings(repo, in_push=True) == ([], [])
        assert "SKIP: approval lookup (on main)" in capsys.readouterr().out

    @pytest.mark.parametrize("kind", [prov.APPROVED, prov.GRANDFATHERED])
    def test_a_passing_answer_has_no_findings(self, repo, monkeypatch, kind) -> None:
        _lookup(monkeypatch, prov.Verdict(kind))
        fx.write_sidecar(repo, SLUG, [EDITED])
        if kind == prov.GRANDFATHERED:
            fx.write_sidecar(repo, SLUG, [BLOCK])
        assert _findings(repo) == ([], [])

    @pytest.mark.parametrize("kind", [prov.MISSING, prov.UNVERIFIED])
    def test_enforce_fails_with_the_remedy(self, repo, monkeypatch, kind) -> None:
        _lookup(monkeypatch, prov.Verdict(kind, "why"))
        fx.write_sidecar(repo, SLUG, [EDITED])
        failures, advisories = _findings(repo)
        assert advisories == [] and len(failures) == 1
        assert f"PLAN-demo.yaml: docs/plans/approved/{SLUG}.yaml approval {kind} (why)" in failures[0]
        assert "approved-text-approval.yml" in failures[0]

    @pytest.mark.parametrize("kind", [prov.MISSING, prov.UNVERIFIED])
    def test_advisory_mode_warns_only(self, repo, monkeypatch, kind) -> None:
        _lookup(monkeypatch, prov.Verdict(kind, "why"))
        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
        fx.write_sidecar(repo, SLUG, [EDITED])
        failures, advisories = _findings(repo)
        assert failures == [] and advisories[0].startswith(f"{kind} (advisory): PLAN-demo.yaml")
