"""Mirror test for scripts/checks/decisions/append_only_rule.py (rec-5266): Decision 177 cl.1-4 as a pure comparison.

Ports every classification case of the retired guard's tests (a50ee8de
tests/checks/decisions/test_validate_live_entry_immutability.py) onto compare(), each FAIL paired with its nearest PASS,
with positive assertions on the flagged (decision, kind) pairs, then covers the advisory WARN check and the
no-waiver-route properties.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from scripts.checks.decisions import append_only_rule as mod
from scripts.checks.decisions.append_only_rule import compare, validate_decision_append_only

_LIVE = "docs/DECISIONS.md"
_ARCHIVE = "docs/DECISIONS_ARCHIVE.md"

_BASELINE_BODY = """## Decision 1: A ratified entry (Decided)

**Status:** Decided
**Date:** 2026-01-01

**Problem:**
The original problem statement.

**Decision:**
The original ruling, clause one.
The original ruling, clause two.

**Related:** Decision 2.

---
"""

_STANZA_BODY = """## Decision 3: An entry with a monitored stanza (Decided)

**Status:** Decided
**Date:** 2026-01-01

**Decision:**
A ruling.

```yaml reversal-conditions
decision: 3
review_by: 2026-06-01
on_trigger: "re-decide via /plan; update or re-arm this stanza"
conditions:
  - id: some-condition
    kind: manual
    description: "A condition."
```

**Related:** Decision 1.

