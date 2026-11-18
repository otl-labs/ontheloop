"""Generated decisions index (DCG-08; PLAN-decision-scout-bounded-retrieval; PLAN-decision-index-slim-rows).

A lightweight projection over BOTH docs/DECISIONS.md and docs/DECISIONS_ARCHIVE.md, derived SOLELY via the shared
parser scripts.decisions_md (DAF-03 single-parser rule): number, title, status, decided_date, typed
supersedes/superseded_by/amends edges and a `live` provenance flag on every row. It excludes the parser's volatile
timestamp/content_hash/raw_block fields, so two calls are byte-identical, and `significance`, which only checks read.

Slim rows (rec-3250, rec-5258): the committed docs/decisions-index.json is read whole by every decision-scout round. A
live row adds `currency` (scripts.decisions_md.derive_currency over corpus-wide inbound edge sets built once per build);
a live row that is not a superseded_compacted stub also adds `category_tags` (sorted tags from _CATEGORY_TAG_PATTERNS
matched against title + excerpt, a retrieval aid), `triage_excerpt` (the text only: no source or truncation marker) and,
when its entry has a well-formed envelope, `serves` and `kind` ({"value": ...} or null). Archive rows and
superseded_compacted stubs are skeletons (a stub keeps `currency`). --write serialises one compact sort_keys row per
line, the same parsed object as json.dumps.

The excerpt is derived through the shared parser: build_triage_excerpt (Intent -> Problem -> Context -> Decision
fallback, <=320 chars) feeds both the category tags and `--excerpts <ids | all>`, which prints one JSON line per live id
({number, triage_excerpt, triage_excerpt_source, triage_excerpt_truncated}) and exits 2 naming every id that is not a
live decision (build_excerpts); the markers are served only there.

check_index_freshness(failed) fails on DRIFT (committed != regenerated) OR ABSENCE: a required committed artifact.

CLI: --write, --check (exit 1 on fail), --print, --excerpts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts.decisions_md import decision_header_numbers, derive_currency, extract_entry_envelope, parse_decisions_md

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


def build_triage_excerpt(row: dict[str, Any]) -> tuple[str, str, bool]:
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
        if is_live:
            excerpt, _source, _truncated = build_triage_excerpt(by_number[n])
            entry["currency"] = derive_currency(by_number[n], inbound_supersedes, inbound_amends)
            if entry["currency"] != "superseded_compacted":
                entry["category_tags"] = _derive_category_tags(by_number[n]["title"], excerpt)
                entry["triage_excerpt"] = excerpt
                if extract_entry_envelope(by_number[n]["raw_block"]) is not None:
                    kind_value = by_number[n]["kind"].get("value")
                    entry["serves"] = by_number[n]["serves"]
                    entry["kind"] = {"value": kind_value} if kind_value else None
        decisions.append(entry)

    return {
        "decisions": decisions,
        "metadata": {"generated_by": "scripts.decisions_index"},
    }


def serialize_index(index: dict[str, Any]) -> str:
    """Serialise the index one compact sort_keys row per line (the same parsed object as json.dumps)."""
    rows = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in index["decisions"]]
    meta = json.dumps(index["metadata"], sort_keys=True, separators=(",", ":"))
    body = ",\n".join(rows)
    return '{"decisions":[\n' + body + '\n],"metadata":' + meta + "}\n"


def build_excerpts(numbers: list[int] | None) -> list[dict[str, Any]]:
    """On-demand triage excerpts for live decisions (None = every live id), sorted by number.

    Raises ValueError naming every requested id that is not a live decision.
    """
    live_numbers = decision_header_numbers(paths=[_LIVE_PATH])
    if numbers is not None:
        unknown = sorted(set(numbers) - live_numbers)
        if unknown:
            raise ValueError(f"not live decisions: {', '.join(str(n) for n in unknown)}")
        wanted = set(numbers)
    else:
        wanted = live_numbers
    out: list[dict[str, Any]] = []
    for row in sorted(parse_decisions_md(), key=lambda r: r["decision_id"]):
        if row["decision_id"] in wanted:
            excerpt, source, truncated = build_triage_excerpt(row)
            out.append(
                {
                    "number": row["decision_id"],
                    "triage_excerpt": excerpt,
                    "triage_excerpt_source": source,
                    "triage_excerpt_truncated": truncated,
                }
            )
    return out


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
    group.add_argument(
        "--excerpts",
        metavar="IDS",
        nargs="+",
        help="Print one JSON excerpt line per live decision: ids (comma or space separated) or 'all'.",
    )
    args = parser.parse_args()

    if args.write:
        index = build_index()
        _EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _EXPORT_PATH.write_text(serialize_index(index), encoding="utf-8")
        print(f"Decisions index written to {_EXPORT_PATH}", file=sys.stderr)
    elif args.check:
        failed: list[str] = []
        check_index_freshness(failed)
        if failed:
            for msg in failed:
                print(msg, file=sys.stderr)
            sys.exit(1)
        print("Decisions index is current.", file=sys.stderr)
    elif args.excerpts:
        tokens = [t for chunk in args.excerpts for t in chunk.replace(",", " ").split()]
        try:
            ids = None if tokens == ["all"] else [int(t) for t in tokens]
            lines = build_excerpts(ids)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
        for item in lines:
            print(json.dumps(item, sort_keys=True))
    elif args.print_:
        _print_json(build_index())
    else:
        parser.print_help(sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
