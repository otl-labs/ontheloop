"""collect_budget_episodes: the artifact-window sweep, its anti-masking guards (Decision 55) and
the total_count honesty check that says so when one page does not cover the window.

Split out of the retired single-file test_budget_ingest.py monolith (rec-3288 wave-4 fixups).
"""

from __future__ import annotations

import pytest

from scripts.convergence_health import budget_ingest as bi

from .conftest import _artifact, _budget_block, _caller_for, _fetcher_for


class TestCollectBudgetEpisodes:
    def test_no_artifacts_returns_empty_population(self) -> None:
        scan = bi.collect_budget_episodes(gh_caller=_caller_for([]), artifact_fetcher=_fetcher_for({}))
        assert scan == {
            "scanned": 0,
            "total_count": 0,
            "expired": 0,
            "without_budget": 0,
            "unreadable": 0,
            "episodes": [],
        }

    def test_dead_query_raises_rather_than_reporting_an_empty_population(self) -> None:
        """Decision 55 anti-masking: a None API result (no token / dead query) must never be
        indistinguishable from a genuinely breach-free window."""
        with pytest.raises(RuntimeError, match="Refusing to report an empty budget population"):
            bi.collect_budget_episodes(gh_caller=lambda _url: None, artifact_fetcher=_fetcher_for({}))

    def test_ignores_artifacts_with_other_names(self) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1, name="validation-result")]),
            artifact_fetcher=_fetcher_for({}),
        )
        assert scan["scanned"] == 0
        assert scan["episodes"] == []

    def test_expired_artifact_is_counted_and_never_fetched(self) -> None:
        def _explode(_url: str) -> bytes:
            raise AssertionError("expired artifacts must not be downloaded")

        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1, expired=True)]),
            artifact_fetcher=_explode,
        )
        assert scan["scanned"] == 1
        assert scan["expired"] == 1
        assert scan["episodes"] == []

    def test_pre_968_artifact_without_budget_block_is_counted_not_ingested(self) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1)]),
            artifact_fetcher=_fetcher_for({1: {"selected": []}}),
        )
        assert scan["without_budget"] == 1
        assert scan["episodes"] == []

    def test_malformed_artifact_is_counted_and_does_not_abort_the_sweep(self, capsys: pytest.CaptureFixture[str]) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1), _artifact(2)]),
            artifact_fetcher=_fetcher_for({1: b"corrupt", 2: {"budget": _budget_block()}}),
        )
        assert scan["unreadable"] == 1
        assert len(scan["episodes"]) == 1
        assert "unreadable selection-manifest artifact" in capsys.readouterr().out

    def test_a_wholly_unreadable_sweep_raises_instead_of_reporting_zero_breaches(self) -> None:
        """One bad archive is noise; EVERY downloadable archive failing is a broken download path,
        and reporting "no breaches" from it is the same dead-sensor masking the None-payload guard
        above rejects (Decision 55)."""
        with pytest.raises(RuntimeError, match=r"all 2 downloadable selection-manifest"):
            bi.collect_budget_episodes(
                gh_caller=_caller_for([_artifact(1), _artifact(2)]),
                artifact_fetcher=_fetcher_for({1: b"corrupt", 2: b"also corrupt"}),
            )

    def test_an_all_expired_sweep_is_not_treated_as_a_broken_download_path(self) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1, expired=True)]),
            artifact_fetcher=_fetcher_for({}),
        )
        assert scan["expired"] == 1
        assert scan["unreadable"] == 0

    def test_episode_carries_the_budget_identity_fields(self) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1)]),
            artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
        )
        episode = scan["episodes"][0]
        assert episode["branch"] == "claude/slow-branch"
        assert episode["dominant_phase"] == "pytest"
        assert episode["outcome"] == "breach"
        assert episode["elapsed_s"] == 420.0
        assert episode["limit_s"] == 300.0
        assert episode["run_id"] == "555"
        assert episode["repository"] == "benjamin-blake/theseus"
        assert episode["head_sha"] == "a" * 40
        assert episode["artifact_id"] == 1

    def test_unknown_identity_falls_back_to_the_artifacts_workflow_run(self) -> None:
        block = _budget_block(branch="unknown", run_id="unknown", repository="", dominant_phase=None)
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(7, head_branch="claude/from-run")]),
            artifact_fetcher=_fetcher_for({7: {"budget": block}}),
        )
        episode = scan["episodes"][0]
        assert episode["branch"] == "claude/from-run"
        assert episode["run_id"] == "9007"
        assert episode["repository"] == "unknown"
        assert episode["dominant_phase"] == "unknown"

    def test_resolves_default_callers_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no injected caller and no token, _make_github_caller yields None and the dead-query
        guard fires -- exercising the default-resolution path without any network access."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="Refusing to report an empty budget population"):
            bi.collect_budget_episodes()

    def test_a_window_larger_than_one_page_warns_loudly_that_the_sweep_is_lossy(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Correctness 4: the single-page sweep is NOT self-healing above the page size -- the next
        tick issues the identical newest-first query -- so a truncated window must say so."""
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1)], total_count=250),
            artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
            per_page=100,
        )
        assert scan["total_count"] == 250
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "250" in out and "newest 100" in out

    def test_a_window_the_page_covers_warns_about_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=_caller_for([_artifact(1)], total_count=1),
            artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
            per_page=100,
        )
        assert scan["total_count"] == 1
        assert "WARNING" not in capsys.readouterr().out

    def test_a_response_without_total_count_falls_back_to_the_scanned_count(self) -> None:
        scan = bi.collect_budget_episodes(
            gh_caller=lambda _url: {"artifacts": [_artifact(1)]},
            artifact_fetcher=_fetcher_for({1: {"budget": _budget_block()}}),
        )
        assert scan["total_count"] == scan["scanned"] == 1


def test_collect_and_ingest_query_the_ci_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "example-org/example-repo")
    captured: list[str] = []
    inner = _caller_for([_artifact(1)])

    def _capture(url: str) -> object:
        captured.append(url)
        return inner(url)

    archives = _fetcher_for({1: {"budget": _budget_block()}})
    bi.collect_budget_episodes(gh_caller=_capture, artifact_fetcher=archives)
    bi.ingest_budget_breaches(gh_caller=_capture, artifact_fetcher=archives, open_recs=[], resolved_recs=[], dry_run=True)
    assert len(captured) == 2
    assert all("repos/example-org/example-repo/" in url for url in captured)
