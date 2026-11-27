"""reference_hits: terms, the whole-tree pass, ranking, caps and head code as data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.personas import reference_hits as R

OLD = "def kept(x):\n    return 1\n\n\ndef removed_helper():\n    return 2\n\n\ndef changed_function():\n    return 3\n"
NEW = (
    "def kept(x):\n    return 1\n\n\ndef changed_function():\n    return 4\n\n\n"
    "def __dunder__():\n    return 5\n\n\ndef tiny():\n    return 6\n"
)


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _run(
    root: Path, files: list[dict[str, Any]], base: dict[str, Any], scope: str = "", plan: str | None = None
) -> dict[str, Any]:
    return json.loads(R.reference_hits(root, files, base, scope, plan))


def _terms(doc: dict[str, Any]) -> dict[str, Any]:
    return {t["term"]: t for t in doc["terms"]}


def test_reference_hits_find_references_outside_the_bundle(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    _write(
        tmp_path / "snap",
        {
            "src/pkg/mod.py": NEW,
            "src/pkg/user.py": "from src.pkg.mod import changed_function\nchanged_function()\n",
            "docs/guide.md": "the file src/pkg/mod.py calls changed_function\n",
            "src/pkg/boom.py": f"open({str(marker)!r}, 'w').write('ran')\n",
        },
    )
    files = [{"filename": "src/pkg/mod.py", "status": "modified"}]
    scope = "=== src/pkg/mod.py ===\n" + NEW + "\n\n=== src/pkg/user.py ===\nx"
    doc = _run(tmp_path / "snap", files, {"src/pkg/mod.py": OLD}, scope)
    terms = _terms(doc)
    hits = {h["path"]: h["in_bundle"] for h in terms["changed_function"]["hits"]}
    assert hits["src/pkg/user.py"] is True and hits["docs/guide.md"] is False
    assert {h["path"] for h in terms["src/pkg/mod.py"]["hits"]} >= {"docs/guide.md"}
    assert {h["path"] for h in terms["src.pkg.mod"]["hits"]} == {"src/pkg/user.py"}
    assert not marker.exists()


def test_reference_hits_terms_and_skips(tmp_path: Path, monkeypatch: Any) -> None:
    _write(
        tmp_path / "snap",
        {
            "src/pkg/mod.py": NEW,
            "src/pkg/big.txt": "removed_helper " + "x" * (R.MAX_FILE_BYTES + 1),
            "src/pkg/bin.dat": b"\x00removed_helper",
            "src/pkg/latin.txt": b"removed_helper \xff",
            "docs/plans/PLAN-x.yaml": "removed_helper changed_function\n",
            ".git/config": "removed_helper\n",
            "src/pkg/user.py": "removed_helper()\nkept(1)\ntiny()\n__dunder__()\n",
        },
    )
    files = [{"filename": "src/pkg/mod.py", "status": "modified"}]
    doc = _run(tmp_path / "snap", files, {"src/pkg/mod.py": OLD}, plan="docs/plans/PLAN-x.yaml")
    terms = _terms(doc)
    assert terms["removed_helper"]["kind"] == "removed_symbol" and terms["changed_function"]["kind"] == "symbol"
    assert "kept" not in terms and "tiny" not in terms and "__dunder__" not in terms
    assert [h["path"] for h in terms["removed_helper"]["hits"]] == ["src/pkg/user.py"]
    assert doc["files_skipped"] >= 4
    monkeypatch.setattr(R, "MAX_HITS_PER_TERM", 2)
    _write(tmp_path / "snap", {f"docs/many{i}.md": "changed_function\n" for i in range(5)})
    capped = _terms(_run(tmp_path / "snap", files, {"src/pkg/mod.py": OLD}))["changed_function"]
    assert len(capped["hits"]) == 2 and capped["truncated"] is True
    monkeypatch.setattr(R, "MAX_TERMS", 1)
    assert len(_run(tmp_path / "snap", files, {"src/pkg/mod.py": OLD})["terms"]) == 1


def test_code_callers_survive_plan_history(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(R, "MAX_HITS_PER_TERM", 3)
    plans = {f"docs/plans/PLAN-{i}.yaml": "changed_function here\n" for i in range(10)}
    _write(tmp_path / "snap", {"src/pkg/mod.py": NEW, "tests/test_mod.py": "changed_function()\n", **plans})
    files = [{"filename": "src/pkg/mod.py", "status": "modified"}]
    hits = _terms(_run(tmp_path / "snap", files, {"src/pkg/mod.py": OLD}))["changed_function"]["hits"]
    paths = [h["path"] for h in hits]
    assert paths[0] == "src/pkg/mod.py" and "tests/test_mod.py" in paths and paths.index("tests/test_mod.py") < 2
    assert hits and sum(p.startswith("docs/plans/") for p in paths) <= 1
