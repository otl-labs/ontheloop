"""Mirror test for scripts/telemetry_capture/ci_pass.py: manifest ranking, the gate's off paths, the write-once sidecar,
the budget, counts-only output and the host-fact boundary (Decision 212)."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_capture import ci_pass
from tests.fixtures.telemetry_capture_writer import Writer, factory, records, write
from tests.fixtures.turn_capture_corpus import SID, make_files
from tests.fixtures.turn_capture_v8_cases import sidecar

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = ".github/workflows/ci-rca.yml"
CAPTURE = (
    "enabled: true\nci_repository: example/repo\nci_workflows:\n"
    f"  - {{workflow: {WORKFLOW}, agent: ci_rca, content_policy: full}}\n"
)
PIN_HOOK = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=example/project'
ACTIONS = {"GITHUB_ACTIONS": "true"}
SID2 = "bbbbbbbb-0000-4000-8000-000000000002"
SHA = "a" * 40
LINES = re.compile(
    r"^CI_CAPTURE (off reason=[a-z_]+ sessions=\d+|session=\d+ outcome=[a-z_]+ rows=\d+|done sessions=\d+( [a-z_]+=\d+)+)$"
)


def settings(*commands: str) -> str:
    groups = [{"hooks": [{"type": "command", "command": c} for c in commands]}]
    return json.dumps({"hooks": {"SessionStart": groups}})


def repo(tmp_path: Path, capture: str = CAPTURE, hooks: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "telemetry").mkdir(parents=True, exist_ok=True)
    (root / ".claude").mkdir(exist_ok=True)
    (root / "config" / "telemetry" / "capture.yaml").write_text(capture, encoding="utf-8")
    (root / ".claude" / "settings.json").write_text(hooks or settings(PIN_HOOK), encoding="utf-8")
    return root


def manifest(path: Path, *lines: Any) -> Path:
    path.write_text("".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines), encoding="utf-8")
    return path


def start(sid: str, attempt: int, when: str) -> dict[str, Any]:
    return {"attempt": attempt, "session_id": sid, "started_at": when}


def args(tmp_path: Path, manifests: list[Path], **over: Any) -> argparse.Namespace:
    base = {
        "manifest": [str(m) for m in manifests],
        "workflow_path": WORKFLOW,
        "ref": "refs/heads/main",
        "repository": "example/repo",
        "run_id": "123456789",
        "run_attempt": "1",
        "job": "rca",
        "event_name": "workflow_run",
        "sha": SHA,
        "checkout_sha": None,
        "projects_dir": tmp_path / "projects",
        "budget_s": 180.0,
    }
    return argparse.Namespace(**{**base, **over})


def call(a: argparse.Namespace, root: Path, writer: Writer | None = None, **kw: Any) -> list[str]:
    out: list[str] = []
    assert ci_pass.main_ci_pass(a, ACTIONS, root, factory(writer or Writer()), out.append, **kw) == 0
    assert all(LINES.match(line) for line in out), out
    return out


def transcript(tmp_path: Path, sid: str = SID, slug: str = "-w", pinned: bool = False) -> Path:
    files = make_files(records()[0 if pinned else 1 :])
    renamed = {k.replace(SID, sid): v.replace(SID, sid) for k, v in files.items()}
    if sid != SID:
        for tag in ("u0", "u1", "a0", "m0", "p0", "p1"):
            renamed = {k: v.replace(f'"{tag}"', f'"{sid[:4]}-{tag}"') for k, v in renamed.items()}
    write(tmp_path / "projects" / slug, renamed)
    return tmp_path / "projects" / slug / f"{sid}.jsonl"


def test_manifests_merge_by_session_and_rank_by_started_at_then_manifest_order(tmp_path: Path) -> None:
    sids = [f"{n}{n}{n}{n}{n}{n}{n}{n}-0000-4000-8000-00000000000{n}" for n in range(1, 5)]
    first = manifest(
        tmp_path / "a.sessions.jsonl",
        start(sids[0], 1, "2026-01-01T00:00:02.000Z"),
        {"session_id": sids[0], "exit": 1, "transient": True},
        "not json",
        start(sids[1], 1, "2026-01-01T00:00:01.000Z"),
    )
    second = manifest(
        tmp_path / "b.sessions.jsonl",
        start(sids[2], 1, "2026-01-01T00:00:02.000Z"),
        {"session_id": "bad", "exit": 0},
        start(sids[3], 1, "bad time"),
    )
    diag: Any = __import__("collections").Counter()
    ranked = ci_pass.read_manifests([str(tmp_path / "*.sessions.jsonl")], diag)
    assert [(a.session_id, a.ordinal) for a in ranked] == [(sids[1], 1), (sids[0], 2), (sids[2], 3)]
    assert diag["manifest_malformed"] == 3
    assert ci_pass.read_manifests([str(first), str(second)], __import__("collections").Counter()) == ranked
    assert ci_pass.read_manifests([str(tmp_path / "none*")], diag) == []


def test_off_paths_print_one_fixed_line_and_exit_zero(tmp_path: Path) -> None:
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"), start(SID2, 2, "2026-01-01T00:00:02.000Z"))
    root = repo(tmp_path)
    cases = [
        (root, {"repository": "example/probe"}, "repository_mismatch"),
        (root, {"ref": "refs/heads/work"}, "ref_not_main"),
        (root, {"workflow_path": ".github/workflows/other.yml"}, "not_allow_listed"),
        (root, {"event_name": "pull_request"}, "unsupported_event"),
        (root, {"run_id": "12ab"}, "bad_host_fact"),
        (root, {"sha": "zz"}, "bad_host_fact"),
        (root, {"workflow_path": "ci-rca.yml"}, "bad_host_fact"),
        (repo(tmp_path / "off", "enabled: false\n"), {}, "switch_off"),
        (repo(tmp_path / "nopin", hooks=settings("bash x.sh")), {}, "no_project_ref"),
        (
            repo(tmp_path / "two", hooks=settings(PIN_HOOK, PIN_HOOK.replace("example/project", "example/other"))),
            {},
            "no_project_ref",
        ),
    ]
    for case_root, over, reason in cases:
        assert call(args(tmp_path, [m], **over), case_root) == [f"CI_CAPTURE off reason={reason} sessions=2"]
    assert not (tmp_path / "projects").exists()


def test_missing_and_ambiguous_transcripts_are_skipped_by_token(tmp_path: Path) -> None:
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"), start(SID2, 2, "2026-01-01T00:00:02.000Z"))
    transcript(tmp_path, SID, "-a")
    transcript(tmp_path, SID, "-b")
    out = call(args(tmp_path, [m]), repo(tmp_path))
    assert out[:2] == [
        "CI_CAPTURE session=1 outcome=transcript_ambiguous rows=0",
        "CI_CAPTURE session=2 outcome=transcript_absent rows=0",
    ]
    assert out[2] == "CI_CAPTURE done sessions=2 sent=0 no_rows=0 deferred=0 stopped=0 halted=0 skipped=2"


def test_a_pass_pins_the_session_with_a_write_once_sidecar_and_sends_the_run_pair(tmp_path: Path) -> None:
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"))
    root_file = transcript(tmp_path)
    writer = Writer()
    out = call(args(tmp_path, [m], checkout_sha="c" * 40), repo(tmp_path), writer)
    assert re.fullmatch(r"CI_CAPTURE session=1 outcome=sent rows=\d+", out[0]) and out[1].startswith(
        "CI_CAPTURE done sessions=1 sent=1"
    )
    host = json.loads((root_file.parent / SID / "host-run.json").read_text())
    assert host["project_ref"] == "example/project" and host["agent"] == "ci_rca" and host["content_policy"] == "full"
    assert host["host"] == {
        "kind": "gha",
        "run_id": "123456789",
        "run_attempt": "1",
        "job": "rca",
        "retry_ordinal": 1,
        "event_name": "workflow_run",
        "repository": "example/repo",
        "workflow_path": WORKFLOW,
        "ref": "refs/heads/main",
        "sha": SHA,
        "checkout_sha": "c" * 40,
    }
    rows = [r for p in writer.appends() for b in p["batches"] if b["table"] == "telemetry_agents" for r in b["rows"]]
    assert {r["host_run_ref"] for r in rows} == {"gha:123456789/1/rca/1"} and {r["event_kind"] for r in rows} == {
        "open",
        "close",
    }
    wire = "\n".join(out)
    for secret in (SID, SHA, str(tmp_path), "example"):
        assert secret not in wire
    assert call(args(tmp_path, [m], checkout_sha="c" * 40), repo(tmp_path), writer)[0].startswith(
        "CI_CAPTURE session=1 outcome=no_rows"
    )


def test_a_different_existing_sidecar_is_never_overwritten(tmp_path: Path) -> None:
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"))
    root_file = transcript(tmp_path)
    existing = root_file.parent / SID / "host-run.json"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(sidecar(ordinal=2), encoding="utf-8")
    writer = Writer()
    out = call(args(tmp_path, [m]), repo(tmp_path), writer)
    assert out[0] == "CI_CAPTURE session=1 outcome=pin_sidecar_conflict rows=0" and out[1].endswith("skipped=1")
    assert existing.read_text(encoding="utf-8") == sidecar(ordinal=2) and writer.posts == []


def test_each_session_gets_its_own_ordinal_in_its_sidecar(tmp_path: Path) -> None:
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"), start(SID2, 2, "2026-01-01T00:00:05.000Z"))
    first, second = transcript(tmp_path), transcript(tmp_path, SID2)
    out = call(args(tmp_path, [m]), repo(tmp_path))
    assert out[-1].startswith("CI_CAPTURE done sessions=2 sent=2")
    ordinals = [
        json.loads((p.parent / p.stem / "host-run.json").read_text())["host"]["retry_ordinal"] for p in (first, second)
    ]
    assert ordinals == [1, 2]


def test_an_exhausted_budget_stops_the_remaining_sessions_and_a_failing_pass_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"))
    transcript(tmp_path)
    out = call(args(tmp_path, [m], budget_s=0.0), repo(tmp_path))
    assert out[0] == "CI_CAPTURE session=1 outcome=stopped rows=0" and out[1].endswith("stopped=1 halted=0 skipped=0")

    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(ci_pass, "run_pass", boom)
    assert call(args(tmp_path, [m]), repo(tmp_path))[0] == "CI_CAPTURE session=1 outcome=stopped rows=0"


def test_ci_pass_makes_no_reader_or_source_check_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.telemetry_capture import read_route, verify

    def forbidden(*a: Any, **kw: Any) -> None:
        raise AssertionError("ci-pass must not verify or read")

    monkeypatch.setattr(verify, "run_verify", forbidden)
    monkeypatch.setattr(read_route, "make_routed_reader", forbidden)
    m = manifest(tmp_path / "m.jsonl", start(SID, 1, "2026-01-01T00:00:01.000Z"))
    transcript(tmp_path)
    assert call(args(tmp_path, [m]), repo(tmp_path))[1].startswith("CI_CAPTURE done sessions=1 sent=1")


def _github_env_reads(path: Path) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        literal = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            literal = node.slice.value
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("get", "getenv", "environ"):
                literal = node.args[0].value
        if isinstance(literal, str) and literal.startswith("GITHUB_"):
            hits.append(f"{path.name}:{node.lineno}")
    return hits


def test_no_github_environment_read_outside_the_hook_veto() -> None:
    paths = [REPO / "scripts/telemetry_capture/ci_pass.py", REPO / "scripts/telemetry_capture/runner.py"]
    paths += sorted((REPO / "src/turn_capture").glob("*.py"))
    assert [hit for path in paths for hit in _github_env_reads(path)] == []
    assert len(_github_env_reads(REPO / "scripts/telemetry_capture/switch.py")) == 1


def test_a_quoted_pin_flag_value_resolves_like_the_hook_side_reader(tmp_path: Path) -> None:
    quoted = PIN_HOOK.replace("--project-ref=example/project", '--project-ref="example/project"')
    assert ci_pass.project_ref_home(repo(tmp_path, hooks=settings(quoted))) == "example/project"
