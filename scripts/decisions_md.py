"""Utilities for parsing DECISIONS.md and reading local JSONL log files.

Extracted from backfill_ops_tables.py (deleted in dq-ops-rec-enforcement).

DAF-01 parity pass (PLAN-daf-etl-parity-fidelity): raw_block + content_hash carry the full
heading-inclusive section text as a parity backstop; reversal_conditions and superseded_by
are typed extractions; the Decision-marker regex tolerates decorated markers (e.g.
"**Decision (four invariants):**"); the related-decisions extractor accepts plural
"Decisions 69/78" cites and dedupes; the decided_date Status-suffix fallback is restricted
to ISO-shaped values.

DAF-03 shared-parser consolidation (PLAN-daf-authoring-grammar, Decision 134 cl.3): adds
iter_decision_headings() and decision_header_numbers() as the single public header-parsing
surface every structural consumer imports (the R1 ratification guard, the preflight
open-decisions counter, the forward conformance check) -- both built on the pre-existing
_DECISION_HEADING_RE grammar, never a rework of parse_decisions_md or the byte-reconstruction
invariant. See docs/contracts/decision-entry.yaml for the authoring grammar these enforce.

DCG-06 intent capture (PLAN-dcg-intent-capture, Decision 151): adds an `intent` key, a NEW
separate typed extraction via _extract_multiline_section(body, "Intent") -- the existing
`context` extraction (Rationale/Key details/Context) is untouched. Optional forever: no
required-marker change, no retro-enforcement of the historical band.

DCG-08 decisions index (PLAN-dcg-decisions-index): adds the public extract_amends_edges()
(forward "amends Decision N" title-relation extraction) plus the private
_extract_title_borne_supersedes() sibling, both built on the shared
_extract_title_relation_targets() whitelist-continuation grammar -- never a relation-keyword
blocklist, so a non-Decision target (CD.N, KG.N, prose like "Identity Center") is excluded
structurally rather than enumerated. Surfaces two INDEX-ONLY parse_decisions_md row keys,
"amends" and "title_supersedes", consumed solely by scripts/decisions_index.py -- neither key
is added to _DECISION_BACKFILL_COLS, DecisionPayload, or config/agent/data_quality (Decision
84 warehouse-safety boundary, mirrors the intent key's index-vs-warehouse separation).

Decision-entry flow governance (PLAN-decision-entry-flow-governance, Decision 167): adds
extract_entry_envelope(), a public reader for the optional typed metadata envelope -- a fenced
```yaml block immediately after a '## Decision N:' heading (docs/contracts/decision-entry.yaml
metadata_envelope). parse_decisions_md() prefers envelope values for status / decided_date /
amends / title_supersedes over the legacy bold-marker/title grammar, which remains the fallback
for every entry with no envelope (forward-only; the historical band is untouched, since none of
it carries one). Surfaces a new `significance` row key (the envelope's routing claim dict, or {}
when absent) -- INDEX-ONLY like amends/title_supersedes: never added to _DECISION_BACKFILL_COLS,
DecisionPayload, or the warehouse projection (Decision 166 point 9).

Decision-corpus currency (PLAN-decision-corpus-currency, rec-3055/rec-3056): promotes
is_compacted_stub() (formerly private to scripts.checks.decisions.
validate_decision_entry_conformance, now a module-level alias back to it) to public status here,
alongside its status-half bold_status()/status_is_superseded() and the new derive_currency() --
the single derivation scripts.decisions_index projects as each live row's `currency` field and
scripts.checks.decisions.validate_decision_currency enforces (DAF-03: one derivation, several
consumers, never a re-derived regex).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent

_DECISIONS_MD_PATHS = [
    _REPO_ROOT / "docs" / "DECISIONS.md",
    _REPO_ROOT / "docs" / "DECISIONS_ARCHIVE.md",
]

_DECISION_HEADING_RE = re.compile(
    r"^#{2,3}\s+Decision\s+(\d+):\s*(.+)$",
    re.MULTILINE,
)

# ISO-shaped date prefix (YYYY-MM or YYYY-MM-DD). The decided_date Status-suffix fallback is
# restricted to this shape (DAF-01 fix: dec-067's fallback previously harvested the clause
# status line "remove when reversal condition is met" as a "date").
_ISO_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?")

# Canonical inline superseded-by marker (Decision 134 clause 4 / Plan 3 will formalize this
# spelling in docs/contracts/decision-entry.yaml -- keep it EXACTLY consistent):
#   **Superseded by: Decision N**
# Best-effort tolerance for the historical band's differently-shaped marker:
#   **Superseded by:** Decision N
_SUPERSEDED_BY_RE = re.compile(
    r"\*\*Superseded by:\s*Decision\s+(\d+)\*\*"
    r"|\*\*Superseded by:\*\*\s*Decision\s+(\d+)"
)

# The decision-body marker, tolerating decorated forms (e.g. "**Decision (four invariants):**")
# per Decision 134 clause 4 / audit finding DAF-01. Never widen past "Decision" as the leading
# word -- "**Decision Required By:**" (a different, non-decision-body field used in unheaded
# candidate sections) does not collide because parse_decisions_md only walks "## Decision N:"
# headed blocks.
_DECISION_MARKER_BODY = r"Decision\b[^:]*"


# Terminator sets for typed-field extraction, named per docs/contracts/decision-entry.yaml's
# field_grammar section (CFG-06 class fix, audits/contract-first-governance-33c8667.yaml): the
# contract is the authoritative statement of these regexes -- tests/test_decision_field_grammar.py
# pins both constants against it, so a future divergence fails CI instead of silently corrupting a
# warehouse column.
#
# _MARKER_SECTION_TERMINATOR is the PROSE set (Problem/Intent/Rationale/Reversal conditions, via
# _extract_by_marker_pattern and _extract_section): a following bold marker, a --- block
# separator, or end-of-block/file. It deliberately does NOT stop at an amendment-annotation
# opener (Decision 151 clause 3(ii)): a dated in-section accretion inside an Intent (or other
# prose) section must remain part of the typed value, or a future clarification is silently
# truncated out of the corpus.
_MARKER_SECTION_TERMINATOR = r"(?=\n\*\*\w|\n---|\Z)"

# _TYPED_ID_LIST_TERMINATOR is the prose set PLUS the two amendment-trailer openers -- the
# non-blockquote "[Amendment " form and the GENERAL blockquote-bold-annotation form "\n> \*\*"
# (never a closed list of observed openers: enumerating spellings is the anti-pattern CFG-06
# closes -- two enumeration attempts during planning each missed a further shape). Used ONLY by
# _extract_related_decisions, so a trailer placed after a typed id-list field never contributes
# its cited ids to that field (Decision 146's own trailer naming Decision 160 is a live
# regression case this closes -- rec-2990).
_TYPED_ID_LIST_TERMINATOR = r"(?=\n\*\*\w|\n---|\n\[Amendment |\n> \*\*|\Z)"


def _extract_by_marker_pattern(text: str, marker_body_pattern: str) -> str:
    """Extract the text following a bold marker whose inner span matches marker_body_pattern.

    marker_body_pattern is a raw (unescaped) regex fragment for the text between "**" and
    ":**" -- e.g. re.escape("Problem") for an exact key, or r"Decision\\b[^:]*" to tolerate
    decorated markers. Tries the multiline (marker on its own line, body on following lines)
    form first, then the inline (marker and body on the same line) form.
    """
    full_marker = rf"\*\*{marker_body_pattern}:\*\*"
    multi = re.search(full_marker + r"\s*\n(.*?)" + _MARKER_SECTION_TERMINATOR, text, re.DOTALL)
    if multi:
        return multi.group(1).strip()
    inline = re.search(full_marker + r"\s*(.+?)" + _MARKER_SECTION_TERMINATOR, text, re.DOTALL)
    if inline:
        return inline.group(1).strip()
    return ""


def _extract_section(text: str, *keys: str) -> str:
    for key in keys:
        single = re.search(
            rf"\*\*{re.escape(key)}:\*\*\s*(.+?)" + _MARKER_SECTION_TERMINATOR,
            text,
            re.DOTALL,
        )
        if single:
            return single.group(1).strip()
    return ""


def _extract_multiline_section(text: str, *keys: str) -> str:
    for key in keys:
        result = _extract_by_marker_pattern(text, re.escape(key))
        if result:
            return result
    return ""


def _extract_decision_text(text: str) -> str:
    """Extract the decision body, tolerating decorated Decision markers (DAF-01 fix)."""
    return _extract_by_marker_pattern(text, _DECISION_MARKER_BODY)


def _extract_related_decisions(text: str) -> list[int]:
    """Extract related decision numbers, deduped, preserving first-occurrence order.

    Accepts both the singular "Decision N" form and the plural "Decisions N/M" form (DAF-01
    fix -- the plural form was previously invisible to the singular-only regex, e.g.
    "Decisions 69/78" in Decision 87's Related line).
    """
    m = re.search(r"\*\*Related:\*\*(.+?)" + _TYPED_ID_LIST_TERMINATOR, text, re.DOTALL)
    if not m:
        return []
    related_text = m.group(1)
    seen: set[int] = set()
    result: list[int] = []
    for cite in re.finditer(r"Decisions?\s+(\d+(?:\s*[/,]\s*\d+)*)", related_text):
        for n_str in re.findall(r"\d+", cite.group(1)):
            n = int(n_str)
            if n not in seen:
                seen.add(n)
                result.append(n)
    return result


def _extract_decided_date(text: str) -> str:
    date_m = re.search(r"\*\*Date:\*\*\s*(.+)", text)
    if date_m:
        return date_m.group(1).strip()
    status_m = re.search(r"\*\*Status:\*\*.*?--\s*(.+)", text)
    if status_m:
        candidate = status_m.group(1).strip()
        if _ISO_DATE_PREFIX_RE.match(candidate):
            return candidate
    return ""


def extract_superseded_by(text: str) -> str:
    """Extract a superseded-by target as 'dec-NNN', best-effort for historical prose.

    Public per DAF-03 consolidation (PLAN-size-gov-marker-guard): the Decision 149
    supersession-hop authorization check in scripts/checks/_marker_guard.py reaches across a
    module boundary for this symbol, which is exactly the case the promotion pattern (see
    iter_decision_sections below) exists to cover instead of a private-symbol reach-across.
    """
    m = _SUPERSEDED_BY_RE.search(text)
    if not m:
        return ""
    n = m.group(1) or m.group(2)
    return f"dec-{int(n):03d}"


# Private alias retained so existing internal callers (this module's own parse_decisions_md)
# keep working unchanged.
_extract_superseded_by = extract_superseded_by


# The bold **Status:** marker regex -- promoted here from scripts.checks.decisions.
# validate_decision_entry_conformance (PLAN-decision-corpus-currency, DAF-03) so the status
# derivation it and scripts.checks.decisions.validate_decision_currency both need has exactly
# one definition.
_BOLD_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*(.+)")


def bold_status(block: str) -> str:
    """Normalized **Status:** marker value from a heading-inclusive raw block.

    The FIRST match of _BOLD_STATUS_RE, its captured group split on '--' and stripped (the
    same normalization the pre-promotion _is_compacted_stub used). Returns "" when block carries
    no bold Status marker.

    Public (DAF-03 / PLAN-decision-corpus-currency): the single shared status-marker VALUE
    derivation. status_is_superseded and is_compacted_stub below build on it; so does
    scripts.checks.decisions.validate_decision_currency's invariant I4, which needs the VALUE
    (not a boolean) to prefix-test 'Reversed'/'Deferred'. Re-implementing this regex anywhere
    else is exactly the divergence DAF-03 closes.
    """
    m = _BOLD_STATUS_RE.search(block)
    if not m:
        return ""
    return m.group(1).strip().split("--")[0].strip()


def status_is_superseded(block: str) -> bool:
    """True iff block's bold_status() value is exactly 'Superseded'.

    Public (PLAN-decision-corpus-currency): imported by name in the currency derivation's
    graduated verification step, so it must exist as a stable public symbol, not merely be
    inlined into is_compacted_stub.
    """
    return bold_status(block) == "Superseded"


def is_compacted_stub(block: str) -> bool:
    """A Decision 149 compacted stub: '**Status:** Superseded' plus a live '**Superseded by:
    Decision N**' pointer -- the fixed shape compaction.stub_grammar mandates.

    Public (DAF-03 / PLAN-decision-corpus-currency): promoted BEHAVIOUR-VERBATIM from
    scripts.checks.decisions.validate_decision_entry_conformance._is_compacted_stub, which is
    now a module-level alias to this function -- one definition, two consumers (that check
    module and scripts.decisions_index's currency derivation), never a second implementation.
    """
    return status_is_superseded(block) and bool(extract_superseded_by(block))


def derive_currency(row: dict, inbound_supersedes: set[int], inbound_amends: set[int]) -> str:
    """Derive one of the four currency tokens for a live decision row.

    row is a parser row from parse_decisions_md() (needs 'raw_block', which the index
    projection deliberately excludes). inbound_supersedes / inbound_amends are corpus-wide sets
    -- built ONCE per scripts.decisions_index.build_index() call from its own supersedes/amends
    maps -- never recomputed per row; a row's membership in either set is the only per-row test
    this function performs against them.

    Precedence (first match wins):
      1. 'superseded_compacted' -- row is a compacted stub (is_compacted_stub).
      2. 'superseded_pointer' -- row carries a victim-side superseded_by, OR its number is the
         target of an inbound supersedes edge (title_supersedes/envelope-supersedes from
         another entry).
      3. 'amended' -- row's number is the target of an inbound amends edge, OR its bold status
         marker begins with 'Amended' (the residual status-prose band, empty since Decision 246 compacted Decision 67).
      4. 'current' -- none of the above.

    Both the status_is_superseded/is_compacted_stub checks above and the 'amended' bold-marker
    check here read the BOLD marker on raw_block, never row['status'] -- parse_decisions_md
    prefers the metadata envelope over the bold marker, so the two can diverge on any enveloped
    entry; the bold marker is the corpus-wide spelling and the only one the historical
    status-prose 'amended' band carries.
    """
    block = row.get("raw_block", "")
    number = row["decision_id"]
    if is_compacted_stub(block):
        return "superseded_compacted"
    if row.get("superseded_by") or number in inbound_supersedes:
        return "superseded_pointer"
    if number in inbound_amends or bold_status(block).startswith("Amended"):
        return "amended"
    return "current"


# DCG-08 (PLAN-dcg-decisions-index) title-relation extraction, shared by the public
# extract_amends_edges() and the private _extract_title_borne_supersedes(). Whitelist-
# continuation grammar, not a relation-keyword blocklist: after the relation word, the FIRST
# target must be an immediately-adjacent "Decision(s) N" (_TITLE_RELATION_SEED_RE) -- so a
# non-Decision target (CD.N, KG.N, prose like "Identity Center") never matches, with no need
# to enumerate every non-target word. A further target is accepted ONLY as an explicit ", N" or
# ", Decision(s) N" (_TITLE_RELATION_COMMA_RE) / "/N" (_TITLE_RELATION_SLASH_RE) plural-list
# continuation, an "and N" or "and Decision(s) N" (_TITLE_RELATION_AND_RE) continuation, or a
# "+ Decision(s) N" (_TITLE_RELATION_PLUS_RE) continuation (dec-153: "amends Decision 73
# enforcement + Decision 135") -- optionally preceded by a qualifier on the prior target
# (_TITLE_RELATION_QUALIFIER_RE: a clause qualifier, see _CLAUSE_QUALIFIER, or a single bare descriptive word
# like "enforcement"; a qualifier is consumed but is never itself a number source, and the bare
# word alternative explicitly excludes "and" so it never steals that continuation's own
# keyword). CFG-06 widened the comma and "and" continuations to accept BOTH the bare-number and
# repeated-Decision-token spellings (dec-160's real title, "amends Decision 145, Decision 134
# clause 2, and Decision 146", mixes both in one title) -- the repeated "Decision(s)" token is
# always optional, never required, on either continuation.
#
# A repeated "Decision(s)" token is unambiguous evidence on its own. A BARE number is weaker
# evidence, so CFG-06's post-merge follow-up (code-review finding, confirmed false-positive:
# "amends Decision 116 and 2027 planning cycle" wrongly added 2027) requires the bare-number
# branch of the comma/and continuations to be followed by a citation-list-safe boundary
# (_BARE_NUMBER_BOUNDARY_RE: closing punctuation, another "and", or a recognized qualifier) --
# NOT by open-ended prose. The repeated-token branch carries no such requirement. Any other
# following text -- a semicolon with no recognized qualifier, a different relation word
# ("mirrors", "extends", "builds on"), an ordinal like "2nd amendment", or (for a bare number)
# unrecognized trailing prose -- is not a valid continuation and silently ends the scan for that
# anchor occurrence.
_TITLE_RELATION_SEED_RE = re.compile(r"\s+Decisions?\s+(\d+)\b", re.IGNORECASE)
# A clause qualifier owns every number it enumerates, so a clause list is never read as Decision
# targets (dec-213: "amends Decision 81 cl.1/5/6/7, 91 cl.1" must not yield 5, 6, 7). Singular
# qualifiers (clause, cl., point, pt, section, the section sign) own ONE clause reference, widened
# only by a range ("1-2"), a slash list ("1/5/6/7") or a sub-clause suffix ("3(2)", "1(ii)").
# Plural qualifiers (clauses, points, pts, sections, double section sign) also own a comma list
# closed by at most one final "and N", so a later target after a plural qualifier must repeat the
# "Decision" token. The bare-number boundary recognises the same qualifiers.
_CLAUSE_REF = r"\d+[a-z]?(?:\([^)\s]*\))*(?:\s*[-/]\s*\d+[a-z]?(?:\([^)\s]*\))*)*"
_CLAUSE_QUALIFIER = (
    r"(?:(?:clauses|points|pts\.?|sections|\u00a7\u00a7)\s*" + _CLAUSE_REF + r"(?:\s*,\s*" + _CLAUSE_REF + r")*"
    r"(?:\s*,?\s*(?:and|&)\s*" + _CLAUSE_REF + r")?"
    r"|(?:clause|cl\.|point|pt\.?|section|\u00a7)\s*" + _CLAUSE_REF + r")"
)
_TITLE_RELATION_QUALIFIER_RE = re.compile(r"\s*(?:" + _CLAUSE_QUALIFIER + r"|(?!and\b)[A-Za-z]+)", re.IGNORECASE)
_BARE_NUMBER_BOUNDARY_RE = r"(?=[),;]|\s+(?:and\b|" + _CLAUSE_QUALIFIER + r")|\Z)"
_TITLE_RELATION_AND_RE = re.compile(
    r"\s*,?\s*and\s+(Decisions?\s+)?(\d+)\b(?(1)|" + _BARE_NUMBER_BOUNDARY_RE + r")", re.IGNORECASE
)
_TITLE_RELATION_PLUS_RE = re.compile(r"\s*\+\s*Decisions?\s+(\d+)\b", re.IGNORECASE)
_TITLE_RELATION_COMMA_RE = re.compile(r"\s*,\s*(Decisions?\s+)?(\d+)\b(?(1)|" + _BARE_NUMBER_BOUNDARY_RE + r")", re.IGNORECASE)
_TITLE_RELATION_SLASH_RE = re.compile(r"\s*/\s*(\d+)\b")

# (pattern, capture-group-index-for-the-number) in match-priority order -- AND_RE/COMMA_RE carry
# two groups (optional "Decisions?" prefix, then the digits) so their digits are group 2;
# PLUS_RE/SLASH_RE carry a single digits-only group.
_TITLE_RELATION_CONTINUATIONS: tuple[tuple[re.Pattern[str], int], ...] = (
    (_TITLE_RELATION_AND_RE, 2),
    (_TITLE_RELATION_PLUS_RE, 1),
    (_TITLE_RELATION_COMMA_RE, 2),
    (_TITLE_RELATION_SLASH_RE, 1),
)


def _extract_title_relation_targets(raw_title: str, relation_word: str) -> list[int]:
    """Forward 'RELATION_WORD Decision(s) N[, M][/M][and Decision M][+ Decision M]' targets
    from a raw (unstripped) decision title, deduped, in first-occurrence order.

    Generic helper shared by extract_amends_edges (relation_word="amends") and the internal
    _extract_title_borne_supersedes (relation_word="supersedes"). Never matches an inverse
    phrasing ("amended by Decision N", "superseded by Decision N") because those are spelled
    as different whole words ("amended"/"superseded", not "amends"/"supersedes") -- \\b-bounded
    matching on the exact relation_word excludes them with no explicit direction check needed.
    """
    anchor_re = re.compile(rf"\b{re.escape(relation_word)}\b", re.IGNORECASE)
    seen: set[int] = set()
    targets: list[int] = []

    def _add(n: int) -> None:
        if n not in seen:
            seen.add(n)
            targets.append(n)

    for anchor_m in anchor_re.finditer(raw_title):
        seed_m = _TITLE_RELATION_SEED_RE.match(raw_title, anchor_m.end())
        if not seed_m:
            continue
        _add(int(seed_m.group(1)))
        pos = seed_m.end()
        while True:
            qual_m = _TITLE_RELATION_QUALIFIER_RE.match(raw_title, pos)
            qual_end = qual_m.end() if qual_m else pos
            cont_m = None
            group_idx = 1
            for pattern, idx in _TITLE_RELATION_CONTINUATIONS:
                cont_m = pattern.match(raw_title, qual_end)
                if cont_m:
                    group_idx = idx
                    break
            if not cont_m:
                break
            _add(int(cont_m.group(group_idx)))
            pos = cont_m.end()

    return targets


def extract_amends_edges(raw_title: str) -> list[int]:
    """Forward 'amends Decision(s) N' targets from a raw (unstripped) decision title.

    Public per DCG-08 (PLAN-dcg-decisions-index) -- the decisions-index generator's typed
    amends-edge source. Operates on the RAW title (m.group(2) in parse_decisions_md, before
    the trailing-parenthetical strip that produces the stored `title` field) since the
    parenthetical carrying "(amends Decision N)" is exactly what that strip drops. Never
    matches "amended by Decision N" (the inverse direction -- a different whole word) or a
    non-Decision target (CD.N, KG.N, prose) -- see _extract_title_relation_targets.
    """
    return _extract_title_relation_targets(raw_title, "amends")


def _extract_title_borne_supersedes(raw_title: str) -> list[int]:
    """Forward 'Supersedes Decision(s) N' targets from a raw (unstripped) decision title.

    Internal counterpart to extract_amends_edges (DCG-08), reusing the same generic helper.
    Feeds the index-only 'title_supersedes' parse_decisions_md row key that
    scripts.decisions_index unions with the corpus-wide inverse of the typed superseded_by
    field to build the final 'supersedes' index edge. Never matches 'Superseded by Decision N'
    (the inverse direction -- a different whole word) or a non-Decision target (CD.N, KG.N,
    prose).
    """
    return _extract_title_relation_targets(raw_title, "supersedes")


def iter_decision_headings(content: str) -> list[re.Match[str]]:
    """Return every '## Decision N:' / '### Decision N:' heading match in content, in file order.

    Thin public wrapper over the module's single _DECISION_HEADING_RE grammar -- the shared
    header-parsing primitive every structural consumer (the R1 ratification guard, the
    preflight open-decisions counter, the forward conformance check) imports instead of
    hand-rolling its own regex (DAF-03 / Plan 3, PLAN-daf-authoring-grammar). Does not dedupe
    or sort -- callers that need decision numbers only should use decision_header_numbers().
    """
    return list(_DECISION_HEADING_RE.finditer(content))


def decision_header_numbers(paths: Optional[list[Path]] = None) -> set[int]:
    """Return the set of decision numbers headed '## Decision N:' / '### Decision N:' across paths.

    paths=None reads this module's own default targets (_DECISIONS_MD_PATHS, both DECISIONS.md
    and DECISIONS_ARCHIVE.md under this module's repo root). Pass an explicit paths list when
    the caller roots against a different base (e.g. a test's patched _common.ROOT) -- mirrors
    the existing parse_decisions_md(paths=...) seam. A missing path is silently skipped, same
    as parse_decisions_md.
    """
    if paths is None:
        paths = _DECISIONS_MD_PATHS
    numbers: set[int] = set()
    for md_path in paths:
        if not md_path.exists():
            continue
        content = md_path.read_text(encoding="utf-8", errors="replace")
        numbers.update(int(m.group(1)) for m in iter_decision_headings(content))
    return numbers


def iter_decision_sections(content: str) -> list[tuple[re.Match[str], str]]:
    """Yield (heading_match, raw_block) pairs for every '## Decision N:' heading, in file
    order, WITHOUT dedup or sort -- unlike parse_decisions_md, which dedupes by decision_id
    (first-wins) and sorts by id.

    raw_block is heading-inclusive: it spans from the start of the heading line through (but
    not including) the start of the next heading, or through end-of-file for the last one.
    Concatenating a file's preamble (the text before its first heading) with every raw_block
    here, in order, byte-reconstructs the source file exactly -- the invariant the
    byte-reconstruction coverage test in tests/decisions_md/test_parse.py asserts.

    Public per DAF-03 consolidation (PLAN-size-gov-marker-guard): the single shared
    section-block parser scripts/checks/_marker_guard.py's load_decision_bodies() consumes,
    so that module never hand-rolls a sixth '## Decision' header regex.
    """
    headings = list(_DECISION_HEADING_RE.finditer(content))
    sections: list[tuple[re.Match[str], str]] = []
    for i, m in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        sections.append((m, content[m.start() : end]))
    return sections


# Private alias retained so existing internal callers (this module's own parse_decisions_md)
# keep working unchanged.
_iter_decision_sections = iter_decision_sections


# Bare ```yaml fence, immediately (modulo the conventional blank line) after the heading line --
# never `---` (overloaded as block_separator/field-terminator already) and never a decorated
# info string like ```yaml reversal-conditions (Decision 133's monitored stanza, matched by
# scripts/preflight/decision_conditions.py on that exact info string).
_ENVELOPE_FENCE_RE = re.compile(r"\A#{2,3}\s+Decision\s+\d+:[^\n]*\n+```yaml\n(.*?)\n```", re.DOTALL)


def _envelope_has_well_formed_shape(data: dict) -> bool:
    """Type-check the handful of envelope fields every downstream consumer assumes a shape for
    (parse_decisions_md's list()/str() coercions, the conformance check's int(number)) -- a
    scalar where a list is expected (e.g. 'amends: 150' instead of 'amends: [150]') or a
    non-numeric 'number' must not crash a shared-parser caller with a raw traceback; it is
    exactly as malformed as unparseable YAML, so it is rejected at the same layer.
    """
    number = data.get("number")
    if number is not None and not (isinstance(number, int) and not isinstance(number, bool)):
        return False
    for list_field in ("amends", "supersedes"):
        value = data.get(list_field)
        if value is not None and not (
            isinstance(value, list) and all(isinstance(n, int) and not isinstance(n, bool) for n in value)
        ):
            return False
    significance = data.get("significance")
    if significance is not None and not isinstance(significance, dict):
        return False
    return True


def extract_entry_envelope(block: str) -> Optional[dict]:
    """Parse the typed metadata envelope from a heading-inclusive raw block, or None.

    Returns None when no fenced ```yaml block immediately follows the heading, the fence's
    content is not valid YAML, it does not parse to a mapping, or a known field carries the
    wrong shape (_envelope_has_well_formed_shape) -- a malformed or absent envelope is treated
    identically here (silently absent), so a broken envelope never crashes a corpus-wide parse
    or a downstream consumer's own type-shaped access; scripts.checks.decisions.
    validate_decision_entry_conformance is the layer that turns "no well-formed envelope on a
    new entry" into a FAIL.
    """
    m = _ENVELOPE_FENCE_RE.match(block)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or not _envelope_has_well_formed_shape(data):
        return None
    return data


def parse_decisions_md(paths: Optional[list[Path]] = None) -> list[dict]:
    """Parse DECISIONS.md (and optionally DECISIONS_ARCHIVE.md) into a list of dicts."""
    if paths is None:
        paths = _DECISIONS_MD_PATHS

    now_iso = datetime.now(timezone.utc).isoformat()
    seen: dict[int, dict] = {}

    for md_path in paths:
        if not md_path.exists():
            continue
        content = md_path.read_text(encoding="utf-8", errors="replace")
        for m, full_block in _iter_decision_sections(content):
            decision_id = int(m.group(1))
            if decision_id in seen:
                print(
                    f"WARNING: duplicate decision number {decision_id} in {md_path.name}; keeping "
                    "first-parsed entry, dropping this occurrence",
                    file=sys.stderr,
                )
                continue
            raw_title = m.group(2).strip()
            title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
            body = full_block[m.end() - m.start() :]
            envelope = extract_entry_envelope(full_block)
            if envelope and envelope.get("status"):
                status = str(envelope["status"]).strip()
            else:
                status_m = re.search(r"\*\*Status:\*\*\s*(.+)", body)
                if status_m:
                    status = status_m.group(1).strip().split("--")[0].strip()
                else:
                    paren_m = re.search(r"\(([^)]+)\)\s*$", raw_title)
                    status = paren_m.group(1).strip() if paren_m else ""
            raw_block = full_block.strip()
            content_hash = hashlib.sha256(raw_block.encode("utf-8")).hexdigest()
            seen[decision_id] = {
                "decision_id": decision_id,
                "title": title,
                "status": status,
                "problem": _extract_multiline_section(body, "Problem", "Trigger"),
                "intent": _extract_multiline_section(body, "Intent"),
                "decision_text": _extract_decision_text(body),
                "context": _extract_multiline_section(body, "Rationale", "Key details", "Context"),
                "decided_date": str(envelope["decided_date"]).strip()
                if envelope and envelope.get("decided_date")
                else _extract_decided_date(body),
                "related_decisions": _extract_related_decisions(body),
                "superseded_by": _extract_superseded_by(body),
                "reversal_conditions": _extract_multiline_section(body, "Reversal conditions", "Reversal condition"),
                "amends": list(envelope["amends"]) if envelope and envelope.get("amends") else extract_amends_edges(raw_title),
                "title_supersedes": list(envelope["supersedes"])
                if envelope and envelope.get("supersedes")
                else _extract_title_borne_supersedes(raw_title),
                "significance": envelope.get("significance", {}) if envelope else {},
                "raw_block": raw_block,
                "content_hash": content_hash,
                "created_timestamp": now_iso,
                "last_updated_timestamp": now_iso,
            }

    return sorted(seen.values(), key=lambda r: r["decision_id"])


def read_jsonl(path: Path) -> list[dict]:
    """Read valid JSONL entries from path, skipping blanks, comments, and schema lines."""
    if not path.exists():
        print(f"  WARNING: {path} not found -- skipping", file=sys.stderr)
        return []
    entries: list[dict] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", '{"_schema')):
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"  WARNING: skipping malformed JSON at {path}:{lineno}: {exc}", file=sys.stderr)
    return entries
