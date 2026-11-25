"""The materialise verb: one input JSON per route, built from the resolution, the routes, main's files and the snapshot.

Data only: no network call, no token, and nothing under the snapshot is imported, executed or added to sys.path; snapshot
files are read as bytes through snapshot.read_bytes. Names no registered persona id or contract path. A later add plan
registers its persona's own fields in PROVIDERS. Stdlib only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.personas import execute, resolve_pr, snapshot
from scripts.personas import route as route_mod

Provider = Callable[[Path, dict[str, Any]], dict[str, Any]]
PROVIDERS: dict[str, Provider] = {}


class Unusable(Exception):
    def __init__(self, failure: str, detail: str) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail


def _read_text(root: Path, relpath: str) -> str | None:
    data = snapshot.read_bytes(root, relpath)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unusable("input_unusable", f"{relpath} is not UTF-8") from exc


def shared_fields(root: Path, resolution: dict[str, Any], plan_paths: list[str]) -> dict[str, Any]:
    if len(plan_paths) > 1:
        raise Unusable("input_unusable", "the diff adds or modifies more than one plan")
    plan = _read_text(root, plan_paths[0]) or "" if plan_paths else ""
    return {"change_id": resolution["change_id"], "head_sha": resolution["head_sha"], "plan": plan}


def _fixture(root: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    texts: list[str] = []
    for relpath in ctx["fixture_paths"]:
        if not str(relpath).startswith(route_mod.FIXTURE_DIR):
            raise Unusable("input_unusable", "a fixture path is outside the fixture directory")
        text = _read_text(root, relpath)
        if text is None:
            raise Unusable("input_unusable", f"fixture file {relpath} is absent at head")
        texts.append(text.rstrip("\n"))
    resolution = ctx["resolution"]
    if resolution.get("body_oversized"):
        raise Unusable("input_unusable", "the pull request body is over the size cap")
    body = str(resolution.get("body") or "")
    if body:
        texts.append(body)
    notes = "\n".join(texts)
    if not notes.strip():
        raise Unusable("input_unusable", "the fixture notes are empty")
    return {"notes": notes}


def build_input(
    root: Path, route_id: str, resolution: dict[str, Any], plan_paths: list[str], fixture_paths: list[str] | None = None
) -> dict[str, Any]:
    fields = shared_fields(root, resolution, plan_paths)
    provider = PROVIDERS.get(route_id)
    if provider is None and route_id in execute.fixture_personas(execute.ROOT):
        provider = _fixture
    if provider is None:
        raise Unusable("input_incomplete", f"no input provider is registered for {route_id}")
    fields.update(provider(root, {"route": route_id, "resolution": resolution, "fixture_paths": fixture_paths or []}))
    return fields


def materialise(
    root: Path,
    out: Path,
    resolution: dict[str, Any],
    routes: list[str],
    plan_paths: list[str],
    usable: bool,
    fixture_paths: list[str] | None = None,
) -> None:
    for route_id in routes:
        try:
            if not usable:
                raise Unusable("input_unusable", "the head snapshot was refused")
            body = build_input(root, route_id, resolution, plan_paths, fixture_paths)
            (out / f"input-{route_id}.json").write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
        except (Unusable, snapshot.SnapshotRefused) as exc:
            failure = exc.failure if isinstance(exc, Unusable) else "input_unusable"
            detail = exc.detail if isinstance(exc, Unusable) else str(exc)
            (out / f"failure-{route_id}.json").write_text(json.dumps({"failure": failure, "detail": detail}), encoding="utf-8")


def main(argv: list[str]) -> int:
    resolution = json.loads(resolve_pr.RESOLUTION.read_text(encoding="utf-8"))
    routes_doc = json.loads(route_mod.ROUTES.read_text(encoding="utf-8"))
    state_file = snapshot.STATE
    usable = state_file.exists() and bool(json.loads(state_file.read_text(encoding="utf-8")).get("ok"))
    materialise(
        snapshot.snapshot_dir(),
        Path("prepared"),
        resolution,
        routes_doc["routes"],
        routes_doc["plan_paths"],
        usable,
        routes_doc.get("fixture_paths", []),
    )
    return 0
