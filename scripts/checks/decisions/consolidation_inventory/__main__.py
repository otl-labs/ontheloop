"""CLI of the consolidation coupling inventory."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from scripts import decisions_md
from scripts.checks.decisions.consolidation_inventory import inventory, probe
from scripts.checks.decisions.consolidation_inventory._model import TIERS, Inventory

LOGS = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="consolidation_inventory", description=__doc__)
    parser.add_argument("victims", nargs="+", type=int, metavar="VICTIM")
    parser.add_argument("--root", default=".")
    parser.add_argument("--probe", action="store_true", help="stub the victims in a temp worktree and run what reads them")
    parser.add_argument(
        "--include-citations", action="store_true", help="one hit per bare citation instead of a count per file"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--tier", choices=list(reversed(TIERS)), default="possible", help="minimum tier shown")
    return parser


def _headers(root: Path) -> set[int]:
    found: set[int] = set()
    for rel in LOGS:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found |= {int(m.group(1)) for m in decisions_md.iter_decision_headings(text)}
    return found


def render(inv: Inventory, tier: str) -> str:
    lines: list[str] = []
    hits = inv.sorted_hits(tier)
    info = inv.probe_info
    if info:
        lines.append(f"stub_template: {info['stub_template']} head: {info['head']}")
        lines.extend(f"note: {n}" for n in info["notes"])
        lines.append(f"note: {info['note']}")
    for item in sorted(inv.notes, key=lambda n: (n["surface"], n["reason"])):
        lines.append(f"note: {item['surface']}: {item['reason']}")
    kind = ""
    for hit in hits:
        if hit.kind != kind:
            kind = hit.kind
            lines.append(f"[{kind}]")
        detail = hit.evidence[0].detail if hit.evidence else ""
        lines.append(
            f"  {hit.tier} {hit.effect} {hit.locator} Decision {hit.victim} probe={hit.probe} "
            f"gated={str(hit.gated).lower()} {detail}"
        )
    per_kind = Counter(h.kind for h in hits)
    need = sum(1 for h in hits if h.needs_disposition)
    kinds = " ".join(f"{k}={per_kind[k]}" for k in sorted(per_kind))
    lines.append(f"TOTAL hits={len(hits)} needs_disposition={need} {kinds}".rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    root = Path(ns.root).resolve()
    victims = sorted(set(ns.victims))
    missing = [v for v in victims if v not in _headers(root)]
    if missing:
        print(f"error: no Decision header for {', '.join(map(str, missing))}", file=sys.stderr)
        return 1
    inv = inventory(root, victims, include_citations=ns.include_citations)
    if ns.probe:
        inv = probe(root, inv)
    if ns.as_json:
        print(json.dumps(inv.as_dict(ns.tier), indent=1, sort_keys=True))
    else:
        print(render(inv, ns.tier))
    if inv.probe_failed:
        print(f"error: probe failed: {inv.probe_failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
