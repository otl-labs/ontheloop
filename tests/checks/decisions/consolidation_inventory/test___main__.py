"""Mirror of __main__.py: the CLI in-process over the fixture repo, plus invariants over the live tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.checks.decisions import consolidation_inventory as facade
from scripts.checks.decisions.consolidation_inventory.__main__ import main
from tests.checks.decisions.consolidation_inventory import REAL_ROOT, build_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path)


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_text_and_json_are_deterministic(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, first = _run(capsys, "9001", "9002", "--root", str(repo))
    _, second = _run(capsys, "9002", "9001", "--root", str(repo))
    assert code == 0 and first == second
    assert first.splitlines()[-1].startswith("TOTAL hits=") and "needs_disposition=" in first.splitlines()[-1]
    _, raw = _run(capsys, "9001", "--root", str(repo), "--json")
    _, again = _run(capsys, "9001", "--root", str(repo), "--json")
    data = json.loads(raw)
    assert raw == again and data["schema_version"] == 1 and data["stub_template"] == "compacted-stub-v1"
    assert data["hits"] == sorted(data["hits"], key=lambda h: (h["kind"], h["victim"], h["locator"]))


def test_tier_filter(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, everything = _run(capsys, "9001", "--root", str(repo), "--json")
    _, confirmed = _run(capsys, "9001", "--root", str(repo), "--json", "--tier", "confirmed")
    assert {h["tier"] for h in json.loads(confirmed)["hits"]} == {"confirmed"}
    assert len(json.loads(confirmed)["hits"]) < len(json.loads(everything)["hits"])


def test_exit_codes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["4242", "--root", str(repo)]) == 1
    with pytest.raises(SystemExit) as raised:
        main(["abc", "--root", str(repo)])
    assert raised.value.code == 2
    capsys.readouterr()
    _, once = _run(capsys, "9001", "--root", str(repo), "--json")
    _, twice = _run(capsys, "9001", "9001", "--root", str(repo), "--json")
    assert once == twice


def test_facade_exports_public_surface() -> None:
    for name in ("Hit", "Evidence", "Inventory", "RULES", "STUB_TEMPLATE", "inventory", "probe"):
        assert hasattr(facade, name), name
    assert callable(facade.inventory) and callable(facade.probe)


def test_real_repo_smoke_invariants(capsys: pytest.CaptureFixture[str]) -> None:
    rows = json.loads((REAL_ROOT / "docs/decisions-index.json").read_text(encoding="utf-8"))["decisions"]
    victim = next(r["number"] for r in rows if r.get("live") and r.get("currency") == "current")
    code, first = _run(capsys, str(victim), "--root", str(REAL_ROOT), "--json")
    assert code == 0
    data = json.loads(first)
    assert json.loads(json.dumps(data)) == data
    for hit in data["hits"]:
        path = hit["locator"].split("::")[0].split(":")[0]
        assert (REAL_ROOT / path).exists(), hit["locator"]
    _, second = _run(capsys, str(victim), "--root", str(REAL_ROOT), "--json")
    assert first == second


def test_probe_flag_runs_the_probe(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = build_repo(tmp_path, with_tools=True)
    code, out = _run(capsys, "9002", "--root", str(root), "--probe")
    assert code == 0 and "stub_template: compacted-stub-v1" in out and "probe=breaks" in out


def test_notes_are_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = build_repo(tmp_path, {"docs/contracts/north-star.yaml": "mission: [\n"})
    code, out = _run(capsys, "9001", "--root", str(root))
    assert code == 0 and "note: docs/contracts/north-star.yaml: surface file does not parse" in out


def test_a_failed_probe_exits_1(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.checks.decisions.consolidation_inventory import __main__ as cli

    def failing(root: Path, inv):
        inv.probe_failed = "worktree unavailable"
        return inv

    monkeypatch.setattr(cli, "probe", failing)
    assert main(["9001", "--root", str(repo), "--probe"]) == 1
    assert "probe failed: worktree unavailable" in capsys.readouterr().err