---
"""


def _run(tmp_path: Path, baseline: dict[str, dict[int, str]], current: dict[str, str]) -> list:
    """compare() the baseline maps against `current` corpus text; returns the (decision, kind, line) findings."""
    from scripts.decisions_md import iter_decision_sections

    def parse(text: str) -> dict[int, str]:
        return {int(m.group(1)): block for m, block in iter_decision_sections(text)}

    return compare(baseline[_LIVE], baseline[_ARCHIVE], parse(current.get(_LIVE, "")), parse(current.get(_ARCHIVE, "")))


def _baseline(live: dict[int, str] | None = None, archive: dict[int, str] | None = None) -> dict:
    return {_LIVE: live or {}, _ARCHIVE: archive or {}}


class TestBranchOneAppendOnlyFailures:
    """(i) Live, not newly superseded: exact-line append-only. These must all FAIL."""

    def test_destructive_line_edit_fails(self, tmp_path: Path) -> None:
        mutated = _BASELINE_BODY.replace("The original ruling, clause one.", "The REWRITTEN ruling, clause one.")
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated})
        assert failed
        assert [(d, kind) for d, kind, _ in failed] == [(1, "embedding")]

    def test_line_deletion_fails(self, tmp_path: Path) -> None:
        mutated = _BASELINE_BODY.replace("The original ruling, clause two.\n", "")
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated})
        assert failed

    def test_deleting_one_of_two_duplicate_lines_fails(self, tmp_path: Path) -> None:
        """Injective matching: N identical baseline lines demand N identical current lines."""
        dup_baseline = _BASELINE_BODY.replace(
            "**Problem:**\nThe original problem statement.\n",
            "**Problem:**\nRepeated line.\nRepeated line.\n",
        )
        dup_current = dup_baseline.replace("Repeated line.\nRepeated line.\n", "Repeated line.\n")
        failed = _run(tmp_path, _baseline(live={1: dup_baseline}), {_LIVE: dup_current})
        assert failed

    def test_line_join_fails(self, tmp_path: Path) -> None:
        mutated = _BASELINE_BODY.replace(
            "The original ruling, clause one.\nThe original ruling, clause two.",
            "The original ruling, clause one. The original ruling, clause two.",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated})
        assert failed

    def test_mid_line_splice_fails(self, tmp_path: Path) -> None:
        """A retired micro-shape: corrections land as their own dated line, never spliced in."""
        mutated = _BASELINE_BODY.replace(
            "The original ruling, clause one.",
            "The original ruling, clause one (as corrected 2026-08-25).",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated})
        assert failed

    def test_line_reorder_fails(self, tmp_path: Path) -> None:
        mutated = _BASELINE_BODY.replace(
            "The original ruling, clause one.\nThe original ruling, clause two.",
            "The original ruling, clause two.\nThe original ruling, clause one.",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated})
        assert failed


class TestBranchOneAppendOnlyPasses:
    """(i) The legitimate shapes: these must all PASS."""

    def test_suffix_appended_annotation_passes(self, tmp_path: Path) -> None:
        appended = _BASELINE_BODY + "\n[Amendment 2026-08-25, operator: a dated clarification.]\n"
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: appended})
        assert failed == []

    def test_mid_body_inserted_annotation_passes(self, tmp_path: Path) -> None:
        inserted = _BASELINE_BODY.replace(
            "**Related:** Decision 2.",
            "> **Update (2026-08-25):** a mid-body dated note.\n\n**Related:** Decision 2.",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: inserted})
        assert failed == []

    def test_in_intent_accretion_passes(self, tmp_path: Path) -> None:
        """Decision 151 clause 3(ii): an Intent-section accretion is an insertion, not an edit."""
        with_intent = _BASELINE_BODY.replace(
            "**Problem:**",
            "**Intent:**\nThe durable why.\n\n**Problem:**",
        )
        accreted = with_intent.replace(
            "The durable why.\n",
            "The durable why.\n[Amendment 2026-08-25, operator: a narrowing clarification.]\n",
        )
        failed = _run(tmp_path, _baseline(live={1: with_intent}), {_LIVE: accreted})
        assert failed == []

    def test_trailing_whitespace_only_difference_passes(self, tmp_path: Path) -> None:
        mutated = _BASELINE_BODY.replace(
            "The original ruling, clause one.",
            "The original ruling, clause one.   ",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated})
        assert failed == []

    def test_baseline_absent_new_entry_passes(self, tmp_path: Path) -> None:
        """A brand-new entry has no baseline body to preserve -- conformance owns its shape."""
        new_entry = _BASELINE_BODY.replace("Decision 1:", "Decision 9:")
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: _BASELINE_BODY + new_entry})
        assert failed == []


class TestStanzaCarveOut:
    """The one monitored-surface carve-out: interiors free, existence locked."""

    def test_stanza_interior_edit_passes(self, tmp_path: Path) -> None:
        rearmed = _STANZA_BODY.replace("review_by: 2026-06-01", "review_by: 2026-12-01").replace(
            'description: "A condition."', 'description: "A re-armed condition."'
        )
        failed = _run(tmp_path, _baseline(live={3: _STANZA_BODY}), {_LIVE: rearmed})
        assert failed == []

    def test_stanza_deletion_fails(self, tmp_path: Path) -> None:
        start = _STANZA_BODY.index("```yaml reversal-conditions")
        end = _STANZA_BODY.index("```\n", _STANZA_BODY.index("conditions:")) + len("```\n")
        stripped = _STANZA_BODY[:start] + _STANZA_BODY[end:]
        failed = _run(tmp_path, _baseline(live={3: _STANZA_BODY}), {_LIVE: stripped})
        assert failed

    def test_prose_edit_outside_stanza_still_fails(self, tmp_path: Path) -> None:
        """The carve-out is scoped to the stanza -- it never licenses edits elsewhere."""
        mutated = _STANZA_BODY.replace("A ruling.", "A REWRITTEN ruling.")
        failed = _run(tmp_path, _baseline(live={3: _STANZA_BODY}), {_LIVE: mutated})
        assert failed

    def test_metadata_envelope_fence_is_not_carved_out(self, tmp_path: Path) -> None:
        """A bare ```yaml block is the Decision 167 envelope, NOT the monitored stanza -- editing
        it is a body edit and must fail."""
        enveloped = """## Decision 5: Enveloped entry (Decided)

```yaml
number: 5
status: Decided
```

**Status:** Decided

---
"""
        mutated = enveloped.replace("status: Decided\n```", "status: Superseded\n```")
        failed = _run(tmp_path, _baseline(live={5: enveloped}), {_LIVE: mutated})
        assert failed


class TestStanzaCarveOutCannotBecomeABypass:
    """Regression suite for the carve-out's two bypass holes (code-review H-1 + M-1).

    Both were live and verified before the fix: a fence spelling WIDER than the owning parser's
    exempted a span nothing else policed, and an UNTERMINATED fence lifted every following line
    out of the lock's jurisdiction. Either one let an author rewrite a ratified body with the
    lock silent -- which would have made the ratifying Decision's no-waiver claim false.
    """

    _RULING = "ORIGINAL ratified ruling that must never change."

    def _body(self, fence: str | None, *, close: bool) -> str:
        parts = ["## Decision 4: Probe (Decided)", "", "**Status:** Decided", ""]
        if fence is not None:
            parts += [fence, "decision: 4"]
            if close:
                parts.append("```")
        parts += ["", "**Decision:**", self._RULING, "", "---", ""]
        return "\n".join(parts)

    def test_lock_and_owning_parser_agree_on_every_fence_spelling(self) -> None:
        """The exempted set must equal the monitored set BY CONSTRUCTION -- the property the
        import of the owner's regex buys, and the one a re-spelled local pattern would lose."""
        from scripts.preflight.decision_conditions import _extract_stanza

        for fence in (
            "```yaml reversal-conditions",
            "```yaml  reversal-conditions",
            "```yaml\treversal-conditions",
            "```yaml reversal-conditions  ",
            "```yaml",
        ):
            body = self._body(fence, close=True)
            owner_sees = _extract_stanza(body)[0]
            assert mod.has_reversal_stanza(body) == owner_sees, f"divergence on fence {fence!r}"

    def test_wider_fence_spelling_cannot_shelter_a_destructive_edit(self, tmp_path: Path) -> None:
        """H-1: a two-space fence used to be carved out by the lock but invisible to the owner."""
        baseline = self._body("```yaml  reversal-conditions", close=False)
        tampered = baseline.replace(self._RULING, "REWRITTEN -- destroyed.")
        failed = _run(tmp_path, _baseline(live={4: baseline}), {_LIVE: tampered})
        assert failed, "a wider-than-owner fence spelling sheltered a destructive edit"

    def test_unterminated_fence_grants_no_exemption(self, tmp_path: Path) -> None:
        """M-1: even a CORRECTLY spelled fence grants nothing if it never closes."""
        baseline = self._body("```yaml reversal-conditions", close=False)
        tampered = baseline.replace(self._RULING, "REWRITTEN -- destroyed.")
        failed = _run(tmp_path, _baseline(live={4: baseline}), {_LIVE: tampered})
        assert failed, "an unterminated fence lifted the body out of the lock's jurisdiction"

    def test_unterminated_fence_keeps_its_own_lines_in_the_comparison(self) -> None:
        body = self._body("```yaml reversal-conditions", close=False)
        prepared = mod.prepare_lines(body)
        assert "```yaml reversal-conditions" in prepared
        assert "decision: 4" in prepared
        assert self._RULING in prepared

    def test_closed_fence_still_exempts_its_interior(self) -> None:
        """The carve-out must survive the hardening -- a well-formed stanza is still exempt."""
        body = self._body("```yaml reversal-conditions", close=True)
        prepared = mod.prepare_lines(body)
        assert "decision: 4" not in prepared
        assert "```yaml reversal-conditions" not in prepared
        assert self._RULING in prepared


