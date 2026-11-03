"""Shared raise-marker authorization module (Decision 128 mechanism, audit finding SGE-07).

Consolidates the five copy-pasted raise-marker guards (validate_sloc_budget_raises,
validate_prose_budget_raises, validate_coverage_baseline_edits, validate_mypy_baseline_edits,
validate_composite_action_shell_bodies's R3 leg) onto one shared implementation and upgrades
the check from EXISTENCE ("does a numbered Decision header exist for the cited number") to
AUTHORIZATION ("does that Decision's body actually name the governed entry key"). Full grammar
and field semantics live in docs/contracts/marker-grammar.yaml (Decision 86/127 routing) --
this module is the enforcement counterpart, not the semantics source.

Authorization is computed KEY-SIDE ONLY: the governed entry key's own ancestor prefixes (each
with >=2 path segments, plus the full key regardless of segment count) are generated and
substring-matched against the cited Decision's body. The body is never tokenized -- this is
what makes markdown `**bold**` markup a non-issue rather than a universal wildcard, and what
makes a bare single-segment root mention (`scripts/`, `tests/`) insufficient on its own to
authorize a deeper file.

Top-level placement per Decision 104: `_common.py` is reserved for its five named primitives;
a domain `_shared.py` may not span domains, and this module spans sloc/prose/misc/typing/
ci_guards. Imports ROOT from `_common` and never recomputes it
(tests/test_checks_registry.py::TestNoLocalRootRecomputation enforces this repo-wide).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from scripts.checks import _common
from scripts.checks._marker_guard_state_options import (
    base_corpus,
    bodies_from_text,
    effective_marker,
    mentions,
    transition_failure,
)
from scripts.decisions_md import extract_superseded_by

# Bounds this module's public surface to exactly the nine names
# docs/contracts/marker-grammar.yaml's binding_surface.exports promises (eight at Slice B, plus
# check_state_diff added by PLAN-verifier-weakening-guards) -- validate_marker_grammar_exports
# pins that these nine are PRESENT and no more; __all__ additionally keeps the surface from
# silently growing past what the contract documents (e.g. MarkerEntry, StateEntry,
# RETRO_SCAN_GRANDFATHER, and the two re-exported scripts.decisions_md names stay importable by
# explicit dotted access, but are not part of the promised star-import / documented surface).
__all__ = [
    "RegistrySpec",
    "make_flat_extractor",
    "make_section_extractor",
    "default_base_reader",
    "load_decision_bodies",
    "authorization_failure",
    "check_diff",
    "check_state_diff",
    "check_present_markers",
]

_DECISIONS_REL_PATH = "docs/DECISIONS.md"
_DECISIONS_ARCHIVE_REL_PATH = "docs/DECISIONS_ARCHIVE.md"

# The day-one-empty retroactive-scan grandfather hook (Decision 151 forward-conformance
# departure bound). MUST ship empty -- the measured install-state population is already
# authorization-clean, so any entry would be unjustified. Growing it later is a reviewed
# code edit by design (mirrors the composite guard's _R1_KNOWN_VIOLATORS shape), never a
# config edit.
RETRO_SCAN_GRANDFATHER: frozenset[str] = frozenset()

_MOVED_FROM_RE = re.compile(r"moved from\s+(\S+)")

# Matches both bare ("path/to/file: 500") and optionally-quoted ('"key::with#chars": 500')
# entry lines -- the quoted form is what lets composite's R3 "<action-rel-dir>::<step-id>"
# keys parse without a second regex.
_ENTRY_RE = re.compile(r'^"?(?P<key>[^"]+?)"?:\s*(?P<value>[\d.]+)\s*(?P<comment>#.*)?$')


class MarkerEntry(NamedTuple):
    value: float
    marker: str | None
    reason: str | None


class StateEntry(NamedTuple):
    """The check_state_diff counterpart of MarkerEntry, for a registry whose governed value is a
    STRENGTH (an arbitrary, spec-defined object compared via RegistrySpec.weakened), not a
    number. Deliberately OUT of __all__ -- importable by explicit dotted access only, mirroring
    the deliberate exclusion this module already records for MarkerEntry (both are constructor
    outputs of an extractor, never something a caller builds directly)."""

    state: object
    marker: str | None
    reason: str | None


@dataclass(frozen=True)
class RegistrySpec:
    """One registry's binding to the shared authorization mechanism.

    rel_path: repo-relative path to the registry YAML file.
    token: the marker's own token (e.g. "raise-approved", "baseline-lowered"), distinct per
        registry so a marker copy-pasted from one registry can never silently authorize an
        edit in another.
    gated_direction: "up" (an increase is gated; sloc/prose/mypy/composite-r3) or "down" (a
        decrease is gated; coverage).
    extractor: built by make_flat_extractor / make_section_extractor.
    gates_new_entry: predicate on a NEW entry's value -- whether registering it at all is
        gated (sloc/prose gate every new entry; coverage gates only sub-100; mypy gates only
        >0).
    label: the guard's own violation-summary string (also the sole `failed.append(...)` value
        so existing consumer-test substring assertions on `failed[0]` keep matching).
    relief_text: appended to a violation message when set (prose's non-decompose relief-valve
        text); empty for every other registry.
    moved_from_relief: enables the Decision 161 clause 4 `moved from <old-path>` same-diff
        proof. Default False; True on the mypy spec alone (Decision 161 cl.4 is the only
        Decision naming the form).
    mention_candidates: overrides the default path-ancestor candidate generator. Unset (None)
        for the four path-keyed registries; composite's R3 supplies one for its
        "<action-rel-dir>::<step-id>" key form.
    reason_required: declarative record of whether this registry's extractor was built with
        require_reason=True (a marker with an empty reason parses as no marker at all, matching
        the incumbent composite-only `_RAISE_APPROVED_RE`'s `\\s+\\S` non-empty-reason
        requirement). The actual gate is enforced by the extractor at construction time
        (make_flat_extractor / make_section_extractor's own require_reason parameter), and
        check_state_diff and check_present_markers honour it too (rec-3736) -- this
        field exists so a spec's policy is testable/documented in one place, mirroring
        moved_from_relief. Default False (sloc/prose/coverage/mypy never required a reason);
        True on the composite R3 spec alone.
    state_extractor: builds a dict[str, StateEntry] from raw text, for a registry whose governed
        value is a STRENGTH rather than a number -- the check_state_diff counterpart of
        `extractor`. None (default) on every one of the nine incumbent bindings; set together
        with weakened/gates_deletion, never alone.
    weakened: Callable[[state, state], bool] -- True iff the transition from a BASE state to a
        HEAD state is a weakening along the spec's own strength relation (tightening and no-change
        both return False). None (default) on every incumbent binding.
    gates_deletion: Callable[[state], bool] -- True iff a base state, once its entry is REMOVED at
        head, must be gated (no marker can rescue a deletion -- see check_state_diff). None
        (default) on every incumbent binding.
    fresh_mention: check_state_diff only -- a marker authorizes only text added to the cited Decision in the change.
    boundary_match: all three helpers -- a candidate counts only where no letter, digit, underscore or hyphen adjoins it.
    transition_label: check_state_diff only -- names the gated transition in its message (default weakening).
    """

    rel_path: str
    token: str
    gated_direction: str
    extractor: Callable[[str], dict[str, MarkerEntry]]
    gates_new_entry: Callable[[float], bool]
    label: str
    relief_text: str = ""
    moved_from_relief: bool = False
    mention_candidates: Callable[[str], list[str]] | None = None
    reason_required: bool = False
    state_extractor: Callable[[str], dict[str, StateEntry]] | None = None
    weakened: Callable[[object, object], bool] | None = None
    gates_deletion: Callable[[object], bool] | None = None
    fresh_mention: bool = False
    boundary_match: bool = False
    transition_label: str = "weakening"


def _parse_marker(
    comment: str | None, marker_re: re.Pattern[str], require_reason: bool = False
) -> tuple[str | None, str | None]:
    """require_reason=True mirrors the incumbent composite-only `_RAISE_APPROVED_RE`'s
    `\\s+\\S` non-empty-reason requirement: a marker with no reason text (`# raise-approved:
    dec-162`, nothing after) parses as NO marker at all, so it can never rescue an entry from
    its ceiling. Default False -- sloc/prose/coverage/mypy never required a reason.
    """
    if not comment:
        return None, None
    match = marker_re.search(comment)
    if not match:
        return None, None
    reason = match.group(2).strip()
    if require_reason and not reason:
        return None, None
    return f"dec-{match.group(1)}", (reason or None)


def make_flat_extractor(
    token: str, value_type: type = int, require_reason: bool = False
) -> Callable[[str], dict[str, MarkerEntry]]:
    """A flattened, indentation-blind line scan (sloc/prose/coverage/mypy's shape) -- every
    non-blank, non-full-line-comment line in the text is a candidate entry line, regardless of
    nesting depth. Safe when a registry's keys are globally unique across its surface groups.
    """
    marker_re = re.compile(rf"#\s*{re.escape(token)}:\s*dec-(\d+)\s*(.*)$")

    def _extract(text: str) -> dict[str, MarkerEntry]:
        entries: dict[str, MarkerEntry] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _ENTRY_RE.match(stripped)
            if not match:
                continue
            marker, reason = _parse_marker(match.group("comment"), marker_re, require_reason)
            entries[match.group("key")] = MarkerEntry(value_type(match.group("value")), marker, reason)
        return entries

    return _extract


def make_section_extractor(
    section_key: str, token: str = "raise-approved", value_type: type = int, require_reason: bool = False
) -> Callable[[str], dict[str, MarkerEntry]]:
    """Anchored on a top-level `<section_key>:` line; ends the section at the next column-0
    NON-COMMENT line (comment-immune -- rec-2954 finding 3: the incumbent
    _parse_raw_r3_markers cleared its in-section flag on ANY column-0 line, silently dropping
    every marker declared after a column-0 comment inside the section). Never leaks a scalar
    from a sibling top-level section (e.g. a `classes:` block's `limit: 500`).
    """
    marker_re = re.compile(rf"#\s*{re.escape(token)}:\s*dec-(\d+)\s*(.*)$")
    section_start_re = re.compile(rf"^{re.escape(section_key)}:\s*$")

    def _extract(text: str) -> dict[str, MarkerEntry]:
        entries: dict[str, MarkerEntry] = {}
        in_section = False
        for line in text.splitlines():
            if section_start_re.match(line):
                in_section = True
                continue
            if line and not line[0].isspace() and not line.lstrip().startswith("#"):
                in_section = False
            if not in_section:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _ENTRY_RE.match(stripped)
            if not match:
                continue
            marker, reason = _parse_marker(match.group("comment"), marker_re, require_reason)
            entries[match.group("key")] = MarkerEntry(value_type(match.group("value")), marker, reason)
        return entries

    return _extract


def default_base_reader(rel_path: str) -> str | None:
    """Read a file's content at origin/main; return None if the ref/path is unreachable."""
    result = _common.run(
        ["git", "show", f"origin/main:{rel_path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_common.ROOT,
    )
    if result.returncode != 0:
        return None
    return result.stdout


_decision_bodies_cache: dict[str, dict[int, str]] = {}


def load_decision_bodies() -> dict[int, str]:
    """{decision number: raw heading-inclusive body} across DECISIONS.md + DECISIONS_ARCHIVE.md.

    Built on scripts.decisions_md.iter_decision_sections (Decision 134 clause 3 / DAF-03) --
    never a hand-rolled Decision-heading regex. Memoized PER `_common.ROOT` value (not a
    single blind global) so a patched ROOT in a test gets its own fresh parse rather than
    reusing another test's cached corpus, while a real, unpatched process (production/CI) still
    parses the corpus exactly once regardless of how many of the five guards call this.
    """
    root_key = str(_common.ROOT)
    cached = _decision_bodies_cache.get(root_key)
    if cached is not None:
        return cached

    bodies: dict[int, str] = {}
    for rel_path in (_DECISIONS_REL_PATH, _DECISIONS_ARCHIVE_REL_PATH):
        md_path = _common.ROOT / rel_path
        if not md_path.exists():
            continue
        content = md_path.read_text(encoding="utf-8", errors="replace")
        bodies_from_text(content, bodies)

    _decision_bodies_cache[root_key] = bodies
    return bodies


def _normalize_dec_id(dec_id: str) -> int:
    return int(dec_id.removeprefix("dec-"))


def _expand_ancestor_candidates(target: str) -> list[str]:
    """The full target, plus every "/"-delimited ancestor prefix with >=2 segments. A
    single-segment prefix (e.g. a bare `scripts/` root) is never generated -- that is exactly
    what keeps a root-directory mention from becoming a permanent skeleton key.
    """
    segments = target.split("/")
    candidates = [target]
    for i in range(len(segments) - 1, 1, -1):
        candidates.append("/".join(segments[:i]))
    return candidates


def _mentions_any(body: str, candidates: list[str], bounded: bool = False) -> bool:
    return mentions(body, candidates, bounded)


def authorization_failure(
    dec_id: str,
    key: str,
    bodies: dict[int, str],
    mention_candidates: Callable[[str], list[str]] | None = None,
    *,
    bounded: bool = False,
) -> bool:
    """True (FAIL) iff `dec_id` does not authorize `key`.

    Authorization succeeds when the cited Decision's body contains the key (or a >=2-segment
    ancestor prefix of it) as a literal substring -- generated key-side, never by tokenizing
    the body. When the cited Decision's own body doesn't authorize, ONE supersession hop is
    tried: if that body carries the Decision 149 compact-in-place `**Superseded by: Decision
    N**` marker, Decision N's body is also consulted (closes the Decision 149 compaction
    interaction -- a compacted stub's body is reduced to a one-liner with no path mentions).
    A `dec_id` with no matching header at all (bodies.get returns None) fails unconditionally --
    this subsumes the historical existence-only check as the degenerate case with an empty
    candidate corpus.
    """
    raw_targets = mention_candidates(key) if mention_candidates is not None else [key]
    candidates: list[str] = []
    for target in raw_targets:
        candidates.extend(_expand_ancestor_candidates(target))

    body = bodies.get(_normalize_dec_id(dec_id))
    if body is None:
        return True
    if _mentions_any(body, candidates, bounded):
        return False

    superseded_by = extract_superseded_by(body)
    if superseded_by:
        target_body = bodies.get(_normalize_dec_id(superseded_by))
        if target_body is not None and _mentions_any(target_body, candidates, bounded):
            return False

    return True


def _moved_from_path(reason: str | None) -> str | None:
    if not reason:
        return None
    match = _MOVED_FROM_RE.search(reason)
    return match.group(1) if match else None


def check_diff(spec: RegistrySpec, base_reader: Callable[[str], str | None] | None = None) -> list[str]:
    """Diff-based gate: base_reader defaults to default_base_reader. Returns violation message
    strings (empty when clean, unreachable, or the registry file is absent -- SKIP is silent
    advisory, mirroring every one of the five incumbents' base-unreachable contract).
    """
    reader = base_reader or default_base_reader
    current_path = _common.ROOT / spec.rel_path
    if not current_path.exists():
        print(f"  {spec.rel_path} not found -- nothing to check.")
        return []

    base_text = reader(spec.rel_path)
    if base_text is None:
        print("  SKIP: origin/main unreachable (advisory locally, authoritative in CI).")
        return []

    current_text = current_path.read_text(encoding="utf-8")
    base_entries = spec.extractor(base_text)
    current_entries = spec.extractor(current_text)
    bodies = load_decision_bodies()

    violations: list[str] = []
    for key, entry in sorted(current_entries.items()):
        base_entry = base_entries.get(key)
        new_value = entry.value
        if base_entry is None:
            if not spec.gates_new_entry(new_value):
                continue
            kind = "new registration" if spec.gated_direction == "up" else "new sub-threshold registration"
        else:
            base_value = base_entry.value
            gated = new_value > base_value if spec.gated_direction == "up" else new_value < base_value
            if not gated:
                continue
            verb = "increase" if spec.gated_direction == "up" else "lowered"
            kind = f"{verb} {base_value} -> {new_value}"

        marker = entry.marker
        if marker is None:
            msg = f"{key}: unauthorized {kind} with no `# {spec.token}: dec-NNN` marker on the entry line."
            violations.append(f"{msg} {spec.relief_text}".rstrip() if spec.relief_text else msg)
            continue

        if spec.moved_from_relief:
            old_path = _moved_from_path(entry.reason)
            if old_path is not None:
                if old_path in base_entries and old_path not in current_entries:
                    continue
                violations.append(
                    f"{key}: `{spec.token}: {marker} moved from {old_path}` is unproven -- {old_path} must be "
                    "present in the base entry map and absent from the current one."
                )
                continue

        if authorization_failure(marker, key, bodies, spec.mention_candidates, bounded=spec.boundary_match):
            msg = (
                f"{key}: {spec.token} marker cites {marker}, but its body does not authorize this entry "
                f"(mention `{key}` or a >=2-segment ancestor)."
            )
            violations.append(f"{msg} {spec.relief_text}".rstrip() if spec.relief_text else msg)

    return violations


def check_state_diff(spec: RegistrySpec, base_reader: Callable[[str], str | None] | None = None) -> list[str]:
    """Diff-based gate for a STRENGTH-valued registry (spec.state_extractor / weakened /
    gates_deletion) -- the check_diff sibling for the two holes that shape does not fit:

    base_reader's contract here is STRICTER than check_diff's: it must return "" (never None)
    for a base ref that IS reachable but does not carry spec.rel_path at all (a brand-new
    manifest domain has no base entries, not an unreachable base) -- None is reserved for "the
    base ref itself is unreachable", which SKIPs loudly (prints, never an affirmative pass) and
    returns []. Defaults to default_base_reader, which does not preserve this distinction (every
    real caller wires its own base_reader, e.g. a batched `git cat-file --batch` closure, so this
    default exists for direct/unit-test callers only).

    A HEAD file absent from disk reads as "every base entry deleted" (each walked through
    spec.gates_deletion), never as "nothing to check" -- check_diff's five incumbent registries
    never have their whole file deleted mid-diff, but a whole check-domain manifest can be.

    Authorization mirrors check_diff's: a weakening transition needs a marker on the HEAD entry
    that (a) is present, (b) differs from the base entry's own marker, and (c) names (via
    spec.mention_candidates, or the bare key) an authorizing Decision. The value compared in (b)
    is the marker's normalized `dec-NNN` id, NOT its raw text: a head marker citing the same
    Decision as the base entry's never re-authorizes, however its reason text is reworded, because
    that authorization was spent on a PRIOR transition rather than this one. A
    deletion is gated unconditionally when spec.gates_deletion(base state) is True -- there is no
    head line to carry a marker, so none is ever consulted on that branch.
    """
    if spec.state_extractor is None or spec.weakened is None or spec.gates_deletion is None:
        raise ValueError(f"{spec.rel_path}: check_state_diff requires state_extractor/weakened/gates_deletion")

    reader = base_reader or default_base_reader
    base_text = reader(spec.rel_path)
    if base_text is None:
        print(f"  SKIP: origin/main unreachable for {spec.rel_path} (advisory locally, authoritative in CI).")
        return []
    base_entries = spec.state_extractor(base_text)

    current_path = _common.ROOT / spec.rel_path
    current_text = current_path.read_text(encoding="utf-8", errors="replace") if current_path.exists() else None
    current_entries = spec.state_extractor(current_text) if current_text is not None else {}

    bodies = load_decision_bodies()
    base_bodies, violations = base_corpus(spec, reader)

    for key in sorted(set(base_entries) | set(current_entries)):
        base_entry = base_entries.get(key)
        head_entry = current_entries.get(key)

        if head_entry is None:
            if base_entry is not None and spec.gates_deletion(base_entry.state):
                violations.append(
                    f"{key}: removed while still sequenced -- deletion has no marker escape "
                    "(retirement is mark-then-drop across two PRs)."
                )
            continue

        if base_entry is None:
            continue  # brand-new entry -- always free, regardless of any marker.

        if not spec.weakened(base_entry.state, head_entry.state):
            continue  # tightening or no state change -- always free, regardless of any marker.

        marker = effective_marker(spec, head_entry)
        if marker is None or marker == effective_marker(spec, base_entry):
            violations.append(
                f"{key}: unauthorized {spec.transition_label} with no fresh `# {spec.token}: dec-NNN <reason>` "
                "marker on the entry's own line span (a marker citing the same decision as the "
                "base entry's never re-authorizes)."
            )
            continue

        if failure := transition_failure(
            spec,
            key,
            marker,
            authorization_failure(marker, key, bodies, spec.mention_candidates, bounded=spec.boundary_match),
            bodies,
            base_bodies,
        ):
            violations.append(failure)

    return violations


def check_present_markers(spec: RegistrySpec) -> list[str]:
    """Retroactive gate: every CURRENT entry bearing `spec.token`'s marker, regardless of
    diff status, must be authorization-clean. Closes SGE-07's own acceptance clause -- a
    diff-only guard structurally cannot catch an already-merged, never-re-diffed marker.
    Frozen-hook and moved_from entries are skipped (not failed); moved_from has no base map to
    prove against in this retroactive shape.
    """
    current_path = _common.ROOT / spec.rel_path
    if not current_path.exists():
        return []

    current_text = current_path.read_text(encoding="utf-8")
    entries = spec.extractor(current_text)
    bodies = load_decision_bodies()

    violations: list[str] = []
    for key, entry in sorted(entries.items()):
        marker = effective_marker(spec, entry)
        if marker is None or key in RETRO_SCAN_GRANDFATHER:
            continue
        if spec.moved_from_relief and _moved_from_path(entry.reason) is not None:
            continue
        if authorization_failure(marker, key, bodies, spec.mention_candidates, bounded=spec.boundary_match):
            violations.append(
                f"{key}: retroactive scan -- {spec.token} marker cites {marker}, but its body does not "
                f"authorize this entry (mention `{key}` or a >=2-segment ancestor)."
            )

    return violations
