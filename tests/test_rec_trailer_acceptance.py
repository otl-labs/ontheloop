"""Mirror home for scripts/rec_trailer_acceptance.py (Decision 201, slices A and B).

Covers the bucket x probe-verdict map, the census/rejoin/junit-routing grammar, and every CLI
stage (census/evaluate/junit-verdict/close), git/artifact seams, build_parser, and main.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.backlog_health import census as census_mod
from scripts.backlog_health import probe as probe_mod
from scripts.checks.verification._vp_replay_classify import OUTCOME_CLASSES
from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.ops_portal.closure_gate import ACCEPTANCE_VERDICTS, FAILS, HOLDS, OUT_OF_GRAMMAR, UNMEASURABLE
from scripts.rec_trailer_acceptance import (
    _CENSUS_ARTIFACT,
    _VERDICT_ARTIFACT,
    _current_commit_message,
    _current_sha,
    _default_artifact_dir,
    _make_reader,
    _read_json,
    _write_json,
    build_parser,
    census,
    cmd_census,
    cmd_close,
    cmd_evaluate,
    cmd_junit_verdict,
    evaluate,
    main,
    rejoin,
    verdict_for,
)
from scripts.rec_trailer_acceptance import (
    ROOT as RTA_ROOT,
)
from scripts.rec_trailer_acceptance_junit import ROUTE_JUNIT, ROUTE_REFUSE, ROUTE_STATIC

_REAL_NODE = "tests/test_rec_trailer_acceptance.py::TestRejoin::test_matching_record_survives"


def _run_cmd_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    junit_verdict: Path | None = None,
    live_rows: dict[str, dict] | None = None,
    reader_error: BaseException | None = None,
    close_return: int = 0,
) -> tuple[int, MagicMock]:
    """Shared cmd_close invocation: env vars, a fake rec_by_id reader, patched close_recs_from_trailer."""
    args = argparse.Namespace(artifact_dir=tmp_path, junit_verdict=junit_verdict)
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    reader = MagicMock()
    reader.named.side_effect = reader_error or (
        lambda verb, **kw: [live_rows[kw["id"]]] if kw["id"] in (live_rows or {}) else []
    )
    with (
        patch("scripts.rec_trailer_acceptance._make_reader", return_value=reader),
        patch("scripts.ops_portal.ci_rca_lifecycle.close_recs_from_trailer", return_value=close_return) as mock_close,
    ):
        rc = cmd_close(args)
    return rc, mock_close


class TestVerdictMapping:
    def test_mapping_is_total(self) -> None:
        """Every (bucket, probe_verdict) pair drawn from the upstream vocabularies at runtime
        resolves to a member of ACCEPTANCE_VERDICTS -- never raises, never resolves to None."""
        for bucket, probe_verdict in itertools.product(census_mod.BUCKETS, (*probe_mod.VERDICTS, None)):
            verdict = verdict_for(bucket, probe_verdict)
            assert verdict in ACCEPTANCE_VERDICTS

    def test_probe_vocabulary_parity(self) -> None:
        """The domains iterated above are the REAL upstream vocabularies, derived at runtime --
        never a hardcoded, potentially-stale copy."""
        assert census_mod.BUCKETS == (
            census_mod.PROBEABLE,
            census_mod.PROSE_ONLY,
            census_mod.UNPROBEABLE_UNSAFE,
            census_mod.UNPROBEABLE_SHAPE,
            census_mod.EXPECTED_FAIL_MISSING_NODE,
        )
        assert probe_mod.VERDICTS == frozenset(
            {probe_mod.PASS, probe_mod.FAIL, probe_mod.TIMEOUT, probe_mod.BUDGET_EXHAUSTED, probe_mod.ISOLATION_UNAVAILABLE}
        )

    def test_axis_parity_with_vp_red_before(self) -> None:
        """Decision 189 axis discipline: the intersection of ACCEPTANCE_VERDICTS with
        vp-red-before.yaml's OUTCOME_CLASSES is exactly the single genuinely shared arm,
        "unmeasurable" -- asserted, not merely stated in prose."""
        assert ACCEPTANCE_VERDICTS & set(OUTCOME_CLASSES) == {UNMEASURABLE}

    def test_probeable_pass_holds(self) -> None:
        assert verdict_for(census_mod.PROBEABLE, probe_mod.PASS) == HOLDS

    def test_probeable_fail_fails(self) -> None:
        assert verdict_for(census_mod.PROBEABLE, probe_mod.FAIL) == FAILS

    def test_probeable_timeout_and_budget_and_isolation_are_unmeasurable(self) -> None:
        assert verdict_for(census_mod.PROBEABLE, probe_mod.TIMEOUT) == UNMEASURABLE
        assert verdict_for(census_mod.PROBEABLE, probe_mod.BUDGET_EXHAUSTED) == UNMEASURABLE
        assert verdict_for(census_mod.PROBEABLE, probe_mod.ISOLATION_UNAVAILABLE) == UNMEASURABLE

    def test_non_probed_buckets_never_consult_probe_verdict(self) -> None:
        for bucket in (census_mod.PROSE_ONLY, census_mod.UNPROBEABLE_UNSAFE, census_mod.UNPROBEABLE_SHAPE):
            assert verdict_for(bucket, None) == OUT_OF_GRAMMAR
            assert verdict_for(bucket, probe_mod.PASS) == OUT_OF_GRAMMAR

    def test_expected_fail_missing_node_is_fails(self) -> None:
        assert verdict_for(census_mod.EXPECTED_FAIL_MISSING_NODE, None) == FAILS


class TestCensus:
    def test_undecidable_acceptance_is_out_of_grammar(self) -> None:
        """The census stage EXECUTES the require_decidable ratchet: a trailer-named rec whose
        acceptance carries no decidable assertion is classified out_of_grammar (via
        unprobeable_shape) and never probed."""
        rows = [{"id": "rec-1", "acceptance": "echo done"}]
        entries = census(["rec-1"], rows=rows)
        assert len(entries) == 1
        assert entries[0]["bucket"] == census_mod.UNPROBEABLE_SHAPE

    def test_decidable_probeable_acceptance_stays_probeable(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "grep -q foo bar.py"}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["bucket"] == census_mod.PROBEABLE

    def test_prose_only_untouched_by_the_ratchet(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "Manual review"}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["bucket"] == census_mod.PROSE_ONLY

    def test_absent_row_omitted_from_entries(self) -> None:
        assert census(["rec-missing"], rows=[]) == []


class TestEvaluate:
    def test_isolation_unavailable_resolves_every_entry_to_unmeasurable(self, tmp_path) -> None:
        """evaluate() delegates isolation_available entirely to probe.run_all's own returned
        artifact rather than deciding it itself -- an ISOLATION_UNAVAILABLE run resolves to
        `unmeasurable` for every probeable entry, never a silent pass."""
        entries = [
            {
                "rec_id": "rec-1",
                "acceptance": "grep -q foo bar.py",
                "acceptance_sha256": "abc",
                "bucket": census_mod.PROBEABLE,
            },
            {"rec_id": "rec-2", "acceptance": "Manual review", "acceptance_sha256": "def", "bucket": census_mod.PROSE_ONLY},
        ]
        unavailable_result = {
            "main_sha": "deadbeef",
            "isolation_available": False,
            "verdicts": {"rec-1": probe_mod.ISOLATION_UNAVAILABLE},
        }
        with patch("scripts.rec_trailer_acceptance.probe_mod.run_all", return_value=unavailable_result):
            doc = evaluate(entries, tmp_path, "deadbeef")
        assert doc["isolation_available"] is False
        by_id = {r["rec_id"]: r for r in doc["records"]}
        assert by_id["rec-1"]["verdict"] == UNMEASURABLE
        assert by_id["rec-2"]["verdict"] == OUT_OF_GRAMMAR

    def test_source_is_static(self, tmp_path) -> None:
        doc = evaluate([], tmp_path, "deadbeef")
        assert doc["source"] == "static"


class TestRejoin:
    _CENSUS_DOC = {"entries": [{"rec_id": "rec-1", "acceptance_sha256": "abc123"}]}

    def test_matching_record_survives(self) -> None:
        record = {"rec_id": "rec-1", "acceptance_sha256": "abc123", "verdict": HOLDS}
        verdict_doc = {"records": [record]}
        assert rejoin(self._CENSUS_DOC, verdict_doc) == {"rec-1": record}

    def test_unknown_rec_id_dropped(self) -> None:
        verdict_doc = {"records": [{"rec_id": "rec-999", "acceptance_sha256": "abc123", "verdict": HOLDS}]}
        assert rejoin(self._CENSUS_DOC, verdict_doc) == {}

    def test_changed_acceptance_sha256_dropped(self) -> None:
        verdict_doc = {"records": [{"rec_id": "rec-1", "acceptance_sha256": "stale", "verdict": HOLDS}]}
        assert rejoin(self._CENSUS_DOC, verdict_doc) == {}

    def test_malformed_verdict_dropped(self) -> None:
        verdict_doc = {"records": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "verdict": "not-a-real-verdict"}]}
        assert rejoin(self._CENSUS_DOC, verdict_doc) == {}

    def test_non_dict_record_dropped(self) -> None:
        verdict_doc = {"records": ["not-a-dict"]}
        assert rejoin(self._CENSUS_DOC, verdict_doc) == {}

    def test_absent_records_key_yields_empty(self) -> None:
        assert rejoin(self._CENSUS_DOC, {}) == {}


class TestJunitRouting:
    def test_partition_is_static_with_report_absent(self) -> None:
        """The route is computed from the acceptance TEXT ALONE -- census() never reads a junit
        report, resolves anything on disk, or executes anything, so the routing is provably
        static (it is identical whether or not a junit report exists anywhere)."""
        rows = [{"id": "rec-1", "acceptance": "pytest " + _REAL_NODE}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["route"] == ROUTE_JUNIT

    def test_pure_single_segment_node_id_routes_to_junit_and_leaves_probe_payload(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "pytest " + _REAL_NODE}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["bucket"] == census_mod.PROBEABLE
        assert entries[0]["route"] == ROUTE_JUNIT

        empty_probe_result = {"main_sha": "deadbeef", "isolation_available": True, "verdicts": {}}
        with patch("scripts.rec_trailer_acceptance.probe_mod.run_all", return_value=empty_probe_result) as mock_run_all:
            doc = evaluate(entries, Path("."), "deadbeef")
        mock_run_all.assert_called_once_with([], repo_root=Path("."), main_sha="deadbeef")
        assert doc["records"] == []

    def test_mixed_and_chain_stays_on_probe_path_unchanged(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "grep -q X tests/f.py && pytest " + _REAL_NODE}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["bucket"] == census_mod.PROBEABLE
        assert entries[0]["route"] == ROUTE_STATIC

        probe_result = {"main_sha": "deadbeef", "isolation_available": True, "verdicts": {"rec-1": probe_mod.PASS}}
        with patch("scripts.rec_trailer_acceptance.probe_mod.run_all", return_value=probe_result):
            doc = evaluate(entries, Path("."), "deadbeef")
        assert [r["rec_id"] for r in doc["records"]] == ["rec-1"]
        assert doc["records"][0]["verdict"] == HOLDS

    def test_negated_chain_stays_static(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "! pytest " + _REAL_NODE}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["route"] == ROUTE_STATIC

    def test_piped_chain_stays_static(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "pytest " + _REAL_NODE + " | tee out.log"}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["route"] == ROUTE_STATIC

    def test_long_node_id_list_routes_to_junit_not_out_of_grammar(self) -> None:
        nodes = " ".join([_REAL_NODE] * 7)
        assert len(nodes) > census_mod._MAX_SHAPE_LEN
        long_junit = {"id": "a", "acceptance": f"bin/venv-python -m pytest {nodes} -q"}
        long_chain = {"id": "b", "acceptance": f"true && pytest {nodes}"}
        on_route, off_route = census(["a", "b"], rows=[long_junit, long_chain])
        assert (on_route["bucket"], on_route["route"]) == (census_mod.PROBEABLE, ROUTE_JUNIT)
        assert (off_route["bucket"], off_route["route"]) == (census_mod.UNPROBEABLE_SHAPE, ROUTE_STATIC)

    def test_bare_pytest_path_stays_static(self) -> None:
        rows = [{"id": "rec-1", "acceptance": "pytest tests/x.py"}]
        entries = census(["rec-1"], rows=rows)
        assert entries[0]["route"] == ROUTE_STATIC

    def test_unparseable_pytest_command_is_refused_and_reaches_neither_route(self) -> None:
        """In this tree, slice A's own require_decidable lint already fails-closed on a
        shlex-unparseable command (its own segmentation raises, so no segment can be decidable),
        rerouting it to unprobeable_shape before this plan's grammar routing would ever run --
        the lint-wins precedence in action. This test isolates the ROUTING partition's OWN
        fail-closed behaviour independent of that coupling (defense in depth: the routing must
        never admit an unparseable command to junit even if lint's own gate ever changed), by
        forcing the lint check to pass so census() reaches the grammar classifier directly."""
        cmd = "pytest " + _REAL_NODE + " " + chr(92)
        with patch("scripts.rec_trailer_acceptance.lint_acceptance_command", return_value=(True, None)):
            rows = [{"id": "rec-1", "acceptance": cmd}]
            entries = census(["rec-1"], rows=rows)
        assert entries[0]["bucket"] == census_mod.UNPROBEABLE_SHAPE
        assert entries[0]["route"] == ROUTE_REFUSE
        assert verdict_for(entries[0]["bucket"], None) == OUT_OF_GRAMMAR

        with patch("scripts.rec_trailer_acceptance.probe_mod.run_all") as mock_run_all:
            mock_run_all.return_value = {"main_sha": "deadbeef", "isolation_available": True, "verdicts": {}}
            evaluate(entries, Path("."), "deadbeef")
        mock_run_all.assert_called_once_with([], repo_root=Path("."), main_sha="deadbeef")

        # Confirmed independently: the real (unpatched) lint also refuses this shape on its own
        # decidable check, via a different bucket path -- the two fail-closed mechanisms agree.
        real_lint_ok, _ = lint_acceptance_command(cmd, require_decidable=True)
        assert real_lint_ok is False

    def test_lint_refusal_wins_over_junit_routing(self) -> None:
        """A require_decidable lint refusal reroutes bucket to unprobeable_shape BEFORE grammar
        routing ever runs, so a command that would otherwise be junit-eligible is instead
        out_of_grammar via slice A's own refusal path -- precedence stated in
        docs/contracts/git-ops.yaml#trailer_acceptance_gate."""
        with patch("scripts.rec_trailer_acceptance.lint_acceptance_command", return_value=(False, "refused")):
            rows = [{"id": "rec-1", "acceptance": "pytest " + _REAL_NODE}]
            entries = census(["rec-1"], rows=rows)
        assert entries[0]["bucket"] == census_mod.UNPROBEABLE_SHAPE
        assert entries[0]["route"] == ROUTE_STATIC

    def test_rejoin_refuses_a_rec_id_present_in_both_verdict_documents(self) -> None:
        census_doc = {"entries": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "route": ROUTE_JUNIT}]}
        static_doc = {"records": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "verdict": HOLDS}]}
        junit_doc = {"records": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "verdict": HOLDS}]}
        with pytest.raises(RuntimeError):
            rejoin(census_doc, static_doc, junit_doc)

    def test_rejoin_merges_disjoint_sources(self) -> None:
        census_doc = {
            "entries": [
                {"rec_id": "rec-1", "acceptance_sha256": "abc123", "route": ROUTE_STATIC},
                {"rec_id": "rec-2", "acceptance_sha256": "def456", "route": ROUTE_JUNIT},
            ]
        }
        static_doc = {"records": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "verdict": HOLDS}]}
        junit_doc = {"records": [{"rec_id": "rec-2", "acceptance_sha256": "def456", "verdict": FAILS}]}
        merged = rejoin(census_doc, static_doc, junit_doc)
        assert merged["rec-1"]["verdict"] == HOLDS
        assert merged["rec-2"]["verdict"] == FAILS

    def test_rejoin_synthesizes_report_unavailable_when_junit_doc_is_none(self) -> None:
        """A junit_doc of None (the artifact was never produced) is distinct from a present but
        empty document -- every junit-routed census entry gets an explicit unmeasurable record,
        never silence."""
        census_doc = {"entries": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "route": ROUTE_JUNIT}]}
        static_doc = {"records": []}
        merged = rejoin(census_doc, static_doc, None)
        assert merged["rec-1"]["verdict"] == UNMEASURABLE
        assert merged["rec-1"]["arm"] == "report_unavailable"

    def test_rejoin_empty_but_present_junit_doc_synthesizes_nothing(self) -> None:
        """A present-but-empty junit document is a legitimate result (the junit-verdict stage ran
        and genuinely found no records) -- it must not trigger the None-only synthetic fallback."""
        census_doc = {"entries": [{"rec_id": "rec-1", "acceptance_sha256": "abc123", "route": ROUTE_JUNIT}]}
        static_doc = {"records": []}
        merged = rejoin(census_doc, static_doc, {"records": []})
        assert merged == {}


