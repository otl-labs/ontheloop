import ast
from pathlib import Path

import yaml

from scripts.lambda_manifest import compute_affected_artifacts, load_all, stage_bundle

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "persona_backends"


def test_no_module_imports_scripts():
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n == "scripts" or n.startswith("scripts.") for n in names), path


def test_importlinter_forbids_scripts():
    text = (ROOT / ".importlinter").read_text(encoding="utf-8")
    block = text.split("[importlinter:contract:src-persona-backends-imports-no-scripts]")[1].split("[importlinter:")[0]
    assert "source_modules =\n    src.persona_backends" in block
    assert "    scripts\n" in block


def test_executor_boundary_rows_cover_the_backend():
    rows = yaml.safe_load((ROOT / "config/agent/executor/capabilities.yaml").read_text(encoding="utf-8"))["boundary_patterns"]
    for path in [
        "src/persona_backends/__init__.py",
        "tests/persona_backends/test_events.py",
        "tests/fixtures/persona_backends/fixture_persona.yaml",
        "scripts/run_persona.py",
        "tests/test_run_persona.py",
    ]:
        assert any(row in path for row in rows), path


def test_file_router_allowlists_the_entry_point():
    router = yaml.safe_load((ROOT / "docs/contracts/file-router.yaml").read_text(encoding="utf-8"))
    assert "run_persona.py" in router["scripts_root_allowlist"]["allowed_files"]


def test_excluded_from_data_pipeline(tmp_path):
    data_pipeline = load_all()["data-pipeline"]
    assert "src/persona_backends" in set(data_pipeline.excludes)
    files = sorted(f"src/persona_backends/{p.name}" for p in PACKAGE.glob("*.py"))
    assert "data-pipeline" not in compute_affected_artifacts(files)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    stage_bundle(data_pipeline, stage_dir, skip_pip=True)
    assert [p for p in stage_dir.rglob("*") if "persona_backends" in p.parts] == []
