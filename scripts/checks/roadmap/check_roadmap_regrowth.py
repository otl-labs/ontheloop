"""Roadmap compaction regrowth observable -- REPORT-ONLY (Decision 147 point 4, rec-2781).

Decision 147 compacted docs/ROADMAP-PLATFORM.yaml from 9,996 to 7,329 lines and made a durable
norm of it: ratified/superseded `candidate_decisions` and complete/reserved `tier_items` are
stored compact, their narrative living in DECISIONS.md (Decision 86) or in git history. Point 4
of that Decision asks for a mechanical anti-regrowth observable so the compaction does not
silently erode on the next edit to a terminal item. This is that observable.

REPORT-ONLY IS ABSOLUTE. `failed` is never appended to on ANY path, the roadmap is never
written, and the presubmit exit code is identical with and without this check on every input --
which also means it NEVER RAISES: scripts/checks/validation_result.py's dispatch_recording runs
a check inside registry.outcome_scope with NO try/except, so an escaping exception would abort
the whole tier run and destroy that run's CI-RCA attribution substrate.

WHAT IS REPORTED. One growth line in a fixed grammar (`GROWTH lines= baseline= growth= headroom=
escalate= threshold=`), whose `escalate` field reads true once headroom to the Decision 114
ceiling falls below _ESCALATE_HEADROOM_LINES -- the named trip threshold Decision 147's reversal
condition needs a number for, printed unconditionally and never failing. Then the terminal
content that has re-accumulated: ratified/superseded candidate_decisions carrying more than the
single compact line Decision 147 point 1 retains (CD.7's "fully superseded by CD.28" marker,
which validate_candidate_decision_supersession reads as a literal phrase, is exactly that one
allowed line), and complete/reserved tier_items carrying progress_note/note/decomposition_hints,
multi-line intent, or a non-empty files_in_scope. Then one MALFORMED-STATE line counting the
records whose `state`/`status` is not a string at all -- zero on the live document, and printed
unconditionally so a shape no terminal branch can speak for is surfaced rather than dropped.

DECLARATION (Decision 170 arm (a), which the frozen `_BASELINE_SEED` in
scripts/checks/hygiene/validate_check_accounting.py offers this check no grandfather path out
of: report-only status is not an exemption). Every reachable exit declares --
examined(n, unit="terminal_records") on the normal exit, and ONE skipped(reason) exit that every
unusable-input shape routes to with `failed` left untouched: OSError (an absent or unreadable
roadmap), UnicodeDecodeError (undecodable bytes), yaml.YAMLError (malformed YAML -- a LIVE
fast-tier input, since the Entry's pre_globs gate this check on docs/ROADMAP-*), a non-mapping
document, a non-list `tier_items` or `candidate_decisions`, and a `tier_items` or
`candidate_decisions` list whose ENTRIES are not mappings. That last shape is enumerated
deliberately: it is a list, so a non-list guard passes it through, and an unguarded
`raw_item.get()` on an int, a str or None then raises AttributeError. validate_platform_roadmap
is not exposed to it because it reaches its raw-item loop only after a successful Pydantic load;
this check does its own bare yaml.safe_load with no schema, so it is. Only named exception
classes are caught and there is no bare except anywhere in this module --
scripts/checks/hygiene/validate_declaring_coverage.py is the precedent followed literally.

TOTALITY OVER THE ENTRY SHAPE, not over an enumerated list of them. A mapping entry whose
`status`/`state` value is itself a YAML list or mapping is a SECOND, disjoint hazard from the
non-mapping entry above: the entry passes every shape gate, and a bare
`entry.get("status") in <frozenset>` then raises TypeError on the unhashable value. Every read of
those two fields therefore goes through `_state_of`, which yields the value only when it is a
string; any other shape is neither terminal nor non-terminal, is counted on the MALFORMED-STATE
line and reaches no membership test at all. The shapes this module is total over are the ones
the mirror test's matrix drives (TestUnavailableRoadmapSkips and
TestNonStringStateIsCountedNotRaised), not a list frozen in prose.

ARCHIVED RECORDS (Decision 227). Retired tier_items moved to the roadmap's archive sibling
(<stem>-ARCHIVE<suffix>, derived locally so this Entry's import closure stays scripts/checks/) are
still observed: their regrowth joins the TERMINAL-ITEM ids, and one ARCHIVE line counts them. An
absent archive means no records; every unusable archive shape (unreadable, undecodable, malformed
YAML, a non-mapping document, a non-list tier_items, a non-mapping record) routes to the same single
skipped(reason) exit, with a reason that names the archive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

_ROADMAP_REL_PATH = "docs/ROADMAP-PLATFORM.yaml"
_ARCHIVE_REL_PATH = "docs/ROADMAP-PLATFORM-ARCHIVE.yaml"
_COMPACTION_BASELINE_LINES = 7329  # Decision 147 point 3: 9,996 -> 7,329 lines
_CEILING_LINES = 10_000  # Decision 114, whose _ROADMAP_MAX_LINES this check leaves untouched
_ESCALATE_HEADROOM_LINES = 500
_COMPACT_DETAIL_MAX_LINES = 1  # Decision 147 point 1's retained CD.7 supersession marker
_TERMINAL_CD_STATES: frozenset[str] = frozenset({"ratified", "superseded"})
_TERMINAL_ITEM_STATUSES: frozenset[str] = frozenset({"complete", "reserved"})
_CD_PROSE_FIELDS: tuple[str, ...] = ("detail", "realization_evidence")
_ITEM_PROSE_FIELDS: tuple[str, ...] = ("progress_note", "note", "decomposition_hints")
_REPORT_ONLY_NOTICE = (
    "  REPORT-ONLY: nothing is failed here -- Decision 147's reversal condition escalates a "
    "second ceiling breach to archival or a cited temporary raise, never to another ad hoc trim."
)


def growth_line(line_count: int) -> str:
    """The growth observable's one line, in the grammar the graduated shard matches on.

    `escalate` is the named trip threshold Decision 147's reversal condition is stated against:
    true once headroom to the Decision 114 ceiling drops below _ESCALATE_HEADROOM_LINES. It never
    fails anything -- it is a printed field, not a gate.
    """
    headroom = _CEILING_LINES - line_count
    escalate = "true" if headroom < _ESCALATE_HEADROOM_LINES else "false"
    return (
        f"  GROWTH lines={line_count} baseline={_COMPACTION_BASELINE_LINES} "
        f"growth={line_count - _COMPACTION_BASELINE_LINES} headroom={headroom} "
        f"escalate={escalate} threshold={_ESCALATE_HEADROOM_LINES}"
    )


def _prose_lines(value: Any) -> int:
    """Non-blank lines a stored prose field contributes. A list (decomposition_hints) counts one
    per element and any other non-empty scalar counts one, so a non-string value is measured
    rather than silently ignored."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len([line for line in value.splitlines() if line.strip()])
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return 1


