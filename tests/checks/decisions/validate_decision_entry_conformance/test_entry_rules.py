"""Tests for validate_decision_entry_conformance() (DAF-03 / Decision 167 typed envelope +
Significance routing + Decision 175 widened stub_grammar). Exercises the root/baseline_reader
injection seams rather than real git plumbing for the deterministic cases."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.checks.decisions._baseline import baseline_decision_numbers as _default_baseline_decision_numbers
from scripts.checks.decisions.validate_decision_entry_conformance import (
    _current_decision_entries,
    _is_compacted_stub,
    _load_block_separator_form,
    _load_envelope_config,
    _load_required_markers,
    validate_decision_entry_conformance,
)

_CONTRACT_YAML = "required_markers:\n  - Status\n  - Date\n  - Decision\n"
_CONTRACT_YAML_WITH_BLOCK_SEPARATOR = (
    'required_markers:\n  - Status\n  - Date\n  - Decision\nblock_separator:\n  form: "---"\n'
)

_SIGNIFICANCE_YAML = """\
metadata_envelope:
  required_fields: [number, significance]
significance:
  routing:
    numbered_decision: A durable architectural commitment.
    cd_state_flip: A CD state transition.
    operational_fact: A transient statement. Never a numbered Decision.
    field_semantics: A field's meaning. Never a numbered Decision.
"""

_FULL_CONTRACT_YAML = _CONTRACT_YAML + _SIGNIFICANCE_YAML
_FULL_CONTRACT_YAML_WITH_BLOCK_SEPARATOR = _CONTRACT_YAML_WITH_BLOCK_SEPARATOR + _SIGNIFICANCE_YAML


def _envelope(number: int, significance: str = "numbered_decision", justification: str = "A reason.", **extra: object) -> str:
    lines = [f"number: {number}"]
    for key, value in extra.items():
        if isinstance(value, list):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    if significance is not None:
        lines.append("significance:")
        lines.append(f"  value: {significance}")
        lines.append(f"  justification: {justification}")
    body = "\n".join(lines)
    return f"```yaml\n{body}\n```\n"


def _write_contract(root: Path, contract_text: str = _CONTRACT_YAML) -> None:
    contracts_dir = root / "docs" / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / "decision-entry.yaml").write_text(contract_text, encoding="utf-8")


def _write_decisions_md(root: Path, content: str, archive_content: str = "") -> None:
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "DECISIONS.md").write_text(content, encoding="utf-8")
    (docs_dir / "DECISIONS_ARCHIVE.md").write_text(archive_content, encoding="utf-8")


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, ["init", "-q"])
    _git(repo, ["config", "user.email", "test@example.com"])
    _git(repo, ["config", "user.name", "Test"])


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", message])
    return _git(repo, ["rev-parse", "HEAD"]).stdout.strip()


class TestSkipOnUnreachableBaseline:
    def test_injected_none_baseline_skips(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(tmp_path, "## Decision 1: New entry (Decided)\n\n**Status:** Decided\n")
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: None)
        assert failed == []

    def test_default_reader_skips_with_no_git_repo_at_all(self, tmp_path: Path) -> None:
        """No .git dir under tmp_path -- exercises the DEFAULT baseline_reader, not an injected seam."""
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(tmp_path, "## Decision 1: New entry (Decided)\n\n**Status:** Decided\n")
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path)
        assert failed == []


class TestNewEntryConformance:
    @pytest.mark.parametrize(
        ("body_tail", "expect_fail"),
        [
            ("\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** Do the thing.\n", False),
            ("\n**Status:** Decided\n\n**Decision:** Do the thing.\n", True),  # missing required Date marker
            (
                "\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision (four invariants):** Do the thing.\n",
                False,
            ),  # decorated marker tolerated (decision_marker_tolerance)
        ],
        ids=["canonical_passes", "missing_date_fails", "decorated_marker_tolerated"],
    )
    def test_new_entry_cases(self, tmp_path: Path, body_tail: str, expect_fail: bool) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(tmp_path, "## Decision 5: New entry case (Decided)\n\n" + _envelope(5) + body_tail)
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == (["Decision-entry conformance"] if expect_fail else [])

    def test_no_new_entries_passes_trivially(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _CONTRACT_YAML)  # no significance section needed -- never loaded
        _write_decisions_md(tmp_path, "## Decision 1: Already known (Decided)\n\n**Status:** Decided\n")
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: {1})
        assert failed == []


class TestHistoricalEntryGrandfathered:
    def test_modified_historical_entry_is_not_flagged(self, tmp_path: Path) -> None:
        """Grandfathered even missing required markers/envelope (DPI-07: Decisions 52/53/54)."""
        _write_contract(tmp_path, _CONTRACT_YAML)
        _write_decisions_md(
            tmp_path,
            "## Decision 1: Historical, now missing Date (Decided)\n\n"
            "**Status:** Decided -- April 2026\n\n**Decision:** Historical body.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: {1})
        assert failed == []

    def test_archive_h3_to_h2_promote_does_not_manufacture_a_new_entry(self, tmp_path: Path) -> None:
        """h3-shaped baseline vs h2-promoted current tree -- must not be flagged new (shared '#{2,3}' grammar)."""
        _write_contract(tmp_path, _CONTRACT_YAML)
        # baseline_reader simulates decisions_md.iter_decision_headings() over the origin/main
        # (pre-promote, h3) blob -- decision 52 is visible there too, via the same #{2,3} grammar.
        _write_decisions_md(
            tmp_path,
            "",
            archive_content="## Decision 52: Promoted entry (Decided)\n\n**Status:** Decided -- April 2026\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: {52})
        assert failed == []


class TestWidenedStubGrammarEnforcement:
    """Decision 175: Superseded-status entries enforced off-baseline; prose-only pointers (Decision 149's shape) left alone."""

    @pytest.mark.parametrize(
        ("number", "body", "expect_fail"),
        [
            (44, "**Status:** Superseded\n\n**Decision:** x.\n", True),  # missing Date + pointer
            (
                44,
                "**Status:** Superseded\n\n**Date:** 2026-08-24\n\n**Decision:** x.\n\n**Superseded by: Decision 117**\n",
                False,
            ),
            (
                149,
                "**Status:** Decided\n\n**Date:** 2026-07-01\n\n**Decision:** x.\n\nNote: '**Superseded by: Decision N**'.\n",
                False,
            ),
        ],
        ids=["malformed_fails", "conforming_passes", "full_bodied_left_alone"],
    )
    def test_baseline_present_cases(self, tmp_path: Path, number: int, body: str, expect_fail: bool) -> None:
        _write_contract(tmp_path, _CONTRACT_YAML)
        _write_decisions_md(tmp_path, f"## Decision {number}: Case (Decided)\n\n" + body)
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: {number})
        assert failed == (["Decision-entry conformance"] if expect_fail else [])


