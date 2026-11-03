"""Mirror suite for scripts/checks/_marker_guard_state_options.py (map_source_to_test measures it here).

Drives the option helpers through check_state_diff and check_present_markers; rec-3736's acceptance lives in
TestReasonRequired.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from scripts.checks import _marker_guard
from scripts.checks._marker_guard import RegistrySpec, check_present_markers, check_state_diff


def _extract(text: str) -> dict[str, _marker_guard.StateEntry]:
    entries: dict[str, _marker_guard.StateEntry] = {}
    for line in text.splitlines():
        key_part, _, comment = line.partition("#")
        key, sep, state = key_part.strip().partition("=")
        if not sep:
            continue
        match = re.match(r"\s*opt-approved:\s*(dec-\d+)[ \t]*(.*)$", comment)
        marker = match.group(1) if match else None
        reason = (match.group(2).strip() or None) if match else None
        entries[key.strip()] = _marker_guard.StateEntry(state=state.strip(), marker=marker, reason=reason)
    return entries


def _view(text: str) -> dict[str, _marker_guard.MarkerEntry]:
    return {k: _marker_guard.MarkerEntry(0.0, e.marker, e.reason) for k, e in _extract(text).items()}


def _spec(**overrides: object) -> RegistrySpec:
    fields: dict[str, object] = {
        "rel_path": "config/opt_state.yaml",
        "token": "opt-approved",
        "gated_direction": "down",
        "extractor": _view,
        "gates_new_entry": lambda _value: False,
        "label": "option state guard",
        "reason_required": True,
        "state_extractor": _extract,
        "weakened": lambda base, head: base == "strong" and head == "weak",
        "gates_deletion": lambda base: base == "strong",
    }
    fields.update(overrides)
    return RegistrySpec(**fields)  # type: ignore[arg-type]


def _write(tmp_path: Path, spec: RegistrySpec, body: str) -> None:
    target = tmp_path / spec.rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


class TestReasonRequired:
    """rec-3736: a reasonless marker is no marker on a reason_required spec."""

    def test_reasonless_head_marker_is_no_marker_in_check_state_diff(self, tmp_path: Path) -> None:
        spec = _spec()
        _write(tmp_path, spec, "a=weak  # opt-approved: dec-1\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = check_state_diff(spec, base_reader=lambda _rel: "a=strong\n")
        assert violations and "unauthorized weakening" in violations[0]

    def test_reasonless_base_marker_never_makes_a_head_marker_stale(self, tmp_path: Path) -> None:
        spec = _spec()
        _write(tmp_path, spec, "a=weak  # opt-approved: dec-1 real reason\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            violations = check_state_diff(spec, base_reader=lambda _rel: "a=strong  # opt-approved: dec-1\n")
        assert violations and "does not authorize" in violations[0]
        assert "never re-authorizes" not in violations[0]

    def test_reasonless_marker_is_skipped_by_check_present_markers(self, tmp_path: Path) -> None:
        spec = _spec()
        _write(tmp_path, spec, "a=weak  # opt-approved: dec-999999\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert check_present_markers(spec) == []

    def test_blank_reason_without_reason_required_still_counts(self, tmp_path: Path) -> None:
        spec = _spec(reason_required=False)
        _write(tmp_path, spec, "a=weak  # opt-approved: dec-999999\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert check_present_markers(spec)
            violations = check_state_diff(spec, base_reader=lambda _rel: "a=strong\n")
        assert violations and "does not authorize" in violations[0]
