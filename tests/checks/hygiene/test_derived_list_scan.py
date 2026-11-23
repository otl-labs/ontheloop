"""Tests for scripts/checks/hygiene/derived_list_scan.py (rec-5516's acceptance file).

Every node builds a throwaway git repo; none reads this repo's live lists (Decision 244 cl.4: following the scan's own
advice would make such a node false). The live-tree known answers are the build's one-time evidence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.checks.hygiene import derived_list_rank as rank_mod
from scripts.checks.hygiene import derived_list_scan as scan_mod

_ROOT = Path(__file__).resolve().parents[3]
_FIRST_PARTY = {
    "scripts",
    "scripts.checks",
    "scripts.checks.hygiene",
    "scripts.checks.hygiene.derived_list_scan",
    "scripts.checks.hygiene.derived_list_rank",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _manifest(names: list[str], module_prefix: str = "scripts.checks.alpha") -> str:
    rows = "\n".join(f'    Entry(name="{n}", module="{module_prefix}.{n}", attr="{n}"),' for n in names)
    return f"from scripts.checks._schema import Entry\n\nENTRIES = (\n{rows}\n)\n"


def _yaml_map(key: str, tokens: list[str], value: str) -> str:
    return f"{key}:\n" + "".join(f"  {t}: {value}\n" for t in tokens)


CHECKS = [f"validate_thing_{i}" for i in range(12)]
WORKFLOWS = [f"wf-{i}" for i in range(11)]
DOCS = [f"docs/page_{i}.txt" for i in range(10)]


def _shapes() -> dict[str, str]:
    files = {"scripts/checks/alpha/_manifest.py": _manifest(CHECKS)}
    files.update({f"scripts/checks/alpha/{n}.py": "x = 1\n" for n in CHECKS})
    files.update({f".github/workflows/{w}.yml": "on:\n  workflow_dispatch:\njobs: {}\n" for w in WORKFLOWS})
    files.update(dict.fromkeys(DOCS, "text\n"))
    files["config/workflows_map.yaml"] = _yaml_map("workflows", WORKFLOWS, "{tier: not_a_gate, owner: platform}")
    files["config/ratchet.yaml"] = _yaml_map("budgets", DOCS, "12")
    files["config/prose.yaml"] = "items:\n" + "".join(f"  - free text item number {i}\n" for i in range(12))
    return files


def _classes(root: Path) -> dict[str, dict]:
    return {r["path"]: r for r in scan_mod.scan(root, history=False)["files"]}


def test_classes_on_synthetic_repo(tmp_path: Path) -> None:
    got = _classes(_repo(tmp_path / "r", _shapes()))
    assert got["scripts/checks/alpha/_manifest.py"]["class"] == "derived_candidate"
    assert got["scripts/checks/alpha/_manifest.py"]["kind"] == "check"
    assert got["config/workflows_map.yaml"]["class"] == "derived_candidate"
    assert got["config/workflows_map.yaml"]["kind"] == "workflow"
    assert got["config/ratchet.yaml"]["class"] == "baseline"
    assert got["config/prose.yaml"]["class"] == "none"
    assert got["config/ratchet.yaml"]["step"] == 0


def test_mixed_file_takes_the_strongest_class(tmp_path: Path) -> None:
    files = dict.fromkeys(DOCS, "t\n")
    paths = "listed:\n" + "".join(f"  - {d}\n" for d in DOCS)
    prose = "notes:\n" + "".join(f"  - prose {i}\n" for i in range(10))
    files["config/mixed.yaml"] = _yaml_map("budgets", DOCS, "3") + paths
    files["config/ratchet_and_prose.yaml"] = _yaml_map("budgets", DOCS, "3") + prose
    got = _classes(_repo(tmp_path / "r", files))
    assert got["config/mixed.yaml"]["class"] == "derived_candidate"
    assert got["config/ratchet_and_prose.yaml"]["class"] == "baseline"


def test_generic_stems_do_not_resolve(tmp_path: Path) -> None:
    generic = sorted(rank_mod.GENERIC_STEMS)
    files = {f"pkg{i}/{stem}.py": "" for i, stem in enumerate(generic * 2)}
    files["config/generic.yaml"] = "rows:\n" + "".join(f"  - {s}\n" for s in generic * 2)
    files.update({f"lib/specific_{i}.py": "" for i in range(10)})
    files["config/specific.yaml"] = "rows:\n" + "".join(f"  - specific_{i}\n" for i in range(10))
    got = _classes(_repo(tmp_path / "r", files))
    assert got["config/generic.yaml"]["class"] == "none" and got["config/generic.yaml"]["resolved_share"] == 0.0
    assert got["config/specific.yaml"]["class"] == "derived_candidate" and got["config/specific.yaml"]["kind"] == "stem"


def test_markers_disposition_a_candidate(tmp_path: Path) -> None:
    files = _shapes()
    body = _yaml_map("workflows", WORKFLOWS, "{tier: x}")
    files["config/hand_kept.yaml"] = "# hand-kept: rec-1 the owner keeps this by hand\n" + body
    files["config/verified.yaml"] = "# verified-against: validate_thing_3\n" + body
    files["config/unknown_check.yaml"] = "# verified-against: validate_no_such_check\n" + body
    files["config/no_reason.yaml"] = "# hand-kept: rec-1\n" + body
    files["config/late_banner.yaml"] = "\n" * 11 + "# GENERATED -- DO NOT EDIT\n" + body
    files["config/past_window.yaml"] = "\n" * 15 + "# hand-kept: rec-1 too late to count\n" + body
    files["config/not_a_comment.yaml"] = "note: 'hand-kept: rec-1 inside a value'\n" + body
    got = _classes(_repo(tmp_path / "r", files))
    assert got["config/hand_kept.yaml"]["marker"] == "hand-kept: rec-1"
    assert got["config/verified.yaml"]["marker"] == "verified-against: validate_thing_3"
    assert got["config/late_banner.yaml"]["marker"] == "generated"
    for unmarked in ("unknown_check", "no_reason", "past_window", "not_a_comment"):
        assert got[f"config/{unmarked}.yaml"]["class"] == "derived_candidate"
        assert got[f"config/{unmarked}.yaml"]["marker"] is None, unmarked


def test_generated_files_are_skipped(tmp_path: Path) -> None:
    files = _shapes()
    body = _yaml_map("workflows", WORKFLOWS, "{tier: x}")
    files["config/structural_size_budgets.yaml"] = "generated_pinned:\n  - config/pinned.yaml\n"
    files["config/pinned.yaml"] = body
    files["config/bannered.yaml"] = "# GENERATED -- DO NOT EDIT\n" + body
    got = _classes(_repo(tmp_path / "r", files))
    assert "config/pinned.yaml" not in got and "config/bannered.yaml" not in got
    assert got["config/workflows_map.yaml"]["class"] == "derived_candidate"


def test_generated_roster_that_is_unreadable_or_malformed_skips_nothing(tmp_path: Path) -> None:
    files = {"config/structural_size_budgets.yaml": "generated_pinned: [unclosed\n"}
    assert scan_mod._generated(_repo(tmp_path / "a", files)) == set()
    files = {"config/structural_size_budgets.yaml": "generated_pinned: not-a-list\n"}
    assert scan_mod._generated(_repo(tmp_path / "b", files)) == set()
    assert scan_mod._generated(tmp_path / "missing") == set()


def test_check_names_resolve_without_importing_checks(tmp_path: Path) -> None:
    names = [f"validate_ghost_{i}" for i in range(10)]
    files = {"scripts/checks/ghost/_manifest.py": _manifest(names, "scripts.checks.ghost")}
    files["config/uses_checks.yaml"] = "rows:\n" + "".join(f"  - {n}\n" for n in names)
    root = _repo(tmp_path / "r", files)
    assert not (root / "scripts/checks/ghost/validate_ghost_0.py").exists()
    out = tmp_path / "result.json"
    code = (
        "import json, sys\n"
        "base = set(sys.modules)\n"
        "import yaml\n"
        "yaml_runtime = sorted(set(sys.modules) - base)\n"
        "from scripts.checks.hygiene import derived_list_scan as m\n"
        f"rc = m.main(['--root', {str(root)!r}, '--no-history', '--result', {str(out)!r}])\n"
        "print(json.dumps({'rc': rc, 'new': sorted(set(sys.modules) - base), 'yaml': yaml_runtime}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["rc"] == 0
    imported = report["new"]
    assert "scripts.checks.hygiene.derived_list_scan" in imported
    assert "yaml" in report["yaml"]
    allowed = _FIRST_PARTY | set(report["yaml"])
    foreign = [m for m in imported if m.split(".")[0] not in sys.stdlib_module_names and m not in allowed]
    assert foreign == []
    rows = {r["path"]: r for r in json.loads(out.read_text(encoding="utf-8"))["files"]}
    assert rows["config/uses_checks.yaml"]["class"] == "derived_candidate"
    assert rows["config/uses_checks.yaml"]["kind"] == "check"


def test_assert_dispositioned_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = _shapes()
    files["config/marked.yaml"] = "# hand-kept: rec-1 kept by hand\n" + _yaml_map("workflows", WORKFLOWS, "{t: x}")
    root = _repo(tmp_path / "r", files)

    def code(rel: str) -> int:
        return scan_mod.main(["--root", str(root), "--assert-dispositioned", rel])

    assert code("config/workflows_map.yaml") == 1
    assert "undispositioned config/workflows_map.yaml (11 entries naming workflows)" in capsys.readouterr().out
    assert code("config/marked.yaml") == 0
    assert "dispositioned config/marked.yaml marker=hand-kept: rec-1" in capsys.readouterr().out
    assert code("config/ratchet.yaml") == 0
    assert code("./config/gone.yaml") == 0
    assert "not_a_candidate ./config/gone.yaml" in capsys.readouterr().out


def test_exit_two_on_an_unreadable_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "plain").mkdir()
    assert scan_mod.main(["--root", str(tmp_path / "plain"), "--no-history"]) == 2
    assert "[derived_list] scan error: git ls-files exited" in capsys.readouterr().out


def test_exit_two_when_git_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("no git")

    monkeypatch.setattr(scan_mod.subprocess, "run", boom)
    assert scan_mod.main(["--root", str(tmp_path), "--no-history"]) == 2


def test_exit_two_on_a_manifest_that_does_not_parse(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r", {"scripts/checks/bad/_manifest.py": "ENTRIES = (\n"})
    assert scan_mod.main(["--root", str(root), "--no-history"]) == 2


def test_exit_two_when_the_history_read_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path / "r", _shapes())

    def fail(*_a: object, **_k: object) -> None:
        raise rank_mod.RankError("git log exited 128")

    monkeypatch.setattr(rank_mod, "rank", fail)
    assert scan_mod.main(["--root", str(root)]) == 2


def test_cli_writes_result_and_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path / "r", _shapes())
    result, summary = tmp_path / "out.json", tmp_path / "out.md"
    summary.write_text("stale\n", encoding="utf-8")
    assert scan_mod.main(["--root", str(root), "--result", str(result), "--summary", str(summary)]) == 0
    data = json.loads(result.read_text(encoding="utf-8"))
    assert set(data) == {"schema", "note", "files"} and data["schema"] == 1
    assert "every score is 0" in data["note"]
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("## Derived-list scan (rec-5516)\n") and "stale" not in text
    assert "| config/workflows_map.yaml | derived_candidate | workflow | 11 |" in text
    assert "Note: only 1 commits in the window" in text
    assert "[derived_list] scan files=" in capsys.readouterr().out
    assert scan_mod.main(["--root", str(root), "--no-history"]) == 0


def test_history_scores_reach_the_records(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r", _shapes())
    (root / "scripts/checks/alpha/validate_new.py").write_text("x = 1\n", encoding="utf-8")
    manifest = root / "scripts/checks/alpha/_manifest.py"
    manifest.write_text(_manifest([*CHECKS, "validate_new"]), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add a check")
    result = scan_mod.scan(root, history=True, min_commits=1)
    row = next(r for r in result["files"] if r["path"] == "scripts/checks/alpha/_manifest.py")
    assert result["note"] is None
    assert row["coupled_appends"] == 2 and row["score"] > 2  # the seed commit adds the manifest with its modules
    assert result["files"][0]["path"] == "scripts/checks/alpha/_manifest.py"
    assert "Note:" not in scan_mod.summary_markdown(result)


def test_step_two_when_a_registered_check_reads_the_list(tmp_path: Path) -> None:
    files = _shapes()
    files["scripts/checks/alpha/validate_thing_0.py"] = 'PATH = "config/workflows_map.yaml"\n'
    got = _classes(_repo(tmp_path / "r", files))
    assert got["config/workflows_map.yaml"]["step"] == 2
    assert got["scripts/checks/alpha/_manifest.py"]["step"] == 1


def test_trigger_paths_codeowners_and_lambda_manifests(tmp_path: Path) -> None:
    files = dict.fromkeys(DOCS, "t\n")
    files[".github/workflows/bool_on.yml"] = (
        "name: Bool On\non:\n  push:\n    paths:\n      - docs/page_0.txt\n      - 'docs/*.txt'\n      - '!docs/page_1.txt'\n"
    )
    files[".github/workflows/short.yml"] = "on:\n  push:\n    paths: [docs/page_0.txt]\n  workflow_dispatch:\n"
    files[".github/workflows/list_on.yml"] = "on: [push]\n"
    files[".github/workflows/quoted_on.yml"] = (
        "'on':\n  pull_request:\n    paths: [docs/page_0.txt, docs/page_1.txt, docs/page_2.txt]\n"
    )
    files[".github/CODEOWNERS"] = "# owners\n\n" + "".join(f"/{d} @o\n" for d in DOCS)
    files[".github/CODEOWNERS_TOO_SHORT"] = "/docs/ @o\n"
    files["src/lambdas/fn/manifest.yaml"] = "sources:\n" + "".join(f"  - ./{d}\n" for d in DOCS)
    files["other/ignored.yaml"] = "rows:\n" + "".join(f"  - {d}\n" for d in DOCS)
    got = _classes(_repo(tmp_path / "r", files))
    assert got[".github/workflows/bool_on.yml"]["kind"] == "trigger_paths"
    assert got[".github/workflows/bool_on.yml"]["class"] == "derived_candidate"
    assert got[".github/workflows/short.yml"]["class"] == "none"
    assert got[".github/workflows/list_on.yml"]["class"] == "none"
    assert got[".github/workflows/quoted_on.yml"]["kind"] == "trigger_paths"
    assert got[".github/CODEOWNERS"]["class"] == "derived_candidate" and got[".github/CODEOWNERS"]["entries"] == 10
    assert got["src/lambdas/fn/manifest.yaml"]["kind"] == "path"
    assert "other/ignored.yaml" not in got and ".github/CODEOWNERS_TOO_SHORT" not in got


def test_codeowners_below_the_threshold_is_none(tmp_path: Path) -> None:
    got = _classes(_repo(tmp_path / "r", {".github/CODEOWNERS": "/docs/ @o\n"}))
    assert got[".github/CODEOWNERS"]["class"] == "none" and got[".github/CODEOWNERS"]["entries"] == 0


def test_unparsed_and_deleted_files(tmp_path: Path) -> None:
    root = _repo(tmp_path / "r", {"config/broken.yaml": "a: [unclosed\n", "config/gone.yaml": "a: 1\n"})
    (root / "config/gone.yaml").unlink()
    got = _classes(root)
    assert got["config/broken.yaml"]["kind"] == "unparsed" and got["config/broken.yaml"]["class"] == "none"
    assert "config/gone.yaml" not in got


class TestResolver:
    def _resolver(self, tmp_path: Path) -> scan_mod.Resolver:
        files = {
            "scripts/pkg/mod.py": "",
            "src/pkg2/__init__.py": "",
            "docs/a.txt": "",
            "scripts/checks/z/_manifest.py": _manifest(["validate_z"]),
            ".github/workflows/deploy.yml": "name: Deploy Things\non: [push]\n",
        }
        root = _repo(tmp_path / "r", files)
        scan_mod.static_scan(root)
        tracked = scan_mod.tracked_files(root)
        return scan_mod.Resolver(
            files=frozenset(tracked),
            dirs=frozenset({"docs", "scripts", "scripts/pkg"}),
            stems=frozenset({"a", "mod"}),
            checks=frozenset({"validate_z"}),
            workflows=frozenset({"deploy", "Deploy Things"}),
        )

    def test_each_entity_kind(self, tmp_path: Path) -> None:
        r = self._resolver(tmp_path)
        assert r.kind("docs/a.txt") == "path" and r.kind("/docs/") == "path" and r.kind("./docs") == "path"
        assert r.kind("docs/*.txt") == "glob" and r.kind("docs/*.txt") == "glob" and r.kind("nope/*") is None
        assert r.kind("validate_z") == "check"
        assert r.kind("scripts.pkg.mod") == "module" and r.kind("src.pkg2") == "module" and r.kind("scripts.none") is None
        assert r.kind("Deploy Things") == "workflow" and r.kind("deploy") == "workflow"
        assert r.kind("mod") == "stem"
        assert r.kind("docs/a.txt::job::step") == "path" and r.kind("zzz::docs/a.txt") is None
        assert r.kind(7) is None and r.kind(None) is None and r.kind("/") is None and r.kind("!docs/a.txt") == "path"


class TestCollection:
    def test_classes_and_kinds(self) -> None:
        def c(resolved: int, numeric: int, kinds: dict[str, int] | None = None, trigger: bool = False) -> scan_mod.Collection:
            return scan_mod.Collection(10, resolved, numeric, scan_mod.Counter(kinds or {}), trigger)

        assert c(5, 0).klass == "none"
        assert c(6, 8).klass == "baseline"
        assert c(6, 5).klass == "derived_candidate"
        assert c(6, 6).klass == "none"
        assert c(6, 0, {"path": 2, "stem": 4}).kind == "stem"
        assert c(6, 0, trigger=True).kind == "trigger_paths"
        assert c(0, 0).kind == "none"

    def test_list_pairs(self) -> None:
        assert scan_mod._list_pair({"path": "a", "limit": 3, "max": 4.5}) == ("a", 3)
        assert scan_mod._list_pair({"path": "a", "why": "text", "n": 2}) == ("a", None)
        assert scan_mod._list_pair({"nested": {"x": 1}}) == (None, None)
        assert scan_mod._list_pair([True, "b", "c"]) == ("b", None)
        assert scan_mod._list_pair("plain") == ("plain", None)


class TestManifestParse:
    def test_attribute_entries_and_missing_modules(self) -> None:
        text = 'import s\nE = (s.Entry(name="a", module="scripts.x.a"), Entry(name="b"), Entry(name=1), other(name="c"))\n'
        parsed = scan_mod.parse_manifest(text, "m.py")
        assert parsed.names == ("a", "b") and parsed.modules == ("scripts.x.a", "")
