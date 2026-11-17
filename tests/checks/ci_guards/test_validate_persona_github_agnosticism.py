"""Tests for validate_persona_github_agnosticism(): red on each GitHub coupling fixture, green on the repository."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks.ci_guards.validate_persona_github_agnosticism import validate_persona_github_agnosticism

_MODULE = "scripts.checks.ci_guards.validate_persona_github_agnosticism"

_PIN = {
    "image": "registry.example/persona",
    "digest": None,
    "source_commit": None,
    "build": {
        "dockerfile": "docker/persona/Dockerfile",
        "files": ["docker/persona/Dockerfile"],
        "copy": ["scripts/__init__.py", "scripts/run_persona.py", "src/__init__.py", "src/persona_backends/"],
    },
    "consumers": {"fargate": {"status": "pending", "owning_item": "T4.15:c12"}},
}

_ENTRY = "from src.persona_backends import run\n\n\ndef main() -> None:\n    run()\n"


def _tree(tmp_path: Path, backend: str = "def run() -> None:\n    pass\n", lock: str = "litellm==1.0 \\\n") -> Path:
    files = {
        "config/persona_image.yaml": yaml.safe_dump(_PIN),
        "scripts/__init__.py": "",
        "src/__init__.py": "",
        "scripts/run_persona.py": _ENTRY,
        "src/persona_backends/__init__.py": backend,
        "docker/persona/requirements-persona.txt": lock,
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    with patch(f"{_MODULE}._common.ROOT", root):
        validate_persona_github_agnosticism(failed)
    return failed


def test_clean_fixture_tree_is_green(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path)) == []


def test_docstring_mentioning_github_is_green(tmp_path: Path) -> None:
    body = '"""Talks about github and gh pr view in prose."""\n\n\ndef run() -> None:\n    pass\n'
    assert _run(_tree(tmp_path, backend=body)) == []


@pytest.mark.parametrize(
    "backend",
    [
        'import subprocess\n\n\ndef run() -> None:\n    subprocess.run(["gh", "pr", "view"])\n',
        'import os\n\n\ndef run() -> None:\n    os.system("gh pr view")\n',
        "import github\n\n\ndef run() -> None:\n    pass\n",
        'import importlib\n\n\ndef run() -> None:\n    importlib.import_module("ghapi")\n',
        'import os\n\n\ndef run() -> None:\n    os.environ["GITHUB_TOKEN"]\n',
        'import os\n\n\ndef run() -> None:\n    os.getenv("GH_TOKEN")\n',
    ],
    ids=["argv", "os_system", "import", "import_module", "github_token", "gh_token"],
)
def test_each_coupling_is_red(tmp_path: Path, backend: str) -> None:
    assert _run(_tree(tmp_path, backend=backend))


def test_violation_two_imports_deep_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path, backend="from src.persona_backends import inner\n")
    (root / "src/persona_backends/inner.py").write_text("import githubkit\n", encoding="utf-8")
    assert any("githubkit" in line for line in _run(root))


def test_lock_pin_of_a_github_client_is_red(tmp_path: Path) -> None:
    assert any("PyGithub".lower() in line.lower() for line in _run(_tree(tmp_path, lock="PyGithub==2.0 \\\n")))


def test_closure_module_not_copied_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "scripts/other.py").write_text("X = 1\n", encoding="utf-8")
    (root / "scripts/run_persona.py").write_text("from scripts import other\n" + _ENTRY, encoding="utf-8")
    assert any("scripts/other.py" in line and "not in build.copy" in line for line in _run(root))


def test_unreadable_pin_is_red(tmp_path: Path) -> None:
    assert _run(tmp_path)


def test_live_repository_is_green() -> None:
    failed: list[str] = []
    validate_persona_github_agnosticism(failed)
    assert failed == []