class TestBranchTwoNewlySuperseded:
    """(ii) A Status flip to Superseded demands the strict stub shape."""

    _STUB = """## Decision 1: A ratified entry (Decided)

**Status:** Superseded
**Date:** 2026-08-25

**Decision:** Superseded by Decision 7; see that entry.

**Superseded by: Decision 7**

---
"""

    def test_strict_stub_passes(self, tmp_path: Path) -> None:
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: self._STUB})
        assert failed == []

    def test_pointer_bearing_full_body_status_flip_fails(self, tmp_path: Path) -> None:
        """The round-3 governance finding: a full body carrying BOTH supersession markers passes
        is_compacted_stub, so the shape bound -- not marker presence -- is what closes the bypass."""
        bypass = _BASELINE_BODY.replace("**Status:** Decided", "**Status:** Superseded").replace(
            "**Related:** Decision 2.",
            "**Superseded by: Decision 7**\n\n**Related:** Decision 2.",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: bypass})
        assert failed

    def test_stub_retaining_one_stray_prose_line_fails(self, tmp_path: Path) -> None:
        leaky = self._STUB.replace(
            "**Superseded by: Decision 7**",
            "A lingering rationale sentence.\n\n**Superseded by: Decision 7**",
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: leaky})
        assert failed


class TestBranchThreeArchiveMove:
    """(iii) Archive moves embed the baseline body; relocation is not a content escape."""

    def test_conforming_move_passes(self, tmp_path: Path) -> None:
        moved = _BASELINE_BODY.replace(
            "## Decision 1: A ratified entry (Decided)",
            "## Decision 1: A ratified entry (Superseded, archived 2026-08-25)",
        ).replace("**Status:** Decided", "**Status:** Superseded -- archived")
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: "", _ARCHIVE: moved})
        assert failed == []

    def test_move_with_body_rewrite_fails(self, tmp_path: Path) -> None:
        moved = (
            _BASELINE_BODY.replace(
                "## Decision 1: A ratified entry (Decided)",
                "## Decision 1: A ratified entry (Superseded, archived 2026-08-25)",
            )
            .replace("**Status:** Decided", "**Status:** Superseded -- archived")
            .replace("The original ruling, clause two.", "A rewritten clause two.")
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: "", _ARCHIVE: moved})
        assert failed

    def test_move_dropping_a_body_line_fails(self, tmp_path: Path) -> None:
        moved = _BASELINE_BODY.replace("**Status:** Decided", "**Status:** Superseded -- archived").replace(
            "**Problem:**\nThe original problem statement.\n", ""
        )
        failed = _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: "", _ARCHIVE: moved})
        assert failed


