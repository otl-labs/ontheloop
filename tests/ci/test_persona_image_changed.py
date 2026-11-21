"""Mirror test for scripts/ci/persona_image_changed.py and the two pr-validate steps that use it."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("persona_image_changed", ROOT / "scripts/ci/persona_image_changed.py")
assert _SPEC is not None and _SPEC.loader is not None
script: ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

_PIN = yaml.safe_dump(
    {
        "image": "x/y",
        "digest": None,
        "source_commit": None,
        "build": {"dockerfile": "d/Dockerfile", "files": ["d/Dockerfile", "d/req.txt"], "copy": ["a.py", "pkg/"]},
        "consumers": {"fargate": {"status": "pending", "owning_item": "T4.15:c12"}},
    }
)


def _repo(tmp_path: Path, pin: str | None = _PIN) -> tuple[Path, str]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
            cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q")
    (tmp_path / "config").mkdir()
    if pin is not None:
        (tmp_path / "config/persona_image.yaml").write_text(pin, encoding="utf-8")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    return tmp_path, git("rev-parse", "HEAD")


def _change(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "c"], cwd=root, check=True
    )


@pytest.mark.parametrize(
    "rel",
    [
        "d/Dockerfile", "d/req.txt", "a.py", "pkg/inner/x.py", "config/persona_image.yaml",
        "scripts/ci/persona_image_changed.py", "src/persona_hosts/x.py", "tests/persona_hosts/y.py",
        "tests/fixtures/persona_backends/z.txt",
    ],
)  # fmt: skip
def test_each_input_and_extra_path_triggers(tmp_path: Path, rel: str) -> None:
    root, base = _repo(tmp_path)
    _change(root, rel)
    assert script.decide(root, base)[0] is True


def test_unrelated_path_skips_with_reason(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    _change(root, "docs/other.md")
    assert script.decide(root, base) == (False, "no persona image input changed")


def test_unreadable_pin_fails_closed(tmp_path: Path) -> None:
    root, base = _repo(tmp_path, pin=None)
    assert script.decide(root, base)[0] is True


def test_git_failure_fails_closed(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    assert script.decide(root, "0" * 40)[0] is True


def test_line_parser_matches_yaml_safe_load_on_the_live_pin() -> None:
    text = (ROOT / "config/persona_image.yaml").read_text(encoding="utf-8")
    build = yaml.safe_load(text)["build"]
    assert script.parse_inputs(text) == build["files"] + build["copy"]


def test_main_writes_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(script, "decide", lambda root, base: (False, "none"))
    assert script.main(["--base", "abc"]) == 0
    assert out.read_text() == "run=false\n"
    assert "PERSONA-IMAGE-CONFORMANCE run=false reason=none" in capsys.readouterr().out


def test_ci_step_shape() -> None:
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["pr-validate"]
    steps = job["steps"]
    names = [s.get("name", "") for s in steps]
    fast = next(i for i, n in enumerate(names) if n.startswith("Validate fast tier"))
    decide_i = names.index("Persona image inputs changed?")
    suite_i = names.index("Persona image conformance (local_container suite)")
    pin_i = names.index("Persona image pin verification")
    assert fast < decide_i < suite_i < pin_i < names.index("Upload affected-set selection manifest") == len(steps) - 1
    for step in (steps[decide_i], steps[suite_i]):
        assert "continue-on-error" not in step
    suite = steps[suite_i]
    assert suite["if"] == f"steps.{steps[decide_i]['id']}.outputs.run == 'true'"
    assert "no:randomly" not in suite["run"]
    assert "run_persona" not in suite["run"] and "persona_compute_host" not in suite["run"]
    assert "env" not in suite
    assert job["timeout-minutes"] == 50
