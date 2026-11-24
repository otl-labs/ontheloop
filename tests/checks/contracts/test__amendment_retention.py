"""Tests for amendment_log retention and new-row identity (Decision 275 cl.4, rec-4119)."""

from __future__ import annotations

from pathlib import Path

from scripts.checks.contracts import _amendment_retention as ar
from scripts.checks.contracts import _amendment_terse as terse_tool

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


def _plain(n: int, date: str = "2026-11-01") -> dict:
    return {
        "date": date,
        "semantic_break": False,
        "change_class": "field_add",
        "summary": f"PLAN-x-{n}: " + "long wording " * 30,
    }


def _compact(row: dict) -> dict:
    summary = terse_tool.derive_terse(row, _CFG)
    assert isinstance(summary, str)
    return {**row, "summary": summary}


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

    def test_adding_a_terse_row_to_a_held_base_passes(self) -> None:
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
        out = ar.pass2_messages("c.yaml", _doc(base), _doc([_plain(9)] + base, "b"), True)
        assert len(out) == 1 and out[0].startswith("Contract drift (retention): c.yaml")
        reworded = _doc([{**_full(1), "summary": "r"}], "b")
        out = ar.pass2_messages("c.yaml", _doc([_full(1)]), reworded, True)
        assert sorted(m.split(":")[0] for m in out) == ["Contract drift (amendment)", "Contract drift (change)"]
        out = ar.pass2_messages("c.yaml", _doc([_full(1)]), reworded, False)
        assert len(out) == 1 and out[0].startswith("Contract drift (change): c.yaml")


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
    assert cfg["terse_row"]["keys"] == ["date", "semantic_break", "change_class", "summary"]
    assert cfg["terse_row"]["summary_max_chars"] == 160


def _dated(n: int) -> str:
    return f"2026-10-{10 + n:02d}"


def test_decaying_ratchet() -> None:
    base = [_plain(n, _dated(n)) for n in range(7)]
    one = [_plain(9, "2026-11-20")] + base
    msg = ar.retention_violation("c.yaml", _doc(base), _doc(one, "b"), _CFG)
    assert msg is not None and "--rows 2" in msg and "scripts.checks.contracts.amendment_compact" in msg
    order = terse_tool.compaction_order(one, _CFG)
    assert len(order) == 3
    for count, ok in ((1, False), (2, True)):
        head = list(one)
        for i in order[:count]:
            head[i] = _compact(head[i])
        got = ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG)
        assert (got is None) is ok, (count, got)
    two = [_plain(8, "2026-11-21"), _plain(9, "2026-11-20")] + base
    msg = ar.retention_violation("c.yaml", _doc(base), _doc(two, "b"), _CFG)
    assert msg is not None and "--rows 3" in msg
    head = list(two)
    for done, i in enumerate(terse_tool.compaction_order(two, _CFG)[:3], 1):
        head[i] = _compact(head[i])
        got = ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG)
        assert (got is None) is (done == 3), (done, got)


def test_decay_counts_only_selected_rows() -> None:
    old = [_full(n, _dated(n)) for n in range(3)]
    newest = [_plain(n, f"2026-11-{10 + n:02d}") for n in range(5)]
    base = newest + old
    head = [_plain(9, "2026-11-20")] + base
    msg = ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG)
    assert msg is not None and "--rows 1" in msg and "--rows 2" not in msg
    order = terse_tool.compaction_order(head, _CFG)
    assert len(order) == 1
    done = list(head)
    done[order[0]] = _compact(done[order[0]])
    assert ar.retention_violation("c.yaml", _doc(base), _doc(done, "b"), _CFG) is None


def test_hold_binds_where_the_tool_selects_nothing() -> None:
    base = [_full(n, _dated(n)) for n in range(7)]
    head = [_plain(9, "2026-11-20")] + base
    msg = ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG)
    assert msg is not None and "selects no row" in msg and "compact an older row" in msg
    hand = {k: v for k, v in base[6].items() if k != "migration_story"}
    by_hand = [_plain(9, "2026-11-20")] + base[:6] + [{**hand, "summary": "PLAN-x-6"}]
    assert ar.retention_violation("c.yaml", _doc(base), _doc(by_hand, "b"), _CFG) is None


def test_semantic_break_or_terse_new_row_still_triggers_decay() -> None:
    base = [_plain(n, _dated(n)) for n in range(7)]
    breaking = {**_plain(9, "2026-11-20"), "semantic_break": True}
    for new in (breaking, _terse(9, "2026-11-20")):
        msg = ar.retention_violation("c.yaml", _doc(base), _doc([new] + base, "b"), _CFG)
        assert msg is not None and "--rows 1" in msg, new


