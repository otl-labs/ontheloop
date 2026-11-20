"""Pin, guard and model/effort flags for the two CI `claude -p` agents (PLAN-claude-cli-account-migration)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIN = "2.1.323"
INSTALL_RE = re.compile(r"@anthropic-ai/claude-code@(\S+)")
REVIEW_SH = ROOT / ".github" / "actions" / "subagent-plan-review" / "review.sh"
ACTION_USE = "./.github/actions/subagent-plan-review"


def _install_sites() -> list[tuple[Path, str]]:
    sites: list[tuple[Path, str]] = []
    for path in sorted((ROOT / ".github").rglob("*")):
        if path.is_file() and path.suffix in {".yml", ".yaml", ".sh"}:
            for version in INSTALL_RE.findall(path.read_text(encoding="utf-8")):
                sites.append((path, version))
    return sites


def _workflows() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        out[path.name] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return out


def _step_run(step: dict) -> str:
    return str(step.get("run", ""))


def _reaches_install(workflow: str, job: dict) -> bool:
    for step in job.get("steps", []):
        if step.get("uses") == ACTION_USE:
            return True
        run = _step_run(step)
        if workflow == "ci-rca.yml" and "@anthropic-ai/claude-code@" in run:
            return True
        for script in re.findall(r"scripts/ci/\S+\.sh", run):
            body = (ROOT / script).read_text(encoding="utf-8") if (ROOT / script).is_file() else ""
            if re.search(r"^\s*bash\b[^#\n]*review\.sh", body, re.MULTILINE):
                return True
    return False


def test_every_install_pins_the_current_version() -> None:
    sites = _install_sites()
    names = {p.name for p, _ in sites}
    assert {"ci-rca.yml", "review.sh"} <= names, names
    assert all(v == PIN for _, v in sites), sites


def test_installs_are_guarded_and_run_on_ubuntu_latest() -> None:
    for path in (ROOT / ".github" / "workflows" / "ci-rca.yml", REVIEW_SH):
        text = path.read_text(encoding="utf-8")
        for match in INSTALL_RE.finditer(text):
            before = text[: match.start()].splitlines()[-1]
            assert before.strip() == "" or before.strip().startswith("sudo"), before
            assert "if ! command -v claude" in text[: match.start()].splitlines()[-2], path
    jobs_found = 0
    for name, wf in _workflows().items():
        for job_id, job in (wf.get("jobs") or {}).items():
            if _reaches_install(name, job):
                jobs_found += 1
                assert job.get("runs-on") == "ubuntu-latest", (name, job_id)
    assert jobs_found >= 3


def test_ci_rca_agent_step_passes_model_and_effort() -> None:
    wf = _workflows()["ci-rca.yml"]
    bodies = [_step_run(s) for j in wf["jobs"].values() for s in j.get("steps", []) if s.get("name") == "Run ci-rca agent"]
    assert len(bodies) == 1
    assert '--model "opus[1m]"' in bodies[0]
    assert "--effort high" in bodies[0]
