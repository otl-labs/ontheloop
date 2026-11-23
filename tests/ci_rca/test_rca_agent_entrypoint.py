"""rec-5500: ci-rca.yml's rca_agent and mark_rca steps call scripts/ci/ci_rca_steps.sh; the script keeps their behaviour."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import yaml

from scripts.ci_rca.convergence_dedup import _FILED_REC_RE

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "ci_rca_steps.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci-rca.yml"
FAKE_GH = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$GH_LOG"\n'


def _step(step_id: str) -> dict[str, Any]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["rca"]["steps"]
    return next(s for s in steps if s.get("id") == step_id)


def _effective(run: str) -> list[str]:
    return [ln.strip() for ln in run.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _run(args: list[str], tmp: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "GITHUB_OUTPUT": str(tmp / "gh_output"), "GH_LOG": str(tmp / "gh.log"), **(env or {})}
    (tmp / "bin").mkdir(exist_ok=True)
    fake = tmp / "bin" / "gh"
    fake.write_text(FAKE_GH, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    full["PATH"] = f"{tmp / 'bin'}:{full['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=ROOT, env=full, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def _agent_output(path: Path, result: str) -> Path:
    path.write_text(json.dumps({"type": "result", "result": result}), encoding="utf-8")
    return path


def test_rca_agent_step_calls_extracted_entrypoint() -> None:
    step = _step("rca_agent")
    lines = _effective(step["run"])
    assert lines[0] == "bash scripts/ci/ci_rca_steps.sh prompt > /tmp/ci-rca-prompt.txt"
    assert lines[-1] == "bash scripts/ci/ci_rca_steps.sh check"
    middle = lines[1:-1]
    assert "claude_p_retry.sh" in middle[0] and middle[-1].endswith("< /tmp/ci-rca-prompt.txt || true")
    body = step["run"]
    for banned in ("PROMPT=", "EVIDENCE_CONTEXT", "scripts.ci_rca.filing"):
        assert banned not in body
    assert "${{" not in body
    for key in ("RUN_ID", "BUNDLE_SHA", "BUNDLE_S3_URI", "BUNDLE_PATH", "BUNDLE_LOCAL", "BUNDLE_ABSENT", "RANGE_NOTE"):
        assert key in step["env"], key
    mark = _step("mark_rca")
    assert _effective(mark["run"]) == ["bash scripts/ci/ci_rca_steps.sh mark"]
    assert "ANCHOR_SHA" in mark["env"] and "WORKFLOW_SLUG" in mark["env"] and "${{" not in mark["run"]


def test_ci_rca_steps_script_is_in_the_executor_boundary() -> None:
    caps = yaml.safe_load((ROOT / "config/agent/executor/capabilities.yaml").read_text(encoding="utf-8"))
    patterns = caps["boundary_patterns"]
    assert "scripts/ci/ci_rca_steps.sh" in patterns
    assert patterns[-1] == "scripts/ci/ci_rca_steps.sh"


def test_unknown_subcommand_exits_2(tmp_path: Path) -> None:
    r = _run(["nope"], tmp_path)
    assert r.returncode == 2 and "usage" in r.stderr


def test_prompt_lists_two_bundles(tmp_path: Path) -> None:
    bundles = tmp_path / "b"
    bundles.mkdir()
    (bundles / "a.json").write_text("{}", encoding="utf-8")
    (bundles / "b.json").write_text("{}", encoding="utf-8")
    env = {
        "RUN_ID": "77", "BUNDLE_SHA": "sha1", "BUNDLE_S3_URI": "s3://x/y", "BUNDLE_PATH": "p/q", "BUNDLE_LOCAL": "/l",
        "BUNDLE_ABSENT": "", "RANGE_NOTE": " RANGE.",
    }  # fmt: skip
    r = _run(["prompt", str(bundles)], tmp_path, env)
    assert r.returncode == 0
    want = (
        "Apply the .claude/agents/scheduled/ci-rca.md instructions to failed CI run 77. "
        "The failed log is at /tmp/ci-rca-failed.log."
        f" Evidence bundle(s) are at {bundles}/ -- bundle files (one JSON per failed check): "
        f"{bundles}/a.json {bundles}/b.json . Primary bundle SHA: sha1. Primary S3 URI: s3://x/y. "
        "Pending path: p/q. Local bundle path: /l. RANGE."
    )
    assert r.stdout == want


def test_prompt_bundle_absent_forces_human_review(tmp_path: Path) -> None:
    r = _run(["prompt", str(tmp_path / "none")], tmp_path, {"RUN_ID": "5", "BUNDLE_ABSENT": "true", "RANGE_NOTE": ""})
    assert "bundle_absent=true" in r.stdout and "mandatory human review" in r.stdout
    assert r.stdout.endswith("Do NOT guess earliest_viable_gate/escape_mode without a bundle.")


def test_prompt_without_evidence_has_no_trailing_newline(tmp_path: Path) -> None:
    r = _run(["prompt", str(tmp_path / "none")], tmp_path, {"RUN_ID": "5", "BUNDLE_ABSENT": "", "RANGE_NOTE": ""})
    head = "Apply the .claude/agents/scheduled/ci-rca.md instructions to failed CI run 5."
    assert r.stdout == f"{head} The failed log is at /tmp/ci-rca-failed.log."


def test_check_writes_last_filed_rec(tmp_path: Path) -> None:
    out = _agent_output(tmp_path / "o.json", "done\nFILED: rec-1 alpha\nFILED: rec-2 beta")
    r = _run(["check", str(out)], tmp_path)
    assert r.returncode == 0 and r.stdout == out.read_text(encoding="utf-8")
    assert (tmp_path / "gh_output").read_text(encoding="utf-8") == "rec_id=rec-2\n"


def test_check_prefers_filed_rec_over_trailing_none(tmp_path: Path) -> None:
    out = _agent_output(tmp_path / "o.json", "FILED: rec-9 alpha\nFILED: none")
    r = _run(["check", str(out)], tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "gh_output").read_text(encoding="utf-8") == "rec_id=rec-9\n"


def test_check_without_marker_fails_loud(tmp_path: Path) -> None:
    out = _agent_output(tmp_path / "o.json", "nothing filed")
    r = _run(["check", str(out)], tmp_path)
    assert r.returncode == 1
    assert "::error::ci-rca agent filed no recommendation (no FILED: marker in output)" in r.stdout
    assert not (tmp_path / "gh_output").exists()


def test_mark_posts_one_status_per_pair(tmp_path: Path) -> None:
    out = _agent_output(tmp_path / "o.json", "FILED: rec-1 alpha\nFILED: rec-2")
    fb = tmp_path / "fb.txt"
    r = _run(["mark", str(out), str(fb)], tmp_path, {"ANCHOR_SHA": "abc", "WORKFLOW_SLUG": "ci", "GITHUB_REPOSITORY": "o/r"})
    assert r.returncode == 0
    calls = (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    assert all("repos/o/r/statuses/abc --method POST --field state=success" in c for c in calls)
    assert "--field context=ci-rca/ci/alpha" in calls[0] and "--field context=ci-rca/ci/unknown" in calls[1]
    desc = _FILED_REC_RE.search(calls[0])
    assert desc is not None and desc.group(1) == "rec-1"


def test_mark_includes_fallback_pairs(tmp_path: Path) -> None:
    out = _agent_output(tmp_path / "o.json", "FILED: none")
    fb = tmp_path / "fb.txt"
    fb.write_text("rec-7,gamma\n", encoding="utf-8")
    r = _run(["mark", str(out), str(fb)], tmp_path, {"ANCHOR_SHA": "abc", "WORKFLOW_SLUG": "ci", "GITHUB_REPOSITORY": "o/r"})
    assert r.returncode == 0
    calls = (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and "context=ci-rca/ci/gamma" in calls[0] and "rec-7" in calls[0]


def test_mark_without_anchor_makes_no_call(tmp_path: Path) -> None:
    out = _agent_output(tmp_path / "o.json", "FILED: rec-1 alpha")
    r = _run(["mark", str(out), str(tmp_path / "fb.txt")], tmp_path, {"ANCHOR_SHA": "", "WORKFLOW_SLUG": "ci"})
    assert r.returncode == 0 and not (tmp_path / "gh.log").exists()
    assert r.stdout == ""
