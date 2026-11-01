"""Generated decisions index (audit finding DCG-08, PLAN-dcg-decisions-index, FINAL wave of
the dcg-* audit-consolidation series; PLAN-decision-scout-bounded-retrieval adds `live`, the
triage_excerpt fields, and `category_tags`).

A lightweight, machine-parseable projection over BOTH docs/DECISIONS.md and
docs/DECISIONS_ARCHIVE.md -- number, title, status, decided_date, typed
supersedes/superseded_by/amends edges, a `live` provenance flag, and a bounded triage excerpt --
derived SOLELY via the shared parser scripts.decisions_md (DAF-03 single-parser rule): no second
header/title regex lives here.

build_index() projects ONLY stable fields from each parse_decisions_md() row -- it EXCLUDES the
parser's volatile created_timestamp/last_updated_timestamp/content_hash/raw_block fields, so two
consecutive calls are byte-identical (json.dumps(..., sort_keys=True) equal) and the committed
docs/decisions-index.json can be compared for drift without a timestamp always tripping it.

Edge derivation:
  - amends: the row's own "amends" key -- envelope-sourced (scripts.decisions_md
    extract_entry_envelope's `amends` field) when the entry carries a well-formed metadata
    envelope, else the legacy title-relation extraction (extract_amends_edges). No second
    derivation lives here: this generator consumes whichever value parse_decisions_md() already
    resolved, unchanged (PLAN-decision-entry-flow-governance, Decision 167).
  - superseded_by: the row's typed "superseded_by" string ('dec-NNN' or ''), coerced to
    int | None.
  - supersedes: the corpus-wide UNION of (a) the inverse of every OTHER row's superseded_by
    pointing at this number, and (b) this row's own "title_supersedes" key -- likewise
    envelope-sourced (the envelope's `supersedes` field) when present, else the legacy
    title-borne extraction (scripts.decisions_md._extract_title_borne_supersedes).

`significance` is a parse_decisions_md() row key (the envelope's routing claim dict, or {} when
absent) that is DELIBERATELY NOT projected into docs/decisions-index.json -- it is parser-surfaced
and check-read only (scripts.checks.decisions.validate_decision_entry_conformance), mirroring the
index-only treatment of `intent`/`amends`/`title_supersedes` before them: docs/decisions-index.json
is a read-in-full-every-/plan surface with no stock size guard of its own (Decision 179 retired the
mechanical ceiling that used to bound it indirectly; T2.56 c1 and T1.5 c1 own the residual), so
every projected field on every row still has a real, disclosed read cost even though nothing
mechanically enforces it -- and significance is a per-entry authoring-time claim the index's
consumers (decision-scout triage) have no use for.

`live` derivation (PLAN-decision-scout-bounded-retrieval): whether the decision number is headed
in docs/DECISIONS.md, via scripts.decisions_md.decision_header_numbers(paths=[docs/DECISIONS.md])
-- never a second parse of by_number's cross-file dict, which is last-occurrence-wins across the
two files (overlap is zero today, so this is latent, not live behaviour).

`currency` derivation (PLAN-decision-corpus-currency, rec-3055/rec-3056): a `currency` key is
projected onto every live:true row (never a live:false row) via
scripts.decisions_md.derive_currency, fed the corpus-wide inbound-supersedes and inbound-amends
sets built ONCE per build_index() call from this generator's own supersedes_map / per-row amends
projections -- never recomputed per row. This generator never validates the derived value against
docs/contracts/decision-entry.yaml's vocabulary; that is scripts.checks.decisions.
validate_decision_currency's job (invariant I3), so check_index_freshness keeps failing cleanly on
drift instead of this module surfacing an uncaught traceback on an unexpected value. `currency`
was already the narrowest of the five retrieval-aid keys; as of migration step 5 the other four
(below) share its live-only scope, for the same reason (see next paragraph).

`triage_excerpt` derivation, LIVE ROWS ONLY as of migration step 5 of
audits/contract-first-governance-33c8667.yaml (decision-scout never reads it for an archived row,
and every projected field on every row still carries a real read cost on this
read-in-full-every-/plan surface -- the same rationale this docstring already gives above for
excluding `significance`, Decision 179): a <=320-char excerpt for the decision-scout
bounded-retrieval SPIRIT lane (Decision 152 gate (ii) widened to admit a Decision-clause quote),
fallback order Intent -> Problem -> Context (the parser's Rationale/Key details/Context
extraction) -> Decision (the decision-body marker).
`triage_excerpt_source` names which of the four supplied the excerpt (or "" when none of the four
markers are present at all). `triage_excerpt_truncated` is True iff the source text exceeded 320
chars and was cut.

`category_tags` derivation, LIVE ROWS ONLY (same migration-step-5 scope and rationale as
`triage_excerpt` above): a sorted list of deterministic artifact/process-category tags (see
_CATEGORY_TAG_PATTERNS) matched against title + triage_excerpt only. Closes a recall gap the
excerpt alone left in decision-scout's bounded index-pass triage -- a decision that governs by
ARTIFACT TYPE (e.g. any new Lambda) rather than shared vocabulary was being silently discarded
before a targeted read; the scout now derives the proposed approach's own tag set once and
shortlists by mechanical set-intersection instead of a per-entry judgment call over the whole
index. Generator-internal and revisable without Decision ceremony -- a retrieval aid, not a
governance taxonomy.

check_index_freshness(failed) fails on DRIFT (committed != regenerated) OR ABSENCE -- unlike
scripts.dependency_graph.check_export_freshness (Decision 80 lean posture: no-op when the export
is absent), this index is a required committed artifact (project alignment fork 2: committed JSON +
registered drift check, not a gitignored synced cache), so a missing file is itself a failure.

CLI: --write (regenerate docs/decisions-index.json), --check (freshness, exit 1 on fail),
--print (dump the current index to stdout).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts.decisions_md import decision_header_numbers, derive_currency, parse_decisions_md

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXPORT_PATH = _REPO_ROOT / "docs" / "decisions-index.json"
_LIVE_PATH = _REPO_ROOT / "docs" / "DECISIONS.md"

_TRIAGE_EXCERPT_MAX_CHARS = 320

# Fallback order for triage_excerpt: (row key, source label). "Context" covers the parser's
# Rationale/Key details/Context extraction (whichever marker matched); "Decision" is the
# decision-body marker, the most reliable fallback since it is a REQUIRED marker per
# docs/contracts/decision-entry.yaml.
_TRIAGE_EXCERPT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("intent", "Intent"),
    ("problem", "Problem"),
    ("context", "Context"),
    ("decision_text", "Decision"),
)


def _build_triage_excerpt(row: dict[str, Any]) -> tuple[str, str, bool]:
    """Derive (triage_excerpt, triage_excerpt_source, triage_excerpt_truncated) for one row.

    Fallback order Intent -> Problem -> Context -> Decision; the first non-empty field wins.
    Returns ("", "", False) when none of the four fields are populated (the residual band).
    """
    for row_key, source_label in _TRIAGE_EXCERPT_FALLBACKS:
        text = (row.get(row_key) or "").strip()
        if text:
            truncated = len(text) > _TRIAGE_EXCERPT_MAX_CHARS
            excerpt = text[:_TRIAGE_EXCERPT_MAX_CHARS] if truncated else text
            return excerpt, source_label, truncated
    return "", "", False


# Deterministic artifact/process-category tags (PLAN-decision-scout-bounded-retrieval, Fable
# advice-consult): closes the recall gap the excerpt-only bounded triage left in decision-scout's
# index-pass -- a decision that governs by ARTIFACT TYPE (e.g. "any new Lambda") rather than topic
# vocabulary was silently discarded as IRRELEVANT before ever reaching a targeted read, because the
# scout's own per-entry judgment call over ~113 entries is unreliable (empirically demonstrated:
# three rounds of prose-only tightening left Decisions 126/157 unrecovered). This field converts
# that per-entry judgment into a mechanical set-intersection the scout performs ONCE per dispatch
# (derive the approach's own tag set, then shortlist every entry whose category_tags intersects
# it) -- see .claude/skills/decision-scout/SKILL.md Phase 2 step 3. Matched against title +
# triage_excerpt only (never the full body): a full-body regex would tag a large fraction of the
# corpus with incidental mentions, defeating the bound. Each pattern is kept under ~35% of live
# entries (verified empirically, not just asserted) so the shortlist stays materially smaller than
# the full corpus. The tag vocabulary is generator-internal and revisable without Decision
# ceremony -- it is a retrieval aid, not a governance taxonomy.
_CATEGORY_TAG_PATTERNS: tuple[tuple[str, str], ...] = (
    ("lambda", r"(?i)\blambdas?\b"),
    ("terraform", r"(?i)\bterraform\b|\btf-gated\b"),
    ("iam", r"(?i)\biam\b|\brole\b|\bboundary\b"),
    ("secrets", r"(?i)\bsecrets?\s+manager\b|\bcredentials?\b|\bapi key\b"),
    ("deploy", r"(?i)\bdeploy(?:s|ed|ment|ments)?\b|\bcd channel\b|\bapply\b"),
    ("egress", r"(?i)\begress\b|\bbudget\b|\bneon\b"),
    ("decisions-corpus", r"(?i)\bdecisions(?:_archive)?\.md\b|\bdecision[- ]compact"),
    ("prose-docs", r"(?i)\bprose\b|\bdocumentation\b|\bdocs?/\b"),
)


def _derive_category_tags(title: str, excerpt: str) -> list[str]:
    """Derive the sorted list of category_tags matching title+excerpt against _CATEGORY_TAG_PATTERNS."""
    text = f"{title}\n{excerpt}"
    return sorted(tag for tag, pattern in _CATEGORY_TAG_PATTERNS if re.search(pattern, text))


def _superseded_by_int(raw: str) -> int | None:
    """Coerce the parser's 'dec-NNN' superseded_by string to a bare int, or None if empty."""
    if not raw:
        return None
    # raw is always exactly 'dec-NNN' here -- scripts.decisions_md._extract_superseded_by's
    # only two possible return values are '' or f"dec-{int(n):03d}" -- so split("-")[1] is safe.
    return int(raw.split("-")[1])