class TestArchiveMoveStanzaPresence:
    def test_move_that_drops_the_stanza_fails(self, tmp_path: Path) -> None:
        """Stanza existence survives an archive move, exactly as it does in place."""
        baseline = _STANZA_BODY
        start = baseline.index("```yaml reversal-conditions")
        end = baseline.index("```\n", baseline.index("conditions:")) + len("```\n")
        moved = (
            (baseline[:start] + baseline[end:])
            .replace(
                "## Decision 3: An entry with a monitored stanza (Decided)",
                "## Decision 3: An entry with a monitored stanza (Superseded, archived)",
            )
            .replace("**Status:** Decided", "**Status:** Superseded -- archived")
        )
        failed = _run(tmp_path, _baseline(live={3: baseline}), {_LIVE: "", _ARCHIVE: moved})
        assert failed


class TestStubShapeHelper:
    def test_block_without_a_heading_line_is_reported_as_missing_header(self) -> None:
        assert mod._stub_shape_violation("**Status:** Superseded\n") == "missing '## Decision N:' header line"

    def test_empty_block_is_reported_as_missing_header(self) -> None:
        assert mod._stub_shape_violation("") == "missing '## Decision N:' header line"


class TestFindingKinds:
    """Each kind is reported with the shape the census depends on: line for embedding, None otherwise."""

    def test_embedding_names_the_first_unmatched_baseline_line(self, tmp_path: Path) -> None:
        mutated = _BASELINE_BODY.replace("The original ruling, clause one.", "The REWRITTEN ruling, clause one.")
        assert _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: mutated}) == [
            (1, "embedding", "The original ruling, clause one.")
        ]

    def test_vanished_number_is_reported_without_a_line(self, tmp_path: Path) -> None:
        assert _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {}) == [(1, "vanished", None)]

    def test_stanza_loss_is_reported_without_a_line(self, tmp_path: Path) -> None:
        start = _STANZA_BODY.index("```yaml reversal-conditions")
        end = _STANZA_BODY.index("```\n", _STANZA_BODY.index("conditions:")) + len("```\n")
        stripped = _STANZA_BODY[:start] + _STANZA_BODY[end:]
        assert _run(tmp_path, _baseline(live={3: _STANZA_BODY}), {_LIVE: stripped}) == [(3, "stanza", None)]

    def test_stub_violation_is_reported_without_a_line(self, tmp_path: Path) -> None:
        bypass = _BASELINE_BODY.replace("**Status:** Decided", "**Status:** Superseded")
        assert _run(tmp_path, _baseline(live={1: _BASELINE_BODY}), {_LIVE: bypass}) == [(1, "stub", None)]


