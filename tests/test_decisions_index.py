"""Tests for scripts/decisions_index.py (DCG-08, PLAN-dcg-decisions-index -- Decision 104/131
mirror of the generator; PLAN-decision-scout-bounded-retrieval adds the `live`, triage_excerpt_*,
and `category_tags` coverage below).

Covers: build_index() determinism (byte-stable, no volatile-field leak), both-files coverage
(Decision 146), typed-edge spot-checks spanning both derivation paths (title extraction,
superseded_by inverse map, title-borne supersedes) and both files (live + archive), the
superseded_by string->int coercion, the stable-field-only projection shape, the `live` provenance
flag, the triage_excerpt/triage_excerpt_source/triage_excerpt_truncated fallback logic, and the
category_tags deterministic pattern-matching (the recall-gap fix from the Fable advice-consult).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.decisions_index import (
    _LIVE_PATH,
    _derive_category_tags,
    build_excerpts,
    build_index,
    build_triage_excerpt,
    check_index_freshness,
    main,
    serialize_index,
)
from scripts.decisions_md import decision_header_numbers, extract_entry_envelope, parse_decisions_md, status_is_superseded


def _live_rows(idx: dict) -> list[dict]:
    return [entry for entry in idx["decisions"] if entry["live"]]


def _tagged_rows(idx: dict) -> list[dict]:
    return [entry for entry in idx["decisions"] if "category_tags" in entry]


class TestDeterminism:
    """VP step 1: two consecutive build_index() calls must be byte-identical."""

    def test_build_index_is_byte_stable_across_two_calls(self) -> None:
        first = json.dumps(build_index(), sort_keys=True)
        second = json.dumps(build_index(), sort_keys=True)
        assert first == second

    def test_build_index_excludes_volatile_parser_fields(self) -> None:
        """No created_timestamp/last_updated_timestamp/content_hash/raw_block leak into any
        entry -- these are exactly the fields that would break determinism (a fresh
        now_iso timestamp on every parse_decisions_md() call)."""
        idx = build_index()
        volatile = {"created_timestamp", "last_updated_timestamp", "content_hash", "raw_block"}
        for entry in idx["decisions"]:
            assert not (volatile & entry.keys()), f"dec-{entry['number']} leaked volatile field(s): {volatile & entry.keys()}"

    def test_entry_shape_is_the_stable_projection_only(self) -> None:
        """Archive rows and compacted stubs are skeletons; other live rows add tags; enveloped ones add serves/kind."""
        idx = build_index()
        skeleton_keys = {"number", "title", "status", "decided_date", "supersedes", "superseded_by", "amends", "live"}
        stub_keys = skeleton_keys | {"currency"}
        live_keys = stub_keys | {"category_tags", "triage_excerpt"}
        enveloped = {r["decision_id"] for r in parse_decisions_md() if extract_entry_envelope(r["raw_block"]) is not None}
        for entry in idx["decisions"]:
            if not entry["live"]:
                expected_keys = skeleton_keys
            elif entry["currency"] == "superseded_compacted":
                expected_keys = stub_keys
            else:
                expected_keys = live_keys | {"serves", "kind"} if entry["number"] in enveloped else live_keys
            assert set(entry.keys()) == expected_keys, entry["number"]


class TestSkeletonArchiveRows:
    """live:false rows project the eight stable keys only (rec-3012, rec-3250)."""

    def test_archive_rows_are_skeletons(self) -> None:
        idx = build_index()
        skel = {"number", "title", "status", "decided_date", "supersedes", "superseded_by", "amends", "live"}
        for entry in idx["decisions"]:
            if not entry["live"]:
                assert set(entry.keys()) == skel, entry["number"]

    def test_archive_rows_cost_materially_less_than_live_rows(self) -> None:
        """Archive-row cost stays at most half a live row's: the archive row carries none of a live entry's signal."""
        idx = build_index()["decisions"]
        archive = [e for e in idx if not e["live"]]
        live = [e for e in idx if e["live"]]
        archive_bpr = len(json.dumps(archive)) / len(archive)
        live_bpr = len(json.dumps(live)) / len(live)
        assert archive_bpr < 0.5 * live_bpr, (archive_bpr, live_bpr)


class TestSkeletonCompactedStubRows:
    """rec-5258: a superseded_compacted live row is the archive skeleton plus currency."""

    def test_compacted_stub_rows_are_skeleton_plus_currency(self) -> None:
        skel = {"number", "title", "status", "decided_date", "supersedes", "superseded_by", "amends", "live", "currency"}
        stubs = [e for e in build_index()["decisions"] if e.get("currency") == "superseded_compacted"]
        assert stubs
        for entry in stubs:
            assert set(entry.keys()) == skel, entry["number"]


class TestSlimIndex:
    """rec-3250: slim rows, one per line, excerpts only on demand."""

    def test_live_non_stub_rows_carry_excerpt_text_and_no_marker(self) -> None:
        excerpts = {e["number"]: e for e in build_excerpts(None)}
        carriers = 0
        for entry in build_index()["decisions"]:
            marker_keys = {k for k in entry if k.startswith("triage_excerpt")}
            if entry["live"] and entry["currency"] != "superseded_compacted":
                carriers += 1
                assert marker_keys == {"triage_excerpt"}, entry["number"]
                assert entry["triage_excerpt"] == excerpts[entry["number"]]["triage_excerpt"], entry["number"]
            else:
                assert not marker_keys, entry["number"]
        assert carriers

    def test_serialised_output_is_one_row_per_line_and_parses_to_the_index(self) -> None:
        index = build_index()
        text = serialize_index(index)
        assert json.loads(text) == index
        assert len(text.splitlines()) == len(index["decisions"]) + 2

    def test_committed_index_is_one_row_per_line(self) -> None:
        text = (Path(__file__).resolve().parent.parent / "docs" / "decisions-index.json").read_text(encoding="utf-8")
        assert json.loads(text) == build_index()
        assert text == serialize_index(build_index())


class TestExcerptsCli:
    """--excerpts prints one JSON line per live id and refuses others with exit 2."""

    def test_named_ids_comma_and_space_separated(self, monkeypatch, capsys) -> None:
        for argv in (["--excerpts", "100,164"], ["--excerpts", "100", "164"], ["--excerpts", "164 100"]):
            monkeypatch.setattr("sys.argv", ["decisions_index", *argv])
            main()
            lines = capsys.readouterr().out.splitlines()
            assert len(lines) == 2
            assert lines[0].startswith('{"number": 100,')
            assert lines[1].startswith('{"number": 164,')

    def test_all_serves_every_live_id(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr("sys.argv", ["decisions_index", "--excerpts", "all"])
        main()
        out = capsys.readouterr().out.splitlines()
        live = [e["number"] for e in build_index()["decisions"] if e["live"]]
        assert [json.loads(line)["number"] for line in out] == live
        assert all(re.match(r'\{"number": \d+,', line) for line in out)

    def test_exit_two_names_archived_and_unknown_ids(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr("sys.argv", ["decisions_index", "--excerpts", "36,999999"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "36" in err and "999999" in err

    def test_non_numeric_id_exits_two(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.argv", ["decisions_index", "--excerpts", "abc"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


class TestBothFilesCoverage:
    """VP step 2 / Decision 146: entry set == the shared parser's header set across BOTH
    docs/DECISIONS.md and docs/DECISIONS_ARCHIVE.md -- derived, never hardcoded (Decision 55
    test-count-coupling)."""

    def test_covers_all_headers_both_files(self) -> None:
        idx = build_index()
        numbers = {entry["number"] for entry in idx["decisions"]}
        assert numbers == decision_header_numbers()

    def test_decisions_list_is_sorted_by_number(self) -> None:
        idx = build_index()
        numbers = [entry["number"] for entry in idx["decisions"]]
        assert numbers == sorted(numbers)

    def test_archive_only_entry_present(self) -> None:
        """dec-36 exists only in DECISIONS_ARCHIVE.md (tests/decisions_md/test_parse.py's own
        archive-coverage anchor) -- proves the index covers the archive file too."""
        idx = build_index()
        numbers = {entry["number"] for entry in idx["decisions"]}
        assert 36 in numbers


class TestEnvelopeSourcedEdges:
    """PLAN-decision-entry-flow-governance / Decision 167: envelope-declared amends/supersedes
    edges reach the index through the shared parser, with no second derivation here, and
    `significance` never leaks into the projection even though the parser row carries it."""

    def test_dec167_envelope_amends_reach_the_index(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[167]["amends"] == [150, 160]

    def test_dec167_significance_absent_from_its_projected_entry(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert "significance" not in d[167]


class TestSupersededByCoercion:
    """superseded_by is coerced from the parser's 'dec-NNN' string to a bare int, or None."""

    def test_superseded_by_is_int_not_string(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[69]["superseded_by"] == 78
        assert isinstance(d[69]["superseded_by"], int)

    def test_superseded_by_is_none_when_never_superseded(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[150]["superseded_by"] is None


class TestLiveField:
    """VP step 1 / AC1: `live` derived from decision_header_numbers(paths=[docs/DECISIONS.md]),
    never a hardcoded count (Decision 55 test-count-coupling)."""

    def test_live_matches_file_provenance(self) -> None:
        idx = build_index()
        live_numbers = decision_header_numbers(paths=[_LIVE_PATH])
        indexed_live = {x["number"] for x in idx["decisions"] if x["live"]}
        indexed_archive = {x["number"] for x in idx["decisions"] if not x["live"]}
        assert indexed_live == live_numbers
        assert indexed_archive == {x["number"] for x in idx["decisions"]} - live_numbers

    def test_live_is_bool_not_truthy_value(self) -> None:
        idx = build_index()
        for entry in idx["decisions"]:
            assert isinstance(entry["live"], bool)

    def test_archive_only_entry_is_not_live(self) -> None:
        """dec-36 exists only in DECISIONS_ARCHIVE.md (see TestBothFilesCoverage)."""
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert d[36]["live"] is False


class TestCurrencyProjection:
    """PLAN-decision-corpus-currency: `currency` is a live-only key, matching representative
    classes across the real corpus. Structural/count-free (round-3 de-pinning): no raw
    distribution is asserted, since it grows with every new live Decision."""

    def test_currency_present_iff_live(self) -> None:
        idx = build_index()
        for entry in idx["decisions"]:
            assert ("currency" in entry) == entry["live"], entry["number"]

    def test_representative_classes_and_compacted_set_equals_status_marker_set(self) -> None:
        idx = build_index()["decisions"]
        by_number = {e["number"]: e for e in idx}
        expected = {
            44: "superseded_compacted",
            58: "superseded_compacted",
            37: "superseded_compacted",  # Decision 175 migrate_then_rehome compaction
            80: "superseded_pointer",
            43: "amended",
            67: "superseded_compacted",
            168: "current",
        }
        for n, want in expected.items():
            assert by_number[n]["currency"] == want, (n, by_number[n]["currency"])

        live_numbers = {e["number"] for e in idx if e["live"]}
        marked = {r["decision_id"] for r in parse_decisions_md() if status_is_superseded(r["raw_block"])}
        compacted = {e["number"] for e in idx if e.get("currency") == "superseded_compacted"}
        assert compacted == (marked & live_numbers)


class TestTriageExcerptUnit:
    """VP step 1 / AC1: build_triage_excerpt's fallback order (Intent -> Problem -> Context ->
    Decision), the empty-excerpt branch, the 320-char cap, and the truncation flag -- tested as
    a pure unit against synthetic rows so coverage does not depend on which real decision numbers
    happen to occupy each branch today."""

    def test_intent_wins_when_present(self) -> None:
        row = {"intent": "the intent text", "problem": "the problem text", "context": "ctx", "decision_text": "dec"}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert excerpt == "the intent text"
        assert source == "Intent"
        assert truncated is False

    def test_problem_wins_when_no_intent(self) -> None:
        row = {"intent": "", "problem": "the problem text", "context": "ctx", "decision_text": "dec"}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert excerpt == "the problem text"
        assert source == "Problem"
        assert truncated is False

    def test_context_wins_when_no_intent_or_problem(self) -> None:
        row = {"intent": "", "problem": "", "context": "the context text", "decision_text": "dec"}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert excerpt == "the context text"
        assert source == "Context"
        assert truncated is False

    def test_decision_wins_when_only_decision_text_present(self) -> None:
        row = {"intent": "", "problem": "", "context": "", "decision_text": "the decision text"}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert excerpt == "the decision text"
        assert source == "Decision"
        assert truncated is False

    def test_whitespace_only_fields_are_treated_as_empty(self) -> None:
        row = {"intent": "   \n  ", "problem": "", "context": "", "decision_text": "the decision text"}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert source == "Decision"

    def test_empty_when_no_markers_present_at_all(self) -> None:
        row = {"intent": "", "problem": "", "context": "", "decision_text": ""}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert excerpt == ""
        assert source == ""
        assert truncated is False

    def test_missing_keys_treated_as_empty(self) -> None:
        excerpt, source, truncated = build_triage_excerpt({})
        assert excerpt == ""
        assert source == ""
        assert truncated is False

    def test_320_char_cap_not_truncated_at_exactly_320(self) -> None:
        row = {"intent": "x" * 320, "problem": "", "context": "", "decision_text": ""}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert len(excerpt) == 320
        assert truncated is False

    def test_320_char_cap_truncates_over_320(self) -> None:
        row = {"intent": "x" * 400, "problem": "", "context": "", "decision_text": ""}
        excerpt, source, truncated = build_triage_excerpt(row)
        assert len(excerpt) == 320
        assert excerpt == "x" * 320
        assert truncated is True


class TestCategoryTagsUnit:
    """VP-9-driven fix (Fable advice-consult): _derive_category_tags as a pure unit, independent
    of real corpus content -- covers each pattern class and the sorted/dedup/no-match shapes."""

    def test_lambda_tag_matches_lambda_mention(self) -> None:
        assert "lambda" in _derive_category_tags("Ratify per-Lambda packaging manifests", "")

    def test_terraform_tag_matches_terraform_mention(self) -> None:
        assert "terraform" in _derive_category_tags("Terraform apply routing", "")

    def test_iam_tag_matches_role_or_boundary(self) -> None:
        assert "iam" in _derive_category_tags("A new IAM role", "")
        assert "iam" in _derive_category_tags("", "extends the mandatory permissions boundary")

    def test_secrets_tag_matches_secrets_manager(self) -> None:
        assert "secrets" in _derive_category_tags("Secrets Manager split by value-capability", "")

    def test_deploy_tag_matches_deployment_noun(self) -> None:
        """Regression pin: Decision 126's title says 'deployment model', not 'deploy' -- the
        pattern must match the noun form too, not just the bare verb."""
        assert "deploy" in _derive_category_tags("Two-verb deployment model", "")

    def test_egress_tag_matches_neon_or_budget(self) -> None:
        assert "egress" in _derive_category_tags("Neon catalog egress is a first-class budget", "")

    def test_decisions_corpus_tag_matches_decisions_md(self) -> None:
        assert "decisions-corpus" in _derive_category_tags("DECISIONS.md is the canonical corpus", "")

    def test_prose_docs_tag_matches_docs_path(self) -> None:
        assert "prose-docs" in _derive_category_tags("Sanctioned-prose taxonomy", "")

    def test_no_match_returns_empty_list(self) -> None:
        assert _derive_category_tags("Some entirely unrelated title", "and an unrelated excerpt") == []

    def test_multiple_matches_are_sorted_and_deduped(self) -> None:
        tags = _derive_category_tags("Lambda Terraform Lambda deploy", "")
        assert tags == sorted(set(tags))
        assert tags.count("lambda") == 1

    def test_matches_against_excerpt_too_not_only_title(self) -> None:
        assert _derive_category_tags("Untitled", "reads a Secrets Manager credential") == ["secrets"]


class TestCategoryTagsIntegration:
    """Real-corpus regression pins for the two decisions the category_tags fix specifically
    targets (VP step 9's measured recall gap, closed via the Fable advice-consult) -- Decision
    126 ('deployment model' title, no Lambda/Terraform/IAM/Secrets keyword) and Decision 157
    ('Secrets Manager' in title) must both carry a non-empty category_tags list so decision-scout's
    mechanical shortlist step can find them without relying on per-entry judgment."""

    def test_decision_126_carries_deploy_tag(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert "deploy" in d[126]["category_tags"]

    def test_decision_157_carries_secrets_tag(self) -> None:
        idx = build_index()
        d = {x["number"]: x for x in idx["decisions"]}
        assert "secrets" in d[157]["category_tags"]

    def test_every_entry_carries_a_list_of_strings(self) -> None:
        idx = build_index()
        for entry in _tagged_rows(idx):
            assert isinstance(entry["category_tags"], list)
            assert all(isinstance(t, str) for t in entry["category_tags"])

    def test_category_tags_is_sorted_and_deduped_for_every_entry(self) -> None:
        idx = build_index()
        for entry in _tagged_rows(idx):
            tags = entry["category_tags"]
            assert tags == sorted(set(tags)), f"dec-{entry['number']} category_tags not sorted/deduped: {tags}"

    def test_no_tag_covers_more_than_forty_percent_of_live_entries(self) -> None:
        """Keeps the shortlist materially smaller than the full corpus -- an over-broad tag would
        silently rebuild the whole-file-read cost this plan removes."""
        idx = build_index()
        live = _tagged_rows(idx)
        from collections import Counter

        counts = Counter(tag for entry in live for tag in entry["category_tags"])
        for tag, count in counts.items():
            assert count / len(live) <= 0.40, f"tag {tag!r} covers {count}/{len(live)} live entries -- too broad"


class TestCheckIndexFreshnessDirect:
    """Direct coverage of check_index_freshness() in THIS mapped test file (the coverage checker
    maps scripts/decisions_index.py -> tests/test_decisions_index.py specifically; the equivalent
    branch coverage in tests/checks/decisions/test_validate_decisions_index_freshness.py exercises
    the registered wrapper, a different module, and does not count toward this file's mapping)."""

    def test_fails_when_export_is_absent(self, tmp_path) -> None:
        missing = tmp_path / "nonexistent.json"
        with patch("scripts.decisions_index._EXPORT_PATH", missing):
            failed: list[str] = []
            compared = check_index_freshness(failed)
        assert len(failed) == 1
        assert "missing" in failed[0].lower()
        assert compared is None

    def test_fails_when_export_is_unreadable_json(self, tmp_path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text("not valid json {{{", encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            compared = check_index_freshness(failed)
        assert len(failed) == 1
        assert "cannot read" in failed[0].lower()
        assert compared is None

    def test_fails_when_export_is_stale(self, tmp_path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps({"decisions": [], "metadata": {}}), encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            compared = check_index_freshness(failed)
        assert len(failed) == 1
        assert "stale" in failed[0].lower()
        assert compared == len(build_index()["decisions"]) > 0

    def test_passes_when_export_is_fresh(self, tmp_path) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(build_index(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with patch("scripts.decisions_index._EXPORT_PATH", export_path):
            failed: list[str] = []
            compared = check_index_freshness(failed)
        assert not failed
        assert compared == len(build_index()["decisions"]) > 0


class TestMainCLI:
    """Direct coverage of main()'s argparse dispatch branches (--write / --check / --print /
    no-args), same file-mapping rationale as TestCheckIndexFreshnessDirect above."""

    def test_write_regenerates_the_export(self, tmp_path, monkeypatch) -> None:
        export_path = tmp_path / "decisions-index.json"
        monkeypatch.setattr("scripts.decisions_index._EXPORT_PATH", export_path)
        monkeypatch.setattr("sys.argv", ["decisions_index", "--write"])
        main()
        assert export_path.exists()
        assert json.loads(export_path.read_text(encoding="utf-8")) == build_index()

    def test_check_exits_zero_when_fresh(self, tmp_path, monkeypatch) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps(build_index(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        monkeypatch.setattr("scripts.decisions_index._EXPORT_PATH", export_path)
        monkeypatch.setattr("sys.argv", ["decisions_index", "--check"])
        main()  # does not raise SystemExit when fresh

    def test_check_exits_one_when_stale(self, tmp_path, monkeypatch) -> None:
        export_path = tmp_path / "decisions-index.json"
        export_path.write_text(json.dumps({"decisions": [], "metadata": {}}), encoding="utf-8")
        monkeypatch.setattr("scripts.decisions_index._EXPORT_PATH", export_path)
        monkeypatch.setattr("sys.argv", ["decisions_index", "--check"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_print_dumps_json_to_stdout(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr("sys.argv", ["decisions_index", "--print"])
        main()
        out = capsys.readouterr().out
        assert json.loads(out) == build_index()

    def test_no_args_prints_help_and_exits_one(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.argv", ["decisions_index"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
