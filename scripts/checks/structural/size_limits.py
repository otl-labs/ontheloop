"""Structural-size limit gate (SGE-01/SGE-02/SGE-04, Decision 166).

Walks scripts.checks.structural._classify.iter_measured_files(); for each governed file,
compares effective lines against its `budgets:` roster entry (else its class row's
`limit`), and its longest line against its `long_line_budgets:` roster entry (else its
class row's `max_line_chars`). Registered in BOTH presubmit tiers, unscoped (no
pre_globs) -- measured whole-tree cost is ~0.1s against Decision 73's 300s fast-tier
budget, so pre_globs machinery would cost more complexity than it saves (the
validate_sloc_limits precedent).

Also carries escape_violations() -- the STATIC half of the escape-accountability legs
(SGE-05). It answers "can this registry widen its own gate without anyone being
accountable for it": E1 pins the residual floor, E2 pins every governed class ceiling to a
per-slug mapping that lives in code rather than config, E3 makes every defer_to_incumbent
entry attributable, and E4 keeps a frozen budget note coherent with the roster it
annotates. The BASE-aware half of E4 (direction, un-freezing, newly-deferred) needs the
base-versus-current diff and therefore lives in budget_raises.py instead.

Every leg returns a LEG-LABELLED message and never raises: a governed row carrying a
non-integer limit, a missing residual row and a deleted frozen path are all reported, not
crashed on (Decision 55 -- a crash inside a presubmit check obscures which gate failed).
"""

from __future__ import annotations

from scripts.checks import _common, _marker_guard, registry
from scripts.checks.structural import _byte_budget
from scripts.checks.structural._classify import effective_lines, iter_measured_files, load_registry, longest_line

_RESIDUAL_SLUG = "residual"
_RESIDUAL_LIMIT = 500
_RESIDUAL_MAX_LINE_CHARS = 2000

# Per-slug ceilings a governed class row is permitted to declare, as (limit,
# max_line_chars). SHIPS EMPTY and stays a reviewed CODE edit, never a config drop-in
# (the _marker_guard.RETRO_SCAN_GRANDFATHER shape): a slug absent here defaults to the
# residual defaults above, so a new governed class at 500 needs no edit and only a class
# wanting MORE than 500 carries the friction. Deliberately per-slug rather than a global
# scalar -- granting one class a higher ceiling must not raise it for the other eight
# (Decision 162 R1 pins a per-member set, not a global scalar).
_PERMITTED_MAXIMA: dict[str, tuple[int, int]] = {}

_GLOB_CHARS = ("*", "?", "[")


def _permitted(slug: str) -> tuple[int, int]:
    return _PERMITTED_MAXIMA.get(slug, (_RESIDUAL_LIMIT, _RESIDUAL_MAX_LINE_CHARS))


def _e1_residual_floor(rows: list[dict]) -> list[str]:
    residual = [row for row in rows if row.get("slug") == _RESIDUAL_SLUG]
    if not residual:
        return [
            (
                f"E1: the mandatory `{_RESIDUAL_SLUG}` class row is missing -- every unmatched tracked file would "
                "fall out of the measured set entirely. Restore it as the LAST row at "
                f"{_RESIDUAL_LIMIT}/{_RESIDUAL_MAX_LINE_CHARS}."
            )
        ]
    violations: list[str] = []
    if len(residual) > 1:
        violations.append(f"E1: {len(residual)} `{_RESIDUAL_SLUG}` rows declared -- exactly one is permitted.")
    if rows[-1].get("slug") != _RESIDUAL_SLUG:
        violations.append(
            f"E1: `{_RESIDUAL_SLUG}` must be the LAST class row (first-match-wins terminal default), but the "
            f"last row is `{rows[-1].get('slug')}`."
        )
    row = residual[0]
    for field, expected in (("limit", _RESIDUAL_LIMIT), ("max_line_chars", _RESIDUAL_MAX_LINE_CHARS)):
        value = row.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value != expected:
            violations.append(
                f"E1: `{_RESIDUAL_SLUG}` row declares {field} {value!r}, but the residual floor is pinned at "
                f"{expected} -- raising the default silently raises every unclassified file at once."
            )
    return violations


def _e2_class_ceilings(rows: list[dict]) -> list[str]:
    violations: list[str] = []
    for row in rows:
        if not row.get("governed", False):
            continue
        slug = row.get("slug")
        max_limit, max_chars = _permitted(str(slug))
        for field, ceiling in (("limit", max_limit), ("max_line_chars", max_chars)):
            value = row.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                violations.append(
                    f"E2: governed class row `{slug}` declares a non-integer {field} {value!r} -- a governed row "
                    "must carry an integer ceiling (the `n/a` form belongs to exempt rows only)."
                )
                continue
            if value > ceiling:
                violations.append(
                    f"E2: governed class row `{slug}` declares {field} {value}, above the permitted maximum "
                    f"{ceiling}. A higher ceiling is granted per-slug in scripts/checks/structural/size_limits.py's "
                    "_PERMITTED_MAXIMA, never by editing this config."
                )
    return violations


