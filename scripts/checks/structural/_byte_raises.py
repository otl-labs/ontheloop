"""Raise-marker legs over the `byte_budgets:` roster (Decision 275, amends Decision 166).

Three legs, all bound to scripts/checks/_marker_guard.py (Decision 165), which stays unchanged. Keys are bare
contract file names, every spec uses boundary_match, so a mention of another contract, of docs/contracts alone, or
of a suffix-colliding name (x_ports.yaml for ports.yaml) never authorizes:
  (a) check_diff plus check_present_markers over the roster (a marker must name the entry);
  (b) check_state_diff: a raise needs a marker citing a Decision whose text added in the same change names the
      contract, so a seed marker never authorizes a later raise;
  (c) new entries (check_state_diff treats them as free): the same freshness test.
"""

from __future__ import annotations

from typing import Callable, Optional

from scripts.checks import _common, _marker_guard

_REGISTRY_REL_PATH = "config/structural_size_budgets.yaml"
_SECTION = "byte_budgets"
_TOKEN = "raise-approved"
_extract = _marker_guard.make_section_extractor(_SECTION, token=_TOKEN, value_type=int)


def _states(text: str) -> dict[str, _marker_guard.StateEntry]:
    return {key: _marker_guard.StateEntry(entry.value, entry.marker, entry.reason) for key, entry in _extract(text).items()}


_DIFF_SPEC = _marker_guard.RegistrySpec(
    rel_path=_REGISTRY_REL_PATH,
    token=_TOKEN,
    gated_direction="up",
    extractor=_extract,
    gates_new_entry=lambda _value: True,
    label="Structural-size budget-raise guardrail (Decision 275, byte_budgets)",
    mention_candidates=lambda key: [key],
    boundary_match=True,
)

_STATE_SPEC = _marker_guard.RegistrySpec(
    rel_path=_REGISTRY_REL_PATH,
    token=_TOKEN,
    gated_direction="up",
    extractor=_extract,
    gates_new_entry=lambda _value: True,
    label="Structural-size budget-raise guardrail (Decision 275, byte_budgets freshness)",
    mention_candidates=lambda key: [key],
    state_extractor=_states,
    weakened=lambda base, head: isinstance(base, int) and isinstance(head, int) and head > base,
    gates_deletion=lambda _state: False,
    fresh_mention=True,
    boundary_match=True,
    transition_label="raise",
)


def entry_count(current_text: str) -> int:
    return len(_extract(current_text))


def _new_entry_leg(base_text: str, reader: Callable[[str], Optional[str]]) -> list[str]:
    current_path = _common.ROOT / _REGISTRY_REL_PATH
    if not current_path.exists():
        return []
    base_keys = set(_extract(base_text))
    current = _states(current_path.read_text(encoding="utf-8"))
    bodies = _marker_guard.load_decision_bodies()
    base_bodies, violations = _marker_guard.base_corpus(_STATE_SPEC, reader)
    for key, entry in sorted(current.items()):
        if key in base_keys:
            continue
        if entry.marker is None:
            violations.append(f"{key}: new byte_budgets entry with no `# {_TOKEN}: dec-NNN` marker on the entry line.")
            continue
        failure = _marker_guard.transition_failure(
            _STATE_SPEC,
            key,
            entry.marker,
            _marker_guard.authorization_failure(entry.marker, key, bodies, _STATE_SPEC.mention_candidates, bounded=True),
            bodies,
            base_bodies,
        )
        if failure:
            violations.append(failure)
    return violations


def violations(base_text: Optional[str], base_reader: Optional[Callable[[str], Optional[str]]] = None) -> list[str]:
    """All byte_budgets raise-marker violations; the base registry text is read once by the caller.

    A None base text is the unreachable-base SKIP (advisory locally, authoritative in CI): the diff legs skip
    as check_diff and check_state_diff do, and an empty base is never treated as all-new entries."""
    outer = base_reader or _marker_guard.default_base_reader

    def reader(rel: str) -> Optional[str]:
        return base_text if rel == _REGISTRY_REL_PATH else outer(rel)

    found = (
        _marker_guard.check_diff(_DIFF_SPEC, base_reader=reader)
        + _marker_guard.check_present_markers(_DIFF_SPEC)
        + _marker_guard.check_state_diff(_STATE_SPEC, base_reader=reader)
    )
    if base_text is None:
        return found
    return found + _new_entry_leg(base_text, reader)
