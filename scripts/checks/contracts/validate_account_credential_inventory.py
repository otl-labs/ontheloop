"""Evaluator for docs/contracts/account-credential-inventory.yaml (rec-4542; Decision 195).

Gates the company-cutover inventory: closed key sets and vocabularies, the append-only evidence ledger and its
agreement with each row's status, ref resolution into the sibling contracts, the owning side agreeing with the
status, the Decision 101 shape guard over the raw file, the close-out rule once programme.phase is closed, and
append-only growth against the base.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

from scripts.checks import _common, registry

_CONTRACT = "docs/contracts/account-credential-inventory.yaml"
_LABEL = "Account credential inventory"
_ROW_KEYS = ("id", "provider", "item", "kind", "location", "owner_today", "target", "stage", "rec", "status")
_ROW_OPTIONAL = ("ref", "follows", "expires")
_BILL_KEYS = ("id", "provider", "billed", "payer", "target", "evidence_wanted", "stage", "rec", "status")
_LEDGER_KEYS = ("date", "row", "status", "evidence")
_OPEN_STATUSES = frozenset({"personal", "pre-staged", "moved", "unknown"})
_REC_RE = re.compile(r"^rec-\d+$")
_SHAPES = (
    (re.compile(r"@"), "an at-sign"),
    (re.compile(r"(?<!\d)\d{12}(?!\d)"), "a 12-digit run"),
    (re.compile(r"\barn:", re.IGNORECASE), "an ARN prefix"),
    (re.compile(r"\borg-[a-z]+-[a-z]+-\d+\b"), "a Neon organisation id shape"),
    (re.compile(r"\b[a-z]+-[a-z]+-\d{8}\b"), "a Neon project id shape"),
)
_OWNER_FOR_STATUS = {
    "proven": {"company"},
    "company": {"company"},
    "retired": {"company", "none"},
    "stays": {"founder"},
}
_PAYER_FOR_STATUS = {
    "proven": {"company"},
    "company": {"company", "none"},
    "retired": {"company", "none"},
    "stays": {"personal"},
}


def load_contract(repo_root: Path) -> dict | None:
    """Parsed contract, or None after registry.skipped() when it is missing or unparseable."""
    try:
        import yaml

        data = yaml.safe_load((repo_root / _CONTRACT).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 -- never raise at check time
        registry.skipped(f"{_CONTRACT} unreadable: {type(exc).__name__}")
        return None
    if not isinstance(data, dict):
        registry.skipped(f"{_CONTRACT} is not a mapping")
        return None
    return data


def _date(value: Any) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return None


def _programme(doc: dict) -> dict[str, Any]:
    value = doc.get("programme")
    return value if isinstance(value, dict) else {}


def _names(value: Any) -> set[str]:
    return {x for x in value if isinstance(x, str)} if isinstance(value, (list, dict)) else set()


def _unhashable_fields(entry: dict, skip: tuple[str, ...] = ()) -> list[str]:
    return sorted(str(k) for k, v in entry.items() if k not in skip and isinstance(v, (list, dict)))


def _as_list(doc: dict, key: str) -> list:
    value = doc.get(key)
    return value if isinstance(value, list) else []


def _check_shapes(doc: dict, failed: list[str]) -> None:
    """Invariant (1) section shapes: rows, billing and the ledger are lists of mappings."""
    for section in ("rows", "billing", "evidence_ledger"):
        value = doc.get(section)
        if not isinstance(value, list):
            failed.append(f"{_LABEL}: {section} is not a list")
            continue
        for i, entry in enumerate(value):
            if not isinstance(entry, dict):
                failed.append(f"{_LABEL}: {section}[{i}] is not a mapping")


def _check_keys(section: str, row: dict, failed: list[str]) -> None:
    keys = _ROW_KEYS if section == "rows" else _BILL_KEYS
    rid = row.get("id", "?")
    allowed = set(keys) | (set(_ROW_OPTIONAL) if section == "rows" else set())
    missing = [k for k in keys if k not in row]
    if missing:
        failed.append(f"{_LABEL}: {section} {rid}: missing keys {missing}")
    extra = sorted(str(k) for k in row if k not in allowed)
    if extra:
        failed.append(f"{_LABEL}: {section} {rid}: unknown keys {extra} (an unquoted comma in a flow mapping?)")
    nulls = sorted(str(k) for k, v in row.items() if v is None)
    if nulls:
        failed.append(f"{_LABEL}: {section} {rid}: null values for {nulls}")


def _check_entries(doc: dict, failed: list[str]) -> dict[str, dict]:
    """Invariants (1) and (2) for rows and billing rows; returns every entry by id."""
    statuses = _names(doc.get("status_values"))
    programme = _programme(doc)
    stages = _names(programme.get("stages"))
    owners = _names(doc.get("owner_values"))
    payers = _names(doc.get("payer_values"))
    entries: dict[str, dict] = {}
    for section in ("rows", "billing"):
        for row in _as_list(doc, section):
            if not isinstance(row, dict):
                continue
            rid = row.get("id", "?")
            _check_keys(section, row, failed)
            bad = _unhashable_fields(row, skip=("rec",))
            if bad or isinstance(rid, (list, dict)):
                failed.append(
                    f"{_LABEL}: {section} {rid if not bad else row.get('id', '?')}: non-scalar values for {bad or ['id']}"
                )
                continue
            if rid in entries:
                failed.append(f"{_LABEL}: {section} {rid}: duplicate id")
            entries[rid] = row
            if row.get("status") not in statuses:
                failed.append(f"{_LABEL}: {rid}: status {row.get('status')!r} not in status_values")
            if row.get("stage") not in stages:
                failed.append(f"{_LABEL}: {rid}: stage {row.get('stage')!r} not in programme.stages")
            if section == "rows" and row.get("owner_today") not in owners:
                failed.append(f"{_LABEL}: {rid}: owner_today {row.get('owner_today')!r} not in owner_values")
            if section == "billing" and row.get("payer") not in payers:
                failed.append(f"{_LABEL}: {rid}: payer {row.get('payer')!r} not in payer_values")
            rec = row.get("rec")
            if not isinstance(rec, list) or not all(_REC_RE.match(str(r)) for r in rec):
                failed.append(f"{_LABEL}: {rid}: rec must be a list of rec-NNNN")
    return entries


def _check_vocabularies(doc: dict, failed: list[str]) -> None:
    statuses = _names(doc.get("status_values"))
    for name in ("evidence_required", "terminal_statuses"):
        declared = _names(doc.get(name))
        if not declared <= statuses:
            failed.append(f"{_LABEL}: {name} names statuses outside status_values: {sorted(declared - statuses)}")
    terminal = _names(doc.get("terminal_statuses"))
    if terminal & _OPEN_STATUSES:
        failed.append(f"{_LABEL}: terminal_statuses includes an open status: {sorted(terminal & _OPEN_STATUSES)}")
    if not _OPEN_STATUSES <= statuses:
        failed.append(f"{_LABEL}: status_values is missing an open status: {sorted(_OPEN_STATUSES - statuses)}")
    programme = _programme(doc)
    if programme.get("phase") not in (programme.get("phase_values") or []):
        failed.append(f"{_LABEL}: programme.phase {programme.get('phase')!r} is not in programme.phase_values")


def _check_ledger(doc: dict, entries: dict[str, dict], failed: list[str]) -> None:
    """Invariant (3): the ledger is dated, ordered, names real rows, and its latest entry equals each row status."""
    statuses = _names(doc.get("status_values"))
    required = _names(doc.get("evidence_required"))
    latest: dict[str, str] = {}
    previous: datetime.date | None = None
    for i, entry in enumerate(_as_list(doc, "evidence_ledger")):
        if not isinstance(entry, dict):
            continue
        extra = sorted(str(k) for k in entry if k not in _LEDGER_KEYS)
        if extra:
            failed.append(f"{_LABEL}: evidence_ledger[{i}]: unknown keys {extra}")
        if _unhashable_fields(entry):
            failed.append(f"{_LABEL}: evidence_ledger[{i}]: non-scalar values for {_unhashable_fields(entry)}")
            continue
        missing = [k for k in _LEDGER_KEYS if not entry.get(k)]
        if missing:
            failed.append(f"{_LABEL}: evidence_ledger[{i}]: missing {missing}")
            continue
        day = _date(entry["date"])
        if day is None or (previous is not None and day < previous):
            failed.append(f"{_LABEL}: evidence_ledger[{i}]: date malformed or earlier than the entry before it")
        previous = day or previous
        if entry["row"] not in entries:
            failed.append(f"{_LABEL}: evidence_ledger[{i}]: row {entry['row']!r} does not exist")
        if entry["status"] not in statuses:
            failed.append(f"{_LABEL}: evidence_ledger[{i}]: status {entry['status']!r} not in status_values")
        latest[entry["row"]] = entry["status"]
    for rid, row in entries.items():
        status = row.get("status")
        if status in required and latest.get(rid) != status:
            failed.append(f"{_LABEL}: {rid}: status {status} needs a latest evidence_ledger entry with that status")
        elif rid in latest and latest[rid] != status:
            failed.append(f"{_LABEL}: {rid}: status {status} disagrees with its latest ledger entry ({latest[rid]})")


def _resolve_node(node: Any, parts: list[str]) -> bool:
    """Walk dotted segments; at a list, try every id-joining split of the remaining segments, shortest first."""
    if not parts:
        return True
    if isinstance(node, dict):
        return parts[0] in node and _resolve_node(node[parts[0]], parts[1:])
    if isinstance(node, list):
        by_id = {e.get("id"): e for e in node if isinstance(e, dict)}
        for k in range(1, len(parts) + 1):
            hit = by_id.get(".".join(parts[:k]))
            if hit is not None and _resolve_node(hit, parts[k:]):
                return True
    return False


def _ref_resolves(root: Path, ref: str) -> bool:
    path, _, dotted = ref.partition("#")
    target = (root / path).resolve()
    if not dotted or not target.is_relative_to(root.resolve()) or not target.is_file():
        return False
    try:
        import yaml

        node = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- an unparseable target is an unresolved ref
        return False
    return _resolve_node(node, dotted.split("."))


def _check_refs_and_follows(root: Path, entries: dict[str, dict], failed: list[str]) -> None:
    """Invariants (4) and (5)."""
    for rid, row in entries.items():
        if "ref" in row and not _ref_resolves(root, str(row["ref"])):
            failed.append(f"{_LABEL}: {rid}: ref {row['ref']!r} does not resolve")
        if "expires" in row and _date(row["expires"]) is None:
            failed.append(f"{_LABEL}: {rid}: expires is not an ISO date")
        if (row.get("status") == "follows") != ("follows" in row):
            failed.append(f"{_LABEL}: {rid}: status follows and the follows key go together")
        if "follows" in row:
            target = entries.get(row["follows"])
            if target is None or target.get("status") == "follows":
                failed.append(
                    f"{_LABEL}: {rid}: follows {row['follows']!r} must name an existing row that is not itself follows"
                )


def _check_shape_guard(text: str, failed: list[str]) -> None:
    """Invariant (6): the Decision 101 shapes, reported by line and category, never the matched text."""
    for n, line in enumerate(text.splitlines(), 1):
        for pattern, label in _SHAPES:
            if pattern.search(line):
                failed.append(f"{_LABEL}: line {n}: {label} (publication_rule)")


def _check_close_out(doc: dict, entries: dict[str, dict], failed: list[str]) -> None:
    """Invariant (7)."""
    programme = _programme(doc)
    if programme.get("phase") != "closed":
        return
    terminal = _names(doc.get("terminal_statuses"))
    for rid, row in entries.items():
        effective = entries.get(str(row.get("follows")), row) if row.get("status") == "follows" else row
        if effective.get("status") not in terminal:
            failed.append(f"{_LABEL}: {rid}: programme is closed but the row is {row.get('status')}")


def _check_owning_side(doc: dict, failed: list[str]) -> None:
    """Invariant (8)."""
    for section, side_key, table in (("rows", "owner_today", _OWNER_FOR_STATUS), ("billing", "payer", _PAYER_FOR_STATUS)):
        for row in _as_list(doc, section):
            if not isinstance(row, dict):
                continue
            allowed = table.get(str(row.get("status")))
            if allowed is not None and row.get(side_key) not in allowed:
                failed.append(
                    f"{_LABEL}: {row.get('id', '?')}: {side_key} {row.get(side_key)!r} disagrees with status "
                    f"{row.get('status')} (maintenance.stage_rule)"
                )


def _base_contract(root: Path) -> dict | None:
    base = _common.status_diff_base(root)
    if base is None:
        return None
    result = _common.run(["git", "show", f"{base}:{_CONTRACT}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return None
    try:
        import yaml

        data = yaml.safe_load(result.stdout)
    except Exception:  # noqa: BLE001 -- an unparseable base skips the invariant
        return None
    return data if isinstance(data, dict) else None


def _ids(doc: dict) -> list[str]:
    return [r["id"] for k in ("rows", "billing") for r in _as_list(doc, k) if isinstance(r, dict) and "id" in r]


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(item in it for item in needle)


def _check_append_only(doc: dict, root: Path, failed: list[str]) -> None:
    """Invariant (9): against the base, no row or billing id is removed or reordered and the ledger only grows."""
    base = _base_contract(root)
    if base is None:
        print(f"SKIP: {_LABEL}: append-only invariant (9): no base contract to compare against")
        return
    for section in ("rows", "billing"):
        base_ids, head_ids = _ids({section: base.get(section)}), _ids({section: doc.get(section)})
        for rid in base_ids:
            if rid not in head_ids:
                failed.append(f"{_LABEL}: {section} {rid}: removed since the base (rows are never deleted)")
        kept = [r for r in base_ids if r in head_ids]
        if not _is_subsequence(kept, head_ids):
            failed.append(f"{_LABEL}: {section}: rows reordered since the base")
    base_ledger, head_ledger = _as_list(base, "evidence_ledger"), _as_list(doc, "evidence_ledger")
    if head_ledger[: len(base_ledger)] != base_ledger:
        failed.append(f"{_LABEL}: evidence_ledger: the base ledger is not a prefix (entries edited, removed or reordered)")


@registry.register("validate_account_credential_inventory", owner="platform")
def validate_account_credential_inventory(failed: list[str]) -> None:
    """Gate the cutover inventory's shape, vocabularies, ledger, refs, owning side, close-out and append-only rule."""
    print("\n=== Account credential inventory (rec-4542) ===")
    root = _common.ROOT
    doc = load_contract(root)
    if doc is None:
        failed.append(f"{_LABEL}: {_CONTRACT} is missing or unparseable")
        return
    _check_shape_guard((root / _CONTRACT).read_text(encoding="utf-8"), failed)
    _check_shapes(doc, failed)
    _check_vocabularies(doc, failed)
    entries = _check_entries(doc, failed)
    _check_ledger(doc, entries, failed)
    _check_refs_and_follows(root, entries, failed)
    _check_close_out(doc, entries, failed)
    _check_owning_side(doc, failed)
    _check_append_only(doc, root, failed)
    registry.examined(len(_as_list(doc, "rows")) + len(_as_list(doc, "billing")), unit="inventory_rows")