def _e3_deferral_accountability(rows: list[dict]) -> list[str]:
    sequenced = {step.name for step in registry.pre_sequence()} | {step.name for step in registry.full_sequence()}
    bodies = _marker_guard.load_decision_bodies()
    violations: list[str] = []
    for row in rows:
        deferrals = row.get("defer_to_incumbent") or {}
        if not isinstance(deferrals, dict):
            violations.append(
                f"E3: class row `{row.get('slug')}` declares defer_to_incumbent as {type(deferrals).__name__}, "
                "but it must be a mapping of exact path -> {incumbent, authorized_by}."
            )
            continue
        for path, fields in sorted(deferrals.items()):
            label = f"E3: defer_to_incumbent `{path}`"
            if any(ch in path for ch in _GLOB_CHARS):
                violations.append(
                    f"{label} is a glob -- defer_to_incumbent keys must be exact paths, because a glob "
                    "un-governs every file it happens to match, now and in the future."
                )
            elif not (_common.ROOT / path).exists():
                violations.append(f"{label} names a path that does not exist -- remove the stale deferral.")
            if not isinstance(fields, dict):
                violations.append(f"{label} carries {type(fields).__name__}, not the {{incumbent, authorized_by}} mapping.")
                continue
            incumbent = fields.get("incumbent")
            if not incumbent:
                violations.append(f"{label} declares no `incumbent` -- name the gate that governs this path instead.")
            elif incumbent not in sequenced:
                violations.append(
                    f"{label} names incumbent `{incumbent}`, which is not in the validate check sequence -- "
                    "the deferral points at nothing."
                )
            authorized_by = fields.get("authorized_by")
            if not authorized_by:
                violations.append(f"{label} declares no `authorized_by` -- name the Decision that authorizes it.")
            elif _marker_guard.authorization_failure(str(authorized_by), path, bodies):
                violations.append(
                    f"{label} cites {authorized_by}, but that Decision's body does not name `{path}` -- "
                    "an unattributable deferral un-governs a file with nobody accountable."
                )
    return violations


def _e4_frozen_note_integrity(reg: dict, rows: list[dict]) -> list[str]:
    notes = reg.get("budget_notes") or {}
    if not isinstance(notes, dict):
        return [f"E4: budget_notes must be a mapping of path -> note, got {type(notes).__name__}."]
    budgets = reg.get("budgets") or {}
    deferred: set[str] = set()
    for row in rows:
        entry = row.get("defer_to_incumbent") or {}
        if isinstance(entry, dict):
            deferred |= set(entry)

    violations: list[str] = []
    for path, note in sorted(notes.items()):
        if not isinstance(note, dict):
            violations.append(f"E4: budget_notes `{path}` carries {type(note).__name__}, not a note mapping.")
            continue
        if path not in budgets:
            violations.append(
                f"E4: budget_notes `{path}` annotates no `budgets:` entry -- a note that outlives its entry "
                "describes a roster row a future reader cannot find. Remove the note and the entry together."
            )
        if note.get("frozen") is not True:
            continue
        if not (_common.ROOT / path).exists():
            violations.append(
                f"E4: frozen budget_notes `{path}` names a path that no longer exists on disk -- if its "
                "retires_when migration removed the file, remove the note and its budgets entry together."
            )
        if path in deferred:
            violations.append(
                f"E4: `{path}` is frozen AND deferred to an incumbent -- these dispositions are mutually "
                "exclusive, because a deferred path is dropped before measurement and the freeze governs nothing."
            )
    return violations


def escape_violations(reg: dict) -> list[str]:
    """Leg-labelled accountability violations for one loaded registry mapping.

    E1 residual floor, E2 per-slug class ceiling, E3 deferral accountability, E4 frozen
    budget-note integrity. Every message is prefixed with its leg label so a verification
    assertion can never be satisfied by a different leg than the one it claims to pin.
    """
    rows = reg.get("classes") or []
    if not isinstance(rows, list) or not rows:
        return ["E1: the `classes:` table is missing or empty -- nothing is governed."]
    return (
        _e1_residual_floor(rows)
        + _e2_class_ceilings(rows)
        + _e3_deferral_accountability(rows)
        + _e4_frozen_note_integrity(reg, rows)
        + _byte_budget.e2_violations(rows)
        + _byte_budget.roster_violations(reg, _common.ROOT)
    )


@registry.register("validate_structural_size_limits", owner="platform")
def validate_structural_size_limits(failed: list[str]) -> None:
    """Enforce the structural-size class engine (Decision 166): per-class effective-line
    limit plus the max-line-character companion, over every governed non-Python class."""
    print("\n=== Structural-size limits (Decision 166) ===")
    reg = load_registry()
    classes_by_slug = {row["slug"]: row for row in reg.get("classes") or []}
    budgets: dict[str, int] = reg.get("budgets") or {}
    long_line_budgets: dict[str, int] = reg.get("long_line_budgets") or {}

    errors: list[str] = []
    measured = list(iter_measured_files())
    for rel, slug in measured:
        text = (_common.ROOT / rel).read_text(encoding="utf-8", errors="replace")
        class_row = classes_by_slug[slug]
        relief_valves = class_row.get("relief_valve", "")

        errors.extend(_byte_budget.file_violations(rel, (_common.ROOT / rel).stat().st_size, class_row, reg))

        eff = effective_lines(text)
        limit = budgets.get(rel, class_row["limit"])
        if eff > limit:
            errors.append(
                f"{rel}: {eff} effective lines exceeds limit {limit} (class: {slug}). Relief valves: {relief_valves}"
            )

        longest = longest_line(text)
        char_limit = long_line_budgets.get(rel, class_row["max_line_chars"])
        if longest > char_limit:
            errors.append(
                f"{rel}: longest line {longest} chars exceeds limit {char_limit} (class: {slug}). "
                f"Relief valves: {relief_valves}"
            )

    errors.extend(escape_violations(reg))
    registry.examined(len(measured), unit="measured_files")

    if errors:
        print("Structural-size violations:")
        for e in errors:
            print(f"  - {e}")
        failed.append("Structural-size limits (Decision 166)")
    else:
        print("All governed files within structural-size budgets.")
