"""Pin, guard and model/effort flags for the two CI `claude -p` agents (PLAN-claude-cli-account-migration)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "ci" / "install_claude_cli.sh"
GOLDEN_ROOT = ROOT / "tests" / "fixtures" / "claude_cli_envelopes"
INSTALL_LINE_RE = re.compile(r"\bnpm\s+install\b.*@anthropic-ai/claude-code@")
VERSIONED_SPEC_RE = re.compile(r"@anthropic-ai/claude-code@\d")
GUARD = 'if EXISTING="$(command -v claude)"; then'
REVIEW_SH = ROOT / ".github" / "actions" / "subagent-plan-review" / "review.sh"
ACTION_USE = "./.github/actions/subagent-plan-review"


def _pin() -> str:
    return (ROOT / "config" / "claude-cli-version").read_text(encoding="utf-8").strip()


def _scanned_files() -> list[Path]:
    """Every file under .github/, scripts/ and tests/ except caches and the golden version directories."""
    files: list[Path] = []
    for tree in (".github", "scripts", "tests"):
        for path in sorted((ROOT / tree).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and GOLDEN_ROOT not in path.parents:
                files.append(path)
    return files


def _install_lines() -> list[tuple[Path, str]]:
    lines: list[tuple[Path, str]] = []
    for path in _scanned_files():
        if path.suffix in {".yml", ".yaml", ".sh"}:
            lines += [(path, line) for line in path.read_text(encoding="utf-8").splitlines() if INSTALL_LINE_RE.search(line)]
    return lines


def _literal_versions() -> list[str]:
    """A versioned install spec anywhere, a quoted copy of the pin anywhere, or any copy of the pin in CI code.

    A test docstring may still name the CLI version a past incident ran on; that is history, not a pin.
    """
    found: list[str] = []
    pin = _pin()
    quoted = (f'"{pin}"', f"'{pin}'")
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        in_ci_code = not rel.startswith("tests/")
        if VERSIONED_SPEC_RE.search(text) or any(q in text for q in quoted) or (in_ci_code and pin in text):
            found.append(rel)
    return found


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
        if "install_claude_cli.sh" in run:
            return True
        for script in re.findall(r"scripts/ci/\S+\.sh", run):
            body = (ROOT / script).read_text(encoding="utf-8") if (ROOT / script).is_file() else ""
            if re.search(r"^\s*bash\b[^#\n]*review\.sh", body, re.MULTILINE):
                return True
    return False


def test_every_install_pins_the_current_version() -> None:
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", _pin()), _pin()
    lines = _install_lines()
    assert [path for path, _ in lines] == [INSTALLER], lines
    line = lines[0][1].strip()
    assert line.startswith("sudo npm install -g "), line
    assert '"@anthropic-ai/claude-code@${VERSION}"' in line
    assert "--omit=dev --omit=optional" in line and line.endswith("&& sudo npm cache clean --force"), line
    body = INSTALLER.read_text(encoding="utf-8")
    guard_at, install_at = body.index(GUARD), body.index(lines[0][1])
    assert guard_at < body.index("exit 0", guard_at) < install_at
    assert _literal_versions() == []
    rca_install = [
        _step_run(s) for s in _workflows()["ci-rca.yml"]["jobs"]["rca"]["steps"] if s.get("name") == "Install Claude Code CLI"
    ]
    assert len(rca_install) == 1 and 'bash scripts/ci/install_claude_cli.sh "$GITHUB_WORKSPACE"' in rca_install[0]
    review_call = 'bash "${WORKSPACE_ROOT}/scripts/ci/install_claude_cli.sh" "${WORKSPACE_ROOT}"'
    assert review_call in REVIEW_SH.read_text(encoding="utf-8")


def test_installs_are_guarded_and_run_on_ubuntu_latest() -> None:
    steps = _workflows()["ci-rca.yml"]["jobs"]["rca"]["steps"]
    names = [s.get("name") or s.get("uses", "") for s in steps]
    first_checkout = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout@"))
    install = names.index("Install Claude Code CLI")
    assert first_checkout < install < names.index("Checkout at failing commit"), names
    assert "command -v claude" not in _step_run(steps[install])
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
