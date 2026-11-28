"""The materialise verb: one input JSON per route, built from the resolution, the routes, main's files and the snapshot.

Data only: no network call, no token, and nothing under the snapshot is imported, executed or added to sys.path; snapshot
files are read as bytes through snapshot.read_bytes. Names no registered persona id or contract path. A later add plan
registers its persona's own fields in PROVIDERS. Guidance slots a route's contract declares are filled by
guidance.fill at base_root, the trusted main checkout (default execute.ROOT, resolved at call time), never the head
snapshot. A fixture route's notes may carry one "GUIDANCE-CASE: <name>" line naming a case committed on main; the head
selects the case and never supplies a value. Stdlib only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.personas import egress_launcher, egress_trees, execute, guidance, resolve_pr, snapshot
from scripts.personas import route as route_mod

Provider = Callable[[Path, dict[str, Any]], dict[str, Any]]
PROVIDERS: dict[str, Provider] = {}
CASE_KEY = "_guidance_case"
CASE_PREFIX = "GUIDANCE-CASE:"
CASE_LINE = re.compile(r"^GUIDANCE-CASE: ([a-z][a-z0-9_]*)$")
REQUEST_HEADROOM = 1024 * 1024


def tree_names(base: Path, route_id: str) -> list[str]:
    """The trees (head, main) the routed persona's contract names through read_tree:<tree>, in contract order."""
    contract = execute._contract(base, route_id) or {}  # noqa: SLF001 -- the one contract loader
    caps = contract.get("capabilities") or []
    return [c.split(":", 1)[1] for c in caps if isinstance(c, str) and c.startswith("read_tree:") and ":" in c]


def pack_head(root: Path, out: Path) -> None:
    tar = out / "head.tar"
    try:
        egress_trees.pack(root, tar)
        size = tar.stat().st_size
    except OSError as exc:
        tar.unlink(missing_ok=True)
        raise Unusable("input_unusable", f"the head tree could not be packed ({type(exc).__name__})") from exc
    if size > egress_trees.TREE_MAX_BYTES:
        tar.unlink(missing_ok=True)
        raise Unusable("input_unusable", f"the head tree is {size} bytes, over the {egress_trees.TREE_MAX_BYTES} byte cap")


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
    kept: list[str] = []
    cases: list[str] = []
    for line in "\n".join(texts).split("\n"):
        if line.rstrip("\r").startswith(CASE_PREFIX):
            matched = CASE_LINE.match(line.rstrip("\r"))
            if matched is None:
                raise Unusable("input_unusable", "a GUIDANCE-CASE line is malformed")
            cases.append(matched.group(1))
        else:
            kept.append(line)
    if len(cases) > 1:
        raise Unusable("input_unusable", "the fixture notes name more than one guidance case")
    notes = "\n".join(kept)
    if not notes.strip():
        raise Unusable("input_unusable", "the fixture notes are empty")
    return {"notes": notes, CASE_KEY: cases[0] if cases else None}


def build_input(
    root: Path,
    route_id: str,
    resolution: dict[str, Any],
    plan_paths: list[str],
    fixture_paths: list[str] | None = None,
    *,
    base_root: Path | None = None,
) -> dict[str, Any]:
    base = base_root if base_root is not None else execute.ROOT
    fields = shared_fields(root, resolution, plan_paths)
    is_fixture = route_id in execute.fixture_personas(base)
    provider = PROVIDERS.get(route_id)
    if provider is None and is_fixture:
        provider = _fixture
    if provider is None:
        raise Unusable("input_incomplete", f"no input provider is registered for {route_id}")
    provided = provider(
        root,
        {
            "route": route_id,
            "resolution": resolution,
            "fixture_paths": fixture_paths or [],
            "plan_paths": plan_paths,
            "base_root": base,
        },
    )
    case = provided.pop(CASE_KEY, None)
    fields.update(provided)
    try:
        slots = execute.guidance_slots(base, route_id)
        if not is_fixture and any(guidance.has_case_provider(slot) for slot in slots):
            raise Unusable("input_unusable", "only a fixture persona may declare a case-provided guidance slot")
        if case is not None and not any(guidance.has_case_provider(slot) for slot in slots):
            raise Unusable("input_unusable", "the fixture notes name a guidance case but the contract declares no case slot")
        clash = sorted(slot for slot in slots if slot in fields)
        if clash:
            raise Unusable("input_unusable", f"guidance slot {clash[0]} names a field the input already carries")
        fields.update(guidance.fill(base, route_id, slots, case))
    except (ValueError, OSError) as exc:
        raise Unusable("input_unusable", str(exc)) from exc
    return fields


def materialise(
    root: Path,
    out: Path,
    resolution: dict[str, Any],
    routes: list[str],
    plan_paths: list[str],
    usable: bool,
    fixture_paths: list[str] | None = None,
    *,
    base_root: Path | None = None,
) -> None:
    for route_id in routes:
        try:
            if not usable:
                raise Unusable("input_unusable", "the head snapshot was refused")
            body = build_input(root, route_id, resolution, plan_paths, fixture_paths, base_root=base_root)
            text = json.dumps(body, sort_keys=True)
            size = len(text.encode("utf-8"))
            if size > egress_launcher.MAX_REQUEST - REQUEST_HEADROOM:
                raise Unusable("input_unusable", f"the input is {size} bytes, over the launcher request cap less headroom")
            if "head" in tree_names(base_root if base_root is not None else execute.ROOT, route_id):
                pack_head(root, out)
            (out / f"input-{route_id}.json").write_text(text, encoding="utf-8")
            print(f"PERSONA-VERDICT input {route_id} bytes={size}")
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
        base_root=execute.ROOT,
    )
    return 0


from scripts.personas import review_input, scout_input  # noqa: E402,F401
