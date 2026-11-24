"""Tests for the amendment_log pointer-token and terse-derivation helpers (rec-5452)."""

from __future__ import annotations

from typing import Any

from scripts.checks.contracts import _amendment_retention as ar
from scripts.checks.contracts import _amendment_terse as at

CFG: dict[str, Any] = {
    "keep_full": 2,
    "terse_row": {"keys": ["date", "semantic_break", "change_class", "summary"], "summary_max_chars": 160},
    "exempt_files": ["docs/contracts/north-star.yaml"],
}


PAD = " padding words" * 14


def _row(summary: str, date: str = "2026-11-01", pad: bool = True, **extra: Any) -> dict[str, Any]:
    text = summary + PAD if pad and isinstance(summary, str) else summary
    return {"date": date, "semantic_break": False, "change_class": "field_add", "summary": text, **extra}


def test_pointer_tokens_normalise() -> None:
    text = (
        "PLAN-foo-bar (rec-12, dec-7) Decisions 73, 83 and 101; Decision 275 cl.4 per T4.32, T-1.24 and T2.25a with CD.22 "
        "in PR #2440 at abc1234 and PLAN-x.yaml and PLAN-y's"
    )
    assert at.pointer_tokens(text) == {
        "PLAN-foo-bar",
        "rec-12",
        "Decision 7",
        "Decision 73",
        "Decision 83",
        "Decision 101",
        "Decision 275",
        "T4.32",
        "T-1.24",
        "T2.25a",
        "CD.22",
        "PLAN-x",
        "PLAN-y",
    }
    assert at.pointer_tokens(None) == set()


def test_derive_terse_label_plus_missing_tokens() -> None:
    row = _row("PLAN-a (rec-1): wording about the change; also Decision 5, rec-1 and T2.1 land here.")
    assert at.derive_terse(row, CFG) == "PLAN-a (rec-1); Decision 5, T2.1"
    assert at.derive_terse(_row("PLAN-a (rec-1): only prose here"), CFG) == "PLAN-a (rec-1)"
    assert at.derive_terse(_row("Initial authoring of the thing, rec-9 and PLAN-z"), CFG) == "rec-9, PLAN-z"
    assert at.derive_terse(_row("prose (a: b) c rec-4 then more"), CFG) == "rec-4"
    assert at.derive_terse(_row("PLAN-q (x: y) nl: Decision 8 here"), CFG) == "PLAN-q (x: y) nl; Decision 8"


def test_derive_terse_refusal_reasons() -> None:
    def reason(row: object) -> str:
        out = at.derive_terse(row, CFG)
        assert isinstance(out, at.Refusal)
        return out.reason

    assert reason("nope") == "malformed"
    assert reason({"date": "x"}) == "malformed"
    assert reason(_row(5)) == "malformed"  # type: ignore[arg-type]
    assert reason(_row("PLAN-a x", migration_story="m")) == "extra_keys"
    assert reason(_row("no pointer at all")) == "no_pointer"
    assert reason(_row("only T4.32 and CD.5 here")) == "no_pointer"
    assert reason(_row("PLAN-a (" + ", ".join(f"rec-{n}" for n in range(1, 40)) + "): tail")) == "overflow"
    assert reason(_row("PLAN-a") | {"semantic_break": True}) == "not_full"
    assert reason(_row("PLAN-short", pad=False)) == "not_full"


def test_lossless_judges_any_rewrite() -> None:
    old = _row("PLAN-a (rec-1): long " * 20 + "Decision 5 and T2.1", pad=False)
    good = _row("PLAN-a (rec-1); Decision 5, T2.1", pad=False)
    assert at.lossless(old, good, CFG) is None
    assert at.lossless(old, _row("PLAN-a (rec-1); Decision 5", pad=False), CFG) == "pointer tokens dropped: T2.1"
    assert at.lossless(old, _row("PLAN-a (rec-1); Decision 5, T2.1", date="2026-11-02", pad=False), CFG) == "date changed"
    assert at.lossless(old, good | {"change_class": "other"}, CFG) == "change_class changed"
    assert at.lossless(old, old, CFG) == "the rewritten row is not terse"
    assert at.lossless(old, "x", CFG) == "the rewritten row is not terse"
    assert at.lossless(old, good | {"semantic_break": True}, CFG) == "semantic_break changed"
    assert at.lossless(old, _row("PLAN-a (rec-1); Decision 5, T2.1, CFR-05", pad=False), CFG) is None


def test_compaction_order_mixed_dates() -> None:
    log = [
        _row("PLAN-a " + "w " * 90, "2026-11-05"),
        _row("PLAN-b " + "w " * 90, "2026-11-01"),
        _row("PLAN-c " + "w " * 90, "2026-11-09"),
        _row("PLAN-d " + "w " * 90, "2026-11-01"),
        _row("PLAN-e " + "w " * 90, "2026-11-03"),
    ]
    assert at.rows_past_keep(log, CFG) == [3, 1, 4]
    assert at.compaction_order(log, CFG) == [3, 1, 4]
    log[1]["migration_story"] = "m"
    assert at.compaction_order(log, CFG) == [3, 4]
    log.append(_row("PLAN-t", "2020-01-01", pad=False))
    log.append(_row("PLAN-s " + "w " * 90, "2020-01-01") | {"semantic_break": True})
    assert at.compaction_order(log, CFG) == [3, 4]


def test_malformed_input_never_raises() -> None:
    for bad in (None, 5, [], {}, "x", {"summary": None}, object()):
        assert at.pointer_tokens(bad) == set()
        assert isinstance(at.derive_terse(bad, CFG), at.Refusal)
        assert isinstance(at.derive_terse(bad, None), at.Refusal)
        assert at.lossless(bad, bad, CFG) is not None
        assert at.compaction_order(bad, CFG) == []
    assert at.compaction_order([None, 3, {"date": 1}], {}) == []
    assert at.compactable(_row("PLAN-a: " + "z " * 90), None)
    assert at.exempt_file(None, None) is False


def test_exempt_file_matches_retention_rule(tmp_path: Any) -> None:
    for name in (
        "docs/contracts/north-star.yaml",
        "/abs/elsewhere/north-star.yaml",
        str(tmp_path / "north-star.yaml"),
        "docs/contracts/other.yaml",
        "north-star.yaml.bak",
    ):
        assert at.exempt_file(name, CFG) == ar._exempt_file(name, CFG)
    assert at.exempt_file(tmp_path / "north-star.yaml", CFG) is True


def test_lossless_and_past_keep_reject_non_rows() -> None:
    assert at.lossless("x", _row("PLAN-a", pad=False), CFG) == "a row is not a mapping"
    assert at.rows_past_keep(None, CFG) == []
