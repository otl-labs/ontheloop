"""Tests for scripts/checks/hygiene/derived_list_recs.py (rec-5516): the weekly filer, its workflow and its rows.

Rec writes go through an injected portal_caller and rows (no network).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.backlog_health import census as bh_census
from scripts.checks.hygiene import derived_list_recs as recs
from scripts.executor.acceptance_lint import lint_acceptance_command

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github/workflows/derived-list-scan.yml"


def _file(path: str, score: float, klass: str = "derived_candidate", marker: str | None = None, **extra: Any) -> dict:
    row = {
        "path": path,
        "class": klass,
        "kind": "path",
        "entries": 12,
        "resolved_share": 0.9,
        "coupled_appends": int(score),
        "size_pressure": 0.5,
        "score": score,
        "marker": marker,
        "step": 1 if klass == "derived_candidate" else 0,
    }
    row.update(extra)
    return row


def _result(files: list[dict]) -> dict:
    return {"schema": 1, "note": None, "files": files}


def _write(tmp_path: Path, data: Any, name: str = "r.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class _Portal:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def __call__(self, verb: str, payload: dict) -> Any:
        if self.fail_on and self.fail_on == payload["file"]:
            raise RuntimeError("boom\x07")
        self.calls.append((verb, payload))
        return f"rec-{9000 + len(self.calls)}"


def _open(path: str, rec_id: str = "rec-1", status: str = "open") -> dict:
    return {"id": rec_id, "source": "code-smell", "status": status, "context": f"derived_list={path}\nmore"}


def _run(
    tmp_path: Path, files: list[dict], rows: list[dict] | None = None, portal: _Portal | None = None, flags: tuple = ()
) -> int:
    portal = portal if portal is not None else _Portal()
    return recs.main(["--file", _write(tmp_path, _result(files)), *flags], portal_caller=portal, rows=rows or [])


def _filed(portal: _Portal) -> list[str]:
    return [payload["file"] for _, payload in portal.calls]


SIX = [_file(f"config/l{i}.yaml", score=float(10 - i)) for i in range(6)]


def test_files_at_most_max_and_skips_marked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = [
        _file("config/top_marked.yaml", 99.0, marker="hand-kept: rec-1"),
        _file("config/top_baseline.yaml", 98.0, klass="baseline"),
        _file("config/top_none.yaml", 97.0, klass="none"),
        *SIX,
    ]
    portal = _Portal()
    assert _run(tmp_path, files, portal=portal) == 0
    assert _filed(portal) == ["config/l0.yaml", "config/l1.yaml", "config/l2.yaml"]
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == [f"[derived_list] filed config/l{i}.yaml rec=rec-{9001 + i}" for i in range(3)]
    assert out[3] == "[derived_list] summary candidates=7 filed=3 unchanged=0 skipped_marked=1 stale_open=0"
    assert len(out) == 4


def test_open_rec_keeps_its_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [_open("config/l0.yaml", "rec-1"), _open("config/l1.yaml", "rec-2"), _open("config/l2.yaml", "rec-3")]
    portal = _Portal()
    assert _run(tmp_path, SIX, rows=rows, portal=portal) == 0
    assert portal.calls == []
    assert capsys.readouterr().out.strip() == (
        "[derived_list] summary candidates=6 filed=0 unchanged=3 skipped_marked=0 stale_open=0"
    )


def test_open_rec_on_a_lower_ranked_file_holds_a_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    portal = _Portal()
    assert _run(tmp_path, SIX, rows=[_open("config/l5.yaml")], portal=portal) == 0
    assert _filed(portal) == ["config/l0.yaml", "config/l1.yaml"]
    assert "filed=2 unchanged=1" in capsys.readouterr().out


def test_stale_open_recs_are_counted_and_still_hold_slots(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = [_file("config/now_marked.yaml", 50.0, marker="hand-kept: rec-9"), *SIX]
    rows = [
        _open("config/now_marked.yaml", "rec-1"),
        _open("config/deleted.yaml", "rec-2"),
        _open("config/l3.yaml", "rec-3", status="closed"),
        {"id": "rec-4", "source": "code-smell", "status": "open", "context": "unrelated smell"},
        {"id": "rec-5", "source": "code-smell", "status": "open", "context": None},
        {"id": "rec-6", "source": "code-smell", "status": "open", "context": "derived_list="},
        _open("config/deleted.yaml", "rec-7"),
    ]
    portal = _Portal()
    assert _run(tmp_path, files, rows=rows, portal=portal) == 0
    assert _filed(portal) == ["config/l0.yaml"]
    assert capsys.readouterr().out.splitlines()[-1] == (
        "[derived_list] summary candidates=7 filed=1 unchanged=2 skipped_marked=1 stale_open=2"
    )


def test_a_closed_rec_is_refiled_and_a_second_run_files_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    portal = _Portal()
    assert _run(tmp_path, SIX[:1], rows=[_open("config/l0.yaml", status="closed")], portal=portal) == 0
    assert _filed(portal) == ["config/l0.yaml"]
    first = portal.calls[0][1]
    stored = {"id": "rec-9001", **first}
    again = _Portal()
    assert _run(tmp_path, SIX[:1], rows=[stored], portal=again) == 0
    assert again.calls == []
    assert "filed=0 unchanged=1" in capsys.readouterr().out.splitlines()[-1]


def test_a_failed_write_exits_one_after_attempting_the_rest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    portal = _Portal(fail_on="config/l0.yaml")
    assert _run(tmp_path, SIX, portal=portal) == 1
    assert _filed(portal) == ["config/l1.yaml", "config/l2.yaml"]
    out = capsys.readouterr().out
    assert "[derived_list] FAILED config/l0.yaml: RuntimeError: boom\n" in out
    assert "filed=2" in out


def test_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    portal = _Portal()
    assert _run(tmp_path, SIX, portal=portal, flags=("--dry-run", "--max", "2")) == 0
    assert portal.calls == []
    assert capsys.readouterr().out.splitlines() == [
        "[derived_list] would_file config/l0.yaml",
        "[derived_list] would_file config/l1.yaml",
        "[derived_list] summary candidates=6 would_file=2 unchanged=0 skipped_marked=0 stale_open=0",
    ]


def _mutations() -> list[Any]:
    good = _file("config/a.yaml", 1.0)
    return [
        [],
        {"schema": 2, "note": None, "files": []},
        {"schema": 1, "note": 3, "files": []},
        {"schema": 1, "note": None, "files": {}},
        {"schema": 1, "note": None, "files": ["x"]},
        _result([{**good, "path": "../escape.yaml"}]),
        _result([{**good, "path": "/abs.yaml"}]),
        _result([{**good, "path": 5}]),
        _result([{**good, "class": "other"}]),
        _result([{**good, "kind": "other"}]),
        _result([{**good, "entries": -1}]),
        _result([{**good, "entries": True}]),
        _result([{**good, "coupled_appends": 1.5}]),
        _result([{**good, "score": -1}]),
        _result([{**good, "resolved_share": "1"}]),
        _result([{**good, "marker": 3}]),
        _result([{**good, "step": 7}]),
        _result([{**good, "extra": 1}]),
    ]


@pytest.mark.parametrize("data", _mutations())
def test_malformed_result_exits_two_with_no_write(tmp_path: Path, data: Any, capsys: pytest.CaptureFixture[str]) -> None:
    portal = _Portal()
    assert recs.main(["--file", _write(tmp_path, data)], portal_caller=portal, rows=[]) == 2
    assert portal.calls == [] and "[derived_list] result error:" in capsys.readouterr().out


def test_unreadable_result_exits_two(tmp_path: Path) -> None:
    assert recs.main(["--file", str(tmp_path / "missing.json")], portal_caller=_Portal(), rows=[]) == 2
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert recs.main(["--file", str(tmp_path / "bad.json")], portal_caller=_Portal(), rows=[]) == 2


def test_marker_text_is_cleaned(tmp_path: Path) -> None:
    loaded = recs.load_result(
        _write(tmp_path, _result([_file("config/a.yaml", 1.0, marker="hand-kept: rec-1\x00" + "x" * 500)]))
    )
    assert loaded["files"][0]["marker"] == "hand-kept: rec-1" + "x" * 184


def test_failed_lookup_exits_two_with_no_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    class Boom:
        def current_state(self, *_a: object, **_k: object) -> None:
            raise RuntimeError("reader down")

    monkeypatch.setattr(recs.rec_episode, "_make_reader", lambda profile=None: Boom())
    portal = _Portal()
    assert recs.main(["--file", _write(tmp_path, _result(SIX))], portal_caller=portal) == 2
    assert portal.calls == [] and "[derived_list] lookup error: RuntimeError: reader down" in capsys.readouterr().out


def test_lookup_reads_the_scoped_source_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple] = []

    class Reader:
        def current_state(self, table: str, row_filter: str) -> list[dict]:
            seen.append((table, row_filter))
            return []

    monkeypatch.setattr(recs.rec_episode, "_make_reader", lambda profile=None: Reader())
    assert recs.main(["--file", _write(tmp_path, _result(SIX[:1]))], portal_caller=_Portal()) == 0
    assert seen == [("ops_recommendations", "source = 'code-smell'")]


def test_default_portal_routes_to_the_ops_data_portal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.ops_data_portal as portal_mod

    calls: list[tuple] = []

    def fake_file_rec(fields: dict, profile: str | None = None) -> str:
        calls.append((fields["file"], profile))
        return "rec-77"

    monkeypatch.setattr(portal_mod, "file_rec", fake_file_rec)
    assert recs.main(["--file", _write(tmp_path, _result(SIX[:1])), "--profile", "p"], rows=[]) == 0
    assert calls == [("config/l0.yaml", "p")]


class TestRecText:
    def test_rec_text_carries_token_order_and_a_decidable_acceptance(self) -> None:
        fields = recs.rec_fields(_file("config/ci_like.yaml", 3.0, kind="check", step=2))
        assert fields["title"] == "Derived-list candidate: config/ci_like.yaml (12 entries naming check names)"
        assert (
            fields["source"] == "code-smell" and fields["tags"] == ["derived-list"] and fields["file"] == "config/ci_like.yaml"
        )
        lines = fields["context"].splitlines()
        assert lines[0] == "derived_list=config/ci_like.yaml"
        order = [line for line in lines if line[:2] in {f"{n}." for n in range(1, 7)}]
        assert [line[:2] for line in order] == ["1.", "2.", "3.", "4.", "5.", "6."]
        assert "derive the list at read time" in order[0] and "hand-kept" in order[5]
        assert "The scan suggests step 2 (a registered check already reads this list)." in fields["context"]
        assert "a rec closed without one is re-filed" in fields["context"]
        assert len(fields["context"].strip()) >= 80
        acceptance = fields["acceptance"]
        assert (
            acceptance
            == "bin/venv-python -m scripts.checks.hygiene.derived_list_scan --assert-dispositioned config/ci_like.yaml"
        )
        ok, err = lint_acceptance_command(acceptance, require_discrimination=True)
        assert ok, err
        assert bh_census.classify_command(acceptance) == bh_census.PROBEABLE

    def test_step_one_text(self) -> None:
        context = recs.rec_fields(_file("config/x.yaml", 1.0))["context"]
        assert "The scan suggests step 1.\n" in context


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _effective_permissions(wf: dict, job: dict) -> dict:
    return {**(wf.get("permissions") or {}), **(job.get("permissions") or {})}


class TestWorkflowJob:
    def test_workflow_runs_weekly_on_main_and_never_on_a_pr(self) -> None:
        wf = _workflow()
        on: dict = wf.get("on", wf.get(True)) or {}
        assert set(on) == {"schedule", "workflow_dispatch"}
        crons = [row["cron"] for row in on["schedule"]]
        assert len(crons) == 1 and crons[0].split()[4].isdigit()
        scan, file_recs = wf["jobs"]["scan"], wf["jobs"]["file-recs"]
        assert "id-token" not in _effective_permissions(wf, scan)
        assert scan["if"] == "(github.event_name != 'workflow_dispatch' || github.actor != 'otl-workflow-dispatcher[bot]')"
        assert file_recs["if"] == "github.ref == 'refs/heads/main'" and file_recs["needs"] == "scan"
        assert file_recs["permissions"] == {"id-token": "write", "contents": "read"}
        steps = file_recs["steps"]
        cred_at = next(
            i for i, s in enumerate(steps) if s.get("uses", "").startswith("aws-actions/configure-aws-credentials@")
        )
        assert steps[cred_at]["with"]["role-to-assume"].endswith(":role/agent-platform-github-ci-branch")
        assert len(steps[cred_at]["uses"].split("@")[1].split(" ")[0]) == 40
        filer_at = next(i for i, s in enumerate(steps) if "derived_list_recs" in s.get("run", ""))
        assert filer_at > cred_at
        download = next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact@"))
        upload = next(s for s in scan["steps"] if s.get("uses", "").startswith("actions/upload-artifact@"))
        assert download["with"]["name"] == upload["with"]["name"] == "derived-list-result-${{ github.run_id }}"
        assert download["with"]["path"] in steps[filer_at]["run"]
        scan_run = next(s["run"] for s in scan["steps"] if "derived_list_scan" in s.get("run", ""))
        assert "--result" in scan_run and "$RUNNER_TEMP/derived-list-result.json" in scan_run
        checkout = next(s for s in scan["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}

    def test_every_run_body_is_at_most_three_effective_lines(self) -> None:
        for job in _workflow()["jobs"].values():
            for step in job["steps"]:
                body = [ln for ln in step.get("run", "").splitlines() if ln.strip() and not ln.strip().startswith("#")]
                assert len(body) <= 3, step.get("name")


class TestRegistration:
    def test_taxonomy_row_is_not_a_gate_and_excluded(self) -> None:
        tax = yaml.safe_load((_ROOT / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
        row = tax["workflows"]["derived-list-scan"]
        assert row["tier"] == "not_a_gate" and row["ci_rca"] == "excluded" and "rec-5516" in row["rationale"]

    def test_the_scan_upload_is_classified(self) -> None:
        ev = yaml.safe_load((_ROOT / "docs/contracts/github-actions-evidence.yaml").read_text(encoding="utf-8"))
        rows = [u for u in ev["artifact_uploads"] if u["workflow"].endswith("derived-list-scan.yml")]
        assert [r["retention_class"] for r in rows] == ["ephemeral_handoff"]

    def test_code_smell_is_a_registered_source(self) -> None:
        reg = yaml.safe_load((_ROOT / "config/agent/data_quality/source_registry.yaml").read_text(encoding="utf-8"))
        assert recs.SOURCE in {r["canonical_id"] for r in reg["entries"]}
