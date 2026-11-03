"""Retired tier_item archive -- stubs, integrity and hydration (Decision 227, Decision 147's valve).

docs/ROADMAP-PLATFORM-ARCHIVE.yaml holds the full records of retired (status complete or
reserved) tier_items, moved byte-for-byte out of docs/ROADMAP-PLATFORM.yaml. Each archived item
leaves derive_stub(record) in its original place, so the live file alone still resolves ids,
dependencies and eligibility. scripts.platform_roadmap_state.load hydrates every stub through
hydrate_from_disk before schema validation; raw readers that need retired content use the same
helpers. The conditional logic of every archive-aware check leg lives here, so the checks only
gain always-executed calls. Nothing runs at import.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from scripts.contract_rules import parse_yaml

ARCHIVE_REL_PATH = "docs/ROADMAP-PLATFORM-ARCHIVE.yaml"
STUB_FIELDS: tuple[str, ...] = ("id", "tier", "name", "depends_on", "status", "completed_at")
RESERVED_STUB_FIELDS: tuple[str, ...] = ("id", "tier", "name", "intent", "depends_on", "status")
_ROADMAP_REL_PATH = "docs/ROADMAP-PLATFORM.yaml"
_RETIRED_STATUSES: frozenset[str] = frozenset({"complete", "reserved"})
_ARCHIVE_KEYS: frozenset[str] = frozenset({"archive_of", "tier_items"})


class ArchiveIntegrityError(ValueError):
    """The live roadmap's stubs and its archive's records disagree; .issues names each one."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        super().__init__("archive integrity: " + "; ".join(self.issues))


def archive_path_for(roadmap_path: Path) -> Path:
    """The archive sibling of a roadmap path: <stem>-ARCHIVE<suffix> in the same directory."""
    return roadmap_path.with_name(f"{roadmap_path.stem}-ARCHIVE{roadmap_path.suffix}")


def derive_stub(record: Mapping[str, Any]) -> dict[str, Any]:
    """The stub an archived record leaves in the live file: its present stub fields plus archived: True.

    A reserved record keeps intent (its successor pointer) in place of completed_at.
    """
    fields = RESERVED_STUB_FIELDS if record.get("status") == "reserved" else STUB_FIELDS
    stub = {key: record[key] for key in fields if key in record}
    stub["archived"] = True
    return stub


def _live_items(document: Any) -> list[Mapping[str, Any]]:
    items = document.get("tier_items") if isinstance(document, Mapping) else None
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


def has_stubs(document: Any) -> bool:
    """True when the document is a mapping whose tier_items hold any mapping carrying an archived key."""
    return any("archived" in item for item in _live_items(document))


def _archive_shape_issues(archive: Any) -> list[str]:
    if not isinstance(archive, Mapping) or set(archive) != _ARCHIVE_KEYS:
        return [f"{ARCHIVE_REL_PATH} must be a mapping with exactly the keys archive_of and tier_items"]
    issues: list[str] = []
    if not isinstance(archive["archive_of"], str) or not archive["archive_of"]:
        issues.append(f"{ARCHIVE_REL_PATH} archive_of must be a non-empty string")
    records = archive["tier_items"]
    if not isinstance(records, list) or not all(isinstance(r, Mapping) and isinstance(r.get("id"), str) for r in records):
        issues.append(f"{ARCHIVE_REL_PATH} tier_items must be a list of mappings with string ids")
    return issues


def _record_issues(records: list[Mapping[str, Any]]) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    issues: list[str] = []
    for record in records:
        rid = record["id"]
        if rid in by_id:
            issues.append(f"archive record '{rid}' appears more than once")
        by_id.setdefault(rid, record)
        if record.get("status") not in _RETIRED_STATUSES:
            issues.append(f"archive record '{rid}' has status {record.get('status')!r}, not complete or reserved")
        if "archived" in record:
            issues.append(f"archive record '{rid}' carries an archived key")
    return by_id, issues


def integrity_issues(document: Any, archive: Any) -> list[str]:
    """Every disagreement between the live document's stubs and the archive's records, each naming its id.

    (a) a non-None archive is a mapping of exactly archive_of (non-empty str) and tier_items (mappings
    with str ids); (b) record ids are unique, every record is complete or reserved, and none carries
    archived; (c) every live archived key is True; (d) stub ids are unique and each stub equals
    derive_stub of its one record; (e) every record has a stub; (f) stubs with no archive are one issue.
    """
    items = _live_items(document)
    issues = [
        f"tier_item '{i.get('id')}' carries archived: {i['archived']!r}, not true"
        for i in items
        if i.get("archived", True) is not True
    ]
    stubs = [item for item in items if item.get("archived") is True]
    if archive is None:
        return issues + ([f"{len(stubs)} archived stub(s) but {ARCHIVE_REL_PATH} is absent"] if stubs else [])
    shape = _archive_shape_issues(archive)
    if shape:
        return issues + shape
    by_id, record_issues = _record_issues(archive["tier_items"])
    issues += record_issues
    seen: set[str] = set()
    for stub in stubs:
        sid = str(stub.get("id"))
        record = by_id.get(sid)
        if sid in seen:
            issues.append(f"stub '{sid}' appears more than once")
        elif record is None:
            issues.append(f"stub '{sid}' has no record in {ARCHIVE_REL_PATH}")
        elif dict(stub) != derive_stub(record):
            issues.append(f"stub '{sid}' differs from derive_stub of its archived record")
        seen.add(sid)
    return issues + [f"archive record '{rid}' has no stub in the live roadmap" for rid in by_id if rid not in seen]