class TestCmdJunitVerdict:
    """cmd_junit_verdict is a thin adapter -- delegates to rec_trailer_acceptance_junit.main with
    the artifact-dir and junit-report args forwarded verbatim."""

    def test_forwards_artifact_dir_only_when_junit_report_is_none(self, tmp_path: Path) -> None:
        with patch("scripts.rec_trailer_acceptance_junit.main", return_value=0) as mock_main:
            rc = cmd_junit_verdict(argparse.Namespace(artifact_dir=tmp_path, junit_report=None))
        assert rc == 0
        mock_main.assert_called_once_with(["--artifact-dir", str(tmp_path)])

    def test_forwards_junit_report_when_given(self, tmp_path: Path) -> None:
        report = tmp_path / "pytest-junit.xml"
        with patch("scripts.rec_trailer_acceptance_junit.main", return_value=1) as mock_main:
            rc = cmd_junit_verdict(argparse.Namespace(artifact_dir=tmp_path, junit_report=report))
        assert rc == 1
        mock_main.assert_called_once_with(["--artifact-dir", str(tmp_path), "--junit-report", str(report)])


class TestSeamsAndArtifacts:
    """_make_reader, census()'s live-reader path, git subprocess helpers, and artifact I/O -- what TestCliStages builds on."""

    def test_make_reader_forwards_profile(self) -> None:
        sentinel = object()
        with patch("src.common.ducklake_reader_client.make_reader", return_value=sentinel) as mock_make:
            result = _make_reader(profile="agent_platform")
        mock_make.assert_called_once_with(profile="agent_platform")
        assert result is sentinel

    def test_census_live_reader_classifies_found_row_and_omits_missing_id(self) -> None:
        class FakeReader:
            def named(self, name: str, **kwargs: object) -> list[dict[str, object]]:
                assert name == "rec_by_id"
                if kwargs["id"] == "rec-1":
                    return [{"id": "rec-1", "acceptance": "grep -q foo bar.py"}]
                return []

        entries = census(["rec-1", "rec-missing"], reader=FakeReader())
        assert len(entries) == 1
        assert entries[0]["rec_id"] == "rec-1"
        assert entries[0]["bucket"] == census_mod.PROBEABLE

    def test_current_sha_success(self, tmp_path: Path) -> None:
        with patch("scripts.rec_trailer_acceptance.subprocess.run", return_value=MagicMock(returncode=0, stdout="abc123\n")):
            assert _current_sha(tmp_path) == "abc123"

    def test_current_sha_nonzero_exit_raises_naming_the_command(self, tmp_path: Path) -> None:
        result = MagicMock(returncode=128, stderr="fatal: not a git repository")
        with (
            patch("scripts.rec_trailer_acceptance.subprocess.run", return_value=result),
            pytest.raises(RuntimeError, match="git rev-parse HEAD"),
        ):
            _current_sha(tmp_path)

    def test_current_commit_message_success(self, tmp_path: Path) -> None:
        result = MagicMock(returncode=0, stdout="feat: x\n\nResolves: rec-1\n")
        with patch("scripts.rec_trailer_acceptance.subprocess.run", return_value=result):
            assert _current_commit_message(tmp_path) == "feat: x\n\nResolves: rec-1\n"

    def test_current_commit_message_nonzero_exit_raises_naming_the_command(self, tmp_path: Path) -> None:
        result = MagicMock(returncode=128, stderr="fatal: bad revision 'HEAD'")
        with (
            patch("scripts.rec_trailer_acceptance.subprocess.run", return_value=result),
            pytest.raises(RuntimeError, match="git log -1"),
        ):
            _current_commit_message(tmp_path)

    def test_default_artifact_dir(self) -> None:
        with patch("scripts.rec_trailer_acceptance.tempfile.gettempdir", return_value="/tmp/fake"):
            assert _default_artifact_dir() == Path("/tmp/fake") / "trailer_acceptance"

    def test_write_read_json_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "artifact.json"
        _write_json(path, {"a": 1})
        assert _read_json(path) == {"a": 1}

    def test_read_json_missing_artifact_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="expected artifact not found"):
            _read_json(tmp_path / "missing.json")


