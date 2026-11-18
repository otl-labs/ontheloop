"""Tests for amendment_log retention and new-row identity (Decision 275 cl.4, rec-4119)."""

from __future__ import annotations

from pathlib import Path

from scripts.checks.contracts import _amendment_retention as ar

_CFG = {
    "keep_full": 5,
    "terse_row": {"keys": ["date", "semantic_break", "change_class", "summary"], "summary_max_chars": 160},
    "exempt_files": ["docs/contracts/north-star.yaml"],
}
_REPO = Path(__file__).resolve().parents[3]


def _full(n: int, date: str = "2026-11-01") -> dict:
    return {
        "date": date,
        "semantic_break": False,
        "change_class": "field_add",
        "summary": f"PLAN-x-{n}: " + "long wording " * 30,
        "migration_story": "story",
    }


def _terse(n: int, date: str = "2026-10-01") -> dict:
    return {"date": date, "semantic_break": False, "change_class": "field_add", "summary": f"PLAN-x-{n}: short"}


def _doc(log: list | None, note: str = "a") -> dict:
    data: dict = {"contract": {"id": "c"}, "note": note}
    if log is not None:
        data["amendment_log"] = log
    return data


class TestRowShape:
    def test_terse_needs_exact_keys_and_short_summary(self) -> None:
        assert ar.is_terse(_terse(1), _CFG)
        assert not ar.is_terse(_full(1), _CFG)
        assert not ar.is_terse({**_terse(1), "summary": "x" * 161}, _CFG)
        assert not ar.is_terse({**_terse(1), "extra": 1}, _CFG)
        assert not ar.is_terse("row", _CFG)
        assert not ar.is_terse({**_terse(1), "summary": 5}, _CFG)

    def test_full_rows_exclude_terse_and_semantic_break_rows(self) -> None:
        log = [_full(1), _terse(2), {**_full(3), "semantic_break": True}, "junk"]
        assert len(ar.full_rows(log, _CFG)) == 2

    def test_default_terse_shape_without_a_terse_mapping(self) -> None:
        assert ar.is_terse(_terse(1), {"keep_full": 5})


class TestIdentity:
    def test_compaction_is_not_new(self) -> None:
        assert ar.genuinely_new([_full(1), _full(2)], [_terse(1), _full(2)]) == 0

    def test_added_row_is_new_even_with_a_compaction(self) -> None:
        assert ar.genuinely_new([_full(1), _full(2)], [_terse(1), _full(2), _full(3)]) == 1

    def test_reworded_row_with_a_changed_date_is_not_new(self) -> None:
        reworded = {**_full(1, "2026-12-01"), "summary": "reworded"}
        assert ar.genuinely_new([_full(1)], [reworded]) == 0

    def test_redated_identical_row_is_not_new(self) -> None:
        assert ar.genuinely_new([_full(1, "2026-01-01")], [_full(1, "2026-12-31")]) == 0

    def test_duplicate_dates_and_duplicate_rows_count_by_multiset(self) -> None:
        assert ar.genuinely_new([_full(1)], [_full(1), _full(1)]) == 1
        assert ar.genuinely_new([_full(1), _full(2, "2026-11-01")], [_full(1), _full(2, "2026-11-01")]) == 0

    def test_rows_dropped(self) -> None:
        assert ar.rows_dropped([_full(1), _full(2)], [_full(1)])
        assert not ar.rows_dropped([_full(1)], [_terse(1)])


