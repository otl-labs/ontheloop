"""Packaging scope for the telemetry writer: its own artifact, never bundled into data-pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.lambda_manifest import compute_affected_artifacts

_ROOT = Path(__file__).resolve().parents[3]
_HANDLER = "src/lambdas/ducklake_telemetry_writer/handler.py"


def test_handler_edit_affects_only_the_telemetry_writer_artifact() -> None:
    assert sorted(compute_affected_artifacts([_HANDLER])) == ["ducklake_telemetry_writer"]


def test_data_pipeline_manifest_excludes_the_directory() -> None:
    manifest = yaml.safe_load((_ROOT / "src" / "lambdas" / "data-pipeline" / "manifest.yaml").read_text(encoding="utf-8"))
    assert "src/lambdas/ducklake_telemetry_writer" in manifest["excludes"]


_COMMON_MODULES = (
    "src/common/ducklake_telemetry_txn.py",
    "src/common/ducklake_telemetry_registration.py",
    "src/common/ducklake_telemetry_provision.py",
    "src/common/ducklake_telemetry_append.py",
    "src/common/ducklake_telemetry_blobstore.py",
    "src/common/ducklake_telemetry_blobs.py",
    "src/common/ducklake_telemetry_handshake.py",
)
_KERNEL = ("__init__", "append", "gate", "identity", "timestamps")


def _telemetry_manifest() -> dict:
    return yaml.safe_load(
        (_ROOT / "src" / "lambdas" / "ducklake_telemetry_writer" / "manifest.yaml").read_text(encoding="utf-8")
    )


def test_telemetry_writer_manifest_bundles_the_kernel_and_modules() -> None:
    manifest = _telemetry_manifest()
    includes = manifest["includes"]
    for rel in (f"src/telemetry/{name}.py" for name in _KERNEL):
        assert rel in includes, rel
    for rel in (*_COMMON_MODULES, "src/lambdas/ducklake_telemetry_writer/telemetry_actions.py"):
        assert rel in includes, rel
    for rel in ("src/common/ducklake_partition_layout.py", "src/common/ducklake_maintenance_scope.py"):
        assert rel in includes, rel
    assert "src/common/ducklake_maintenance.py" not in includes
    assert manifest["functions"] == [
        "agent-platform-ducklake-telemetry-writer",
        "agent-platform-ducklake-telemetry-writer-smoke",
    ]


def test_new_common_modules_affect_only_the_telemetry_writer() -> None:
    """Corrected, not loosened (Decision 181): provision and txn also ship in maintenance-smoke (the owner verb) and, since
    Decision 230, in ducklake_maintenance (catalog_actions creates the production telemetry tables)."""
    three = ["ducklake_maintenance", "ducklake_maintenance_smoke", "ducklake_telemetry_writer"]
    assert sorted(compute_affected_artifacts(["src/common/ducklake_telemetry_provision.py"])) == three
    assert sorted(compute_affected_artifacts(["src/common/ducklake_telemetry_txn.py"])) == three
    assert sorted(compute_affected_artifacts(["src/common/ducklake_telemetry_registration.py"])) == [
        "ducklake_telemetry_writer"
    ]
    assert sorted(compute_affected_artifacts(["src/lambdas/ducklake_telemetry_writer/telemetry_actions.py"])) == [
        "ducklake_telemetry_writer"
    ]


_SMOKE_CLOSURE = (
    "src/lambdas/ducklake_maintenance_smoke/telemetry_actions.py",
    "src/common/ducklake_telemetry_provision.py",
    "src/common/ducklake_telemetry_txn.py",
    "src/common/ducklake_partition_layout.py",
    "src/common/ducklake_maintenance_scope.py",
    *(f"src/telemetry/{name}.py" for name in _KERNEL),
)


def test_maintenance_smoke_manifest_bundles_the_create_verb_and_its_closure() -> None:
    manifest = yaml.safe_load(
        (_ROOT / "src" / "lambdas" / "ducklake_maintenance_smoke" / "manifest.yaml").read_text(encoding="utf-8")
    )
    for rel in _SMOKE_CLOSURE:
        assert rel in manifest["includes"], rel
    assert "ducklake_telemetry_registration.py" not in " ".join(manifest["includes"])
    assert "create_telemetry_tables" in manifest["notes"] and "Decision 216" in manifest["notes"]


def test_create_verb_module_affects_only_maintenance_smoke() -> None:
    assert sorted(compute_affected_artifacts(["src/lambdas/ducklake_maintenance_smoke/telemetry_actions.py"])) == [
        "ducklake_maintenance_smoke"
    ]
    excludes = yaml.safe_load((_ROOT / "src" / "lambdas" / "data-pipeline" / "manifest.yaml").read_text(encoding="utf-8"))[
        "excludes"
    ]
    for rel in ("src/common/ducklake_telemetry_txn.py", "src/common/ducklake_telemetry_provision.py"):
        assert rel in excludes, rel
    assert "src/lambdas/ducklake_maintenance_smoke" in excludes


def test_data_pipeline_excludes_every_ducklake_telemetry_module() -> None:
    excludes = yaml.safe_load((_ROOT / "src" / "lambdas" / "data-pipeline" / "manifest.yaml").read_text(encoding="utf-8"))[
        "excludes"
    ]
    on_disk = sorted(p.relative_to(_ROOT).as_posix() for p in (_ROOT / "src" / "common").glob("ducklake_telemetry_*.py"))
    assert on_disk == sorted(_COMMON_MODULES)
    for rel in on_disk:
        assert rel in excludes, rel


def test_append_modules_bundled_request_shared_with_reader() -> None:
    """The shared reader stopped bundling request.py with the event verbs (Decision 236 cl.2); the name is history."""
    includes = _telemetry_manifest()["includes"]
    for rel in (
        "src/telemetry/request.py",
        "src/common/ducklake_telemetry_append.py",
        "src/lambdas/ducklake_telemetry_writer/telemetry_edge.py",
        "src/lambdas/ducklake_telemetry_writer/telemetry_log.py",
    ):
        assert rel in includes, rel
        assert (_ROOT / rel).is_file(), rel
    assert sorted(compute_affected_artifacts(["src/common/ducklake_telemetry_append.py"])) == ["ducklake_telemetry_writer"]
    assert sorted(compute_affected_artifacts(["src/telemetry/request.py"])) == ["ducklake_telemetry_writer"]
    assert "append_telemetry" in _telemetry_manifest()["notes"]


def test_blob_modules_bundled_adapter_shared_with_reader() -> None:
    """The shared reader stopped bundling the blob adapter with transcript_content (Decision 236 cl.3); the name is history."""
    manifest = _telemetry_manifest()
    blob_modules = (
        "src/telemetry/blobs.py",
        "src/common/ducklake_telemetry_blobstore.py",
        "src/common/ducklake_telemetry_blobs.py",
        "src/common/ducklake_telemetry_handshake.py",
    )
    for rel in blob_modules:
        assert rel in manifest["includes"] and (_ROOT / rel).is_file(), rel
        assert sorted(compute_affected_artifacts([rel])) == ["ducklake_telemetry_writer"], rel
    assert not [rel for rel in manifest["includes"] if rel.startswith("src/telemetry_client")]
    reader = yaml.safe_load((_ROOT / "src" / "lambdas" / "ducklake_reader" / "manifest.yaml").read_text(encoding="utf-8"))
    for rel in blob_modules:
        assert rel not in reader["includes"], rel
    assert not [rel for rel in reader["includes"] if rel.startswith("src/telemetry_client")]
    assert "begin_blobs" in manifest["notes"]
    pipeline = yaml.safe_load((_ROOT / "src" / "lambdas" / "data-pipeline" / "manifest.yaml").read_text(encoding="utf-8"))
    for rel in (*blob_modules[1:], "src/telemetry_client"):
        assert rel in pipeline["excludes"], rel
