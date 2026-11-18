"""fires / holds / gate semantics of decision_conditions and the two Decision 179 predicates (rec-5264)."""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.preflight import decision_conditions as dc

TODAY = date(2026, 11, 18)


def _write(tmp_path: Path, condition: str, review_by: str = "2026-12-01", decision: int = 901) -> Path:
    text = (
        f"## Decision {decision}: Fixture\n\n```yaml reversal-conditions\n"
        f"decision: {decision}\nreview_by: {review_by}\non_trigger: review it\nconditions:\n{condition}```\n"
    )
    path = tmp_path / "DECISIONS.md"
    path.write_text(text, encoding="utf-8")
    return path


def _repo(extra: str = "") -> str:
    return f"  - id: c1\n    kind: repo_state\n    description: d\n    predicate: p\n    params: {{n: 1}}\n{extra}"


def _one(tmp_path: Path, condition: str, preds: dict[str, Any], review_by: str = "2026-12-01", **kw: Any):
    path = _write(tmp_path, condition, review_by)
    return dc.evaluate(paths=[path], predicates=preds, today=kw.pop("today", TODAY), **kw)[0]


def test_on_true_fires_immediately_and_holds_is_recorded(tmp_path: Path) -> None:
    r = _one(tmp_path, _repo(), {"p": lambda n: True})
    assert r.state == "fired" and r.conditions[0].holds is True and r.conditions[0].fired is True


def test_at_review_condition_fires_only_once_review_by_passes(tmp_path: Path) -> None:
    cond = _repo("    fires: at_review\n")
    before = _one(tmp_path, cond, {"p": lambda n: True}, review_by="2026-12-01")
    assert before.state == "not-due" and before.conditions[0].holds is True and before.conditions[0].fired is False
    after = _one(tmp_path, cond, {"p": lambda n: True}, review_by="2026-11-18")
    assert after.state == "fired" and after.fired_condition_ids == ["c1"]
    quiet = _one(tmp_path, cond, {"p": lambda n: False}, review_by="2026-11-18")
    assert quiet.state == "manual-review-due" and quiet.conditions[0].holds is False


@pytest.mark.parametrize(
    "condition",
    [
        "  - id: c1\n    kind: manual\n    description: d\n    fires: at_review\n",
        _repo("    fires: sometimes\n"),
    ],
)
def test_bad_fires_is_malformed(tmp_path: Path, condition: str) -> None:
    assert _one(tmp_path, condition, {"p": lambda n: True}).state == "MALFORMED"


def test_gate_false_predicate_without_at_review_is_malformed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dc, "_GATE_EXEMPT", {"p"})
    assert _one(tmp_path, _repo(), {"p": lambda n: True}).state == "MALFORMED"
    assert _one(tmp_path, _repo("    fires: at_review\n"), {"p": lambda n: False}).state == "not-due"


def test_as_of_is_injected_and_capped_at_review_by(tmp_path: Path) -> None:
    seen: list[date] = []

    def p(n: int, as_of: date | None = None) -> bool:
        assert as_of is not None
        seen.append(as_of)
        return False

    cond = _repo("    fires: at_review\n")
    _one(tmp_path, cond, {"p": p}, review_by="2026-12-01")
    _one(tmp_path, cond, {"p": p}, review_by="2026-11-01")
    assert seen == [TODAY, date(2026, 11, 1)]


def test_params_carrying_as_of_is_malformed(tmp_path: Path) -> None:
    cond = "  - id: c1\n    kind: repo_state\n    description: d\n    predicate: p\n    params: {as_of: 2026-01-01}\n"
    assert _one(tmp_path, cond, {"p": lambda as_of=None: True}).state == "MALFORMED"


def test_gate_true_skips_only_gate_false_predicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dc, "_GATE_EXEMPT", {"slow"})
    calls: list[str] = []

    def slow(n: int) -> bool:
        calls.append("slow")
        return True

    def fast(n: int) -> bool:
        calls.append("fast")
        return False

    two = (
        "  - id: a\n    kind: repo_state\n    description: d\n    predicate: slow\n    params: {n: 1}\n    fires: at_review\n"
        "  - id: b\n    kind: repo_state\n    description: d\n    predicate: fast\n    params: {n: 1}\n"
    )
    r = _one(tmp_path, two, {"slow": slow, "fast": fast}, review_by="2026-11-01", gate=True)
    assert calls == ["fast"] and r.state == "manual-review-due" and r.conditions[0].holds is None
    calls.clear()
    r = _one(tmp_path, two, {"slow": slow, "fast": fast}, review_by="2026-11-01")
    assert calls == ["slow", "fast"] and r.state == "fired"


def test_gate_true_still_checks_registration_and_binding_of_exempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dc, "_GATE_EXEMPT", {"slow"})
    bad = (
        "  - id: a\n    kind: repo_state\n    description: d\n    predicate: slow\n"
        "    params: {wrong: 1}\n    fires: at_review\n"
    )
    assert _one(tmp_path, bad, {"slow": lambda n: True}, gate=True).state == "MALFORMED"
    assert _one(tmp_path, bad, {}, gate=True).state == "MALFORMED"


def test_gate_true_runs_raising_gate_safe_predicate(tmp_path: Path) -> None:
    def boom(n: int) -> bool:
        raise RuntimeError("boom")

    assert _one(tmp_path, _repo(), {"p": boom}, gate=True).state == "MALFORMED"


def test_assert_not_surfaced_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hist = (
        "  - id: inflow\n    kind: repo_state\n    description: d\n    predicate: decision_entries_per_plan_exceed\n"
        "    params: {window_days: 90, max_entries_per_plan: 0.3}\n    fires: at_review\n"
    )
    due = _write(tmp_path, hist, review_by="2020-01-01", decision=901)
    assert dc._cli(["--assert-not-surfaced", "901", str(due)]) == 1
    assert dc._cli(["--assert-not-surfaced", "902", str(due)]) == 0
    future = _write(tmp_path, hist, review_by="2999-01-01", decision=901)
    assert dc._cli(["--assert-not-surfaced", "901", str(future)]) == 0
    bad = _write(tmp_path, "  - id: c\n    kind: nonsense\n    description: d\n", decision=901)
    assert dc._cli(["--assert-not-surfaced", "901", str(bad)]) == 1
    capsys.readouterr()


def test_gate_exempt_predicates_are_pinned() -> None:
    assert dc._GATE_EXEMPT == {"decision_entries_per_plan_exceed"}


def test_decision_179_predicates_are_registered_and_bind_its_params() -> None:
    reg = dc._PREDICATE_REGISTRY
    inspect.signature(reg["decision_index_bytes_exceed"]).bind(max_bytes=110608)
    inspect.signature(reg["decision_entries_per_plan_exceed"]).bind(window_days=90, max_entries_per_plan=0.30)


def test_decision_index_bytes_exceed_compares_file_size(tmp_path: Path) -> None:
    index = tmp_path / "idx.json"
    index.write_bytes(b"x" * 100)
    assert dc.decision_index_bytes_exceed(99, index_path=index) is True
    assert dc.decision_index_bytes_exceed(100, index_path=index) is False


def test_entries_per_plan_predicate_compares_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.preflight.corpus_measures as cm

    monkeypatch.setattr(cm, "entries_per_plan", lambda window_days, as_of=None, root=".": (30, 100))
    assert dc.decision_entries_per_plan_exceed(90, 0.29) is True
    assert dc.decision_entries_per_plan_exceed(90, 0.30) is False