class TestCliStages:
    """cmd_census/cmd_evaluate/cmd_close/build_parser/main -- all portal egress stubbed (see _stub_ops_data_portal)."""

    def test_cmd_census_no_trailer_is_noop(self, tmp_path: Path) -> None:
        args = argparse.Namespace(artifact_dir=tmp_path)
        with patch("scripts.rec_trailer_acceptance._current_commit_message", return_value="feat: nothing here"):
            rc = cmd_census(args)
        assert rc == 0
        assert json.loads((tmp_path / _CENSUS_ARTIFACT).read_text(encoding="utf-8")) == {"ids": [], "entries": []}

    def test_cmd_census_with_trailer_writes_entries(self, tmp_path: Path) -> None:
        args = argparse.Namespace(artifact_dir=tmp_path)
        fake_entries = [
            {"rec_id": "rec-1", "acceptance": "grep -q x y", "acceptance_sha256": "abc", "bucket": census_mod.PROBEABLE}
        ]
        with (
            patch("scripts.rec_trailer_acceptance._current_commit_message", return_value="feat: x\n\nResolves: rec-1"),
            patch("scripts.rec_trailer_acceptance.census", return_value=fake_entries) as mock_census,
        ):
            rc = cmd_census(args)
        assert rc == 0
        mock_census.assert_called_once_with(["rec-1"], repo_root=RTA_ROOT)
        assert json.loads((tmp_path / _CENSUS_ARTIFACT).read_text(encoding="utf-8")) == {
            "ids": ["rec-1"],
            "entries": fake_entries,
        }

    @pytest.mark.parametrize("use_payload", [True, False])
    def test_cmd_evaluate_reads_from_payload_or_artifact_dir(self, tmp_path: Path, use_payload: bool) -> None:
        if use_payload:
            payload_path = tmp_path / "payload.json"
            _write_json(payload_path, {"entries": [{"rec_id": "rec-1"}]})
            args = argparse.Namespace(artifact_dir=tmp_path, payload=payload_path)
        else:
            _write_json(tmp_path / _CENSUS_ARTIFACT, {"ids": ["rec-1"], "entries": [{"rec_id": "rec-1"}]})
            args = argparse.Namespace(artifact_dir=tmp_path, payload=None)
        fake_result = {"sha": "deadbeef", "isolation_available": True, "source": "static", "records": []}
        with (
            patch("scripts.rec_trailer_acceptance._current_sha", return_value="deadbeef"),
            patch("scripts.rec_trailer_acceptance.evaluate", return_value=fake_result) as mock_eval,
        ):
            rc = cmd_evaluate(args)
        assert rc == 0
        mock_eval.assert_called_once_with([{"rec_id": "rec-1"}], RTA_ROOT, "deadbeef")
        assert json.loads((tmp_path / _VERDICT_ARTIFACT).read_text(encoding="utf-8")) == fake_result

    def test_cmd_close_no_trailer_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_json(tmp_path / _CENSUS_ARTIFACT, {"ids": [], "entries": []})
        rc, mock_close = _run_cmd_close(tmp_path, monkeypatch)
        assert rc == 0
        mock_close.assert_not_called()

    def test_cmd_close_reads_junit_verdict_file_when_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--junit-verdict names a file that exists: cmd_close reads it and threads it into rejoin() as junit_doc."""
        _write_json(
            tmp_path / _CENSUS_ARTIFACT,
            {"ids": ["rec-1"], "entries": [{"rec_id": "rec-1", "acceptance_sha256": "abc", "route": ROUTE_JUNIT}]},
        )
        _write_json(tmp_path / _VERDICT_ARTIFACT, {"records": []})
        junit_verdict_path = tmp_path / "junit_verdict.json"
        _write_json(
            junit_verdict_path,
            {"records": [{"rec_id": "rec-1", "acceptance_sha256": "abc", "verdict": HOLDS}]},
        )
        rc, mock_close = _run_cmd_close(tmp_path, monkeypatch, junit_verdict=junit_verdict_path)
        assert rc == 0
        _, kwargs = mock_close.call_args
        assert kwargs["acceptance_verdicts"] == {"rec-1": {"rec_id": "rec-1", "acceptance_sha256": "abc", "verdict": HOLDS}}

    def test_close_requires_acceptance_verdict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Graduated (VP step 1): cmd_close hands close_recs_from_trailer require_acceptance_verdict=True and the
        REJOINED verdict mapping (a stale acceptance_sha256 is dropped by rejoin(), never trusted)."""
        _write_json(
            tmp_path / _CENSUS_ARTIFACT,
            {
                "ids": ["rec-1", "rec-2"],
                "entries": [
                    {"rec_id": "rec-1", "acceptance_sha256": "abc"},
                    {"rec_id": "rec-2", "acceptance_sha256": "def"},
                ],
            },
        )
        _write_json(
            tmp_path / _VERDICT_ARTIFACT,
            {
                "records": [
                    {"rec_id": "rec-1", "acceptance_sha256": "abc", "verdict": HOLDS},
                    {"rec_id": "rec-2", "acceptance_sha256": "stale-mismatch", "verdict": HOLDS},
                ]
            },
        )
        rc, mock_close = _run_cmd_close(tmp_path, monkeypatch)
        assert rc == 0
        _, kwargs = mock_close.call_args
        assert kwargs["require_acceptance_verdict"] is True
        assert kwargs["acceptance_verdicts"] == {"rec-1": {"rec_id": "rec-1", "acceptance_sha256": "abc", "verdict": HOLDS}}

    @pytest.mark.parametrize(
        ("argv", "command", "handler"),
        [
            (["census", "--artifact-dir", "/tmp/x"], "census", cmd_census),
            (["evaluate", "--payload", "/tmp/p.json"], "evaluate", cmd_evaluate),
            (["close"], "close", cmd_close),
        ],
    )
    def test_build_parser_defines_three_subcommands(self, argv: list[str], command: str, handler: object) -> None:
        args = build_parser().parse_args(argv)
        assert args.command == command
        assert args.handler is handler

    def test_main_applies_default_artifact_dir_when_omitted(self, tmp_path: Path) -> None:
        handler = MagicMock(return_value=0)
        ns = argparse.Namespace(artifact_dir=None, handler=handler)
        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = ns
        with (
            patch("scripts.rec_trailer_acceptance.build_parser", return_value=mock_parser),
            patch("scripts.rec_trailer_acceptance._default_artifact_dir", return_value=tmp_path),
        ):
            rc = main(["census"])
        assert rc == 0
        assert ns.artifact_dir == tmp_path
        handler.assert_called_once_with(ns)

    def test_main_runtimeerror_returns_1_and_prints_fail_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        handler = MagicMock(side_effect=RuntimeError("boom"))
        ns = argparse.Namespace(artifact_dir=Path("/tmp/already-set"), handler=handler)
        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = ns
        with patch("scripts.rec_trailer_acceptance.build_parser", return_value=mock_parser):
            rc = main(["close"])
        assert rc == 1
        assert "FAIL: boom" in capsys.readouterr().err


