"""scripts/ci/notice_digest.py and .github/workflows/notice-digest.yml (PLAN-wording-approval-routing)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.ci import notice_digest as nd
from scripts.roadmap import approval_routing as ar

REPO = Path(__file__).resolve().parents[2]
ZERO = "0" * 40
MAP = {
    "lane": {"rung": "sampled", "sampled_since": "2090-01-01", "kind_floor": 300},
    "gated": {"files": ["docs/DECISIONS.md"], "yaml_keys": {}, "md_sections": {}},
    "notice": {"surfaces": ["docs/", "config/"], "wording_suffixes": [".md", ".yaml"]},
    "demotions": {"rows": [], "restorations": []},
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _commit(root: Path, files: dict[str, str], message: str = "c (#7)") -> str:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(nd, "ROOT", tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {"docs/a.md": "a", "scripts/x.py": "a", "docs/DECISIONS.md": "a"}, "base")
    return tmp_path


def _with_map(root: Path, **lane: object) -> str:
    return _commit(root, {ar.MAP_REL: yaml.safe_dump({**MAP, "lane": {**MAP["lane"], **lane}})}, "map")


def test_inert_without_a_map_and_on_read_all(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _git(repo, "rev-parse", "HEAD")
    _commit(repo, {"docs/a.md": "b"})
    assert nd.main(["--before", first, "--after", "HEAD", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"inert: lane not on sampled at {first}" in out and "no notice-class changes" in out
    before = _with_map(repo, rung="read_all")
    _commit(repo, {"docs/a.md": "c"})
    assert nd.main(["--before", before, "--after", "HEAD", "--dry-run"]) == 0
    assert "inert: lane not on sampled" in capsys.readouterr().out


def test_a_notice_commit_makes_one_row_with_the_plan_intent(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _with_map(repo)
    plan = {"slug": "demo", "intent": "Do the thing. Then more.", "implementation_declared": True}
    side = {
        "schema_version": 1,
        "slug": "demo",
        "blocks": [{"id": "b", "file": "docs/DECISIONS.md", "before": "a", "after": "b"}],
    }
    before = _commit(repo, {"docs/plans/approved/demo.yaml": yaml.safe_dump(side)}, "sidecar (#6)")
    sha = _commit(
        repo,
        {
            "docs/plans/PLAN-demo.yaml": yaml.safe_dump(plan),
            "docs/a.md": "b",
            "docs/DECISIONS.md": "b",
            "docs/other.md": "x",
            "scripts/x.py": "b",
        },
        "wording (#9)",
    )
    assert nd.main(["--before", before, "--after", sha, "--dry-run"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("PR #")]
    assert len(lines) == 1
    row = lines[0]
    assert row.startswith("PR #9 wording (#9) | plan demo: Do the thing. | notice: ")
    assert (
        "docs/a.md (notice)" in row
        and "docs/other.md (notice)" in row
        and "docs/DECISIONS.md" not in row.split("notice:")[1].split("|")[0]
    )
    assert "tripwires: none" in row and row.endswith(f"sample: {'yes' if int(sha, 16) % 5 == 0 else 'no'}")


def test_code_only_and_gated_only_commits_make_no_row(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _with_map(repo)
    _commit(repo, {"scripts/x.py": "b"})
    sha = _commit(repo, {"docs/DECISIONS.md": "b"})
    assert nd.main(["--before", before, "--after", sha, "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "no notice-class changes"


def test_tripwires_for_an_unbound_gated_file_and_a_demoted_file(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    demoted = {**MAP, "demotions": {"rows": ["docs/d.md"], "restorations": []}}
    before = _commit(repo, {ar.MAP_REL: yaml.safe_dump(demoted), "docs/d.md": "a"}, "map")
    sha = _commit(repo, {"docs/a.md": "b", "docs/DECISIONS.md": "b", "docs/d.md": "b"}, "mixed (#3)")
    assert nd.main(["--before", before, "--after", sha, "--dry-run"]) == 0
    assert "tripwires: docs/DECISIONS.md, docs/d.md | " in capsys.readouterr().out


def test_sample_flag_follows_the_sha_modulo_five(repo: Path) -> None:
    before = _with_map(repo)
    seen: set[str] = set()
    for i in range(40):
        sha = _commit(repo, {"docs/a.md": str(i)})
        row = nd.compose_row(sha, f"{sha}^", ar.load_map(repo, before))
        assert row is not None
        flag = row.rsplit("sample: ", 1)[1]
        assert flag == ("yes" if int(sha, 16) % 5 == 0 else "no")
        seen.add(flag)
        if seen == {"yes", "no"}:
            break
    assert seen == {"yes", "no"}


def test_map_file_overrides_the_parent_map_and_an_all_zero_before_reads_only_after(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    sha = _commit(repo, {"docs/a.md": "b"})
    mapfile = tmp_path_factory.mktemp("m") / "map.yaml"
    mapfile.write_text(yaml.safe_dump(MAP), encoding="utf-8")
    assert nd.main(["--before", ZERO, "--after", sha, "--map-file", str(mapfile), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("PR #7") == 1 and "inert" not in out


def _gh_recorder(monkeypatch: pytest.MonkeyPatch, replies: list[SimpleNamespace]) -> list[list[str]]:
    calls: list[list[str]] = []
    bodies: list[str] = []

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if cmd[0] != "gh":
            return real_run(cmd, **kwargs)
        calls.append(list(cmd))
        if "--body-file" in cmd:
            bodies.append(Path(cmd[cmd.index("--body-file") + 1]).read_text(encoding="utf-8"))
        return replies.pop(0) if replies else SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(nd.subprocess, "run", fake_run)
    real_popen = subprocess.Popen

    def guarded_popen(cmd, *args, **kwargs):  # noqa: ANN001
        assert cmd[0] != "gh", "gh goes through subprocess.run only"
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(nd.subprocess, "Popen", guarded_popen)
    calls.append(bodies)  # type: ignore[arg-type]
    return calls


def _ok(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_posting_uses_the_lowest_labelled_issue_and_a_body_file(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _gh_recorder(monkeypatch, [_ok(json.dumps([{"number": 9}, {"number": 4}]))])
    nd.post(["row one", "row two"])
    cmds = calls[1:]
    assert cmds[0][:4] == ["gh", "issue", "list", "--label"]
    assert [c[:4] for c in cmds[1:]] == [["gh", "issue", "comment", "4"]] * 2
    assert calls[0] == ["row one", "row two"]


def test_a_missing_issue_creates_the_label_then_the_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    exists = SimpleNamespace(returncode=1, stdout="", stderr="label already exists")
    calls = _gh_recorder(monkeypatch, [_ok("[]"), exists, _ok(), _ok(json.dumps([{"number": 12}])), _ok()])
    nd.post(["only"])
    kinds = [tuple(c[1:3]) for c in calls[1:]]
    assert kinds == [("issue", "list"), ("label", "create"), ("issue", "create"), ("issue", "list"), ("issue", "comment")]
    assert calls[-1][3] == "12"


def test_gh_failures_are_loud(monkeypatch: pytest.MonkeyPatch, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _gh_recorder(monkeypatch, [_ok("[]"), SimpleNamespace(returncode=1, stdout="", stderr="denied")])
    with pytest.raises(RuntimeError, match="label create failed"):
        nd.post(["x"])
    _gh_recorder(monkeypatch, [_ok("[]"), _ok(), _ok(), _ok("[]")])
    with pytest.raises(RuntimeError, match="not found after creating"):
        nd.post(["x"])
    _gh_recorder(monkeypatch, [SimpleNamespace(returncode=1, stdout="", stderr="boom")])
    before = _with_map(repo)
    sha = _commit(repo, {"docs/a.md": "b"})
    assert nd.main(["--before", before, "--after", sha]) == 1
    assert "ERROR: gh issue list failed: boom" in capsys.readouterr().err


def test_dry_run_calls_no_gh_and_a_long_body_is_cut(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    calls = _gh_recorder(monkeypatch, [])
    before = _with_map(repo)
    sha = _commit(repo, {"docs/a.md": "b"})
    assert nd.main(["--before", before, "--after", sha, "--dry-run"]) == 0
    assert calls == [[]]
    cut = nd._fit("x" * 70000 + " (notice)")
    assert len(cut) <= nd.LIMIT and cut.endswith("more files: see the PR")
    assert nd._fit("short") == "short"


def _workflow() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/notice-digest.yml").read_text(encoding="utf-8"))


def test_workflow_shape() -> None:
    text = (REPO / ".github/workflows/notice-digest.yml").read_text(encoding="utf-8")
    wf = _workflow()
    assert wf[True] == {"push": {"branches": ["main"]}}
    assert wf["permissions"] == {"contents": "read", "issues": "write"}
    assert "concurrency" not in wf and "workflow_dispatch" not in text
    job = wf["jobs"]["digest"]
    assert "concurrency" not in job and set(wf["jobs"]) == {"digest"}
    steps = job["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    assert steps[1]["id"] == "map" and "if" not in steps[1]
    names = [s.get("uses", "").split("@")[0] for s in steps]
    assert names.index("actions/setup-python") > 1
    assert all(s["if"] == "steps.map.outputs.present == 'true'" for s in steps[2:])
    assert any(s.get("run") == "python -m pip install pyyaml -c requirements.txt" for s in steps)
    assert not re.search(r"run:.*\$\{\{", text) and "secrets." not in text and "id-token" not in text
    for step in steps:
        assert "${{" not in step.get("run", "")
    digest = steps[-1]
    assert digest["env"]["BEFORE"] == "${{ github.event.before }}" and digest["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_ci_rca_watches_notice_digest() -> None:
    rca = yaml.safe_load((REPO / ".github/workflows/ci-rca.yml").read_text(encoding="utf-8"))
    assert "notice-digest" in rca[True]["workflow_run"]["workflows"]
