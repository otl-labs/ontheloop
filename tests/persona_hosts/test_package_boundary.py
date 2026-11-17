"""Boundary tests for src/persona_hosts: import contract, capability patterns, data-pipeline exclude, no upward imports."""

from __future__ import annotations

import ast
import configparser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HOSTS = ROOT / "src" / "persona_hosts"
ROOTS = ("docker/persona/", "config/persona_image.yaml", "src/persona_hosts/", "tests/persona_hosts/")


def test_importlinter_contract_forbids_backend_and_scripts() -> None:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter")
    sections = [s for s in parser.sections() if "src.persona_hosts" in parser[s].get("source_modules", "")]
    assert sections, "no importlinter contract has src.persona_hosts as a source module"
    forbidden = parser[sections[0]]["forbidden_modules"].split()
    assert {"scripts", "src.persona_backends", "src.lambdas", "src.data"} <= set(forbidden)


def test_capabilities_boundary_patterns_cover_the_four_roots() -> None:
    data = yaml.safe_load((ROOT / "config/agent/executor/capabilities.yaml").read_text(encoding="utf-8"))
    flat = str(data)
    for entry in ROOTS:
        assert entry in flat, entry


def test_data_pipeline_manifest_excludes_persona_hosts() -> None:
    data = yaml.safe_load((ROOT / "src/lambdas/data-pipeline/manifest.yaml").read_text(encoding="utf-8"))
    assert "src/persona_hosts" in data["excludes"]


def test_no_import_of_scripts_or_backends_under_persona_hosts() -> None:
    offenders = []
    for path in HOSTS.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            offenders += [
                f"{path.name}: {n}" for n in names if n.split(".")[0] == "scripts" or n.startswith("src.persona_backends")
            ]
    assert not offenders