class TestCloseReadsLiveRows:
    """cmd_close reads each trailer rec live via rec_by_id: no sync(), no cache file, loud on reader failure."""

    def _stage(self, tmp_path: Path) -> None:
        _write_json(
            tmp_path / _CENSUS_ARTIFACT, {"ids": ["rec-1"], "entries": [{"rec_id": "rec-1", "acceptance_sha256": "abc"}]}
        )
        _write_json(
            tmp_path / _VERDICT_ARTIFACT, {"records": [{"rec_id": "rec-1", "acceptance_sha256": "abc", "verdict": HOLDS}]}
        )

    def test_close_hands_live_rows_to_closer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stage(tmp_path)
        rc, mock_close = _run_cmd_close(tmp_path, monkeypatch, live_rows={"rec-1": {"id": "rec-1", "status": "open"}})
        assert rc == 0
        assert mock_close.call_args.args[3] == {"rec-1": {"id": "rec-1", "status": "open"}}

    def test_close_never_reads_cache_file_or_syncs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stage(tmp_path)
        monkeypatch.setattr("scripts.rec_trailer_acceptance.ROOT", tmp_path)
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / ".recommendations-log.jsonl").write_text('{"id": "rec-1", "status": "stale"}\n', encoding="utf-8")
        with patch("scripts.ops_data_portal.sync") as mock_sync:
            rc, mock_close = _run_cmd_close(tmp_path, monkeypatch)
        assert rc == 0
        mock_sync.assert_not_called()
        assert mock_close.call_args.args[3] == {}

    def test_close_raises_on_reader_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stage(tmp_path)
        with pytest.raises(RuntimeError, match="reader down"):
            _run_cmd_close(tmp_path, monkeypatch, reader_error=RuntimeError("reader down"))