def build_index() -> dict[str, Any]:
    """Build the deterministic decisions index dict (docs/decisions-index.json shape).

    Sorted by number. Excludes every volatile parse_decisions_md field -- only
    number/title/status/decided_date/supersedes/superseded_by/amends survive the projection.
    """
    rows = parse_decisions_md()
    by_number = {row["decision_id"]: row for row in rows}
    superseded_by_map = {n: _superseded_by_int(row.get("superseded_by", "")) for n, row in by_number.items()}
    live_numbers = decision_header_numbers(paths=[_LIVE_PATH])

    supersedes_map: dict[int, set[int]] = {n: set() for n in by_number}
    for n, row in by_number.items():
        sb = superseded_by_map[n]
        if sb is not None and sb in supersedes_map:
            # row n's superseded_by == sb means "n is superseded BY sb" -> sb supersedes n.
            supersedes_map[sb].add(n)
        for target in row.get("title_supersedes", []):
            if target in supersedes_map:
                # row n's own title says "Supersedes Decision target" -> n supersedes target.
                supersedes_map[n].add(target)

    amends_map: dict[int, list[int]] = {
        n: sorted(t for t in row.get("amends", []) if t in by_number) for n, row in by_number.items()
    }

    # Corpus-wide inbound edge sets for derive_currency, built ONCE here from the maps above --
    # never recomputed per row (PLAN-decision-corpus-currency). inbound_supersedes is every
    # number that is a VICTIM of someone else's outbound supersedes (the union of
    # supersedes_map's own values, which already folds in both the superseded_by inverse and
    # title_supersedes); inbound_amends is every number targeted by someone else's amends list.
    inbound_supersedes: set[int] = set()
    for supersedes_targets in supersedes_map.values():
        inbound_supersedes.update(supersedes_targets)
    inbound_amends: set[int] = set()
    for amends_targets in amends_map.values():
        inbound_amends.update(amends_targets)

    decisions = []
    for n in sorted(by_number):
        is_live = n in live_numbers
        entry: dict[str, Any] = {
            "number": n,
            "title": by_number[n]["title"],
            "status": by_number[n]["status"],
            "decided_date": by_number[n].get("decided_date", ""),
            "supersedes": sorted(supersedes_map[n]),
            "superseded_by": superseded_by_map[n],
            "amends": amends_map[n],
            "live": is_live,
        }
        # Skeleton archive rows (rec-3012, migration step 5): a live:false row never derives or
        # carries these five keys -- decision-scout only ever triages live:true rows, and every
        # projected field on every row still has a real read cost on this read-in-full-every-/plan
        # surface (Decision 179 retired the mechanical ceiling; the cost itself did not go away).
        if is_live:
            excerpt, excerpt_source, excerpt_truncated = _build_triage_excerpt(by_number[n])
            entry["triage_excerpt"] = excerpt
            entry["triage_excerpt_source"] = excerpt_source
            entry["triage_excerpt_truncated"] = excerpt_truncated
            entry["category_tags"] = _derive_category_tags(by_number[n]["title"], excerpt)
            entry["currency"] = derive_currency(by_number[n], inbound_supersedes, inbound_amends)
        decisions.append(entry)

    return {
        "decisions": decisions,
        "metadata": {"generated_by": "scripts.decisions_index"},
    }


