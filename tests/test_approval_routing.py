"""Unit nodes for scripts/roadmap/approval_routing.py over a temp git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.roadmap import approval_routing as ar

MAP = {
    "contract": {"id": "approval-routing"},
    "amendment_log": [{"date": "2026-01-01", "summary": "created"}],
    "lane": {"rung": "sampled", "sampled_since": "2026-01-01", "kind_floor": 300},
    "gated": {
        "files": ["docs/DECISIONS.md", "**/CLAUDE.md"],
        "yaml_keys": {"docs/contracts/c.yaml": ["always_ask"]},
        "md_sections": {"docs/s.md": ["## Gate", "12p."]},
    },
    "notice": {"surfaces": ["docs/", ".claude/", "config/"], "wording_suffixes": [".md", ".yaml", ".yml", ".txt"]},
    "demotions": {"rows": [], "restorations": []},
}
MAP_TEXT = "# header\n" + yaml.safe_dump(MAP, sort_keys=False)


def _run(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _commit(root: Path, files: dict[str, str | None], message: str = "change", body: str = "") -> str:
    for rel, text in files.items():
        path = root / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _run(root, "add", "-A")
    _run(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message, "-m", body or "x")
    return _run(root, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("repo")
    _run(root, "init", "-q", "-b", "main")
    return root


def test_map_loading(repo: Path) -> None:
    assert ar.load_map(repo, None) is None
    assert ar.parse_map(":\n - [") is None
    assert ar.parse_map("a: 1") is None
    assert ar.parse_map(None) is None
    sha = _commit(repo, {ar.MAP_REL: MAP_TEXT})
    m = ar.load_map(repo, sha)
    assert m and ar.active(m) and ar.kind_floor(m) == 300
    assert ar.active({**m, "lane": {"rung": "read_all"}}) is False
    assert ar.kind_floor({"lane": {"kind_floor": True}}) is None
    assert ar.kind_floor(None) is None
    assert ar.load_map(repo, sha + "x") is None


def _gated(repo: Path, base: str, head: str, files: list[str], m: dict | None = None) -> dict[str, str]:
    return dict(ar.gated_changes(m or MAP, repo, base, head, files))


def test_selectors(repo: Path) -> None:
    base = _commit(
        repo,
        {
            "docs/DECISIONS.md": "a",
            "CLAUDE.md": "a",
            "x/y/CLAUDE.md": "a",
            "docs/contracts/c.yaml": "# c\nalways_ask: [1]\nother: 1\n",
            "docs/s.md": "# T\n## Gate\nold\n## Other\nkeep\n12o. a\n12p. old\n  more\n12q. z\n",
        },
    )
    head = _commit(
        repo,
        {
            "docs/DECISIONS.md": "b",
            "CLAUDE.md": "b",
            "x/y/CLAUDE.md": "b",
            "docs/contracts/c.yaml": "# c\nalways_ask: [2]\nother: 1\n",
            "docs/s.md": "# T\n## Gate\nnew\n## Other\nkeep\n12o. a\n12p. new\n  more\n12q. z\n",
        },
    )
    files = ["docs/DECISIONS.md", "CLAUDE.md", "x/y/CLAUDE.md", "docs/contracts/c.yaml", "docs/s.md"]
    got = _gated(repo, base, head, files)
    assert set(got) == set(files)
    assert "gated.files" in got["CLAUDE.md"] and "gated.files" in got["x/y/CLAUDE.md"]
    notice_head = _commit(
        repo,
        {
            "docs/contracts/c.yaml": "# edited comment\nalways_ask: [2]\nother: 2\n",
            "docs/s.md": "# T\n## Gate\nnew\n## Other\nchanged\n12o. changed\n12p. new\n  more\n12q. z\n",
        },
    )
    assert _gated(repo, head, notice_head, ["docs/contracts/c.yaml", "docs/s.md"]) == {}
    only_item = _commit(repo, {"docs/s.md": "# T\n## Gate\nnew\n## Other\nchanged\n12o. changed\n12p. NEW\n  more\n12q. z\n"})
    assert list(_gated(repo, notice_head, only_item, ["docs/s.md"])) == ["docs/s.md"]
    added_key = _commit(repo, {"docs/contracts/c.yaml": "always_ask: [2]\nother: 2\nx: 1\n"})
    assert _gated(repo, notice_head, added_key, ["docs/contracts/c.yaml"]) == {}
    dropped = _commit(repo, {"docs/contracts/c.yaml": "other: 2\n"})
    assert list(_gated(repo, added_key, dropped, ["docs/contracts/c.yaml"])) == ["docs/contracts/c.yaml"]
    broken = _commit(repo, {"docs/contracts/c.yaml": "always_ask: [\n"})
    assert list(_gated(repo, dropped, broken, ["docs/contracts/c.yaml"])) == ["docs/contracts/c.yaml"]
    missing = _commit(repo, {"docs/s.md": "# T\n## Other\nonly\n"})
    assert list(_gated(repo, only_item, missing, ["docs/s.md"])) == ["docs/s.md"]
    assert _gated(repo, base, head, ["scripts/x.py", "docs/note.md"]) == {}


def test_demotions_and_malformed_rows(repo: Path) -> None:
    sha = _commit(repo, {"a.py": "1"})
    m = {**MAP, "demotions": {"rows": ["a.py", {"file": "b.md"}], "restorations": []}}
    assert set(_gated(repo, sha, sha, ["a.py", "b.md", "c.md"], m)) == {"a.py", "b.md"}
    bad = {**MAP, "demotions": {"rows": [5, {"x": 1}], "restorations": [7]}}
    reasons = _gated(repo, sha, sha, [], bad)
    assert list(reasons) == [ar.MAP_REL] and len(ar.gated_changes(bad, repo, sha, sha, [])) == 3
    assert len(ar.gated_changes({**MAP, "demotions": {"rows": "x"}}, repo, sha, sha, [])) == 1


def test_exempt(repo: Path) -> None:
    base = MAP_TEXT
    rows = {**MAP, "demotions": {"rows": ["x.py"], "restorations": []}}
    log = rows["amendment_log"] + [{"date": "2026-02-01", "summary": "demote"}]
    full = "# header\n" + yaml.safe_dump({**rows, "amendment_log": log}, sort_keys=False)
    assert ar.exempt(MAP, ar.MAP_REL, base, full)
    log_only = "# header\n" + yaml.safe_dump({**MAP, "amendment_log": log}, sort_keys=False)
    assert not ar.exempt(MAP, ar.MAP_REL, base, log_only)
    edited = "# header\n" + yaml.safe_dump(
        {**rows, "amendment_log": [{"date": "2026-01-02", "summary": "x"}] + log[1:]}, sort_keys=False
    )
    assert not ar.exempt(MAP, ar.MAP_REL, base, edited)
    assert not ar.exempt(MAP, ar.MAP_REL, base, "# edited\n" + full.split("\n", 1)[1])
    assert not ar.exempt(MAP, ar.MAP_REL, base, "# header\n# new\n" + full.split("\n", 1)[1])
    floor = {**rows, "amendment_log": log, "lane": {**MAP["lane"], "kind_floor": 1}}
    assert not ar.exempt(MAP, ar.MAP_REL, base, "# header\n" + yaml.safe_dump(floor, sort_keys=False))
    numbered = {**MAP, "demotions": {"rows": [7], "restorations": []}, "amendment_log": log}
    assert not ar.exempt(MAP, ar.MAP_REL, base, "# header\n" + yaml.safe_dump(numbered, sort_keys=False))
    assert not ar.exempt(MAP, ar.MAP_REL, None, full)
    assert not ar.exempt(MAP, "docs/x.md", "a: 1", "a: 1\nb: 2")


def test_exempt_boundary_patterns() -> None:
    cap = "config/agent/executor/capabilities.yaml"
    base = "boundary_patterns:\n  - a\n  - b\n# maturity ceiling\nother: 1\n"
    assert ar.exempt(MAP, cap, base, "boundary_patterns:\n  - a\n  - b\n  - c\n# maturity ceiling\nother: 1\n")
    group = "boundary_patterns:\n  - a\n  - b\n  # group note\n  - c\n# maturity ceiling\nother: 1\n"
    assert ar.exempt(MAP, cap, base, group)
    assert not ar.exempt(MAP, cap, base, "boundary_patterns:\n  - a\n# maturity ceiling\nother: 1\n")
    assert not ar.exempt(MAP, cap, base, "boundary_patterns:\n  - b\n  - a\n# maturity ceiling\nother: 1\n")
    assert not ar.exempt(MAP, cap, base, "boundary_patterns:\n  - a\n  - c\n# maturity ceiling\nother: 1\n")
    real = (Path(__file__).resolve().parents[1] / cap).read_text(encoding="utf-8")
    assert "boundary_patterns" in real
    start = real.index("boundary_patterns:")
    end = real.index("\n", real.index("\n  - ", start))
    appended = real[:end] + "\n  # group\n  - zz_new_entry" + real[end:]
    assert ar.exempt(MAP, cap, real, appended) is _boundary_appendable(real, appended)


def _boundary_appendable(real: str, appended: str) -> bool:
    before, after = yaml.safe_load(real)["boundary_patterns"], yaml.safe_load(appended)["boundary_patterns"]
    return len(after) == len(before) + 1 and after[: len(before)] == before


def test_notice_changes(repo: Path) -> None:
    sha = _commit(repo, {"a": "1"})
    changed = [
        "scripts/x.py",
        "tests/t.py",
        ".github/workflows/w.yml",
        ".claude/hooks/x.py",
        ".claude/hooks/x.sh",
        ".claude/settings.json",
        ".claude/skills/x/SKILL.md",
        "config/y.yaml",
        "docs/DECISIONS.md",
        "docs/s.md",
    ]
    got = ar.notice_changes(MAP, repo, sha, sha, changed)
    assert got == [".claude/skills/x/SKILL.md", "config/y.yaml"] or set(got) >= {".claude/skills/x/SKILL.md", "config/y.yaml"}
    assert "docs/DECISIONS.md" not in got and "scripts/x.py" not in got and ".claude/settings.json" not in got
    assert not {"tests/t.py", ".github/workflows/w.yml", ".claude/hooks/x.py", ".claude/hooks/x.sh"} & set(got)


def test_reverted_files(tmp_path: Path) -> None:
    root = tmp_path
    _run(root, "init", "-q", "-b", "main")
    old = {"lane": {**MAP["lane"], "sampled_since": "2000-01-01"}}
    m = {**MAP, **old, "demotions": {"rows": [], "restorations": ["docs/restored.md"]}}
    _commit(root, {"docs/a.md": "1", "code.py": "1", "docs/DECISIONS.md": "1", "docs/restored.md": "1", "docs/r.md": "1"})
    _commit(root, {"docs/a.md": "2"}, "revert: docs a")
    _commit(root, {"code.py": "2"}, "fix", "This reverts commit abc123.")
    _commit(root, {"docs/DECISIONS.md": "2"}, "Revert gated")
    _commit(root, {"docs/restored.md": "2"}, "revert restored")
    _commit(root, {"docs/r.md": "2"}, "unrelated")
    files, reason = ar.reverted_files(m, root, "HEAD")
    assert reason is None and files == ["code.py", "docs/a.md"]
    assert ar.reverted_files({**m, "lane": {**m["lane"], "sampled_since": "2090-01-01"}}, root, "HEAD") == ([], None)
    files, reason = ar.reverted_files(m, root, "nope")
    assert files == [] and reason
    assert ar.reverted_files({**m, "lane": {"sampled_since": "soon"}}, root, "HEAD")[1]
    pairs = ar.gated_changes(m, root, "HEAD", "HEAD", ["code.py", "docs/a.md", "docs/r.md"])
    assert {f for f, _ in pairs} == {
        "code.py",
        "docs/a.md",
    }


def test_bound_files_and_declared_plan(repo: Path) -> None:
    plan = "docs/plans/PLAN-x.yaml"
    side = "docs/plans/approved/x.yaml"
    before = _commit(repo, {plan: "implementation_declared: false\n", side: yaml.safe_dump({"blocks": [{"file": "a.md"}, 5]})})
    flip = _commit(repo, {plan: "implementation_declared: true\n"})
    other = _commit(repo, {plan: "implementation_declared: true\nnote: 1\n"})
    assert ar.bound_files(repo, flip, "x") == {"a.md"}
    assert ar.bound_files(repo, flip, "absent") == set()
    assert ar.declared_plan(repo, flip) == "x"
    assert ar.declared_plan(repo, other) is None
    assert ar.declared_plan(repo, before) is None


def test_capabilities_lists_the_new_boundary_patterns() -> None:
    root = Path(__file__).resolve().parents[1]
    patterns = yaml.safe_load((root / "config/agent/executor/capabilities.yaml").read_text(encoding="utf-8"))[
        "boundary_patterns"
    ]
    for want in ("docs/contracts/approval-routing.yaml", "approval_routing", "notice_digest"):
        assert want in patterns


def test_total_on_malformed_map_shapes_and_git_failures(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = _commit(repo, {"docs/q.md": "a"})
    bad_sections = {**MAP, "gated": {**MAP["gated"], "md_sections": {"docs/q.md": [5]}}}
    assert list(_gated(repo, sha, sha, ["docs/q.md"], bad_sections)) == ["docs/q.md"]
    base_text = "demotions: nope\nx: 1\n"
    assert not ar.exempt(MAP, ar.MAP_REL, base_text, base_text + "y: 2\n")
    monkeypatch.setattr(
        ar, "_git", lambda root, *args: "h\x1fsubject\x1fThis reverts commit x\x1e" if args[0] == "log" else None
    )
    assert ar.reverted_files({**MAP, "lane": {**MAP["lane"], "sampled_since": "2000-01-01"}}, repo, "HEAD") == (
        [],
        "git diff-tree failed at h",
    )
