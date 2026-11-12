"""The scout-input fingerprint (T4.32 c4): a versioned digest of exactly the plan inputs a decision-scout judged.

The payload and digest are specified by the ledger's plan_scout_input_fingerprint field
(docs/contracts/exit-criteria-ledger.yaml); this is the only module that computes them. Fail closed: any input that
cannot be read or parsed is an error, never a digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.decisions_md import parse_decisions_md
from scripts.roadmap import plan_edges
from scripts.roadmap.approved_words import ApprovedTextError, Sidecar, parse_sidecar, words
from scripts.roadmap.plan_edges import PlanEdgesError
from scripts.roadmap.scope_projection import project_items

PAYLOAD_VERSION = 1
PAYLOAD_KEYS: tuple[str, ...] = (
    "v",
    "intent",
    "scope_paths",
    "verification_tier",
    "plan_type",
    "cites",
    "amends",
    "introduces",
    "decisions",
    "items",
    "edges",
    "roadmap_projection",
    "approved_blocks",
    "north_star",
)
HASHED_BLOCK_FILES = frozenset({"docs/DECISIONS.md", "docs/ROADMAP-PLATFORM.yaml", "docs/contracts/north-star.yaml"})
FIELD_CLASSES: dict[str, str] = {
    "PlanDocument.schema_version": "excluded",
    "PlanDocument.slug": "derived",
    "PlanDocument.intent": "hashed",
    "PlanDocument.plan_type": "hashed",
    "PlanDocument.verification_tier": "hashed",
    "PlanDocument.plan_path": "excluded",
    "PlanDocument.phase": "derived",
    "PlanDocument.scope": "hashed",
    "PlanDocument.bundled_recommendations": "excluded",
    "PlanDocument.followon_recs": "excluded",
    "PlanDocument.closes_criteria": "derived",
    "PlanDocument.infrastructure_dependencies": "excluded",
    "PlanDocument.acceptance_criteria": "excluded",
    "PlanDocument.verification_plan": "excluded",
    "PlanDocument.test_obligations": "excluded",
    "PlanDocument.constraints": "excluded",
    "PlanDocument.context": "excluded",
    "PlanDocument.pre_implementation_checklist": "excluded",
    "PlanDocument.execution_steps": "excluded",
    "PlanDocument.work_areas": "excluded",
    "PlanDocument.rollback": "excluded",
    "PlanDocument.tier_waiver": "excluded",
    "PlanDocument.handoff_policy": "excluded",
    "PlanDocument.fallback_reevaluation": "excluded",
    "PlanDocument.implementation_declared": "excluded",
    "PlanDocument.weakening_dispositions": "excluded",
    "PlanDocument.amends": "hashed",
    "PlanDocument.cites": "hashed",
    "PlanDocument.introduces": "hashed",
    "PlanDocument.principal": "excluded",
    "PlanDocument.gate_rounds": "excluded",
    "ScopeEntry.file": "hashed",
    "ScopeEntry.action": "excluded",
    "ScopeEntry.purpose": "excluded",
    "ScopeEntry.new_imports": "excluded",
}
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")
_REPO_ROOT = Path(__file__).resolve().parents[2]


class FingerprintError(Exception):
    """An input the fingerprint cannot be computed from; the message names it."""


@dataclass(frozen=True)
class Tree:
    decision_hashes: Mapping[int, str]
    roadmap: Any
    edge_map: Mapping[str, Mapping[str, Any]]
    north_star: str | None


def load_tree(root: Path) -> Tree:
    root = Path(root)
    decisions_md = root / "docs" / "DECISIONS.md"
    if not decisions_md.is_file():
        raise FingerprintError(f"cannot read decisions file {decisions_md}")
    rows = parse_decisions_md(paths=[decisions_md, root / "docs" / "DECISIONS_ARCHIVE.md"])
    hashes: dict[int, str] = {}
    for row in rows:
        try:
            number, content_hash = int(row["decision_id"]), str(row["content_hash"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FingerprintError(f"decisions row without a usable decision_id and content_hash: {exc!r}") from exc
        hashes[number] = content_hash
    roadmap = plan_edges.load_roadmap(root)
    north_star = root / "docs" / "contracts" / "north-star.yaml"
    return Tree(
        decision_hashes=hashes,
        roadmap=roadmap,
        edge_map=plan_edges.roadmap_edges(roadmap),
        north_star=hashlib.sha256(north_star.read_bytes()).hexdigest() if north_star.is_file() else None,
    )


def _require(plan: Mapping[str, Any], key: str) -> Any:
    if key not in plan or plan[key] is None:
        raise FingerprintError(f"plan is missing required key {key!r}")
    return plan[key]


def _scope_paths(plan: Mapping[str, Any]) -> list[str]:
    scope = _require(plan, "scope")
    if not isinstance(scope, list):
        raise FingerprintError("plan key 'scope' must be a list")
    paths: set[str] = set()
    for entry in scope:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("file"), str):
            raise FingerprintError("plan key 'scope' entries need a string 'file'")
        paths.add(entry["file"])
    return sorted(paths)


def _project_rows(tree: Tree, scope_paths: list[str]) -> list[dict[str, Any]]:
    rows = []
    for row in project_items(tree.roadmap.tier_items, scope_paths):
        row = dict(row)
        row["open_criteria"] = sorted(row["open_criteria"], key=lambda c: c["id"])
        for key in ("depends_on", "related_candidate_decisions", "matched"):
            row[key] = sorted(set(row[key]))
        rows.append(row)
    return sorted(rows, key=lambda r: r["id"])


def _approved_blocks(sidecar: Sidecar | None) -> list[str]:
    if sidecar is None:
        return []
    digests = {
        hashlib.sha256(json.dumps([b.file, words(b.before), words(b.after)]).encode("utf-8")).hexdigest()
        for b in sidecar.blocks
        if b.file in HASHED_BLOCK_FILES
    }
    return sorted(digests)


def projection(
    plan: Mapping[str, Any], tree: Tree, sidecar: Sidecar | None = None, extra_cites: Iterable[str] = ()
) -> dict[str, Any]:
    intent = _require(plan, "intent")
    if not isinstance(intent, str):
        raise FingerprintError("plan key 'intent' must be a string")
    scope_paths = _scope_paths(plan)
    tier = _require(plan, "verification_tier")
    plan_type = _require(plan, "plan_type")
    for key, value in (("verification_tier", tier), ("plan_type", plan_type)):
        if not isinstance(value, str):
            raise FingerprintError(f"plan key {key!r} must be a string")
    edges = plan_edges.extract_edges(plan, tree.edge_map, extra_cites)
    return {
        "v": PAYLOAD_VERSION,
        "intent": " ".join(intent.split()),
        "scope_paths": scope_paths,
        "verification_tier": tier,
        "plan_type": plan_type,
        "cites": list(edges.cites),
        "amends": list(edges.amends),
        "introduces": list(edges.introduces),
        "decisions": [[n, tree.decision_hashes.get(n)] for n in edges.decision_numbers],
        "items": list(edges.items),
        "edges": {item: edges.edges[item] for item in edges.items},
        "roadmap_projection": _project_rows(tree, scope_paths),
        "approved_blocks": _approved_blocks(sidecar),
        "north_star": tree.north_star,
    }


def fingerprint(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"v{payload['v']}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


def _read_plan(path: Path) -> Mapping[str, Any]:
    try:
        plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise FingerprintError(f"cannot read plan {path}: {exc}") from exc
    if not isinstance(plan, Mapping):
        raise FingerprintError(f"plan {path} is not a mapping")
    return plan


def _read_sidecar(root: Path, plan: Mapping[str, Any]) -> Sidecar | None:
    slug = plan.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise FingerprintError("plan has no usable 'slug' (the sidecar lookup needs it)")
    path = root / "docs" / "plans" / "approved" / f"{slug}.yaml"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FingerprintError(f"cannot read sidecar {path}: {exc}") from exc
    return parse_sidecar(text)


def _run(args: argparse.Namespace) -> str:
    root = Path(args.root)
    plan = _read_plan(Path(args.plan))
    extra = [t for t in args.with_cites.split(",") if t] if args.with_cites else []
    payload = projection(plan, load_tree(root), _read_sidecar(root, plan), extra)
    return json.dumps(payload, sort_keys=True) if args.json else f"scout_input_fingerprint={fingerprint(payload)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute a plan's scout-input fingerprint.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--root", default=str(_REPO_ROOT))
    parser.add_argument("--with-cites", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        line = _run(args)
    except (PlanEdgesError, FingerprintError, ApprovedTextError, OSError, yaml.YAMLError) as exc:
        print(f"FINGERPRINT-ERROR: {exc}", file=sys.stderr)
        return 2
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
