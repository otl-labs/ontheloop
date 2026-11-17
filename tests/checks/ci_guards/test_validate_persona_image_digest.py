"""Tests for validate_persona_image_digest(): red on each fixture violation, green on the repository."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks.ci_guards.validate_persona_image_digest import validate_persona_image_digest
from src.persona_hosts import load_pin

_MODULE = "scripts.checks.ci_guards.validate_persona_image_digest"
_REPO = Path(__file__).resolve().parents[3]
_DIGEST = "sha256:" + "a" * 64
_BASE = "FROM python:3.12-slim@" + _DIGEST
_LOCK = "docker/persona/requirements-persona.txt"
_COPIES = ["scripts/run_persona.py", "src/persona_backends/"]

_PIN = {
    "image": "registry.example/persona",
    "digest": None,
    "source_commit": None,
    "build": {
        "dockerfile": "docker/persona/Dockerfile",
        "files": ["docker/persona/Dockerfile", "docker/persona/Dockerfile.dockerignore", _LOCK],
        "copy": ["scripts/run_persona.py", "src/persona_backends/"],
    },
    "consumers": {
        "local_container": {"status": "built", "location": "src/persona_hosts/local_container/"},
        "github_actions_shell": {"status": "pending", "owning_item": "T4.2:c8"},
    },
}

_DEPENDABOT = """\
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/docker/persona"
  - package-ecosystem: "docker"
    directory: "/docker/persona"
"""

_ROADMAP = {"tier_items": [{"id": "T4.2", "exit_criteria": [{"id": "c8", "status": "open"}]}]}


def _dockerfile(copies: list[str], base: str = _BASE) -> str:
    return "\n".join([base, f"COPY {_LOCK} /tmp/r.txt", *(f"COPY {c} /app/{c}" for c in copies)]) + "\n"


def _ignore(entries: list[str]) -> str:
    return "\n".join(["*", *(f"!{e}" for e in entries), "**/*.pyc"]) + "\n"


def _tree(tmp_path: Path, **override: str) -> Path:
    name = load_pin(_REPO).image
    pin = {**_PIN, "image": name}
    copies = list(pin["build"]["copy"])
    files = {
        "config/persona_image.yaml": yaml.safe_dump(pin),
        "docker/persona/Dockerfile": _dockerfile(copies),
        "docker/persona/Dockerfile.dockerignore": _ignore([*copies, _LOCK]),
        _LOCK: "x==1 \\\n",
        ".github/dependabot.yml": _DEPENDABOT,
        "docs/ROADMAP-PLATFORM.yaml": yaml.safe_dump(_ROADMAP),
        "src/persona_hosts/local_container/build.py": "from src.persona_hosts import load_pin\n\nload_pin('.')\n",
        "docs/plans/PLAN-x.yaml": f"mentions {name}: in a plan\n",
        **override,
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    with patch(f"{_MODULE}._common.ROOT", root):
        validate_persona_image_digest(failed)
    return failed


def _name() -> str:
    return load_pin(_REPO).image


def test_clean_tree_is_green_and_plans_may_name_the_image(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path)) == []


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({".github/workflows/x.yml": "image: {name}:latest\n"}, "image reference"),
        ({"src/adapter.py": "REF = '{name}@" + _DIGEST + "'\n"}, "image reference"),
        ({"docker/persona/Dockerfile": _dockerfile(_COPIES, base="FROM python:3.12-slim")}, "digest"),
        ({"docker/persona/Dockerfile": _dockerfile(["scripts/run_persona.py", "src/persona_backends/", "extra/"])}, "extra/"),
        ({"docker/persona/Dockerfile": _dockerfile(["scripts/run_persona.py"])}, "never copied"),
        ({"docker/persona/Dockerfile.dockerignore": _ignore(["scripts/run_persona.py", _LOCK])}, "allow-list"),
        ({"src/persona_hosts/local_container/build.py": "X = 1\n"}, "load_pin"),
        ({".github/dependabot.yml": _DEPENDABOT.replace('"docker"', '"npm"')}, "docker update"),
        ({".github/dependabot.yml": _DEPENDABOT.replace('"pip"', '"npm"')}, "pip update"),
        ({"docs/ROADMAP-PLATFORM.yaml": yaml.safe_dump({"tier_items": []})}, "open roadmap criterion"),
    ],
    ids=["workflow_tag", "adapter_digest", "unpinned_from", "uncopied_extra", "copy_missing", "ignore_missing",
         "no_load_pin", "no_docker_update", "no_pip_update", "pending_owner"],
)  # fmt: skip
def test_each_violation_is_red(tmp_path: Path, override: dict[str, str], needle: str) -> None:
    name = _name()
    failed = _run(_tree(tmp_path, **{k: v.replace("{name}", name) for k, v in override.items()}))
    assert any(needle in line for line in failed), failed


def test_missing_built_location_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "src/persona_hosts/local_container/build.py").unlink()
    (root / "src/persona_hosts/local_container").rmdir()
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    assert any("does not exist" in line for line in _run(root))


def test_live_repository_is_green() -> None:
    failed: list[str] = []
    validate_persona_image_digest(failed)
    assert failed == []
