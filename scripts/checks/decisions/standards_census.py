"""Standards census (rec-5367): named queries over the legacy stock and a verifier for audits/standards-cleanup-census.yaml.

Not a registered check. QUERIES computes class membership from the shipped measures (Decisions 275, 276, 278,
rec-5263); the census file records only dispositions, owners, reasons and guards, never counts or member lists.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from scripts import decisions_md
from scripts.checks import _common, registry
from scripts.checks.contracts import _amendment_retention, _evaluator_coverage
from scripts.checks.decisions import consolidation, decisions_size_measure, entry_measure

CENSUS_REL = "audits/standards-cleanup-census.yaml"
DECISIONS_REL = "docs/DECISIONS.md"
INDEX_REL = "docs/decisions-index.json"
ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"
ENTRY_CONTRACT_REL = "docs/contracts/decision-entry.yaml"
EXEMPTIONS_REL = "config/decision_options_exemptions.yaml"
BUDGETS_REL = "config/structural_size_budgets.yaml"
CONTRACTS_REL = "docs/contracts"
DISPOSITIONS = frozenset({"exempt", "consolidate", "conform", "handed_off", "clean"})
MEASURES = frozenset({"query", "judgement", "gate", "census_workflow", "read_set_report"})
CLASS_FIELDS = ("id", "standard", "source", "query", "measure", "disposition", "owner", "guarded_by", "reason")
EXCEPTION_FIELDS = ("id", "class", "root", "owner", "disposition", "reason")
_REC = re.compile(r"rec-\d+")
_DECISION_OWNER = re.compile(r"Decision (\d+)(?: cl\.\d+)?")
_ROADMAP_OWNER = re.compile(r"T\d+\.\d+")
_ROADMAP_DONE = frozenset({"complete", "retired"})


@dataclasses.dataclass(frozen=True)
class Unreadable:
    text: str


def _read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _load_yaml(root: Path, rel: str) -> Any:
    try:
        return yaml.safe_load(_read(root, rel) or "")
    except yaml.YAMLError:
        return None


def _natural(value: str) -> list[Any]:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value)]


def _sorted(members: Any) -> list[str]:
    return sorted({str(m) for m in members}, key=_natural)


def _live_entries(root: Path) -> list[tuple[int, str]]:
    """(number, block) of live, non-stub entries of docs/DECISIONS.md, per the index row and the stub grammar."""
    rows = {r.get("number"): r for r in consolidation.load_rows(root)}
    found = []
    for match, block in decisions_md.iter_decision_sections(_read(root, DECISIONS_REL) or ""):
        number = int(match.group(1))
        row = rows.get(number)
        if row is None or not row.get("live") or row.get("currency") == "superseded_compacted":
            continue
        if decisions_md.is_compacted_stub(block):
            continue
        found.append((number, block))
    return found


def _governance(root: Path) -> decisions_size_measure.Governance | Unreadable:
    gov = decisions_size_measure.size_governance(_read(root, ENTRY_CONTRACT_REL) or "")
    return Unreadable(gov) if isinstance(gov, str) else gov


def measured_cap_over(root: Path) -> list[str] | Unreadable:
    gov = _governance(root)
    if isinstance(gov, Unreadable):
        return gov
    if gov.measured_cap is None:
        return Unreadable("decision-entry.yaml unreadable: no size_governance.measured_size_norm.cap_bytes")
    excluded = (
        entry_measure.METADATA_ENVELOPE,
        entry_measure.REVERSAL_CONDITIONS,
        entry_measure.OPTIONS_CONSIDERED,
        entry_measure.DATED_ANNOTATIONS,
    )
    return _sorted(
        number
        for number, block in _live_entries(root)
        if entry_measure.measured_bytes(block, excluded, gov.options_marker, dated_only=False) > gov.measured_cap
    )


def options_missing(root: Path) -> list[str] | Unreadable:
    gov = _governance(root)
    if isinstance(gov, Unreadable):
        return gov
    exempt = {title for _, title in decisions_size_measure.exemption_rows(_read(root, EXEMPTIONS_REL))}
    return _sorted(
        number
        for number, block in _live_entries(root)
        if decisions_size_measure.title_key(block.split("\n", 1)[0]) not in exempt
        and entry_measure.options_block(block, gov.options_marker) is None
    )


def envelope_missing(root: Path) -> list[str]:
    return _sorted(n for n, block in _live_entries(root) if decisions_md.extract_entry_envelope(block) is None)


def consolidation_candidates(root: Path) -> list[str] | Unreadable:
    threshold = consolidation.load_threshold(root)
    if threshold is None:
        return Unreadable("decision-compaction.yaml unreadable: no consolidation.threshold")
    return _sorted(consolidation.candidates(consolidation.load_rows(root), threshold))


def manual_conditions(root: Path) -> list[str]:
    found = []
    for number, block in _live_entries(root):
        span = entry_measure.stanza_span(block)
        if span is None:
            continue
        text = block[span[0] : span[1]].split("\n", 1)[1].rsplit("\n```", 1)[0]
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        conditions = data.get("conditions") if isinstance(data, dict) else None
        for cond in conditions if isinstance(conditions, list) else []:
            if isinstance(cond, dict) and cond.get("kind") == "manual":
                found.append(f"{number}:{cond.get('id')}")
    return _sorted(found)


def byte_seeded(root: Path) -> list[str]:
    data = _load_yaml(root, BUDGETS_REL)
    budgets = data.get("byte_budgets") if isinstance(data, dict) else None
    return _sorted(budgets) if isinstance(budgets, dict) else []


def _contract_files(root: Path) -> list[Path]:
    base = root / CONTRACTS_REL
    return sorted(base.glob("*.yaml")) if base.is_dir() else []


def amendment_rows_over_keep(root: Path) -> list[str] | Unreadable:
    cfg = _amendment_retention.load_retention(root)
    if cfg is None:
        return Unreadable("contract-population.yaml unreadable: no amendment_log_retention.keep_full")
    exempt = {Path(str(f)).name for f in cfg.get("exempt_files") or []}
    found = []
    for path in _contract_files(root):
        if path.name in exempt:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except yaml.YAMLError:
            continue
        log = data.get("amendment_log") if isinstance(data, dict) else None
        if isinstance(log, list) and len(_amendment_retention.full_rows(log, cfg)) > cfg["keep_full"]:
            found.append(f"{CONTRACTS_REL}/{path.name}")
    return _sorted(found)


def evaluator_zero_coverage(root: Path) -> list[str] | Unreadable:
    coverage = _evaluator_coverage.load_coverage(root)
    if isinstance(coverage, str):
        return Unreadable(coverage)
    found = []
    for path in _contract_files(root):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except yaml.YAMLError:
            continue
        contract = data.get("contract") if isinstance(data, dict) else None
        if not isinstance(contract, dict) or contract.get("class") != "D":
            continue
        body = _evaluator_coverage.body_keys(data)
        row = coverage.get(path.name)
        asserted = set(row.asserts) & set(body) if row else set()
        instructed: set[str] = set()
        if row and row.triggers:
            instructed, _ = _evaluator_coverage.instructed_keys(path.name, row.triggers, body)
        if not asserted and not instructed:
            found.append(f"{CONTRACTS_REL}/{path.name}")
    return _sorted(found)


QUERIES: dict[str, Callable[[Path], list[str] | Unreadable]] = {
    "measured_cap_over": measured_cap_over,
    "options_missing": options_missing,
    "envelope_missing": envelope_missing,
    "consolidation_candidates": consolidation_candidates,
    "manual_conditions": manual_conditions,
    "byte_seeded": byte_seeded,
    "amendment_rows_over_keep": amendment_rows_over_keep,
    "evaluator_zero_coverage": evaluator_zero_coverage,
}


def load(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _live_decisions(root: Path) -> set[int]:
    return {r["number"] for r in consolidation.load_rows(root) if r.get("live") and isinstance(r.get("number"), int)}


def _open_roadmap_ids(root: Path) -> set[str]:
    data = _load_yaml(root, ROADMAP_REL)
    items = data.get("tier_items") if isinstance(data, dict) else None
    return {
        str(i["id"]) for i in items or [] if isinstance(i, dict) and "id" in i and str(i.get("status")) not in _ROADMAP_DONE
    }


def _owner_defect(owner: Any, live: set[int], roadmap: set[str]) -> str | None:
    text = str(owner)
    if _REC.fullmatch(text) or _ROADMAP_OWNER.fullmatch(text) and text in roadmap:
        return None
    match = _DECISION_OWNER.fullmatch(text)
    if match and int(match.group(1)) in live:
        return None
    return f"owner {text!r} is not rec-NNNN, a live Decision or an open roadmap item"


def _missing(row: Any, fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if not isinstance(row, dict) or f not in row]


def _class_row_defects(row: dict, ctx: dict[str, Any]) -> list[str]:
    defects = []
    tag = f"row {row.get('id')}"
    if missing := _missing(row, CLASS_FIELDS):
        return [f"{tag}: missing {', '.join(missing)}"]
    if row["disposition"] not in DISPOSITIONS:
        defects.append(f"{tag}: disposition {row['disposition']!r} is outside {sorted(DISPOSITIONS)}")
    if row["measure"] not in MEASURES:
        defects.append(f"{tag}: measure {row['measure']!r} is outside {sorted(MEASURES)}")
    elif row["measure"] == "query":
        if row["query"] not in QUERIES:
            defects.append(f"{tag}: query {row['query']!r} is not a named query")
    elif row["query"] is not None:
        defects.append(f"{tag}: measure {row['measure']} takes a null query")
    if bad := _owner_defect(row["owner"], ctx["live"], ctx["roadmap"]):
        defects.append(f"{tag}: {bad}")
    guard = row["guarded_by"]
    if guard not in ctx["checks"] and guard != row["owner"]:
        defects.append(f"{tag}: guarded_by {guard!r} is not a registered check or the row's own owner")
    return defects


def _exception_defects(row: Any, class_ids: set[Any]) -> list[str]:
    tag = f"exception {row.get('id') if isinstance(row, dict) else row!r}"
    if missing := _missing(row, EXCEPTION_FIELDS):
        return [f"{tag}: missing {', '.join(missing)}"]
    defects = []
    if row["class"] not in class_ids:
        defects.append(f"{tag}: class {row['class']!r} is not a class row")
    if not isinstance(row["root"], int) or isinstance(row["root"], bool):
        defects.append(f"{tag}: root {row['root']!r} is not an int")
    if not _REC.fullmatch(str(row["owner"])):
        defects.append(f"{tag}: owner {row['owner']!r} is not rec-NNNN")
    if row["disposition"] not in DISPOSITIONS:
        defects.append(f"{tag}: disposition {row['disposition']!r} is outside {sorted(DISPOSITIONS)}")
    return defects


def _result_text(query: str, result: list[str] | Unreadable) -> str | None:
    return f"{query} unreadable: {result.text}" if isinstance(result, Unreadable) else None


def verify(census: dict[str, Any], root: Path) -> tuple[list[str], list[str]]:
    """(defects, notes) for a loaded census against the repository at root."""
    rows = census.get("rows")
    exceptions = census.get("exceptions") or []
    if not isinstance(rows, list) or not rows:
        return ["census has no rows list"], []
    ctx = {"live": _live_decisions(root), "roadmap": _open_roadmap_ids(root), "checks": set(registry.all_checks())}
    defects: list[str] = []
    seen: set[Any] = set()
    for row in rows:
        rid = row.get("id") if isinstance(row, dict) else None
        if rid in seen:
            defects.append(f"duplicate id {rid!r}")
        seen.add(rid)
        defects += _class_row_defects(row, ctx) if isinstance(row, dict) else [f"row {row!r} is not a mapping"]
    class_ids = {r.get("id") for r in rows if isinstance(r, dict)}
    for row in exceptions if isinstance(exceptions, list) else []:
        if isinstance(row, dict) and row.get("id") in seen:
            defects.append(f"duplicate id {row.get('id')!r}")
        seen.add(row.get("id") if isinstance(row, dict) else None)
        defects += _exception_defects(row, class_ids)
    notes: list[str] = []
    result = QUERIES["consolidation_candidates"](root)
    if isinstance(result, Unreadable):
        defects.append(_result_text("consolidation_candidates", result) or "")
    else:
        roots = {str(r["root"]) for r in exceptions if isinstance(r, dict) and "root" in r}
        notes += [f"note: consolidation candidate {m} has no exception row" for m in result if m not in roots]
        notes += [f"note: exception root {r} is no longer a candidate" for r in sorted(roots - set(result), key=_natural)]
    for row in rows:
        if isinstance(row, dict) and row.get("measure") == "query" and row.get("query") in QUERIES:
            defect = _result_text(row["query"], QUERIES[row["query"]](root))
            if defect:
                defects.append(defect)
    return [d for d in defects if d], notes


def read_owner_defects(census: dict[str, Any], root: Path, reader: Any, fetch: Callable[..., Any]) -> list[str]:
    """Defects from reading every rec owner of standing debt through the reader."""
    owners: dict[str, bool] = {}
    members = {name: QUERIES[name](root) for name in QUERIES}
    for row in census.get("rows") or []:
        if not isinstance(row, dict) or row.get("disposition") == "clean" or not _REC.fullmatch(str(row.get("owner"))):
            continue
        result = members.get(str(row.get("query")))
        standing = row.get("query") is None or isinstance(result, Unreadable) or bool(result)
        owners[row["owner"]] = owners.get(row["owner"], False) or standing
    candidates = members["consolidation_candidates"]
    for row in census.get("exceptions") or []:
        if isinstance(row, dict) and _REC.fullmatch(str(row.get("owner"))):
            standing = isinstance(candidates, Unreadable) or str(row.get("root")) in candidates
            owners[row["owner"]] = owners.get(row["owner"], False) or standing
    if not owners:
        return []
    recs, missing = fetch(reader, sorted(owners))
    defects = [f"owner {m} does not exist" for m in missing]
    for rec in recs:
        status = rec.get("status")
        if status != "open" and owners.get(rec.get("id")):
            defects.append(f"owner {rec.get('id')} is {status}, not open, while its debt stands")
    return defects


def _cmd_list(root: Path, query: str) -> int:
    if query not in QUERIES:
        print(f"{query} unreadable")
        return 1
    result = QUERIES[query](root)
    if isinstance(result, Unreadable):
        print(f"{query} unreadable")
        return 1
    print("\n".join(result))
    return 0


def _cmd_summary(root: Path, census: dict[str, Any]) -> int:
    code = 0
    for row in census.get("rows") or []:
        if not isinstance(row, dict) or not row.get("query"):
            continue
        result = QUERIES[row["query"]](root) if row["query"] in QUERIES else Unreadable("unknown query")
        if isinstance(result, Unreadable):
            print(f"{row['id']} {row['query']} unreadable")
            code = 1
        else:
            print(f"{row['id']} {row['query']} {len(result)}")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="standards_census")
    parser.add_argument("--list", dest="list_query")
    parser.add_argument("--summary", metavar="CENSUS")
    parser.add_argument("--verify", metavar="CENSUS")
    parser.add_argument("--read-owners", action="store_true")
    parser.add_argument("--root", default=str(_common.ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.list_query:
        return _cmd_list(root, args.list_query)
    if args.summary:
        return _cmd_summary(root, load(root / args.summary))
    if not args.verify:
        parser.error("one of --list, --summary or --verify is required")
    census = load(root / args.verify)
    defects, notes = verify(census, root)
    if args.read_owners:
        from src.common import ducklake_reader_client as client

        defects += read_owner_defects(census, root, client.make_reader(profile="agent_platform"), client.fetch_recs_by_ids)
    for line in [*notes, *(f"defect: {d}" for d in defects)]:
        print(line)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
