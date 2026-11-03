#!/usr/bin/env python3
"""rec-4372: pure input-identity record and attribution for the DuckLake Lambda zips, no network.

record_inputs() reads the build tooling's git blob ids and, per built artifact, its md5, every
zip entry's uncompressed sha256, and the subset of entries that are proven verbatim copies of a
git-tracked repo file (the entry's git blob sha1 equals `git ls-files -s`'s blob id for that path
-- the same proof scripts/lambda_manifest.py's stage_bundle relies on). attribute() compares a
reference record (the PR speculative-plan upload) against a current one (the merge-time rebuild)
and says, for every artifact whose md5 differs, whether every difference traces to a tracked input
that moved (schema 1; a record format change bumps it). Used by scripts/ci/ducklake_artifacts.py's
push-path assert so a merge between a PR's last plan and its own merge -- which can edit a shared
module main already moved -- does not fail closed on a stale PR-time reference (Decision 154
Update, 2026-10-17).
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path
from typing import Any

SCHEMA = 1

TOOLING_FILES: tuple[str, ...] = (
    "scripts/build_lambda.py",
    "scripts/build_lambda_config.py",
    "scripts/build_lambda_packaging.py",
    "scripts/build_lambda_deploy.py",
    "scripts/lambda_manifest.py",
)

LAYER_INPUT = "config/lambda/ducklake/version.yaml"

_FUNCTION_MANIFEST_SLUG: dict[str, str] = {
    "ducklake-writer.zip": "ducklake_writer",
    "ducklake-reader.zip": "ducklake_reader",
    "ducklake-maintenance.zip": "ducklake_maintenance",
    "ducklake-catalog-dr.zip": "ducklake_catalog_dr",
}


def _git_blob_sha1(data: bytes) -> str:
    """Hash *data* the way `git hash-object` would, without touching the git object store."""
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def _ls_files_blob_ids(root: Path) -> dict[str, str]:
    """{repo-relative path: blob id} for every tracked file, from one `git ls-files -s` call."""
    result = subprocess.run(["git", "ls-files", "-s"], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    ids: dict[str, str] = {}
    for line in result.stdout.splitlines():
        meta, path = line.split("\t", 1)
        _mode, blob, _stage = meta.split()
        ids[path] = blob
    return ids


def _manifest_packaging(manifest_path: str, root: Path) -> tuple[list[str], list[str]]:
    """(directory prefixes, explicit file paths) among the manifest's `includes:` entries --
    used to tell whether the CURRENT manifest still packages a path that only the reference's
    zip carried."""
    from scripts.lambda_manifest import load  # noqa: PLC0415 -- deferred: only function artifacts need it

    manifest = load(root / manifest_path)
    dirs: list[str] = []
    files: list[str] = []
    for raw in manifest.includes:
        clean = raw.rstrip("/")
        (dirs if (root / clean).is_dir() else files).append(clean + "/" if (root / clean).is_dir() else clean)
    return sorted(dirs), sorted(files)


def _record_function_inputs(
    raw_entries: dict[str, bytes], slug: str, tracked: dict[str, str], root: Path
) -> tuple[dict[str, str], str | None, list[str], list[str]]:
    manifest_path = f"src/lambdas/{slug}/manifest.yaml"
    inputs: dict[str, str] = {}
    if manifest_path not in tracked:
        return inputs, None, [], []
    inputs[manifest_path] = tracked[manifest_path]
    for entry_name, entry_bytes in raw_entries.items():
        if entry_name in tracked and _git_blob_sha1(entry_bytes) == tracked[entry_name]:
            inputs[entry_name] = tracked[entry_name]
    include_dirs, include_files = _manifest_packaging(manifest_path, root)
    return inputs, manifest_path, include_dirs, include_files


def record_inputs(packages_dir: str | Path, names: list[str], root: str | Path) -> dict[str, Any]:
    """Build the schema-1 input-identity record for *names* (built zips under *packages_dir*)."""
    packages_root, repo_root = Path(packages_dir), Path(root)
    tracked = _ls_files_blob_ids(repo_root)
    tooling = {path: tracked[path] for path in TOOLING_FILES if path in tracked}

    artifacts: dict[str, Any] = {}
    for name in names:
        data = (packages_root / name).read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            raw_entries = {info.filename: zf.read(info.filename) for info in zf.infolist()}
        entries = {entry_name: hashlib.sha256(entry_bytes).hexdigest() for entry_name, entry_bytes in raw_entries.items()}

        if name.endswith("-layer.zip"):
            inputs = {LAYER_INPUT: tracked[LAYER_INPUT]} if LAYER_INPUT in tracked else {}
            artifacts[name] = {
                "md5": hashlib.md5(data).hexdigest(),
                "kind": "layer",
                "entries": entries,
                "inputs": inputs,
                "manifest_path": None,
                "manifest_includes": [],
                "manifest_files": [],
            }
            continue

        slug = _FUNCTION_MANIFEST_SLUG.get(name)
        inputs, manifest_path, include_dirs, include_files = (
            _record_function_inputs(raw_entries, slug, tracked, repo_root) if slug else ({}, None, [], [])
        )
        artifacts[name] = {
            "md5": hashlib.md5(data).hexdigest(),
            "kind": "function",
            "entries": entries,
            "inputs": inputs,
            "manifest_path": manifest_path,
            "manifest_includes": include_dirs,
            "manifest_files": include_files,
        }

    return {"schema": SCHEMA, "tooling": tooling, "artifacts": artifacts}


def _attribute_layer(reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    ref_blob, cur_blob = reference["inputs"].get(LAYER_INPUT), current["inputs"].get(LAYER_INPUT)
    if cur_blob is not None and cur_blob != ref_blob:
        return {"verdict": "moved", "inputs": [LAYER_INPUT], "reason": f"{LAYER_INPUT} moved"}
    return {
        "verdict": "unattributed",
        "inputs": [],
        "reason": "container bytes differ but the layer's tracked input did not move",
    }


def _attribute_function(reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    ref_entries, cur_entries = reference["entries"], current["entries"]
    ref_inputs, cur_inputs = reference["inputs"], current["inputs"]
    manifest_path = current.get("manifest_path")
    manifest_moved = bool(manifest_path) and ref_inputs.get(manifest_path) != cur_inputs.get(manifest_path)
    include_dirs = current.get("manifest_includes", [])
    include_files = current.get("manifest_files", [])

    attributed: list[str] = []
    for entry in sorted(ref_entries.keys() & cur_entries.keys()):
        if ref_entries[entry] == cur_entries[entry]:
            continue
        ref_blob, cur_blob = ref_inputs.get(entry), cur_inputs.get(entry)
        if ref_blob is not None and cur_blob is not None and ref_blob != cur_blob:
            attributed.append(entry)
        else:
            return {
                "verdict": "unattributed",
                "inputs": [],
                "reason": f"{entry}: present on both sides with no attributable input move",
            }

    for entry in sorted(cur_entries.keys() - ref_entries.keys()):
        if cur_inputs.get(entry) is not None and (manifest_moved or any(entry.startswith(d) for d in include_dirs)):
            attributed.append(entry)
        else:
            return {"verdict": "unattributed", "inputs": [], "reason": f"{entry}: new entry with no attributable input"}

    for entry in sorted(ref_entries.keys() - cur_entries.keys()):
        still_packaged = entry in include_files or any(entry.startswith(d) for d in include_dirs)
        if ref_inputs.get(entry) is not None and not still_packaged:
            attributed.append(entry)
        else:
            return {
                "verdict": "unattributed",
                "inputs": [],
                "reason": f"{entry}: entry removed but the current manifest still packages it (or it had no reference input)",
            }

    if not attributed:
        return {"verdict": "unattributed", "inputs": [], "reason": "only the container bytes differ"}
    return {"verdict": "moved", "inputs": sorted(attributed), "reason": "moved inputs: " + ", ".join(sorted(attributed))}


def attribute(reference: dict[str, Any], current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per artifact whose md5 differs between *reference* and *current*: "moved" (every
    difference traces to a tracked input) or "unattributed" (fail closed). Unchanged artifacts
    carry no verdict. Iterates `current["artifacts"]` in its own (caller-supplied) order."""
    cur_artifacts = current.get("artifacts", {})
    ref_artifacts = reference.get("artifacts", {})
    missing = sorted(name for name in cur_artifacts if name not in ref_artifacts)
    if reference.get("schema") != current.get("schema") or missing:
        reason = (
            "reference schema differs from current (fail closed)"
            if reference.get("schema") != current.get("schema")
            else f"reference record is missing artifact(s) the current record carries: {missing}"
        )
        return {name: {"verdict": "unattributed", "inputs": [], "reason": reason} for name in cur_artifacts}

    ref_tooling, cur_tooling = reference.get("tooling", {}), current.get("tooling", {})
    tooling_moved = sorted(path for path in cur_tooling if ref_tooling.get(path) != cur_tooling.get(path))

    verdicts: dict[str, dict[str, Any]] = {}
    for name, cur in cur_artifacts.items():
        ref = ref_artifacts[name]
        if ref.get("md5") == cur.get("md5"):
            continue
        if tooling_moved:
            verdicts[name] = {"verdict": "moved", "inputs": list(tooling_moved), "reason": "build tooling moved"}
        elif cur.get("kind") == "layer":
            verdicts[name] = _attribute_layer(ref, cur)
        else:
            verdicts[name] = _attribute_function(ref, cur)
    return verdicts