class TestBlockSeparatorSubCheck:
    """CFG-06 forward enforcement of block_separator (rec-2991) -- new entries only."""

    @pytest.mark.parametrize(
        ("body_tail", "expect_fail"),
        [
            ("\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** Do the thing.\n", True),
            ("\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** Do the thing.\n\n---\n", False),
        ],
        ids=["missing_separator", "with_separator"],
    )
    def test_new_entry_separator_cases(self, tmp_path: Path, body_tail: str, expect_fail: bool) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML_WITH_BLOCK_SEPARATOR)
        _write_decisions_md(tmp_path, "## Decision 5: Separator case (Decided)\n\n" + _envelope(5) + body_tail)
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == (["Decision-entry conformance"] if expect_fail else [])

    def test_historical_entry_without_separator_is_grandfathered(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _CONTRACT_YAML_WITH_BLOCK_SEPARATOR)
        _write_decisions_md(
            tmp_path,
            "## Decision 1: Historical, no trailing separator (Decided)\n\n"
            "**Status:** Decided -- April 2026\n\n**Decision:** Historical body.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: {1})
        assert failed == []

    def test_missing_block_separator_key_is_a_tolerated_absence(self, tmp_path: Path) -> None:
        """No block_separator key in the default fixture -- the sub-check must no-op, not fail."""
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)  # no block_separator key
        _write_decisions_md(
            tmp_path,
            "## Decision 5: A new entry with no trailing separator (Decided)\n\n"
            + _envelope(5)
            + "\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** Do the thing.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []


class TestMissingContract:
    """Contract-shape failures. metadata_envelope/significance load is deferred until AFTER the
    no-new-entries early-return, so a bare required_markers-only contract stays valid with no new
    entries but fails once one needs it -- exercised via the "new" fixtures below."""

    @pytest.mark.parametrize(
        ("contract_text", "md_content", "expected_snippet"),
        [
            (None, "## Decision 1: X (Decided)\n\n**Status:** Decided\n", "decision-entry.yaml"),
            ("header_form: something\n", "## Decision 1: X (Decided)\n\n**Status:** Decided\n", "required_markers"),
            (_CONTRACT_YAML, "## Decision 5: New (Decided)\n\n**Status:** Decided\n", "metadata_envelope"),
            (
                _CONTRACT_YAML + "metadata_envelope:\n  required_fields: [number, significance]\n",
                "## Decision 5: New (Decided)\n\n**Status:** Decided\n",
                "significance.routing",
            ),
        ],
        ids=["missing_file", "missing_required_markers", "missing_metadata_envelope", "missing_significance_routing"],
    )
    def test_contract_shape_failures(
        self, tmp_path: Path, contract_text: str | None, md_content: str, expected_snippet: str
    ) -> None:
        if contract_text is not None:
            _write_contract(tmp_path, contract_text=contract_text)
        _write_decisions_md(tmp_path, md_content)
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert len(failed) == 1
        assert expected_snippet in failed[0]


class TestLoadEnvelopeConfigDirect:
    """Direct coverage of _load_envelope_config's failure branches -- unreachable through the
    main check (a bad contract already halts at _load_required_markers, same file)."""

    def test_missing_contract_file_fails(self, tmp_path: Path) -> None:
        failed: list[str] = []
        result = _load_envelope_config(tmp_path, failed)
        assert result is None
        assert "not found" in failed[0]

    @pytest.mark.parametrize(
        ("contract_text", "expected_snippet"),
        [
            ("metadata_envelope: [unclosed\n", "could not parse"),
            ("- just\n- a\n- list\n", "unexpected top-level shape"),
        ],
        ids=["malformed_yaml", "non_dict_top_level"],
    )
    def test_load_failure_cases(self, tmp_path: Path, contract_text: str, expected_snippet: str) -> None:
        _write_contract(tmp_path, contract_text=contract_text)
        failed: list[str] = []
        result = _load_envelope_config(tmp_path, failed)
        assert result is None
        assert expected_snippet in failed[0]

    def test_well_formed_config_returns_fields_and_routing(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        failed: list[str] = []
        result = _load_envelope_config(tmp_path, failed)
        assert failed == []
        assert result is not None
        fields, routing = result
        assert fields == ["number", "significance"]
        assert set(routing) == {"numbered_decision", "cd_state_flip", "operational_fact", "field_semantics"}


class TestIsCompactedStubHelper:
    """Direct unit coverage of _is_compacted_stub's branches."""

    @pytest.mark.parametrize(
        ("block", "expected"),
        [
            ("## Decision 1: No status (Decided)\n\n**Decision:** X.\n", False),
            ("**Status:** Decided\n\n**Decision:** X.\n", False),
            ("**Status:** Superseded\n\n**Decision:** X.\n", False),
            ("**Status:** Superseded\n\n**Decision:** See Decision 6.\n\n**Superseded by: Decision 6**\n", True),
        ],
        ids=["no_status_marker", "status_not_superseded", "superseded_no_pointer", "superseded_with_pointer"],
    )
    def test_is_compacted_stub(self, block: str, expected: bool) -> None:
        assert _is_compacted_stub(block) is expected


class TestEnvelopeMissingRequiredField:
    """A malformed 'number: abc' produces the ordinary FAIL, never an unhandled ValueError."""

    @pytest.mark.parametrize(
        "envelope_yaml",
        ["significance:\n  value: numbered_decision\n  justification: reason.\n", "number: abc\n"],
        ids=["number_absent", "number_non_numeric"],
    )
    def test_malformed_number_field_fails(self, tmp_path: Path, envelope_yaml: str) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(
            tmp_path,
            f"## Decision 5: Malformed number case (Decided)\n\n```yaml\n{envelope_yaml}```\n\n"
            "**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == ["Decision-entry conformance"]


class TestEnvelopeWellFormedness:
    """VP step 4: envelope presence, well-formedness, and the number-match sub-check."""

    def test_new_entry_with_no_envelope_at_all_fails(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(
            tmp_path,
            "## Decision 5: No envelope (Decided)\n\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == ["Decision-entry conformance"]

    def test_number_mismatch_fails(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(
            tmp_path,
            "## Decision 5: Number mismatch (Decided)\n\n"
            + _envelope(999)
            + "\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == ["Decision-entry conformance"]

    def test_number_match_passes(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(
            tmp_path,
            "## Decision 5: Number match (Decided)\n\n"
            + _envelope(5)
            + "\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []

    def test_compacted_stub_is_exempt_from_envelope_obligation(self, tmp_path: Path) -> None:
        """A compacted stub is exempt from the envelope obligation even on a new-in-diff number
        (a same-session-new compacted number is a degenerate but legal case)."""
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(
            tmp_path,
            "## Decision 5: Compacted stub (Decided)\n\n"
            "**Status:** Superseded\n\n**Date:** 2026-07-16\n\n"
            "**Decision:** Superseded by Decision 6; see its body.\n\n"
            "**Superseded by: Decision 6**\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []


class TestEnvelopeBodyConflict:
    """VP step 4: envelope/bold-marker conflict on one field fails; agreement/absence passes."""

    @pytest.mark.parametrize(
        ("envelope_kwargs", "body_tail", "expect_fail"),
        [
            ({"status": "Decided"}, "\n**Status:** Superseded\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n", True),
            ({"status": "Decided"}, "\n**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n", False),
            ({"decided_date": "2026-07-16"}, "\n**Status:** Decided\n\n**Date:** 2099-01-01\n\n**Decision:** X.\n", True),
        ],
        ids=["status_conflict", "status_agreement", "decided_date_conflict"],
    )
    def test_conflict_cases(self, tmp_path: Path, envelope_kwargs: dict, body_tail: str, expect_fail: bool) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(
            tmp_path, "## Decision 5: Conflict case (Decided)\n\n" + _envelope(5, **envelope_kwargs) + body_tail
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == (["Decision-entry conformance"] if expect_fail else [])

    def test_bold_date_marker_absent_passes(self, tmp_path: Path) -> None:
        """Envelope declares decided_date but the body has no bold **Date:** marker -- nothing to
        conflict with, so this passes (required_markers patched to Status/Decision only, isolating
        this sub-check from marker-presence)."""
        _write_contract(
            tmp_path,
            "required_markers:\n  - Status\n  - Decision\n" + _SIGNIFICANCE_YAML,
        )
        _write_decisions_md(
            tmp_path,
            "## Decision 5: Date marker absent (Decided)\n\n"
            + _envelope(5, decided_date="2026-07-16")
            + "\n**Status:** Decided\n\n**Decision:** X.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []


class TestSignificanceEnforcement:
    """VP step 3: presence, contract-read token vocabulary, non-empty justification, and
    rejection of "Never a numbered Decision" rows on a new entry."""

    def _fixture(self, significance_yaml: str) -> str:
        return (
            "## Decision 5: Significance case (Decided)\n\n"
            f"```yaml\nnumber: 5\n{significance_yaml}```\n\n"
            "**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n"
        )

    @pytest.mark.parametrize(
        "significance_yaml",
        [
            "",
            "significance:\n  value: not_a_real_token\n  justification: reason.\n",
            'significance:\n  value: numbered_decision\n  justification: ""\n',
            "significance:\n  value: operational_fact\n  justification: reason.\n",
            "significance:\n  value: field_semantics\n  justification: reason.\n",
        ],
        ids=["absent", "unknown_token", "empty_justification", "operational_fact", "field_semantics"],
    )
    def test_significance_fail_cases(self, tmp_path: Path, significance_yaml: str) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(tmp_path, self._fixture(significance_yaml))
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == ["Decision-entry conformance"]

    @pytest.mark.parametrize("token", ["numbered_decision", "cd_state_flip"], ids=["numbered_decision", "cd_state_flip"])
    def test_significance_pass_cases(self, tmp_path: Path, token: str) -> None:
        _write_contract(tmp_path, _FULL_CONTRACT_YAML)
        _write_decisions_md(tmp_path, self._fixture(f"significance:\n  value: {token}\n  justification: reason.\n"))
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []

    def test_historical_band_entry_without_significance_is_untouched(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, _CONTRACT_YAML)
        _write_decisions_md(tmp_path, "## Decision 1: Historical (Decided)\n\n**Status:** Decided\n")
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: {1})
        assert failed == []


class TestContractTokenParity:
    """VP step 8: the routing tokens the check enforces are exactly decision-entry.yaml's keys,
    and rejected rows are read from the contract's own text, not a hardcoded list."""

    def test_contract_parity_added_fifth_routing_row_is_accepted_with_no_code_change(self, tmp_path: Path) -> None:
        contract = (
            _CONTRACT_YAML
            + "metadata_envelope:\n  required_fields: [number, significance]\n"
            + "significance:\n  routing:\n    numbered_decision: A durable commitment.\n"
            "    cd_state_flip: A CD flip.\n    operational_fact: A fact. Never a numbered Decision.\n"
            "    field_semantics: A field. Never a numbered Decision.\n"
            "    fifth_row: A brand-new row not hardcoded anywhere in the check.\n"
        )
        _write_contract(tmp_path, contract)
        _write_decisions_md(
            tmp_path,
            "## Decision 5: Fifth row (Decided)\n\n"
            "```yaml\nnumber: 5\nsignificance:\n  value: fifth_row\n  justification: reason.\n```\n\n"
            "**Status:** Decided\n\n**Date:** 2026-07-16\n\n**Decision:** X.\n",
        )
        failed: list[str] = []
        validate_decision_entry_conformance(failed, root=tmp_path, baseline_reader=lambda r: set())
        assert failed == []

    def test_contract_parity_derived_token_set_matches_contract_keys(self, tmp_path: Path) -> None:
        data = yaml.safe_load(_SIGNIFICANCE_YAML)
        routing = data["significance"]["routing"]
        rejected = {k for k, v in routing.items() if "Never a numbered Decision" in v}
        assert rejected == {"operational_fact", "field_semantics"}
        assert set(routing) - rejected == {"numbered_decision", "cd_state_flip"}


class TestRealContractIntegration:
    """Sanity check against the REAL decision-entry.yaml, not a test-local assumption."""

    def test_real_contract_required_markers_are_status_date_decision(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        contract_path = repo_root / "docs" / "contracts" / "decision-entry.yaml"
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert data["required_markers"] == ["Status", "Date", "Decision"]

    def test_real_contract_metadata_envelope_declares_significance_as_required(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        contract_path = repo_root / "docs" / "contracts" / "decision-entry.yaml"
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert "significance" in data["metadata_envelope"]["required_fields"]
        assert "Significance" not in data["required_markers"]

    def test_real_repo_conformance_check_is_green(self) -> None:
        failed: list[str] = []
        validate_decision_entry_conformance(failed)
        assert failed == []


class TestDefaultBaselineDecisionNumbersRealRepo:
    """Covers the reachable-origin/main branch of the shared baseline reader (every test above
    bypasses it via the injected baseline_reader= seam) against a real throwaway repo."""

    def test_reads_decision_numbers_from_both_files_at_origin_main_ref(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_contract(repo, _CONTRACT_YAML)
        _write_decisions_md(
            repo,
            "## Decision 1: Base entry (Decided)\n\n**Status:** Decided\n",
            archive_content="## Decision 2: Archive entry (Decided)\n\n**Status:** Decided\n",
        )
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        assert _default_baseline_decision_numbers(repo) == {1, 2}


class TestCurrentDecisionEntriesHelper:
    """_current_decision_entries' two continue branches: a missing file and a cross-file dup."""

    def test_missing_archive_file_is_skipped(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "DECISIONS.md").write_text("## Decision 1: Only live (Decided)\n\n**Status:** Decided\n", encoding="utf-8")
        # DECISIONS_ARCHIVE.md deliberately not created (unlike _write_decisions_md, which
        # always writes both files even with an empty archive_content).
        entries = _current_decision_entries(tmp_path)
        assert set(entries) == {1}

    def test_duplicate_number_across_files_keeps_first(self, tmp_path: Path) -> None:
        _write_decisions_md(
            tmp_path,
            "## Decision 1: Live version (Decided)\n\n**Status:** Decided\n",
            archive_content="## Decision 1: Archive version (Decided)\n\n**Status:** Decided\n",
        )
        entries = _current_decision_entries(tmp_path)
        assert "Live version" in entries[1]
        assert "Archive version" not in entries[1]


class TestLoadRequiredMarkersMalformedYaml:
    def test_malformed_yaml_fails_with_parse_error_message(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, contract_text="required_markers: [unclosed\n")
        failed: list[str] = []
        result = _load_required_markers(tmp_path, failed)
        assert result is None
        assert len(failed) == 1
        assert "could not parse" in failed[0]


class TestLoadBlockSeparatorFormHelper:
    """Direct unit coverage of _load_block_separator_form's tolerated-absence branches."""

    def test_missing_contract_file_returns_none(self, tmp_path: Path) -> None:
        assert _load_block_separator_form(tmp_path) is None

    def test_malformed_yaml_returns_none(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, contract_text="block_separator: [unclosed\n")
        assert _load_block_separator_form(tmp_path) is None

    def test_non_dict_contract_returns_none(self, tmp_path: Path) -> None:
        _write_contract(tmp_path, contract_text="- just\n- a\n- list\n")
        assert _load_block_separator_form(tmp_path) is None
