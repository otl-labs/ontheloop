"""Tests for scripts/checks/decisions/append_only_census_recs.py (rec-5410's acceptance file).

Rec writes go through an injected portal_caller and rows (no network). TestAssert builds throwaway git histories with a
copy of the census tests' repo helper (no cross-test import).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.backlog_health import census as bh_census
from scripts.checks.decisions import append_only_census as census_mod
from scripts.checks.decisions import append_only_census_recs as recs
from scripts.executor.acceptance_lint import lint_acceptance_command

_ROOT = Path(__file__).resolve().parents[3]
M1 = "a" * 40
M2 = "b" * 40
D16 = "0123456789abcdef"


def _entry(
    merge: str = M1, pr: Any = 7, decisions: tuple[int, ...] = (65, 210), kinds: tuple[str, ...] = ("embedding",)
) -> dict:
    return {
        "merge": merge,
        "pr": pr,
        "decisions": [{"decision": n, "digest": D16, "kinds": list(kinds), "line": f"line {n}"} for n in decisions],
    }


def _result(opened: list[dict] | None = None, replayed: list[str] | None = None, defects: list[str] | None = None) -> dict:
    opened = opened if opened is not None else []
    shas = replayed if replayed is not None else sorted({e["merge"] for e in opened})
    return {
        "schema": 1,
        "head": "c" * 40,
        "merges": len(shas),
        "replayed": shas,
        "acknowledged": 0,
        "open": opened,
        "ledger_defects": defects or [],
    }


def _write(tmp_path: Path, data: Any, name: str = "r.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class _Portal:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def __call__(self, verb: str, payload: dict) -> Any:
        if self.fail_on and self.fail_on in json.dumps(payload):
            raise RuntimeError("boom")
        self.calls.append((verb, payload))
        return f"rec-{9000 + len(self.calls)}" if verb == "file" else True


def _run(
    tmp_path: Path, result: dict, rows: list[dict] | None = None, portal: _Portal | None = None, flags: tuple = ()
) -> int:
    portal = portal if portal is not None else _Portal()
    return recs.main(["--file", _write(tmp_path, result), *flags], portal_caller=portal, rows=rows if rows is not None else [])


def _stored(entry: dict, rec_id: str = "rec-1", status: str = "open") -> dict:
    fields = recs._merge_fields(entry)
    return {"id": rec_id, **fields, "status": status}


def _stored_ledger(defects: list[str], rec_id: str = "rec-2") -> dict:
    return {"id": rec_id, **recs._ledger_fields(defects)}


class TestFileOnFinding:
    def test_one_merge_with_two_decisions_files_one_rec(self, tmp_path: Path, capsys) -> None:
        portal = _Portal()
        entry = _entry()
        assert _run(tmp_path, _result([entry]), portal=portal) == 0
        assert [v for v, _ in portal.calls] == ["file"]
        fields = portal.calls[0][1]
        assert fields["context"].split("\n", 1)[0] == f"merge={M1}"
        assert fields["source"] == "decision_census" and fields["priority"] == "Medium" and fields["automatable"] is False
        assert fields["acceptance"] == recs.acceptance_for(M1, [65, 210])
        assert fields["title"] == "Append-only census: merge aaaaaaaa (#7) rewrote ratified lines in Decision 65, 210"
        out = capsys.readouterr().out
        assert f"filed merge={M1} rec=rec-9001" in out
        assert "[decision_census] summary open_merges=1 filed=1 updated=0 unchanged=0 closed=0 ledger=none" in out

    def test_repeat_run_writes_nothing(self, tmp_path: Path, capsys) -> None:
        portal = _Portal()
        assert _run(tmp_path, _result([_entry()]), rows=[_stored(_entry())], portal=portal) == 0
        assert portal.calls == []
        assert "unchanged merge=" in capsys.readouterr().out

    def test_changed_digest_updates_context_only(self, tmp_path: Path) -> None:
        portal = _Portal()
        changed = _entry()
        changed["decisions"][0]["digest"] = "f" * 16
        assert _run(tmp_path, _result([changed]), rows=[_stored(_entry())], portal=portal) == 0
        verb, payload = portal.calls[0]
        assert verb == "update" and payload["id"] == "rec-1" and set(payload) == {"id", "context"}

    def test_one_decision_acknowledged_updates_title_context_and_acceptance(self, tmp_path: Path) -> None:
        portal = _Portal()
        assert _run(tmp_path, _result([_entry(decisions=(210,))]), rows=[_stored(_entry())], portal=portal) == 0
        _, payload = portal.calls[0]
        assert set(payload) == {"id", "title", "context", "acceptance"}
        assert payload["title"].endswith("in Decision 210") and "(210)" in payload["acceptance"]

    def test_closed_or_declined_rec_is_not_reused(self, tmp_path: Path) -> None:
        for status in ("closed", "declined"):
            portal = _Portal()
            assert _run(tmp_path, _result([_entry()]), rows=[_stored(_entry(), status=status)], portal=portal) == 0
            assert [v for v, _ in portal.calls] == ["file"]

    def test_replayed_and_no_longer_open_merge_is_closed(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_RUN_ID", "55")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        portal = _Portal()
        assert _run(tmp_path, _result([], replayed=[M1]), rows=[_stored(_entry())], portal=portal) == 0
        verb, payload = portal.calls[0]
        assert verb == "update" and payload["status"] == "closed"
        assert "decision_census.yaml" in payload["resolution"] and "actions/runs/55" in payload["resolution"]
        assert "closed merge=" in capsys.readouterr().out

    def test_close_without_a_run_id_has_no_run_url(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
        portal = _Portal()
        _run(tmp_path, _result([], replayed=[M1]), rows=[_stored(_entry())], portal=portal)
        assert "Run:" not in portal.calls[0][1]["resolution"]

    def test_merge_the_result_never_replayed_is_left_open(self, tmp_path: Path) -> None:
        portal = _Portal()
        assert _run(tmp_path, _result([], replayed=[M2]), rows=[_stored(_entry())], portal=portal) == 0
        assert portal.calls == []

    def test_ledger_defects_file_one_rollup_that_closes_when_they_clear(self, tmp_path: Path, capsys) -> None:
        portal = _Portal()
        defects = ["row merge=aaaaaaaa decision=1: stale digest"]
        assert _run(tmp_path, _result([], defects=defects), portal=portal) == 0
        assert portal.calls[0][0] == "file" and portal.calls[0][1]["acceptance"] == recs._LEDGER_ACCEPTANCE
        assert portal.calls[0][1]["context"].startswith("census_class=ledger_defect")
        assert "ledger=filed" in capsys.readouterr().out
        portal = _Portal()
        assert _run(tmp_path, _result([]), rows=[_stored_ledger(defects)], portal=portal) == 0
        assert portal.calls[0][1]["status"] == "closed"
        assert "ledger=closed" in capsys.readouterr().out

    def test_unchanged_and_updated_rollup(self, tmp_path: Path, capsys) -> None:
        defects = ["d1"]
        portal = _Portal()
        _run(tmp_path, _result([], defects=defects), rows=[_stored_ledger(defects)], portal=portal)
        assert portal.calls == [] and "ledger=unchanged" in capsys.readouterr().out
        _run(tmp_path, _result([], defects=["d1", "d2"]), rows=[_stored_ledger(defects)], portal=portal)
        assert portal.calls[0][0] == "update" and "ledger=updated" in capsys.readouterr().out

    def test_a_token_for_one_merge_never_matches_a_prefix_sharing_merge(self, tmp_path: Path) -> None:
        other = M1[:-1] + "b"
        portal = _Portal()
        assert (
            _run(tmp_path, _result([_entry(merge=other)], replayed=[M1, other]), rows=[_stored(_entry())], portal=portal) == 0
        )
        verbs = [(v, p.get("status")) for v, p in portal.calls]
        assert verbs == [("file", "open"), ("update", "closed")]

    def test_title_omits_the_pr_when_null(self) -> None:
        assert "(#" not in recs._merge_title(_entry(pr=None))

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.update(extra=1),
            lambda d: d.update(head="abc"),
            lambda d: d["open"][0].update(merge="abc"),
            lambda d: d["open"][0]["decisions"][0].update(decision="65"),
            lambda d: d["open"][0]["decisions"][0].update(kinds=[]),
            lambda d: d["open"][0]["decisions"][0].update(kinds=["nope"]),
            lambda d: d["open"][0]["decisions"][0].update(digest="xyz"),
            lambda d: d["open"][0]["decisions"][0].update(decision=True),
            lambda d: d["open"][0]["decisions"][0].update(line=5),
            lambda d: d["open"][0]["decisions"][0].pop("line"),
            lambda d: d["open"][0].update(pr="7"),
            lambda d: d["open"][0].update(decisions=[]),
            lambda d: d["open"][0].update(extra=1),
            lambda d: d.update(replayed=[]),
            lambda d: d.update(replayed="x"),
            lambda d: d.update(open="x"),
            lambda d: d.update(open=["x"]),
            lambda d: d.update(ledger_defects=[1]),
            lambda d: d.update(ledger_defects="x"),
            lambda d: d.update(merges="3"),
            lambda d: d.update(schema=2),
        ],
    )
    def test_malformed_result_exits_two_with_no_write(self, tmp_path: Path, mutate, capsys) -> None:
        data = _result([_entry()])
        mutate(data)
        portal = _Portal()
        assert _run(tmp_path, data, portal=portal) == 2
        assert portal.calls == [] and "RESULT ERROR" in capsys.readouterr().out

    def test_unreadable_or_non_object_result_exits_two(self, tmp_path: Path) -> None:
        assert recs.main(["--file", str(tmp_path / "missing.json")], rows=[]) == 2
        assert recs.main(["--file", _write(tmp_path, [1])], rows=[]) == 2
        bad = tmp_path / "bad.json"
        bad.write_text("{", encoding="utf-8")
        assert recs.main(["--file", str(bad)], rows=[]) == 2

    def test_control_characters_and_length_are_cut_from_untrusted_text(self, tmp_path: Path) -> None:
        entry = _entry()
        entry["decisions"][0]["line"] = "x\x07y" + "z" * 500
        data = _result([entry], defects=["d\x00" + "q" * 500])
        loaded = recs.load_result(_write(tmp_path, data))
        shown = loaded["open"][0]["decisions"][0]["line"]
        assert "\x07" not in shown and len(shown) == 300
        assert "\x00" not in loaded["ledger_defects"][0] and len(loaded["ledger_defects"][0]) == 300

    def test_failed_lookup_exits_two_with_no_write(self, tmp_path: Path, monkeypatch, capsys) -> None:
        class Boom:
            def current_state(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("reader down")

        monkeypatch.setattr(recs.rec_episode, "_make_reader", lambda profile=None: Boom())
        portal = _Portal()
        assert recs.main(["--file", _write(tmp_path, _result([_entry()]))], portal_caller=portal) == 2
        assert portal.calls == [] and "LOOKUP ERROR" in capsys.readouterr().out

    def test_lookup_reads_the_scoped_source_once(self, tmp_path: Path, monkeypatch) -> None:
        seen: list[tuple] = []

        class Reader:
            def current_state(self, table: str, row_filter: str | None = None) -> list:
                seen.append((table, row_filter))
                return []

        monkeypatch.setattr(recs.rec_episode, "_make_reader", lambda profile=None: Reader())
        assert recs.main(["--file", _write(tmp_path, _result([])), "--profile", "p"], portal_caller=_Portal()) == 0
        assert seen == [("ops_recommendations", "source = 'decision_census'")]

    def test_one_failing_write_still_lets_the_others_run_then_exits_one(self, tmp_path: Path, capsys) -> None:
        portal = _Portal(fail_on=M1)
        result = _result([_entry(merge=M1), _entry(merge=M2)])
        assert _run(tmp_path, result, portal=portal) == 1
        assert [p["context"].split("\n")[0] for _, p in portal.calls] == [f"merge={M2}"]
        out = capsys.readouterr().out
        assert "FAILED file merge=" in out and "filed=1" in out

    def test_dry_run_writes_nothing_and_prints_would_file(self, tmp_path: Path, capsys) -> None:
        portal = _Portal()
        rows = [_stored(_entry(merge=M2), rec_id="rec-5")]
        result = _result([_entry()], replayed=[M1, M2])
        assert _run(tmp_path, result, rows=rows, portal=portal, flags=("--dry-run",)) == 0
        out = capsys.readouterr().out
        assert portal.calls == []
        assert f"would-file merge={M1}\n" in out and f"would-close merge={M2} rec=rec-5" in out

    def test_dry_run_would_update(self, tmp_path: Path, capsys) -> None:
        changed = _entry()
        changed["decisions"][0]["digest"] = "f" * 16
        assert _run(tmp_path, _result([changed]), rows=[_stored(_entry())], portal=_Portal(), flags=("--dry-run",)) == 0
        assert "would-update merge=" in capsys.readouterr().out

    def test_default_portal_routes_to_the_ops_data_portal(self, tmp_path: Path, monkeypatch) -> None:
        import scripts.ops_data_portal as portal_mod

        calls: list[tuple] = []
        monkeypatch.setattr(portal_mod, "file_rec", lambda fields, profile=None: calls.append(("file", profile)) or "rec-77")
        monkeypatch.setattr(
            portal_mod, "update_rec", lambda rec_id, updates, profile=None: calls.append(("update", rec_id, profile)) or True
        )
        data = _result([_entry()], replayed=[M1, M2])
        rows = [_stored(_entry(merge=M2), rec_id="rec-5")]
        assert recs.main(["--file", _write(tmp_path, data), "--profile", "p"], rows=rows) == 0
        assert calls == [("file", "p"), ("update", "rec-5", "p")]


class TestAcceptanceShape:
    def test_decidable_probeable_and_lints(self) -> None:
        many = recs.acceptance_for(M1, list(range(100, 120)))
        for cmd in (recs.acceptance_for(M1, [65, 210]), many, recs._LEDGER_ACCEPTANCE):
            ok, err = lint_acceptance_command(cmd, require_decidable=True)
            assert ok, err
        assert bh_census.classify_command(recs.acceptance_for(M1, [65, 210])) == bh_census.PROBEABLE
        assert bh_census.classify_command(many) == bh_census.PROBEABLE

    def _tree(self, tmp_path: Path, rows: list[int], annotation: bool) -> Path:
        (tmp_path / "config/agent").mkdir(parents=True)
        (tmp_path / "docs").mkdir()
        lines = [f"  - {{merge: {M1}, decision: {n}, digest: {D16}}}  # pragma: allowlist secret" for n in rows]
        (tmp_path / "config/agent/decision_census.yaml").write_text(
            "acknowledged:\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
        note = f"> **Update (2026-11-19, rec-1):** merge {M1[:8]} noted.\n" if annotation else "nothing\n"
        (tmp_path / "docs/DECISIONS.md").write_text(note, encoding="utf-8")
        (tmp_path / "docs/DECISIONS_ARCHIVE.md").write_text("", encoding="utf-8")
        return tmp_path

    def _exit(self, tree: Path, cmd: str) -> int:
        return subprocess.run(["bash", "-c", cmd], cwd=tree, capture_output=True).returncode

    def test_holds_with_every_row_and_the_annotation(self, tmp_path: Path) -> None:
        assert self._exit(self._tree(tmp_path, [65, 210], True), recs.acceptance_for(M1, [65, 210])) == 0

    def test_fails_with_a_row_missing(self, tmp_path: Path) -> None:
        assert self._exit(self._tree(tmp_path, [65], True), recs.acceptance_for(M1, [65, 210])) != 0

    def test_fails_without_the_annotation(self, tmp_path: Path) -> None:
        assert self._exit(self._tree(tmp_path, [65, 210], False), recs.acceptance_for(M1, [65, 210])) != 0

    def test_a_row_for_another_decision_does_not_count(self, tmp_path: Path) -> None:
        assert self._exit(self._tree(tmp_path, [65, 11], True), recs.acceptance_for(M1, [65, 210])) != 0


_D1 = "## Decision 1: First (Decided)\n\n**Status:** Decided\n**Date:** 2026-01-01\n\n**Decision:**\nRuling one.\n\n---\n"
_D2 = _D1.replace("1: First", "2: Second").replace("Ruling one.", "Ruling two, original.")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, live: str, subject: str, ledger: str | None = None) -> str:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs/DECISIONS.md").write_text(live, encoding="utf-8")
    (repo / "docs/DECISIONS_ARCHIVE.md").write_text("", encoding="utf-8")
    if ledger is not None:
        (repo / "config/agent").mkdir(parents=True, exist_ok=True)
        (repo / "config/agent/decision_census.yaml").write_text(ledger, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    anchor = _commit(root, _D1 + "\n" + _D2, "anchor")
    rewrite = _commit(root, _D1 + "\n" + _D2.replace("original", "REWRITTEN"), "rewrite two (#7)")
    monkeypatch.setattr(census_mod, "ANCHOR", anchor)
    monkeypatch.setattr(census_mod, "SELF_TEST_FIXTURE", (rewrite, (2,)))
    monkeypatch.setattr(recs._common, "ROOT", root)
    return {"root": root, "rewrite": rewrite}


class TestAssert:
    def _ack(self, repo: dict, digest: str | None = None, annotate: bool = True) -> None:
        digest = digest or census_mod.census(repo["root"]).findings[0].digest
        body = _D2.replace("original", "REWRITTEN")
        if annotate:
            body += f"> **Update (2026-11-19, rec-1):** Merge {repo['rewrite'][:8]} rewrote this entry.\n"
        ledger = yaml.safe_dump({"acknowledged": [{"merge": repo["rewrite"], "decision": 2, "digest": digest}]})
        _commit(repo["root"], _D1 + "\n" + body, "ack", ledger=ledger)

    def test_clean_ledger_exits_zero(self, repo: dict) -> None:
        self._ack(repo)
        assert recs.main(["--assert-ledger-clean"]) == 0

    def test_open_finding_without_a_row_is_not_a_ledger_defect(self, repo: dict) -> None:
        assert recs.main(["--assert-ledger-clean"]) == 0

    def test_defective_ledger_exits_one(self, repo: dict, capsys) -> None:
        self._ack(repo, digest="0" * 16)
        assert recs.main(["--assert-ledger-clean"]) == 1
        assert "LEDGER_DEFECT" in capsys.readouterr().out

    def test_incomplete_census_exits_two(self, repo: dict, capsys) -> None:
        assert recs.main(["--assert-ledger-clean", "--head", "no-such-rev"]) == 2
        assert "CENSUS ERROR" in capsys.readouterr().out

    def test_blind_detector_exits_two(self, repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(census_mod, "SELF_TEST_FIXTURE", (repo["root"].name and census_mod.ANCHOR, (2,)))
        assert recs.main(["--assert-ledger-clean"]) == 2


class TestWorkflowJob:
    def _wf(self) -> dict:
        return yaml.safe_load((_ROOT / ".github/workflows/decision-census.yml").read_text(encoding="utf-8"))

    def test_file_recs_assumes_the_branch_role_and_runs_the_filer_on_the_artifact(self) -> None:
        wf = self._wf()
        census_steps = wf["jobs"]["census"]["steps"]
        upload = next(s for s in census_steps if s.get("name") == "Upload census result")
        steps = wf["jobs"]["file-recs"]["steps"]
        cred = next(s for s in steps if s.get("uses", "").startswith("aws-actions/configure-aws-credentials@"))
        assert cred["with"]["role-to-assume"].endswith(":role/agent-platform-github-ci-branch")
        assert len(cred["uses"].split("@")[1].split(" ")[0]) == 40
        download = next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact@"))
        assert download["with"]["name"] == upload["with"]["name"] == "decision-census-result-${{ github.run_id }}"
        run = next(s["run"] for s in steps if "append_only_census_recs" in s.get("run", ""))
        assert run.startswith(".venv/bin/python -m scripts.checks.decisions.append_only_census_recs --file ")
        assert download["with"]["path"] in run

    def test_the_census_step_is_one_line_passes_result_and_fails_only_above_exit_one(self) -> None:
        step = next(s for s in self._wf()["jobs"]["census"]["steps"] if s.get("name") == "Run census")
        assert "\n" not in step["run"].strip() and "--result" in step["run"] and step["run"].endswith("|| test $? -le 1")


class TestRegistration:
    def test_decision_census_is_a_registered_source(self) -> None:
        reg = yaml.safe_load((_ROOT / "config/agent/data_quality/source_registry.yaml").read_text(encoding="utf-8"))
        ids = [r["canonical_id"] for r in reg["entries"]]
        assert "decision_census" in ids and len(ids) == len(set(ids))

    def test_taxonomy_rationale_names_its_own_recs(self) -> None:
        tax = yaml.safe_load((_ROOT / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
        row = tax["workflows"]["decision-census"]
        assert row["tier"] == "not_a_gate" and row["ci_rca"] == "excluded"
        assert "decision_census recs" in row["rationale"]

    def test_the_census_upload_is_classified(self) -> None:
        ev = yaml.safe_load((_ROOT / "docs/contracts/github-actions-evidence.yaml").read_text(encoding="utf-8"))
        rows = [u for u in ev["artifact_uploads"] if u["workflow"].endswith("decision-census.yml")]
        assert [r["retention_class"] for r in rows] == ["ephemeral_handoff"]

    def test_kind_words_match_the_census_rule(self) -> None:
        source = (_ROOT / "scripts/checks/decisions/append_only_rule.py").read_text(encoding="utf-8")
        for kind in recs.KINDS:
            assert f'"{kind}"' in source
