"""Hermetic: the CLI contract -- arguments, JSON and --table output, exit codes (fake core, no git, no reader)."""

from __future__ import annotations

import importlib
import json
import runpy
import sys
from typing import Any

import pytest

from scripts.recs_related.envelope import Envelope, Hit

cli = importlib.import_module("scripts.recs_related.__main__")
core = importlib.import_module("scripts.recs_related.related_recs")

pytestmark = pytest.mark.unit


def _hit(rid: str, tier: str, why: list[str], file: str | None = "a.py", priority: str | None = "high") -> Hit:
    return Hit(rid, f"title {rid}", priority, "open", "S", "manual", file, tier, 2.5, why, "snip")


def _env(
    hits: list[Hit], total: int | None = None, next_exclude: list[str] | None = None, notes: list[str] | None = None
) -> Envelope:
    return Envelope(
        query={"files": ["a.py"], "aliases": [], "terms": ["x"]},
        query_files=[{"path": "a.py", "status": "tracked"}],
        hits=hits,
        total=len(hits) if total is None else total,
        direct_total=sum(1 for h in hits if h.tier == "direct"),
        channels={"imports": "ok", "history": "unavailable: shallow clone"},
        next_exclude=next_exclude,
        notes=notes or [],
    )


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    record: dict[str, Any] = {"env": _env([_hit("rec-1", "direct", ["direct:a.py"])])}

    def fake(files, terms_text, limit, exclude):
        record["args"] = (list(files), terms_text, limit, exclude)
        if isinstance(record["env"], Exception):
            raise record["env"]
        return record["env"]

    monkeypatch.setattr(cli, "related_recs", fake)
    return record


class TestCli:
    def test_default_output_is_the_envelope_json(self, seen: dict[str, Any], capsys: pytest.CaptureFixture[str]):
        assert cli.main(["a.py", "b.py"]) == 0
        out = capsys.readouterr().out
        assert json.loads(out) == seen["env"].to_dict()
        assert seen["args"] == (["a.py", "b.py"], "", 20, [])

    def test_options_are_passed_through(self, seen: dict[str, Any]):
        assert cli.main(["a.py", "--terms", "retry backoff", "--limit", "7", "--exclude", "rec-1, rec-2,,"]) == 0
        assert seen["args"] == (["a.py"], "retry backoff", 7, ["rec-1", " rec-2"])

    def test_terms_alone_are_a_valid_query(self, seen: dict[str, Any]):
        assert cli.main(["--terms", "retry"]) == 0
        assert seen["args"][0] == []

    def test_table_prints_one_line_per_hit_and_an_envelope_line(
        self, seen: dict[str, Any], capsys: pytest.CaptureFixture[str]
    ):
        seen["env"] = _env(
            [
                _hit("rec-1", "direct", ["direct:a.py"]),
                _hit("rec-2", "near", ["ancestor_dir:docs/contracts"], file="docs/contracts", priority=None),
                _hit("rec-3", "near", ["upstream:a.py", "ancestor_dir:docs"], file="b.py"),
                _hit("rec-4", "term", ["term:retry"], file=None),
            ],
            total=9,
            next_exclude=["rec-1", "rec-2"],
            notes=["a note"],
        )
        assert cli.main(["a.py", "--table"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].split("\t")[:5] == ["rec-1", "high", "direct", "2.50", "a.py"]
        assert lines[1].split("\t")[:5] == ["rec-2", "-", "near(dir)", "2.50", "docs/contracts"]
        assert lines[2].split("\t")[2] == "near"  # a mixed-reason near hit is not directory-only
        assert lines[3].split("\t")[:5] == ["rec-4", "high", "term", "2.50", "-"]
        assert lines[4] == (
            "# total=9 returned=4 complete=false direct_total=1 direct_complete=true next_exclude=rec-1,rec-2 "
            "channels[imports=ok,history=unavailable: shallow clone]"
        )
        assert lines[5] == "# note: a note"

    def test_table_envelope_line_marks_a_finished_sequence(self, seen: dict[str, Any], capsys: pytest.CaptureFixture[str]):
        assert cli.main(["a.py", "--table"]) == 0
        last = capsys.readouterr().out.splitlines()[-1]
        assert "complete=true" in last and "next_exclude=-" in last

    def test_no_files_and_no_terms_exits_2(self, seen: dict[str, Any], capsys: pytest.CaptureFixture[str]):
        assert cli.main([]) == 2
        assert cli.main(["--terms", "   "]) == 2
        assert "at least one FILE or --terms" in capsys.readouterr().err
        assert "args" not in seen

    def test_an_unusable_query_from_the_core_exits_2(self, seen: dict[str, Any], capsys: pytest.CaptureFixture[str]):
        seen["env"] = ValueError("recs_related: empty query -- bad")
        assert cli.main(["bad,name.py"]) == 2
        assert "empty query" in capsys.readouterr().err

    def test_a_reader_failure_exits_1_with_the_error_on_stderr_and_nothing_on_stdout(
        self, seen: dict[str, Any], capsys: pytest.CaptureFixture[str]
    ):
        seen["env"] = RuntimeError("reader down")
        assert cli.main(["a.py"]) == 1
        captured = capsys.readouterr()
        assert captured.out == "" and "RuntimeError: reader down" in captured.err

    @pytest.mark.parametrize("bad", ["0", "101", "many"])
    def test_limit_outside_1_to_100_is_rejected(self, seen: dict[str, Any], bad: str):
        with pytest.raises(SystemExit) as raised:
            cli.main(["a.py", "--limit", bad])
        assert raised.value.code == 2

    def test_python_dash_m_entry_point(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        monkeypatch.setattr(core, "related_recs", lambda *a, **k: _env([]))
        monkeypatch.setattr(sys, "argv", ["scripts.recs_related", "a.py"])
        with pytest.raises(SystemExit) as raised:
            runpy.run_module("scripts.recs_related", run_name="__main__")
        assert raised.value.code == 0
        assert json.loads(capsys.readouterr().out)["returned"] == 0
