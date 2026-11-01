"""Decision 226 cl.3-4: scripts.ci_rca.red_overlap. No network: the reader and the selector are injected."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scripts.ci_rca import red_overlap as ro

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
SLO = 4


def _manifest(selected: list[str] | None = None, deferred: list[str] | None = None, **flags: bool) -> dict:
    return {"manifest": {"selected": selected or [], "deferred": deferred or [], **flags}}


def _selector(by_path: dict[str, dict]):
    """Selector fake: keyed by the first path of the entry list; default is a clean empty manifest."""

    def select(entries: list[tuple[str, str]]) -> dict:
        return by_path.get(entries[0][1], _manifest())

    return select


def _red(
    rec_id: str = "rec-1", age_h: float = 1.0, file: str = "scripts/a.py", ctx: dict | None = None, **extra: object
) -> dict:
    created = (NOW - timedelta(hours=age_h)).isoformat()
    return {
        "id": rec_id,
        "source": "ci_rca",
        "status": "open",
        "file": file,
        "created_timestamp": created,
        "context_v2_json": json.dumps(ctx if ctx is not None else {}),
        **extra,
    }


def _ev(reds, work=(), bundled=(), selector=None):
    return ro.evaluate(list(reds), list(work), list(bundled), NOW, SLO, selector or _selector({}))


def _rules(verdict: dict) -> list[str]:
    return [r["rule"] for r in verdict["reds"]]


class TestEvaluate:
    def test_no_reds_is_clear(self) -> None:
        assert _ev([])["verdict"] == "clear"

    def test_past_slo_blocks_regardless_of_track_or_overlap(self) -> None:
        track_a = {"repair_track": "test_defect", "workflow_tier": "CI"}
        v = _ev([_red(age_h=SLO + 1, ctx=track_a)], work=[("M", "scripts/zzz.py")])
        assert v["verdict"] == "blocked"
        assert _rules(v) == ["slo_breached"]

    def test_in_slo_track_a_clears(self) -> None:
        v = _ev([_red(ctx={"repair_track": "test_defect", "workflow_tier": "CI"})], work=[("M", "scripts/a.py")])
        assert (v["verdict"], _rules(v)) == ("clear", ["track_a_in_slo"])

    def test_test_defect_on_non_ci_tier_is_not_exempt(self) -> None:
        v = _ev([_red(ctx={"repair_track": "test_defect", "workflow_tier": "unknown"})], work=[("M", "scripts/a.py")])
        assert _rules(v) == ["overlap"]

    def test_empty_red_file_set_blocks(self) -> None:
        v = _ev([_red(file="")], work=[("M", "scripts/b.py")])
        assert (v["verdict"], _rules(v)) == ("blocked", ["undeterminable"])

    @pytest.mark.parametrize("flag", ["fallback", "full_suite_forced"])
    @pytest.mark.parametrize("side", ["work", "red"])
    def test_selector_fallback_or_full_suite_blocks(self, flag: str, side: str) -> None:
        key = "scripts/b.py" if side == "work" else "scripts/a.py"
        v = _ev([_red()], work=[("M", "scripts/b.py")], selector=_selector({key: _manifest(**{flag: True})}))
        assert _rules(v) == ["selector_fail_closed"]

    def test_shared_affected_path_blocks_and_names_it(self) -> None:
        sel = _selector(
            {
                "scripts/b.py": _manifest(selected=["tests/test_shared.py"]),
                "scripts/a.py": _manifest(deferred=["tests/test_shared.py"]),
            }
        )
        v = _ev([_red()], work=[("M", "scripts/b.py")], selector=sel)
        assert _rules(v) == ["overlap"]
        assert v["reds"][0]["shared"] == ["tests/test_shared.py"]

    def test_shared_list_is_capped(self) -> None:
        many = [f"tests/t{i:02d}.py" for i in range(30)]
        sel = _selector({"scripts/b.py": _manifest(selected=many), "scripts/a.py": _manifest(selected=many)})
        v = _ev([_red()], work=[("M", "scripts/b.py")], selector=sel)
        assert len(v["reds"][0]["shared"]) == 20

    def test_disjoint_sets_clear(self) -> None:
        sel = _selector(
            {"scripts/b.py": _manifest(selected=["tests/test_b.py"]), "scripts/a.py": _manifest(selected=["tests/test_a.py"])}
        )
        v = _ev([_red()], work=[("M", "scripts/b.py")], selector=sel)
        assert (v["verdict"], _rules(v)) == ("clear", ["disjoint"])

    def test_unparseable_timestamp_blocks_as_slo_breached(self) -> None:
        v = _ev([_red(created_timestamp="not-a-time")], work=[("M", "scripts/b.py")])
        assert _rules(v) == ["slo_breached"]
        assert v["reds"][0]["age_hours"] is None

    def test_naive_and_datetime_timestamps_parse(self) -> None:
        naive = _red(created_timestamp=(NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat())
        dt = _red(rec_id="rec-2", created_timestamp=NOW - timedelta(hours=1))
        v = _ev([naive, dt], work=[("M", "scripts/b.py")])
        assert [r["age_hours"] for r in v["reds"]] == [1.0, 1.0]

    def test_bundled_red_clears_as_repair(self) -> None:
        v = _ev([_red(age_h=SLO + 5)], work=[("M", "scripts/a.py")], bundled=["rec-1"])
        assert (v["verdict"], _rules(v)) == ("clear", ["repairs_this_red"])

    def test_plan_bundling_one_red_is_not_blocked_by_another_past_slo(self) -> None:
        reds = [_red("rec-1", age_h=SLO + 1), _red("rec-2", age_h=SLO + 9, file="scripts/c.py")]
        sel = _selector(
            {"scripts/b.py": _manifest(selected=["tests/test_b.py"]), "scripts/c.py": _manifest(selected=["tests/test_c.py"])}
        )
        v = _ev(reds, work=[("M", "scripts/b.py")], bundled=["rec-1"], selector=sel)
        assert _rules(v) == ["repairs_this_red", "disjoint"]
        assert v["verdict"] == "clear"

    def test_default_selector_is_the_real_derivation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[object] = []
        import scripts.checks.deps.affected_tests as at

        monkeypatch.setattr(at, "derive_affected_tests", lambda entries: calls.append(entries) or _manifest())
        ro.evaluate([_red()], [("M", "scripts/b.py")], [], NOW, SLO)
        assert len(calls) == 2


class _Reader:
    def __init__(self, rows: list[dict] | Exception) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def named(self, verb: str, **kw: object) -> list[dict]:
        self.calls.append((verb, kw))
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


def _contract(tmp_path: Path, slo: object = SLO) -> Path:
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump({"recovery_policy": {"slo_hours": slo}}), encoding="utf-8")
    return path


def _run(tmp_path: Path, rows, *, plan=None, files=(), report=None, slo=SLO):
    return ro.run(
        plan,
        list(files),
        report=report or tmp_path / "missing-report.json",
        contract=_contract(tmp_path, slo),
        reader=_Reader(rows),
        selector=_selector({}),
        now=NOW,
    )


class TestInputs:
    def test_reader_exception_is_unavailable(self, tmp_path: Path) -> None:
        verdict, code = _run(tmp_path, RuntimeError("boom"))
        assert code == 2
        assert verdict["verdict"] == "unavailable"

    def test_no_arguments_skips_overlap_rules_but_keeps_slo_and_undeterminable(self, tmp_path: Path) -> None:
        rows = [_red("rec-1", file="scripts/a.py"), _red("rec-2", age_h=SLO + 1), _red("rec-3", file="")]
        verdict, code = _run(tmp_path, rows)
        assert [r["rule"] for r in verdict["reds"]] == ["disjoint", "slo_breached", "undeterminable"]
        assert code == 1

    def test_six_open_reds_are_all_evaluated_and_closed_ones_ignored(self, tmp_path: Path) -> None:
        rows = [_red(f"rec-{i}") for i in range(6)] + [
            _red("rec-9", status="closed"),
            {"id": "x", "source": "other", "status": "open"},
        ]
        reader = _Reader(rows)
        verdict, _ = ro.run(None, [], report=tmp_path / "none.json", contract=_contract(tmp_path), reader=reader, now=NOW)
        assert len(verdict["reds"]) == 6
        assert reader.calls == [("preflight_signal_rows", {"followon_ids": ""})]

    def test_unparseable_context_is_not_track_a(self, tmp_path: Path) -> None:
        verdict, _ = _run(tmp_path, [_red(context_v2_json="{not json")], files=["scripts/b.py"])
        assert verdict["reds"][0]["track"] == "product_regression"

    def test_affected_nodeids_extend_the_red_file_set(self, tmp_path: Path) -> None:
        red = _red(file="", ctx={"affected_nodeids": ["tests/test_x.py::TestA::test_b"]})
        verdict, _ = _run(tmp_path, [red], files=["scripts/b.py"])
        assert verdict["reds"][0]["rule"] == "disjoint"

    def test_likely_resolved_id_does_not_block(self, tmp_path: Path) -> None:
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"recs_read_status": "ok", "ci_rca_likely_resolved_recs": [{"id": "rec-1"}, "rec-7"]}))
        verdict, code = _run(tmp_path, [_red("rec-1", age_h=SLO + 3), _red("rec-7")], report=report)
        assert (verdict["reds"], code) == ([], 0)

    @pytest.mark.parametrize(
        "body",
        ["{broken", json.dumps({"recs_read_status": "reader_unreachable", "ci_rca_likely_resolved_recs": ["rec-1"]}), "[]"],
    )
    def test_missing_or_not_ok_report_excludes_nothing(self, tmp_path: Path, body: str) -> None:
        report = tmp_path / "report.json"
        report.write_text(body)
        verdict, _ = _run(tmp_path, [_red("rec-1")], report=report)
        assert len(verdict["reds"]) == 1

    def test_missing_report_file_excludes_nothing(self, tmp_path: Path) -> None:
        verdict, _ = _run(tmp_path, [_red("rec-1")])
        assert len(verdict["reds"]) == 1

    def test_lazy_reader_construction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.common.ducklake_reader_client as client

        monkeypatch.setattr(client, "make_reader", lambda: _Reader([]))
        verdict, code = ro.run(None, [], report=tmp_path / "none.json", contract=_contract(tmp_path), now=NOW)
        assert (verdict["verdict"], code) == ("clear", 0)

    def test_run_defaults_now_and_selector(self, tmp_path: Path) -> None:
        verdict, code = ro.run(None, [], report=tmp_path / "none.json", contract=_contract(tmp_path), reader=_Reader([]))
        assert (verdict["verdict"], code) == ("clear", 0)


class TestCli:
    def _plan(self, tmp_path: Path, bundled: list[str] | None = None) -> Path:
        path = tmp_path / "PLAN-x.yaml"
        scope = [
            {"file": "scripts/new.py", "action": "Create"},
            {"file": "scripts/old.py", "action": "Delete"},
            {"file": "scripts/mod.py", "action": "Modify"},
            {"file": "scripts/other.py", "action": "Rename"},
            {"purpose": "no file"},
        ]
        path.write_text(yaml.safe_dump({"scope": scope, "bundled_recommendations": bundled or []}), encoding="utf-8")
        return path

    def test_plan_scope_maps_actions(self, tmp_path: Path) -> None:
        entries, bundled = ro._work_from_plan(self._plan(tmp_path, ["rec-5"]))
        assert entries == [
            ("A", "scripts/new.py"),
            ("D", "scripts/old.py"),
            ("M", "scripts/mod.py"),
            ("M", "scripts/other.py"),
        ]
        assert bundled == ["rec-5"]

    def test_empty_plan_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert ro._work_from_plan(path) == ([], [])

    def test_main_prints_one_json_line_and_exit_codes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(ro, "CONTRACT_PATH", _contract(tmp_path))
        monkeypatch.setattr(ro, "REPORT_PATH", tmp_path / "none.json")
        import src.common.ducklake_reader_client as client

        monkeypatch.setattr(client, "make_reader", lambda: _Reader([_red(file="")]))
        code = ro.main(["--files", "scripts/b.py"])
        out = capsys.readouterr().out.strip().splitlines()
        assert code == 1 and len(out) == 1
        assert json.loads(out[0])["verdict"] == "blocked"

        monkeypatch.setattr(client, "make_reader", lambda: _Reader([]))
        assert ro.main(["--plan", str(self._plan(tmp_path)), "--report", str(tmp_path / "r.json")]) == 0
        assert len(capsys.readouterr().out.strip().splitlines()) == 1

        monkeypatch.setattr(client, "make_reader", lambda: _Reader(RuntimeError("down")))
        assert ro.main([]) == 2
        assert json.loads(capsys.readouterr().out)["verdict"] == "unavailable"

    def test_selector_banner_stays_off_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        def noisy(entries: list[tuple[str, str]]) -> dict:
            print("AFFECTED-SET CAP banner")
            return _manifest()

        ro.run(
            None,
            ["scripts/b.py"],
            report=tmp_path / "n.json",
            contract=_contract(tmp_path),
            reader=_Reader([_red()]),
            selector=noisy,
            now=NOW,
        )
        captured = capsys.readouterr()
        assert captured.out == "" and "banner" in captured.err

    def test_module_entry_point(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import runpy

        import src.common.ducklake_reader_client as client

        monkeypatch.setattr(client, "make_reader", lambda: _Reader(RuntimeError("down")))
        monkeypatch.setattr("sys.argv", ["red_overlap"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("scripts.ci_rca.red_overlap", run_name="__main__")
        assert exc.value.code == 2


class TestPolicySource:
    def test_slo_comes_from_the_contract(self, tmp_path: Path) -> None:
        red = _red(age_h=2)
        short, _ = _run(tmp_path, [red], slo=1)
        long, _ = _run(tmp_path, [red], slo=3)
        assert short["slo_hours"] == 1 and short["reds"][0]["rule"] == "slo_breached"
        assert long["slo_hours"] == 3 and long["reds"][0]["rule"] == "disjoint"

    @pytest.mark.parametrize("bad", [None, 0, -4, True, "4", 2.5])
    def test_missing_or_non_positive_slo_exits_2(self, tmp_path: Path, bad: object) -> None:
        verdict, code = _run(tmp_path, [_red()], slo=bad)
        assert code == 2 and verdict["verdict"] == "unavailable"

    def test_shipped_contract_value_is_a_positive_int(self) -> None:
        assert ro._load_slo_hours(ro.CONTRACT_PATH) == 4