def _state_of(raw: Any) -> str | None:
    """The stored `state`/`status` AS A STRING, or None for every other shape.

    A non-string value (a YAML list, a mapping, a number, an absent key) is neither terminal nor
    non-terminal. Reading both fields by name through this guard is what keeps the terminal
    selectors' set-membership tests from raising TypeError on an UNHASHABLE value: dispatch_recording
    wraps a check body with no try/except, so that raise would abort the whole tier run.
    """
    return raw if isinstance(raw, str) else None


def malformed_state_ids(records: list[Any], key: str) -> list[str]:
    """Ids of records whose `key` value is not a string -- counted and printed rather than
    silently dropped, since neither terminal branch can speak for them."""
    return [str(record.get("id")) for record in records if _state_of(record.get(key)) is None]


def terminal_cd_ids(candidate_decisions: list[Any]) -> list[str]:
    """Ratified/superseded candidate_decisions whose detail prose exceeds the one compact line
    Decision 147 point 1 retains."""
    return [
        str(cd.get("id"))
        for cd in candidate_decisions
        if _state_of(cd.get("state")) in _TERMINAL_CD_STATES
        and sum(_prose_lines(cd.get(field)) for field in _CD_PROSE_FIELDS) > _COMPACT_DETAIL_MAX_LINES
    ]


def _item_regrew(item: dict) -> bool:
    """Whether one complete/reserved tier_item carries content Decision 147 point 2 removed."""
    if any(_prose_lines(item.get(field)) for field in _ITEM_PROSE_FIELDS):
        return True
    if _prose_lines(item.get("intent")) > 1:
        return True
    return bool(item.get("files_in_scope"))


def terminal_item_ids(tier_items: list[Any]) -> list[str]:
    """Complete/reserved tier_items carrying progress_note/note/decomposition_hints, multi-line
    intent, or a non-empty files_in_scope."""
    return [
        str(item.get("id"))
        for item in tier_items
        if _state_of(item.get("status")) in _TERMINAL_ITEM_STATUSES and _item_regrew(item)
    ]


