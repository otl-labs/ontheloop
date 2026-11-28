"""The decision_scout input provider (docs/contracts/personas/decision_scout.yaml; PLAN-decision-scout-add).

Pure: no network and no token. The plan and its approved-text sidecar are read from the head snapshot as data (never
imported, executed or installed); the north star, decision index and corpus, and everything computed from them, are read
at base_root, the trusted main checkout. Lives in the shell package outside the persona image's build.copy, so persona
code stays GitHub-agnostic (Decision 205 cl.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.north_star_models import load_north_star
from scripts.north_star_shortlist import MISSION, shortlist
from scripts.personas import materialise, route, snapshot
from scripts.personas.materialise import Unusable
from scripts.personas.rescout import SLUG_RE
from scripts.roadmap import approved_words, scope_projection

CONTRACT = "docs/contracts/personas/decision_scout.yaml"
NORTH_STAR = "docs/contracts/north-star.yaml"
INDEX = "docs/decisions-index.json"
DECISIONS = "docs/DECISIONS.md"
ROADMAP = "docs/ROADMAP-PLATFORM.yaml"


def _base_read(base: Path, rel: str) -> str:
    try:
        return (base / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unusable("input_unusable", f"{rel} is unreadable at the base") from exc


def _index_rows(base: Path) -> list[dict[str, Any]]:
    try:
        doc = json.loads(_base_read(base, INDEX))
    except ValueError as exc:
        raise Unusable("input_unusable", f"{INDEX} does not load at the base") from exc
    rows = doc.get("decisions") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise Unusable("input_unusable", f"{INDEX} does not load at the base")
    return rows


def _tag_shortlist(rows: list[dict[str, Any]]) -> str:
    tags: dict[str, list[Any]] = {}
    live = [r for r in rows if r.get("live")]
    for row in live:
        for tag in row.get("category_tags") or []:
            tags.setdefault(str(tag), []).append(row.get("number"))
    return json.dumps({"tags": {t: sorted(ids) for t, ids in sorted(tags.items())}, "live_count": len(live)}, sort_keys=True)


def _carriers(base: Path, rows: list[dict[str, Any]]) -> str:
    try:
        doc = load_north_star(base / NORTH_STAR)
    except ValueError as exc:
        raise Unusable("input_unusable", f"{NORTH_STAR} does not load at the base") from exc
    ids = [MISSION, *(p.id for p in doc.principles if p.status == "active")]
    out: dict[str, Any] = {}
    for ident in ids:
        found = shortlist([ident], doc, rows)
        out[ident] = {"carried_by": found["carried_by"][ident], "serves_only": found["serves_only"]}
    return json.dumps(out, sort_keys=True)


def _projection(base: Path, plan: dict[str, Any]) -> str:
    try:
        scope = plan.get("scope") if isinstance(plan, dict) else None
        paths = (
            [r["file"] for r in scope if isinstance(r, dict) and isinstance(r.get("file"), str)]
            if isinstance(scope, list)
            else []
        )
        rows = scope_projection.project(paths, base / ROADMAP)
        return json.dumps({"status": "available", "rows": rows}, sort_keys=True)
    except Exception as exc:  # noqa: BLE001 - any failure is reported as unavailable with its type
        return json.dumps({"status": "unavailable", "reason": type(exc).__name__}, sort_keys=True)


def _sidecar(root: Path, slug: str) -> str:
    try:
        data = snapshot.read_bytes(root, approved_words.sidecar_rel(slug))
        return "" if data is None else data.decode("utf-8")
    except (snapshot.SnapshotRefused, UnicodeDecodeError) as exc:
        raise Unusable("input_unusable", "the approved-text sidecar is not a readable text file") from exc


def build(root: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    base = ctx.get("base_root") or materialise.execute.ROOT
    plan_text = materialise.shared_fields(root, ctx["resolution"], list(ctx.get("plan_paths") or []))["plan"]
    if not plan_text.strip():
        raise Unusable("input_incomplete", "the change carries no plan to judge")
    plan = route.parse_plan(plan_text)
    slug = plan.get("slug") if plan else None
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise Unusable("input_unusable", "the plan does not parse as data or has no usable slug")
    rows = _index_rows(base)
    return {
        "plan": plan_text,
        "approved_text": _sidecar(root, slug),
        "north_star": _base_read(base, NORTH_STAR),
        "decisions_index": _base_read(base, INDEX),
        "decisions": _base_read(base, DECISIONS),
        "tag_shortlist": _tag_shortlist(rows),
        "north_star_carriers": _carriers(base, rows),
        "roadmap_projection": _projection(base, plan),
    }


materialise.PROVIDERS["decision_scout"] = build
