"""North-star carried_by shortlist projection (T4.36 c5, Decision 243 clause 8): the second mechanical
shortlist the decision scout runs at step 3. Given north-star ids (NS.n or mission) it prints one JSON line
naming the Decisions that carry them.

Pure-local, credential-free and read-only (Decision 105 R1-R3 guard, the scope_projection.py pattern): no
warehouse or portal call, no write, and import is side-effect-free. Imports only the standard library and
scripts.north_star_models.

Output keys: engaged (input order, deduplicated), carried_by (id -> its list), serves_only (live rows whose
serves names an engaged id and that no engaged carried_by lists), not_live (carried numbers with no live row),
shortlist (sorted: live carried numbers plus serves_only). serves widens the shortlist only; carried_by is the
authoritative link.

Exit codes: 0 printed; 2 on no arguments or an unknown or retired id; 1 when either input file fails to load.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.north_star_models import NorthStarDocument, load_north_star

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX_PATH = ROOT / "docs" / "decisions-index.json"
MISSION = "mission"
USAGE = "Usage: north_star_shortlist.py <NS.n|mission> [...]"


def carriers(doc: NorthStarDocument) -> dict[str, list[int]]:
    """Active north-star id (plus mission) -> the Decision numbers that carry it."""
    out = {MISSION: list(doc.mission.carried_by)}
    out.update({p.id: list(p.carried_by) for p in doc.principles if p.status == "active"})
    return out


def shortlist(ids: Sequence[str], doc: NorthStarDocument, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Project engaged north-star ids onto the live Decision rows; raises KeyError(list) on unknown ids."""
    known = carriers(doc)
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise KeyError(unknown)
    engaged = list(dict.fromkeys(ids))
    live = {r["number"] for r in rows if r.get("live")}
    carried = {i: known[i] for i in engaged}
    union = {n for ns in carried.values() for n in ns}
    serves_only = sorted(
        r["number"] for r in rows if r.get("live") and r["number"] not in union and set(r.get("serves") or []) & set(engaged)
    )
    return {
        "engaged": engaged,
        "carried_by": carried,
        "serves_only": serves_only,
        "not_live": sorted(union - live),
        "shortlist": sorted((union & live) | set(serves_only)),
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    rows = data.get("decisions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{path}: 'decisions' is missing or not a list")
    return rows


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        doc = load_north_star()
        rows = _load_rows(DEFAULT_INDEX_PATH)
    except ValueError as exc:
        print(f"north_star_shortlist: {exc}", file=sys.stderr)
        return 1
    try:
        result = shortlist(args, doc, rows)
    except KeyError as exc:
        print(f"not an active north star: {', '.join(exc.args[0])}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