def hydrate(document: Any, archive: Any) -> Any:
    """The document with each stub replaced IN PLACE by its archived record (a shallow copy).

    A non-mapping document, or one with no stubs and no archive, is returned unchanged. Any
    integrity issue raises ArchiveIntegrityError.
    """
    stubbed = has_stubs(document)
    if not isinstance(document, Mapping) or (archive is None and not stubbed):
        return document
    issues = integrity_issues(document, archive)
    if issues:
        raise ArchiveIntegrityError(issues)
    if not stubbed:
        return document
    by_id = {record["id"]: record for record in archive["tier_items"]}
    items = [
        by_id[item["id"]] if isinstance(item, Mapping) and "archived" in item else item for item in document["tier_items"]
    ]
    return {**document, "tier_items": items}


def _base_records(archive_text: str | None) -> dict[str, Any]:
    try:
        archive = None if archive_text is None else parse_yaml(archive_text)
    except yaml.YAMLError:
        archive = None
    records = archive.get("tier_items") if isinstance(archive, Mapping) else None
    return {str(r.get("id")): r for r in records if isinstance(r, Mapping)} if isinstance(records, list) else {}


def hydrate_base(document: Any, archive_text: str | None) -> Any:
    """LENIENT diff-base hydrator (origin/main was validated when it merged): never raises.

    Each stub is replaced in place by the base archive's record with the same id when one exists,
    with no stub-equality check, so a later STUB_FIELDS revision never reads every archived record
    as edited. None, unparseable or non-mapping archive text means no records.
    """
    if not has_stubs(document):
        return document
    records = _base_records(archive_text)
    items = [
        records.get(str(item.get("id")), item) if isinstance(item, Mapping) and "archived" in item else item
        for item in document["tier_items"]
    ]
    return {**document, "tier_items": items}


def read_archive(roadmap_path: Path) -> Any:
    """The parsed archive sibling of roadmap_path, or None when it is absent."""
    path = archive_path_for(roadmap_path)
    return parse_yaml(path.read_text(encoding="utf-8")) if path.exists() else None


def archive_texts(roadmap_path: Path) -> list[str]:
    """[the archive sibling's text], or [] when it is absent."""
    path = archive_path_for(roadmap_path)
    return [path.read_text(encoding="utf-8")] if path.exists() else []


def hydrate_from_disk(document: Any, roadmap_path: Path) -> Any:
    """hydrate(document, read_archive(roadmap_path)) -- the one call load() makes."""
    return hydrate(document, read_archive(Path(roadmap_path)))


def edited_records_with_bare_strings(archive: Any, base_items: Mapping[str, Any]) -> list[str]:
    """Sorted ids of archive records that differ from (or are absent in) base_items and carry a bare-string criterion."""
    records = archive.get("tier_items") if isinstance(archive, Mapping) else None
    return sorted(
        str(record.get("id"))
        for record in (records if isinstance(records, list) else [])
        if isinstance(record, Mapping)
        and record != base_items.get(str(record.get("id")))
        and any(isinstance(crit, str) for crit in record.get("exit_criteria") or [])
    )


def criterion_ii_archive_issues(roadmap_path: Path, base_reader: Callable[[str], str | None]) -> list[str]:
    """validate_platform_roadmap criterion (ii) over archived records: one FAIL line per record edited
    relative to origin/main that still carries a bare-string criterion. A pure move passes."""
    archive = read_archive(Path(roadmap_path))
    if archive is None:
        return []
    base_text = base_reader(_ROADMAP_REL_PATH)
    if base_text is None:
        print("  SKIP: origin/main roadmap unreachable -- criterion (ii) archive leg not evaluated")
        return []
    base = hydrate_base(parse_yaml(base_text), base_reader(ARCHIVE_REL_PATH))
    base_items = {str(item.get("id")): item for item in _live_items(base)}
    return [
        f"  FAIL: archived tier_item '{rid}' is edited relative to origin/main but still has bare-string criteria "
        "-- convert to ExitCriterion objects"
        for rid in edited_records_with_bare_strings(archive, base_items)
    ]