def test_old_row_changes_only_by_lossless_compaction() -> None:
    cfg = _CFG
    old = [_plain(n, _dated(n)) for n in range(3)]
    story = {**_plain(5, "2026-10-05"), "migration_story": "see rec-77"}

    def fails(base: list, head: list) -> list[str]:
        return ar.change_violation(base, head, cfg)

    assert fails(old, old) == []
    assert fails(old, [_compact(old[0]), old[1], old[2]]) == []
    new = _plain(9, old[0]["date"])
    assert fails(old, [new, _compact(old[0]), old[1], old[2]]) == []
    assert fails(old, [_compact(old[0]), old[1], old[2], _plain(9)]) == []
    assert fails(old, [{**old[0], "summary": "reworded"}, old[1], old[2]])
    assert fails(old, [{**old[0], "summary": "short"}, old[1], old[2]])
    assert fails([story], [{**story, "summary": "PLAN-x-5"}])
    bare_story = {k: v for k, v in story.items() if k != "migration_story"}
    assert fails([story], [{**bare_story, "summary": "PLAN-x-5"}])
    assert fails([story], [{**bare_story, "summary": "PLAN-x-5, rec-77"}]) == []
    assert fails(old, [{**old[0], "date": "2027-01-01"}, old[1], old[2]])
    assert fails(old, old[:2])
    broke = {**old[0], "semantic_break": True}
    assert fails([broke], [{**broke, "summary": "reworded"}])
    assert fails([_terse(1)], [{**_terse(1), "summary": "PLAN-x-1: shorter"}])
    bare = {**_plain(1), "summary": "T4.32 conversion " + "long wording " * 30}
    assert fails([bare], [{**bare, "summary": "T4.32 conversion"}])
    assert fails([bare], [{**bare, "summary": "T4.32 conversion, PLAN-foo"}]) == []


def test_new_non_break_row_is_terse_or_compactable() -> None:
    def bad(row: object) -> bool:
        return bool(ar.new_row_violation([row], [0], _CFG, None))

    assert bad(_full(1))
    assert bad({**_plain(1), "summary": "no pointer here " + "long wording " * 30})
    assert bad({**_plain(1), "summary": "PLAN-x-1 " + "z" * 200 + ": rest"})
    assert bad("junk")
    assert bad({**_terse(1), "summary": "short wording"})
    assert bad({**_terse(1), "summary": "T4.32 and CD.5"})
    assert bad({**_full(1), "semantic_break": "false"})
    assert bad({k: v for k, v in _full(1).items() if k != "semantic_break"})
    assert not bad(_terse(1))
    assert not bad(_plain(1))
    assert not bad({**_full(1), "semantic_break": True})


def _text(rows: list[dict], comment_at: int | None = None) -> str:
    out = ["amendment_log:\n"]
    for i, row in enumerate(rows):
        out.append(f'  - date: "{row["date"]}"\n')
        if comment_at == i:
            out.append("    # shared note\n")
        out.append(f"    semantic_break: false\n    change_class: {row['change_class']}\n")
        out.append(f'    summary: "{row["summary"]}"\n')
    return "".join(out)


def test_comment_in_span_cannot_make_a_row_unselectable() -> None:
    rows = [_plain(n, _dated(n)) for n in range(8)]
    rows.sort(key=lambda r: r["date"], reverse=True)
    base_text = _text(rows)
    for at in (0, 7):
        head_text = _text(rows, comment_at=at)
        found = ar.selectability_violation(rows, rows, base_text, head_text, _CFG)
        assert len(found) == 1, (at, found)
    assert ar.selectability_violation(rows, rows, base_text, base_text, _CFG) == []
    assert ar.selectability_violation(rows, rows, None, _text(rows, 0), _CFG) == []
    assert ar.selectability_violation(rows, rows, base_text, None, _CFG) == []


def test_none_config_and_exempt_file_skip_every_leg(monkeypatch) -> None:
    base = [_plain(n, _dated(n)) for n in range(7)]
    head = [_plain(9, "2026-11-20"), {**base[0], "summary": "reworded"}] + base[1:]
    monkeypatch.setattr(ar, "load_retention", lambda root=None: None)
    assert ar.pass2_messages("c.yaml", _doc(base), _doc(head, "b"), False, "x", "y") == []
    monkeypatch.setattr(ar, "load_retention", lambda root=None: _CFG)
    assert ar.pass2_messages("north-star.yaml", _doc(base), _doc(head, "b"), False) == []
    assert ar.pass2_messages("c.yaml", _doc(base), _doc(head, "b"), False)


def test_tool_short_of_the_hold_names_the_rows_owed_by_hand() -> None:
    base = [_plain(0, "2026-10-10")] + [_full(n, _dated(n + 1)) for n in range(6)]
    head = [_plain(8, "2026-11-21"), _plain(9, "2026-11-20")] + base
    msg = ar.retention_violation("c.yaml", _doc(base), _doc(head, "b"), _CFG)
    assert msg is not None and "--rows 1" in msg
    assert "1 more must be compacted by hand" in msg and "candidates dated 2026-10-11" in msg