def _write_corpus(root: Path, live: str, archive: str = "") -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "DECISIONS.md").write_text(live, encoding="utf-8")
    (docs / "DECISIONS_ARCHIVE.md").write_text(archive, encoding="utf-8")


class TestWarnCheck:
    def test_rewritten_ratified_line_warns_and_never_fails(self, tmp_path: Path, capsys) -> None:
        _write_corpus(tmp_path, _BASELINE_BODY.replace("clause one.", "REWRITTEN clause one."))
        failed: list[str] = []
        validate_decision_append_only(
            failed, root=tmp_path, baseline_body_reader=lambda _r: _baseline(live={1: _BASELINE_BODY})
        )
        assert failed == []
        assert "WARN: Decision 1 embedding" in capsys.readouterr().out

    def test_append_only_edit_prints_no_warn(self, tmp_path: Path, capsys) -> None:
        _write_corpus(tmp_path, _BASELINE_BODY + "\n> **Update (2026-11-19):** a note.\n")
        failed: list[str] = []
        validate_decision_append_only(
            failed, root=tmp_path, baseline_body_reader=lambda _r: _baseline(live={1: _BASELINE_BODY})
        )
        out = capsys.readouterr().out
        assert failed == [] and "  WARN:" not in out and "PASS" in out

    def test_unreachable_baseline_skips(self, tmp_path: Path, capsys) -> None:
        _write_corpus(tmp_path, _BASELINE_BODY)
        failed: list[str] = []
        validate_decision_append_only(failed, root=tmp_path, baseline_body_reader=lambda _r: None)
        assert failed == [] and "SKIP" in capsys.readouterr().out

    def test_absent_archive_file_is_treated_as_empty(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DECISIONS.md").write_text(_BASELINE_BODY, encoding="utf-8")
        assert mod._current_entries(tmp_path, _ARCHIVE) == {}


class TestNoWaiverRoute:
    """The rule reads no config and exposes no escape hatch (Decision 163 anti-pattern; Decision 177 cl.5)."""

    def test_module_source_contains_no_waiver_or_allowlist_vocabulary(self) -> None:
        source = inspect.getsource(mod)
        body = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
        lowered = body.lower()
        for token in ("waiver", "allowlist", "raise-approved", "grandfather", "exempt_file", "skip_list"):
            assert token not in lowered, f"waiver-shaped vocabulary {token!r} present in the rule's code"

    def test_module_imports_no_config_or_yaml_loader(self) -> None:
        source = inspect.getsource(mod)
        assert "import yaml" not in source
        assert "yaml.safe_load" not in source
        assert "config/" not in source

    def test_check_signature_exposes_no_waiver_parameter(self) -> None:
        assert set(inspect.signature(validate_decision_append_only).parameters) == {"failed", "root", "baseline_body_reader"}


class TestBaselineAlreadySupersededIsSkipped:
    def test_edit_of_a_body_superseded_at_baseline_yields_no_finding(self, tmp_path: Path) -> None:
        superseded = _BASELINE_BODY.replace("**Status:** Decided", "**Status:** Superseded")
        edited = superseded.replace("clause one.", "changed.")
        assert _run(tmp_path, _baseline(live={1: superseded}), {_LIVE: edited}) == []
