"""The overdue-review sensor files exactly one owning rec per due stanza (rec-5264)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.preflight import reversal_reviews as rr

ROOT = Path(__file__).resolve().parents[1]
MANUAL = "  - id: c1\n    kind: manual\n    description: d\n"


def _stanza(decision: int, review_by: str, conditions: str = MANUAL) -> str:
    return (
        f"## Decision {decision}: Fixture {decision}\n\n```yaml reversal-conditions\ndecision: {decision}\n"
        f"review_by: {review_by}\non_trigger: revisit it\nconditions:\n{conditions}```\n\n"
    )


def _md(tmp_path: Path, *blocks: str) -> list[Path]:
    path = tmp_path / "DECISIONS.md"
    path.write_text("".join(blocks), encoding="utf-8")
    return [path]


class Store:
    """In-memory reader and portal with SQL LIKE semantics."""

    def __init__(self, recs: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.recs = list(recs or [])
        self.reads: list[str] = []
        self.fail = fail

    def named(self, verb: str, **kw: Any) -> list[dict[str, Any]]:
        assert verb == "recs_by_title_prefix"
        if self.fail:
            raise RuntimeError("reader down")
        self.reads.append(kw["title_prefix"])
        pat = "^" + "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in kw["title_prefix"]) + "$"
        return [r for r in self.recs if re.match(pat, r["title"], re.S)]

    def file_rec(self, fields: dict[str, Any], profile: str | None = None) -> str:
        rec_id = f"rec-{9000 + len(self.recs)}"
        self.recs.append({"id": rec_id, "status": fields["status"], "title": fields["title"], "fields": fields})
        return rec_id


def _run(paths: list[Path], store: Store, **kw: Any) -> tuple[dict[str, int], list[str]]:
    lines: list[str] = []
    out = rr.run(paths=paths, reader=store, file_rec=store.file_rec, emit=lines.append, **kw)
    return out, lines


def test_overdue_stanza_yields_exactly_one_rec(tmp_path: Path) -> None:
    paths = _md(tmp_path, _stanza(901, "2020-01-01"))
    store = Store()
    first, lines = _run(paths, store)
    second, _ = _run(paths, store)
    assert len(store.recs) == 1 and first["filed"] == 1 and second["filed"] == 0 and second["owned"] == 1
    assert lines[0].startswith("[reversal_review] Decision 901 manual-review-due filed rec-")


def test_filed_rec_fields(tmp_path: Path) -> None:
    store = Store()
    _run(_md(tmp_path, _stanza(901, "2020-01-01")), store)
    f = store.recs[0]["fields"]
    assert f["title"] == "Review Decision 901 before 2020-01-01: reversal review due"
    assert f["source"] == "reversal_review" and f["status"] == "open" and f["priority"] == "High"
    assert f["acceptance"] == "bin/venv-python -m scripts.preflight.decision_conditions --assert-not-surfaced 901"
    assert f["file"].endswith("DECISIONS.md")
    assert "`revisit it`" in f["context"] and "scripts.preflight.review_dates --next" in f["context"]


@pytest.mark.parametrize("title", ["Review Decision 901 before X", "Supersede Decision 901 now"])
def test_owned_by_either_prefix_files_nothing(tmp_path: Path, title: str) -> None:
    store = Store([{"id": "rec-1", "status": "in_progress", "title": title}])
    out, lines = _run(_md(tmp_path, _stanza(901, "2020-01-01")), store)
    assert out["filed"] == 0 and lines[0].endswith("owned by rec-1")


def test_closed_owner_gets_a_new_rec(tmp_path: Path) -> None:
    store = Store([{"id": "rec-1", "status": "closed", "title": "Review Decision 901 before X"}])
    out, _ = _run(_md(tmp_path, _stanza(901, "2020-01-01")), store)
    assert out["filed"] == 1


def test_prefix_boundary_17_never_matches_173(tmp_path: Path) -> None:
    store = Store([{"id": "rec-1", "status": "open", "title": "Review Decision 173 before X"}])
    out, _ = _run(_md(tmp_path, _stanza(17, "2020-01-01")), store)
    assert out["filed"] == 1


def test_dry_run_files_nothing(tmp_path: Path) -> None:
    store = Store()
    out, lines = _run(_md(tmp_path, _stanza(901, "2020-01-01")), store, dry_run=True)
    assert store.recs == [] and out["filed"] == 0 and lines[0].endswith("would-file")


def test_malformed_stanza_filed_once_across_two_runs(tmp_path: Path) -> None:
    paths = _md(tmp_path, _stanza(902, "2999-01-01", "  - id: c\n    kind: nonsense\n    description: d\n"))
    store = Store()
    first, _ = _run(paths, store)
    second, lines = _run(paths, store)
    assert first["filed"] == 1 and first["malformed"] == 1 and len(store.recs) == 1
    assert store.recs[0]["title"].startswith("Review Decision 902 stanza unreadable: ")
    assert second["filed"] == 0 and "(error: " in lines[0]


def test_two_owners_warn(tmp_path: Path) -> None:
    store = Store(
        [
            {"id": "rec-2", "status": "open", "title": "Supersede Decision 901 x"},
            {"id": "rec-1", "status": "open", "title": "Review Decision 901 x"},
        ]
    )
    _, lines = _run(_md(tmp_path, _stanza(901, "2020-01-01")), store)
    assert lines[0].endswith("owned by rec-1, rec-2 (warning: more than one owner)")


def test_fired_title_quotes_condition_ids(tmp_path: Path) -> None:
    cond = "  - id: big\n    kind: repo_state\n    description: d\n    predicate: p\n"
    store = Store()
    results = rr.evaluate(paths=_md(tmp_path, _stanza(901, "2999-01-01", cond)), predicates={"p": lambda: True})
    rr.run(results=results, paths=[], reader=store, file_rec=store.file_rec, emit=lambda _l: None)
    assert store.recs[0]["title"] == "Review Decision 901 before 2999-01-01: reversal condition fired (`big`)"


def test_reads_fail_loud(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        _run(_md(tmp_path, _stanza(901, "2020-01-01")), Store(fail=True))


def test_no_due_stanza_makes_no_reader_call(tmp_path: Path) -> None:
    store = Store()
    out, lines = _run(_md(tmp_path, _stanza(901, "2999-01-01")), store)
    assert store.reads == [] and out == {"due": 0, "filed": 0, "owned": 0, "malformed": 0}
    assert lines == ["[reversal_review] summary due=0 filed=0 owned=0 malformed=0"]


def test_reader_calls_stay_at_two_however_many_are_due(tmp_path: Path) -> None:
    store = Store()
    _run(_md(tmp_path, *[_stanza(n, "2020-01-01") for n in (901, 902, 903)]), store)
    assert store.reads == ["Review Decision %", "Supersede Decision %"] and len(store.recs) == 3


def test_every_filed_title_matches_its_owner_sweep(tmp_path: Path) -> None:
    cond = "  - id: big\n    kind: repo_state\n    description: d\n    predicate: p\n"
    results = [
        *rr.evaluate(paths=_md(tmp_path, _stanza(901, "2020-01-01"))),
        *rr.evaluate(paths=_md(tmp_path, _stanza(902, "2999-01-01", cond)), predicates={"p": lambda: True}),
        *rr.evaluate(paths=_md(tmp_path, _stanza(903, "2999-01-01", "  - id: c\n    kind: bad\n    description: d\n"))),
    ]
    store = Store()
    rr.run(results=results, paths=[], reader=store, file_rec=store.file_rec, emit=lambda _l: None)
    assert len(store.recs) == 3
    again = Store(store.recs)
    out = rr.run(results=results, paths=[], reader=again, file_rec=again.file_rec, emit=lambda _l: None)
    assert out["filed"] == 0 and out["owned"] == 3


def test_source_is_registered() -> None:
    data = yaml.safe_load((ROOT / "config/agent/data_quality/source_registry.yaml").read_text(encoding="utf-8"))
    assert "reversal_review" in {e["canonical_id"] for e in data["entries"]}
