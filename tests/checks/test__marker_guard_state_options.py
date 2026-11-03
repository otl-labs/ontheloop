"""Mirror suite for scripts/checks/_marker_guard_state_options.py (map_source_to_test measures it here).

Drives the option helpers through check_state_diff and check_present_markers; rec-3736's acceptance lives in
TestReasonRequired.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from scripts.checks import _marker_guard
from scripts.checks._marker_guard import RegistrySpec, check_diff, check_present_markers, check_state_diff


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


_DEC = "## Decision 7: Option fixture (Decided)\n\n"


def _corpus(tmp_path: Path, decisions: str, archive: str = "") -> None:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "DECISIONS.md").write_text(decisions, encoding="utf-8")
    (docs / "DECISIONS_ARCHIVE.md").write_text(archive, encoding="utf-8")
    _marker_guard._decision_bodies_cache.clear()


def _reader(base: str, decisions: str | None = "# Decisions\n", archive: str | None = "") -> object:
    files = {"config/opt_state.yaml": base, "docs/DECISIONS.md": decisions, "docs/DECISIONS_ARCHIVE.md": archive}
    return lambda rel: files.get(rel, "")


def _run(
    tmp_path: Path, spec: RegistrySpec, head: str, base: str, dec_head: str, dec_base: str | None = "# Decisions\n"
) -> list[str]:
    _write(tmp_path, spec, head)
    _corpus(tmp_path, dec_head)
    with patch("scripts.checks._common.ROOT", tmp_path):
        return check_state_diff(spec, base_reader=_reader(base, dec_base))  # type: ignore[arg-type]


_BASE_STRONG = "NS.1=strong\n"
_HEAD_WEAK = "NS.1=weak  # opt-approved: dec-7 approval: ref 2026-11-03\n"


class TestBoundaryMatch:
    def _fails(self, tmp_path: Path, body: str, key: str = "NS.1", *, bounded: bool = True) -> bool:
        spec = _spec(boundary_match=bounded)
        _write(tmp_path, spec, f"{key}=weak  # opt-approved: dec-7 reason\n")
        _corpus(tmp_path, _DEC + body + "\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            return bool(check_state_diff(spec, base_reader=lambda _rel: f"{key}=strong\n"))

    def test_ten_does_not_name_one(self, tmp_path: Path) -> None:
        assert self._fails(tmp_path, "Only NS.10 here.")

    def test_range_does_not_name_either_end(self, tmp_path: Path) -> None:
        assert self._fails(tmp_path, "see NS.1-NS.9 only", "NS.1")
        assert self._fails(tmp_path, "see NS.1-NS.9 only", "NS.9")

    def test_permission_does_not_name_mission(self, tmp_path: Path) -> None:
        assert self._fails(tmp_path, "permission and mission-critical", "mission")

    def test_delimited_mentions_name_the_key(self, tmp_path: Path) -> None:
        for body in ("(NS.1)", "NS.1, and more", "ends with NS.1"):
            assert not self._fails(tmp_path, body)

    def test_default_substring_still_authorizes(self, tmp_path: Path) -> None:
        assert not self._fails(tmp_path, "Only NS.10 here.", bounded=False)

    def test_present_markers_honour_boundary(self, tmp_path: Path) -> None:
        spec = _spec(boundary_match=True)
        _write(tmp_path, spec, _HEAD_WEAK)
        _corpus(tmp_path, _DEC + "NS.10 only\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert check_present_markers(spec)

    def test_check_diff_honours_boundary(self, tmp_path: Path) -> None:
        spec = _spec(boundary_match=True, gated_direction="up", extractor=lambda t: _flat(t))
        _write(tmp_path, spec, "NS.1=5  # opt-approved: dec-7 reason\n")
        _corpus(tmp_path, _DEC + "NS.10 only\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert check_diff(spec, base_reader=lambda _rel: "NS.1=1\n")


def _flat(text: str) -> dict[str, _marker_guard.MarkerEntry]:
    out: dict[str, _marker_guard.MarkerEntry] = {}
    for key, entry in _extract(text).items():
        out[key] = _marker_guard.MarkerEntry(float(str(entry.state)), entry.marker, entry.reason)
    return out


class TestFreshMention:
    def test_decision_absent_at_base_authorizes(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, _DEC + "Names NS.1.\n") == []

    def test_dated_note_appended_authorizes_even_when_base_named_it(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        base = _DEC + "Names NS.1.\n"
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, base + "Note 2026-11-03: NS.1 changed.\n", base) == []

    def test_spent_mention_does_not_authorize(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        base = _DEC + "Names NS.1.\n"
        out = _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, base + "Unrelated note.\n", base)
        assert out and "no text added" in out[0]

    def test_repeating_a_base_line_is_still_spent(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        base = _DEC + "Names NS.1.\n"
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, base + "Names NS.1.\n", base)

    def test_rewritten_or_reordered_base_line_never_authorizes(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        base = _DEC + "Line A.\nLine B.\n"
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, _DEC + "Line A.\nLine B changed NS.1.\n", base)
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, _DEC + "Line B.\nLine A.\nNS.1\n", base)

    def test_interleaved_pure_insertion_authorizes(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        base = "".join(f"{x}\n" for x in ("A", "B", "C", "D", "E"))
        head = "".join(f"{x}\n" for x in ("B", "C", "D", "A", "x1", "B", "x2 NS.1", "C", "x3", "D", "x4", "E"))
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, _DEC + head, _DEC + base) == []

    def test_unreadable_corpus_skips_loudly_and_unauthorized_still_fails(self, tmp_path: Path, capsys: object) -> None:
        spec = _spec(fresh_mention=True)
        out = _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, _DEC + "Names NS.1.\n", None)
        assert out == []
        assert capsys.readouterr().out.count("SKIP") == 1  # type: ignore[attr-defined]
        out = _run(tmp_path, spec, "NS.1=weak\n", _BASE_STRONG, _DEC + "Names NS.1.\n", None)
        assert out and "unauthorized weakening" in out[0]

    def test_empty_decisions_file_at_the_base_is_a_violation(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        out = _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, _DEC + "Names NS.1.\n", "")
        assert any("Decision corpus absent at the base" in v for v in out)

    def test_archive_body_counts_as_base_body(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        _write(tmp_path, spec, _HEAD_WEAK)
        _corpus(tmp_path, "# Decisions\n", _DEC + "Names NS.1.\n")
        reader = _reader(_BASE_STRONG, "# Decisions\n", _DEC + "Names NS.1.\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert check_state_diff(spec, base_reader=reader)  # type: ignore[arg-type]

    def test_present_markers_ignore_freshness(self, tmp_path: Path) -> None:
        spec = _spec(fresh_mention=True)
        _write(tmp_path, spec, _HEAD_WEAK)
        _corpus(tmp_path, _DEC + "Names NS.1.\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            assert check_present_markers(spec) == []

    def test_default_keeps_head_only_authorization(self, tmp_path: Path) -> None:
        spec = _spec()
        base = _DEC + "Names NS.1.\n"
        assert _run(tmp_path, spec, _HEAD_WEAK, _BASE_STRONG, base, base) == []


class TestTransitionLabel:
    def test_label_replaces_weakening(self, tmp_path: Path) -> None:
        spec = _spec(transition_label="change")
        _write(tmp_path, spec, "NS.1=weak\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            out = check_state_diff(spec, base_reader=lambda _rel: _BASE_STRONG)
        assert out and "unauthorized change with no fresh" in out[0]

    def test_default_label_is_weakening(self, tmp_path: Path) -> None:
        spec = _spec()
        _write(tmp_path, spec, "NS.1=weak\n")
        with patch("scripts.checks._common.ROOT", tmp_path):
            out = check_state_diff(spec, base_reader=lambda _rel: _BASE_STRONG)
        assert out and "unauthorized weakening with no fresh" in out[0]
