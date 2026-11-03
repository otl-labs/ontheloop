"""rec-4687's acceptance file: the premerge acceptance preview (scripts/session/merge_step.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import rec_trailer_acceptance as acceptance
from scripts.session import merge_step as ms
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"


def _rows(*ids: str, status: str = "open") -> list[dict]:
    return [{"id": i, "status": status, "acceptance": "echo ok"} for i in ids]


def _run_premerge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundled: list[str], rows, verdicts, routes=None):
    repo = fx.make_pr_branch(tmp_path, monkeypatch, SLUG, bundled)
    msg = fx.message_file(tmp_path, fx.head_message(repo))
    seen: dict = {}

    def fake_census(ids, rows=None, *, repo_root, **_):
        seen["census"] = (ids, rows, repo_root)
        return [{"rec_id": i, "route": (routes or {}).get(i, "static"), "bucket": "probeable"} for i in ids]

    def fake_evaluate(entries, repo_root, sha):
        seen["entries"] = [dict(e) for e in entries]
        return {"records": [{"rec_id": e["rec_id"], "verdict": verdicts[e["rec_id"]], "arm": "ok"} for e in entries]}

    def fetch(ids):
        if isinstance(rows, Exception):
            raise rows
        return rows

    monkeypatch.setattr(ms, "_fetch_rows", fetch)
    monkeypatch.setattr(acceptance, "census", fake_census)
    monkeypatch.setattr(acceptance, "evaluate", fake_evaluate)
    with patch("scripts.ops_data_portal.update_rec") as update:
        code = ms.main(["premerge", "--slug", SLUG, "--root", str(repo), "--message-file", str(msg)])
    update.assert_not_called()
    return code, seen


class TestPremergePreview:
    def test_holds_prints_holds_and_exits_zero(self, tmp_path, monkeypatch, capsys):
        code, _ = _run_premerge(tmp_path, monkeypatch, ["rec-1"], _rows("rec-1"), {"rec-1": "holds"})
        assert code == 0
        assert "PREVIEW rec-1 holds arm=ok" in capsys.readouterr().out

    @pytest.mark.parametrize("verdict", ["fails", "out_of_grammar"])
    def test_blocking_verdict_exits_one_naming_the_rec(self, tmp_path, monkeypatch, capsys, verdict):
        code, _ = _run_premerge(tmp_path, monkeypatch, ["rec-1"], _rows("rec-1"), {"rec-1": verdict})
        assert code == 1
        assert f"PREVIEW rec-1 {verdict}" in capsys.readouterr().out

    def test_unmeasurable_is_printed_not_fatal(self, tmp_path, monkeypatch, capsys):
        code, _ = _run_premerge(tmp_path, monkeypatch, ["rec-1"], _rows("rec-1"), {"rec-1": "unmeasurable"})
        assert code == 0
        assert "PREVIEW rec-1 unmeasurable" in capsys.readouterr().out

    def test_junit_routed_entry_is_evaluated_through_the_static_probe(self, tmp_path, monkeypatch):
        _, seen = _run_premerge(
            tmp_path, monkeypatch, ["rec-1"], _rows("rec-1"), {"rec-1": "holds"}, routes={"rec-1": "junit"}
        )
        assert [e["route"] for e in seen["entries"]] == ["static"]

    def test_reader_failure_prints_unavailable_and_no_holds(self, tmp_path, monkeypatch, capsys):
        code, _ = _run_premerge(tmp_path, monkeypatch, ["rec-1"], RuntimeError("reader down"), {})
        out = capsys.readouterr().out
        assert code == 0
        assert "PREVIEW unavailable: reader down" in out and "holds" not in out
