"""Tests for scripts/north_star_shortlist.py (PLAN-scout-north-star-lane, T4.36 c5)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import north_star_shortlist as ns
from scripts.north_star_models import NorthStarDocument, load_north_star

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "north_star_shortlist.py"


@pytest.fixture
def doc() -> NorthStarDocument:
    d = load_north_star().model_copy(deep=True)
    d.mission.carried_by = [9]
    by_id = {p.id: p for p in d.principles}
    by_id["NS.1"].carried_by = [1, 2, 3]
    by_id["NS.2"].carried_by = [3, 4, 5]
    by_id["NS.3"].status = "retired"
    return d


ROWS = [
    {"number": 1, "live": True, "serves": []},
    {"number": 2, "live": True, "serves": []},
    {"number": 3, "live": True, "serves": []},
    {"number": 4, "live": True, "serves": []},
    {"number": 5, "live": False, "serves": []},
    {"number": 6, "live": True, "serves": ["NS.1"]},
    {"number": 7, "live": True, "serves": ["NS.2"]},
    {"number": 8, "live": False, "serves": ["NS.1"]},
    {"number": 9, "live": True},
    {"number": 10, "live": True, "serves": None},
]


class TestShortlist:
    def test_union_is_sorted_and_deduplicated(self, doc: NorthStarDocument) -> None:
        out = ns.shortlist(["NS.2", "NS.1"], doc, ROWS)
        assert out["shortlist"] == [1, 2, 3, 4, 6, 7]
        assert out["not_live"] == [5]

    def test_engaged_keeps_input_order_and_drops_duplicates(self, doc: NorthStarDocument) -> None:
        out = ns.shortlist(["NS.2", "NS.1", "NS.2"], doc, ROWS)
        assert out["engaged"] == ["NS.2", "NS.1"]
        assert out["carried_by"] == {"NS.2": [3, 4, 5], "NS.1": [1, 2, 3]}

    def test_mission_is_accepted(self, doc: NorthStarDocument) -> None:
        out = ns.shortlist(["mission"], doc, ROWS)
        assert out["carried_by"] == {"mission": [9]}
        assert out["shortlist"] == [9]

    def test_serves_only_adds_live_row_serving_engaged_id(self, doc: NorthStarDocument) -> None:
        out = ns.shortlist(["NS.1"], doc, ROWS)
        assert out["serves_only"] == [6]
        assert 6 in out["shortlist"]

    def test_serves_only_excludes_non_engaged_archived_and_carried_rows(self, doc: NorthStarDocument) -> None:
        out = ns.shortlist(["NS.1"], doc, ROWS)
        assert 7 not in out["serves_only"]
        assert 8 not in out["serves_only"]
        assert not {1, 2, 3} & set(out["serves_only"])

    def test_not_live_is_excluded_from_shortlist(self, doc: NorthStarDocument) -> None:
        out = ns.shortlist(["NS.2"], doc, ROWS)
        assert 5 in out["not_live"]
        assert 5 not in out["shortlist"]

    def test_unknown_and_retired_ids_raise_keyerror_naming_them(self, doc: NorthStarDocument) -> None:
        with pytest.raises(KeyError) as exc:
            ns.shortlist(["NS.1", "NS.0", "NS.3"], doc, ROWS)
        assert exc.value.args[0] == ["NS.0", "NS.3"]


class TestMain:
    @pytest.fixture
    def wired(self, doc: NorthStarDocument, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        index = tmp_path / "decisions-index.json"
        index.write_text(json.dumps({"decisions": ROWS}), encoding="utf-8")
        monkeypatch.setattr(ns, "DEFAULT_INDEX_PATH", index)
        monkeypatch.setattr(ns, "load_north_star", lambda: doc)
        return index

    def test_prints_one_json_line_and_exits_zero(self, wired: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert ns.main(["NS.1"]) == 0
        out = capsys.readouterr().out
        assert out.count("\n") == 1
        assert sorted(json.loads(out)) == ["carried_by", "engaged", "not_live", "serves_only", "shortlist"]

    def test_reads_sys_argv_when_argv_is_none(
        self, wired: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["north_star_shortlist.py", "mission"])
        assert ns.main() == 0
        assert json.loads(capsys.readouterr().out)["engaged"] == ["mission"]

    def test_no_arguments_exits_two_with_usage(self, wired: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert ns.main([]) == 2
        assert "Usage:" in capsys.readouterr().err

    def test_unknown_id_exits_two_naming_it(self, wired: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert ns.main(["NS.0"]) == 2
        assert "NS.0" in capsys.readouterr().err

    def test_retired_id_exits_two_naming_it(self, wired: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert ns.main(["NS.1", "NS.3"]) == 2
        assert "NS.3" in capsys.readouterr().err

    def test_unreadable_north_star_file_exits_one(
        self, wired: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom() -> NorthStarDocument:
            raise ValueError("north-star.yaml: unreadable")

        monkeypatch.setattr(ns, "load_north_star", boom)
        assert ns.main(["NS.1"]) == 1
        assert "unreadable" in capsys.readouterr().err

    def test_load_failure_is_checked_before_ids_are_resolved(self, wired: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ns, "DEFAULT_INDEX_PATH", wired.with_name("missing.json"))
        assert ns.main(["NS.0"]) == 1

    def test_missing_index_file_exits_one(
        self, wired: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(ns, "DEFAULT_INDEX_PATH", wired.with_name("missing.json"))
        assert ns.main(["NS.1"]) == 1
        assert "missing.json" in capsys.readouterr().err

    def test_invalid_index_json_exits_one(self, wired: Path, capsys: pytest.CaptureFixture[str]) -> None:
        wired.write_text("{not json", encoding="utf-8")
        assert ns.main(["NS.1"]) == 1
        assert "invalid JSON" in capsys.readouterr().err

    @pytest.mark.parametrize("payload", [{}, {"metadata": {}}, {"decisions": {}}, {"decisions": "x"}, []])
    def test_index_without_a_decisions_list_exits_one(
        self, wired: Path, capsys: pytest.CaptureFixture[str], payload: object
    ) -> None:
        wired.write_text(json.dumps(payload), encoding="utf-8")
        assert ns.main(["NS.1"]) == 1
        assert "decisions" in capsys.readouterr().err


class TestImportHygiene:
    def test_only_first_party_import_is_north_star_models(self) -> None:
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        first_party = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "scripts":
                first_party.add(node.module)
            elif isinstance(node, ast.Import):
                first_party |= {a.name for a in node.names if a.name.split(".")[0] == "scripts"}
        assert first_party == {"scripts.north_star_models"}

    def test_import_has_no_side_effects(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", "import scripts.north_star_shortlist"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.returncode == 0
        assert proc.stdout == "" and proc.stderr == ""


class TestRealTree:
    def test_python_dash_m_covers_every_active_ids_carried_by(self) -> None:
        doc = load_north_star()
        ids = ["mission", *sorted(doc.active_ids())]
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.north_star_shortlist", *ids],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        union = set(doc.mission.carried_by) | {n for p in doc.principles if p.status == "active" for n in p.carried_by}
        assert out["engaged"] == ids
        assert set(out["shortlist"]) | set(out["not_live"]) == union | set(out["serves_only"])
        assert set(out["shortlist"]) <= union | set(out["serves_only"])