def check_index_freshness(failed: list[str]) -> int | None:
    """Fail on drift OR absence of the committed docs/decisions-index.json (DCG-08).

    Unlike scripts.dependency_graph.check_export_freshness (no-op when absent, Decision 80
    lean-by-default posture for a compute-on-demand oracle), this index IS a required
    committed artifact -- absence is itself a failure, with a regenerate hint, never a silent
    no-op. repo_root-relative display mirrors check_export_freshness's ValueError fallback.

    Returns the number of regenerated decision entries compared against the committed export, or
    None when the export is absent or unreadable (the failure is appended; nothing was compared).
    """
    current = build_index()

    if not _EXPORT_PATH.exists():
        try:
            path_display = _EXPORT_PATH.relative_to(_REPO_ROOT)
        except ValueError:
            path_display = _EXPORT_PATH
        failed.append(
            f"Decisions index {path_display} is missing. Regenerate: bin/venv-python -m scripts.decisions_index --write"
        )
        return None

    try:
        committed = json.loads(_EXPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failed.append(f"Decisions index freshness: cannot read committed export: {exc}")
        return None

    if committed != current:
        try:
            path_display = _EXPORT_PATH.relative_to(_REPO_ROOT)
        except ValueError:
            path_display = _EXPORT_PATH
        failed.append(
            f"Decisions index {path_display} is stale (drifted from docs/DECISIONS.md + "
            "docs/DECISIONS_ARCHIVE.md). Regenerate: bin/venv-python -m scripts.decisions_index --write"
        )
    return len(current["decisions"])


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generated decisions index -- typed supersedes/superseded_by/amends edges (DCG-08).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help=f"Write the index to {_EXPORT_PATH}.")
    group.add_argument(
        "--check", action="store_true", help="Check freshness against the committed export; exit 1 on drift/absence."
    )
    group.add_argument("--print", action="store_true", dest="print_", help="Print the current index to stdout.")
    args = parser.parse_args()

    if args.write:
        index = build_index()
        _EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _EXPORT_PATH.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Decisions index written to {_EXPORT_PATH}", file=sys.stderr)
    elif args.check:
        failed: list[str] = []
        check_index_freshness(failed)
        if failed:
            for msg in failed:
                print(msg, file=sys.stderr)
            sys.exit(1)
        print("Decisions index is current.", file=sys.stderr)
    elif args.print_:
        _print_json(build_index())
    else:
        parser.print_help(sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
