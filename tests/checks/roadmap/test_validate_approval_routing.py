"""validate_approval_routing over a temp git repo (PLAN-wording-approval-routing)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.roadmap import validate_approval_routing as check
from scripts.roadmap import approval_routing as ar

MAP = {
    "contract": {"id": "approval-routing"},
    "amendment_log": [{"date": "2026-01-01", "summary": "created"}],
    "lane": {"rung": "sampled", "sampled_since": "2090-01-01", "kind_floor": 300},
    "gated": {"files": ["docs/DECISIONS.md", "docs/contracts/approval-routing.yaml"], "yaml_keys": {}, "md_sections": {}},
    "notice": {"surfaces": ["docs/", ".claude/"], "wording_suffixes": [".md", ".yaml"]},
    "demotions": {"rows": [], "restorations": []},
}
PR = "pull_request"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _commit(root: Path, files: dict[str, str], message: str = "c") -> str:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _map_text(**lane: object) -> str:
    return yaml.safe_dump({**MAP, "lane": {**MAP["lane"], **lane}}, sort_keys=False)


def _decision(number: int, kind: str | None) -> str:
    tail = f"kind:\n  value: {kind}\n  justification: j.\n" if kind else ""
    return f"## Decision {number}: T{number}\n\n```yaml\nnumber: {number}\n{tail}```\n\nbody\n"


def _sidecar(slug: str, blocks: list[tuple[str, str]]) -> str:
    items = [{"id": f"b{i}", "file": f, "before": "a", "after": b} for i, (f, b) in enumerate(blocks)]
    return yaml.safe_dump({"schema_version": 1, "slug": slug, "blocks": items})


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(
        tmp_path,
        {
            ar.MAP_REL: _map_text(),
            "docs/DECISIONS.md": "# Decisions\n\n" + _decision(5, "direction") + "\n" + _decision(6, "mechanism"),
            "docs/note.md": "a",
            "scripts/x.py": "a",
            "docs/plans/approved/bind.yaml": _sidecar("bind", [("docs/DECISIONS.md", "b")]),
            "docs/plans/approved/other.yaml": _sidecar("other", [("docs/other.md", "b")]),
        },
    )
    return tmp_path, base


def _run(root: Path, base: str, changed: list[str], event: str | None = PR) -> tuple[list[str], str]:
    failed: list[str] = []
    check.validate_approval_routing(failed, root=root, base=base, changed=changed, event=event)
    return failed, ""


def _declare(root: Path, slug: str, extra: dict | None = None) -> str:
    rel = f"docs/plans/PLAN-{slug}.yaml"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(yaml.safe_dump({"slug": slug, "implementation_declared": True, **(extra or {})}), encoding="utf-8")
    return rel


def test_unbound_gated_change_fails_in_pr_and_advises_elsewhere(repo, capsys) -> None:
    root, base = repo
    (root / "docs/DECISIONS.md").write_text("changed", encoding="utf-8")
    failed, _ = _run(root, base, ["docs/DECISIONS.md"])
    assert failed == ["Approval routing"] and "no declared plan's base sidecar" in capsys.readouterr().out
    failed, _ = _run(root, base, ["docs/DECISIONS.md"], event=None)
    assert failed == [] and "ADVISORY: docs/DECISIONS.md" in capsys.readouterr().out


def test_declared_plan_sidecar_binding_the_file_passes_and_another_file_fails(repo) -> None:
    root, base = repo
    (root / "docs/DECISIONS.md").write_text("changed", encoding="utf-8")
    assert _run(root, base, ["docs/DECISIONS.md", _declare(root, "bind")])[0] == []
    assert _run(root, base, ["docs/DECISIONS.md", _declare(root, "other")])[0] == ["Approval routing"]
    assert _run(root, base, ["docs/DECISIONS.md", _declare(root, "nosidecar")])[0] == ["Approval routing"]


def test_notice_and_code_changes_pass_and_examine_the_changed_files(repo, capsys) -> None:
    root, base = repo
    (root / "docs/note.md").write_text("b", encoding="utf-8")
    (root / "scripts/x.py").write_text("b", encoding="utf-8")
    assert _run(root, base, ["docs/note.md", "scripts/x.py"])[0] == []
    assert "PASS" in capsys.readouterr().out
    assert _run(root, base, ["scripts/x.py"])[0] == []


def test_inert_without_an_active_map_at_the_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    skips: list[str] = []
    monkeypatch.setattr(registry, "skipped", skips.append)
    _git(tmp_path, "init", "-q", "-b", "main")
    none = _commit(tmp_path, {"docs/DECISIONS.md": "a"})
    (tmp_path / ar.MAP_REL).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ar.MAP_REL).write_text(_map_text(), encoding="utf-8")
    (tmp_path / "docs/DECISIONS.md").write_text("b", encoding="utf-8")
    assert _run(tmp_path, none, ["docs/DECISIONS.md", ar.MAP_REL])[0] == []
    assert f"inert: lane not on sampled at {none}" in capsys.readouterr().out and len(skips) == 1
    read_all = _commit(tmp_path, {ar.MAP_REL: _map_text(rung="read_all")})
    assert _run(tmp_path, read_all, ["docs/DECISIONS.md"])[0] == [] and len(skips) == 2
    assert skips[1].startswith("approval-routing map absent or lane not on sampled")


def test_unparseable_map_at_the_base_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {ar.MAP_REL: "lane: [unterminated\n"})
    assert _run(tmp_path, base, [])[0] == ["Approval routing"]
    assert _run(tmp_path, base, [], event=None)[0] == []


def test_push_context_is_skipped(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    root, base = repo
    skips: list[str] = []
    monkeypatch.setattr(registry, "skipped", skips.append)
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: "abc")
    (root / "docs/DECISIONS.md").write_text("changed", encoding="utf-8")
    assert _run(root, base, ["docs/DECISIONS.md"])[0] == [] and skips == ["push context"]


def test_deleted_gated_file_and_deleted_map_fail(repo) -> None:
    root, base = repo
    (root / "docs/DECISIONS.md").unlink()
    assert _run(root, base, ["docs/DECISIONS.md"])[0] == ["Approval routing"]
    (root / ar.MAP_REL).unlink()
    assert _run(root, base, [ar.MAP_REL])[0] == ["Approval routing"]


def test_demoted_and_reverted_files_fail_without_a_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "push_context_base", lambda root=None: None)
    _git(tmp_path, "init", "-q", "-b", "main")
    demo = yaml.safe_load(_map_text(sampled_since="2000-01-01"))
    demo["demotions"]["rows"] = ["docs/demoted.md"]
    _commit(tmp_path, {"docs/demoted.md": "a", "docs/w.md": "a", "scripts/c.py": "a"})
    _commit(tmp_path, {"docs/w.md": "b"}, "revert: w")
    _commit(tmp_path, {"scripts/c.py": "b"}, "Revert c")
    base = _commit(tmp_path, {ar.MAP_REL: yaml.safe_dump(demo)})
    for rel in ("docs/demoted.md", "docs/w.md", "scripts/c.py"):
        (tmp_path / rel).write_text("new", encoding="utf-8")
        assert _run(tmp_path, base, [rel])[0] == ["Approval routing"], rel
    assert _run(tmp_path, base, ["docs/other.md"])[0] == []


def test_a_revert_detector_failure_fails_closed(repo, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    root, base = repo
    monkeypatch.setattr(ar, "reverted_files", lambda m, r, b: ([], "git log failed"))
    assert _run(root, base, ["docs/note.md"])[0] == ["Approval routing"]
    assert "revert detector failed: git log failed" in capsys.readouterr().out


def test_demotion_append_with_its_amendment_row_passes_and_a_malformed_row_fails(repo) -> None:
    root, base = repo
    data = yaml.safe_load(_map_text())
    data["demotions"]["rows"] = ["docs/x.md"]
    data["amendment_log"].append({"date": "2026-02-01", "summary": "demote"})
    (root / ar.MAP_REL).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert _run(root, base, [ar.MAP_REL])[0] == []
    data["demotions"]["rows"] = [7]
    (root / ar.MAP_REL).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert _run(root, base, [ar.MAP_REL])[0] == ["Approval routing"]


def _leg_b(root: Path, base: str, drafts: list[tuple[int | None, str]], rounds: list[str], amends: list[int]) -> list[str]:
    text = ""
    for i, (_, kind) in enumerate(drafts):
        env = f"number: {{{{N}}}}\namends: {amends}\nkind:\n  value: {kind}\n  justification: j.\n"
        text += f"## Decision {{{{N}}}}: D{i}\n\n```yaml\n{env}```\n\nbody\n\n"
    side = _sidecar("lb", [("docs/DECISIONS.md", text)])
    (root / "docs/plans/approved/lb.yaml").write_text(side, encoding="utf-8")
    plan = {"slug": "lb", "gate_rounds": [{"gate": "kind-blind", "round": i, "verdict": v} for i, v in enumerate(rounds, 1)]}
    (root / "docs/plans/PLAN-lb.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
    failed, _ = _run(root, base, ["docs/plans/approved/lb.yaml", "docs/plans/PLAN-lb.yaml"])
    return failed


def test_leg_b_mechanism_drafts(repo) -> None:
    root, base = repo
    assert _leg_b(root, base, [(None, "mechanism")], [], []) == ["Approval routing"]
    assert _leg_b(root, base, [(None, "mechanism")], ["direction"], []) == ["Approval routing"]
    assert _leg_b(root, base, [(None, "mechanism")], ["mechanism"], [5]) == ["Approval routing"]
    assert _leg_b(root, base, [(None, "mechanism")], ["mechanism"], [99]) == ["Approval routing"]
    assert _leg_b(root, base, [(None, "mechanism"), (None, "direction")], ["mechanism"], []) == ["Approval routing"]
    assert _leg_b(root, base, [(None, "mechanism")], ["direction", "mechanism"], [6]) == []
    assert _leg_b(root, base, [(None, "direction")], [], [5]) == []


def test_a_build_pr_with_an_unchanged_presidecar_is_not_rejudged(repo) -> None:
    root, base = repo
    text = "## Decision {{N}}: D\n\n```yaml\nnumber: {{N}}\nkind:\n  value: mechanism\n  justification: j.\n```\n\nbody\n"
    side = _sidecar("old", [("docs/DECISIONS.md", text)])
    base = _commit(root, {"docs/plans/approved/old.yaml": side, "docs/plans/PLAN-old.yaml": "slug: old\n"})
    assert _run(root, base, ["docs/plans/approved/old.yaml", "docs/plans/PLAN-old.yaml"])[0] == []


def test_an_unreadable_base_sidecar_binds_nothing_and_an_unreadable_plan_drafts_nothing(repo) -> None:
    root, base = repo
    base = _commit(root, {"docs/plans/approved/bad.yaml": "blocks: []\n"})
    (root / "docs/DECISIONS.md").write_text("changed", encoding="utf-8")
    assert _run(root, base, ["docs/DECISIONS.md", _declare(root, "bad")])[0] == ["Approval routing"]
    (root / "docs/plans/approved/lb.yaml").write_text(_sidecar("lb", [("docs/x.md", "b")]), encoding="utf-8")
    (root / "docs/plans/PLAN-lb.yaml").write_text("- a\nb: [", encoding="utf-8")
    assert _run(root, base, ["docs/plans/approved/lb.yaml"])[0] == []