class TestRetention:
    def test_adding_a_row_with_a_compaction_passes(self) -> None:
        base = [_full(n) for n in range(5)]
        head = [_full(9)] + base[:4] + [_terse(4)]
        assert ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG) is None

    def test_a_base_above_keep_full_may_not_grow(self) -> None:
        base = [_full(n) for n in range(8)]
        assert ar.retention_violation("c.yaml", _doc(base), _doc([_full(9)] + base), _CFG) is not None
        assert ar.retention_violation("c.yaml", _doc(base), _doc([_full(9)] + base[:7] + [_terse(7)]), _CFG) is None

    def test_adding_a_terse_row_never_fails(self) -> None:
        base = [_full(n) for n in range(7)]
        assert ar.retention_violation("c.yaml", _doc(base), _doc([_terse(9)] + base), _CFG) is None

    def test_semantic_break_rows_are_exempt(self) -> None:
        base = [{**_full(n), "semantic_break": True} for n in range(7)]
        head = [{**_full(9), "semantic_break": True}] + base
        assert ar.retention_violation("c.yaml", _doc(base), _doc(head), _CFG) is None

    def test_a_dropped_row_fails(self) -> None:
        msg = ar.retention_violation("c.yaml", _doc([_full(1), _full(2)]), _doc([_full(1)]), _CFG)
        assert msg is not None and "lost 1 row" in msg

    def test_exempt_file_and_missing_config_report_nothing(self) -> None:
        base = [_full(n) for n in range(5)]
        head = [_full(9)] + base
        assert ar.retention_violation("north-star.yaml", _doc(base), _doc(head), _CFG) is None
        assert ar.retention_violation("c.yaml", _doc(base), _doc(head), {"keep_full": 5, "exempt_files": []}) is not None

    def test_missing_config_reports_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(ar, "load_retention", lambda root=None: None)
        base = [_full(n) for n in range(5)]
        assert ar.retention_violation("c.yaml", _doc(base), _doc([_full(9)] + base)) is None

    def test_malformed_logs_never_raise(self) -> None:
        assert ar.retention_violation("c.yaml", "text", {"amendment_log": "x"}, _CFG) is None
        assert ar.retention_violation("c.yaml", _doc(None), _doc(None), _CFG) is None


class TestRewrite:
    def test_reworded_old_row_offered_as_the_new_row_fails_whatever_the_date(self) -> None:
        base = _doc([_full(1)], "a")
        for date in ("2026-11-01", "2026-12-25"):
            head = _doc([{**_full(1, date), "summary": "reworded"}], "b")
            assert ar.rewrite_violation(base, head) is not None

    def test_genuinely_new_row_passes(self) -> None:
        assert ar.rewrite_violation(_doc([_full(1)], "a"), _doc([_full(2), _full(1)], "b")) is None

    def test_silent_when_no_row_was_offered_or_nothing_else_changed(self) -> None:
        assert ar.rewrite_violation(_doc([_full(1)], "a"), _doc([_full(1)], "b")) is None
        assert ar.rewrite_violation(_doc([_full(1)], "a"), _doc([_terse(1)], "a")) is None
        assert ar.rewrite_violation("x", _doc([])) is None

    def test_compaction_with_unchanged_content_is_silent(self) -> None:
        assert ar.rewrite_violation(_doc([_full(1)]), _doc([_terse(1)])) is None


class TestPass2Messages:
    def test_labels(self, monkeypatch) -> None:
        monkeypatch.setattr(ar, "load_retention", lambda root=None: _CFG)
        base = [_full(n) for n in range(5)]
        out = ar.pass2_messages("c.yaml", _doc(base), _doc([_full(9)] + base, "b"), True)
        assert len(out) == 1 and out[0].startswith("Contract drift (retention): c.yaml")
        out = ar.pass2_messages("c.yaml", _doc([_full(1)]), _doc([{**_full(1), "summary": "r"}], "b"), True)
        assert len(out) == 1 and out[0].startswith("Contract drift (amendment): c.yaml")
        assert ar.pass2_messages("c.yaml", _doc([_full(1)]), _doc([{**_full(1), "summary": "r"}], "b"), False) == []


class TestLoad:
    def test_absent_or_malformed_mapping_is_none(self, tmp_path: Path) -> None:
        assert ar.load_retention(tmp_path) is None
        target = tmp_path / "docs" / "contracts"
        target.mkdir(parents=True)
        (target / "contract-population.yaml").write_text("amendment_log_retention: {keep_full: x}\n", encoding="utf-8")
        assert ar.load_retention(tmp_path) is None
        (target / "contract-population.yaml").write_text("[unclosed\n", encoding="utf-8")
        assert ar.load_retention(tmp_path) is None
        (target / "contract-population.yaml").write_text("amendment_log_retention: {keep_full: 3}\n", encoding="utf-8")
        assert ar.load_retention(tmp_path) == {"keep_full": 3}


def test_adding_a_row_past_keep_full_without_compacting_fails() -> None:
    base = [_full(n) for n in range(5)]
    head = [_full(9)] + base
    msg = ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG)
    assert msg is not None and "compact an older row" in msg


def test_real_retention_mapping() -> None:
    cfg = ar.load_retention(_REPO)
    assert cfg is not None
    assert cfg["keep_full"] == 5
    assert cfg["exempt_files"] == ["docs/contracts/north-star.yaml"]
    assert cfg["terse_row"]["summary_max_chars"] == 160