def unusable_reason(document: Any) -> str | None:
    """Why a loaded document cannot be measured, or None when it can.

    A pure predicate with no declaration of its own, so the single skipped() call stays on the
    check's own body where the path-aware declaring-coverage walker can see it.
    """
    if not isinstance(document, dict):
        return "document is not a mapping"
    for key in ("tier_items", "candidate_decisions"):
        block = document.get(key) or []
        if not isinstance(block, list):
            return f"{key} is not a list"
        if not all(isinstance(entry, dict) for entry in block):
            return f"{key} holds a non-mapping entry"
    return None


def archive_records(roadmap_path: Path) -> tuple[list[Any], str | None]:
    """The archived records beside roadmap_path, and why they cannot be measured (None when they can).

    A pure reader with no declaration of its own: an absent archive is no records, and every other
    unusable shape yields a reason the check body routes to its single skipped() exit.
    """
    archive_path = roadmap_path.with_name(f"{roadmap_path.stem}-ARCHIVE{roadmap_path.suffix}")
    try:
        archive = yaml.safe_load(archive_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], f"{_ARCHIVE_REL_PATH} unusable ({type(exc).__name__}) -- no growth report emitted"
    records = archive.get("tier_items") if isinstance(archive, dict) else None
    if not isinstance(archive, dict):
        detail = "document is not a mapping"
    elif not isinstance(records, list):
        detail = "tier_items is not a list"
    elif not all(isinstance(record, dict) for record in records):
        detail = "tier_items holds a non-mapping record"
    else:
        return records, None
    return [], f"{_ARCHIVE_REL_PATH} unusable ({detail}) -- no growth report emitted"


@registry.register("check_roadmap_regrowth", owner="platform")
def check_roadmap_regrowth(failed: list[str], roadmap_path: Path | None = None) -> None:
    """Report roadmap growth against the Decision 147 compaction baseline and the terminal
    content that has re-accumulated. Never appends to `failed`, never writes the roadmap and
    never raises.

    `roadmap_path` is the named injection seam the mirror test and the verification plan drive;
    it defaults to _common.ROOT / docs / ROADMAP-PLATFORM.yaml.
    """
    print("\n=== Roadmap compaction regrowth (Decision 147, report-only) ===")
    path = roadmap_path if roadmap_path is not None else _common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml"
    text = ""
    document: Any = None
    reason: str | None = None
    try:
        text = path.read_text(encoding="utf-8")
        document = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        reason = f"{_ROADMAP_REL_PATH} unusable ({type(exc).__name__}) -- no growth report emitted"
    if reason is None:
        detail = unusable_reason(document)
        reason = None if detail is None else f"{_ROADMAP_REL_PATH} unusable ({detail}) -- no growth report emitted"
    archived, archive_reason = archive_records(path)
    reason = reason or archive_reason
    if reason is not None:
        print(f"  SKIP: {reason}")
        registry.skipped(reason)
        return
    tier_items = document.get("tier_items") or []
    candidate_decisions = document.get("candidate_decisions") or []
    regrown_cds = terminal_cd_ids(candidate_decisions)
    regrown_items = sorted(set(terminal_item_ids(tier_items)) | set(terminal_item_ids(archived)))
    terminal_cds = [cd for cd in candidate_decisions if _state_of(cd.get("state")) in _TERMINAL_CD_STATES]
    terminal_items = [item for item in tier_items if _state_of(item.get("status")) in _TERMINAL_ITEM_STATUSES]
    malformed = malformed_state_ids(candidate_decisions, "state")
    malformed_items = malformed_state_ids(tier_items, "status")
    print(growth_line(len(text.splitlines())))
    print(f"  TERMINAL-CD terminal={len(terminal_cds)} regrown={len(regrown_cds)} ids={regrown_cds}")
    print(f"  TERMINAL-ITEM terminal={len(terminal_items)} regrown={len(regrown_items)} ids={regrown_items}")
    print(f"  ARCHIVE records={len(archived)}")
    print(f"  MALFORMED-STATE cds={len(malformed)} items={len(malformed_items)} ids={malformed + malformed_items}")
    print(_REPORT_ONLY_NOTICE)
    registry.examined(len(terminal_cds) + len(terminal_items), unit="terminal_records")
