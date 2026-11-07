"""Advisory emergent-north-star detector (Decision 243 clause 8; T4.36 c6).

Pure-local and credential-free: recomputed from committed files on every preflight and held only in the gitignored
preflight cache. ``detect`` is a pure function over Entry rows; ``load_entries`` is the only reader; ``preflight_bucket``
never raises. Nothing here is promoted, filed or stored.
"""

from __future__ import annotations

import collections
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from scripts.decisions_md import parse_decisions_md
from scripts.north_star_models import load_north_star
from scripts.north_star_shortlist import carriers as north_star_carriers

DETECTOR_VERSION = 1
STOPWORDS = frozenset(
    """a an the and or of to in on for by with is are be been was were it its this that these those as at from not no
never every each any all one two three than then into onto per via under over only own so do does did can may must
should would will shall when where which who whom whose what why how there here their them they we our us you your
decision decisions clause clauses amends amend amended supersedes superseded entry entries plan plans rec recs t1
t2 t3 t4 cd new now today also same other more most less such but if else because while still yet just make makes made
use uses used using set sets has have had none""".split()
)
TOKEN = re.compile(r"[a-z][a-z0-9_]+(?:-[a-z0-9_]+)*")
DF_MIN = 2
DF_MAX_SHARE = 0.15
CUT_PERCENTILE = 0.95
CUT_FLOOR = 0.03
CUT_CEIL = 0.08
MIN_CLUSTER = 4
HUB_PERCENTILE = 0.90
MIN_CITERS = 3
MIN_LATER = 10
MAX_CLUSTERS = 5
MAX_TERMS = 5
MAX_RISING = 5
MAX_HUBS = 10

DEFAULT_INDEX = Path("docs/decisions-index.json")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTH = re.compile(r"\d{4}-\d{2}")


@dataclass(frozen=True)
class Entry:
    number: int
    text: str
    amends: tuple[int, ...]
    supersedes: tuple[int, ...]
    related: tuple[int, ...]
    kind: str | None
    serves: tuple[str, ...] | None
    decided: date | None


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[int(p * (len(ordered) - 1))] if ordered else 0.0


def detect(entries: Iterable[Entry], carriers: Mapping[str, frozenset[int]], today: date) -> dict[str, Any]:
    live = sorted(entries, key=lambda e: e.number)
    by_num = {e.number: e for e in live}
    nums = sorted(by_num)
    total = len(nums)
    tokens = {n: {t for t in TOKEN.findall(by_num[n].text.lower()) if len(t) > 2 and t not in STOPWORDS} for n in nums}
    df = collections.Counter(t for s in tokens.values() for t in s)
    hi = DF_MAX_SHARE * total
    kept = {n: frozenset(t for t in s if DF_MIN <= df[t] <= hi) for n, s in tokens.items()}
    idf = {t: math.log(total / c) for t, c in df.items()}

    def sim(a: int, b: int) -> float:
        union = kept[a] | kept[b]
        return sum(idf[t] for t in kept[a] & kept[b]) / sum(idf[t] for t in union) if union else 0.0

    pair_sims = [sim(a, b) for i, a in enumerate(nums) for b in nums[i + 1 :]]
    cut = min(max(_percentile(pair_sims, CUT_PERCENTILE), CUT_FLOOR), CUT_CEIL)

    pairs = {frozenset((n, m)) for n in nums for m in (*by_num[n].amends, *by_num[n].supersedes) if m in by_num and m != n}
    adjacency: dict[int, set[int]] = collections.defaultdict(set)
    edge_sim: dict[frozenset[int], float] = {}
    for pair in sorted(pairs, key=sorted):
        a, b = sorted(pair)
        s = sim(a, b)
        if s >= cut:
            adjacency[a].add(b)
            adjacency[b].add(a)
            edge_sim[pair] = s

    seen: set[int] = set()
    clusters: list[dict[str, Any]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, comp = [start], set()
        while stack:
            x = stack.pop()
            if x not in comp:
                comp.add(x)
                stack.extend(sorted(adjacency[x] - comp))
        seen |= comp
        if len(comp) < MIN_CLUSTER:
            continue
        members = sorted(comp)
        shared = collections.Counter(t for m in members for t in kept[m])
        need = max(2, math.ceil(len(members) / 2))
        terms = sorted((t for t, c in shared.items() if c >= need), key=lambda t: (-shared[t], -idf[t], t))[:MAX_TERMS]
        carried = {k: len(v & comp) for k, v in sorted(carriers.items()) if v & comp}
        served = collections.Counter(s for m in members for s in (by_num[m].serves or ()))
        kinds = collections.Counter(by_num[m].kind or "undeclared" for m in members)
        sims = [s for pair, s in edge_sim.items() if pair <= comp]
        clusters.append(
            {
                "members": members,
                "terms": terms,
                "carried": carried,
                "served": dict(sorted(served.items())),
                "uncovered": not carried and not served,
                "kinds": dict(sorted(kinds.items())),
                "mean_similarity": round(sum(sims) / len(sims), 3),
            }
        )
    clusters.sort(key=lambda c: (not c["uncovered"], -c["mean_similarity"], -len(c["members"]), c["members"][0]))

    citers: dict[int, set[int]] = collections.defaultdict(set)
    for n in nums:
        for m in by_num[n].related:
            if m in by_num and m < n:
                citers[m].add(n)
    hub_cut = _percentile([float(len(citers[m])) for m in nums], HUB_PERCENTILE)
    hubs = sorted((m for m in nums if len(citers[m]) > hub_cut), key=lambda m: (-len(citers[m]), m))
    carried_all = set().union(*carriers.values()) if carriers else set()

    rising: list[dict[str, Any]] = []
    for m in nums:
        later = sum(1 for n in nums if n > m)
        c = len(citers[m])
        if c < MIN_CITERS or later < MIN_LATER or c > hub_cut or m in carried_all or by_num[m].kind == "mechanism":
            continue
        d = by_num[m].decided
        months = round(max(1.0, (today - d).days / 30.44), 1) if d else None
        rising.append(
            {
                "decision": m,
                "citers": c,
                "later": later,
                "share": round(c / later, 3),
                "months": months,
                "per_month": round(c / months, 2) if months else None,
            }
        )
    rising.sort(key=lambda r: (-r["share"], -r["citers"], r["decision"]))

    return {
        "detector_version": DETECTOR_VERSION,
        "as_of": today.isoformat(),
        "corpus": {
            "live": total,
            "amends_edges": len(pairs),
            "kept_edges": len(edge_sim),
            "edge_cut": round(cut, 3),
            "hub_cut": hub_cut,
            "serves_declared": sum(1 for n in nums if by_num[n].serves),
            "serves_eligible": sum(1 for n in nums if by_num[n].serves is not None),
            "carried": len(carried_all & set(nums)),
        },
        "clusters": clusters[:MAX_CLUSTERS],
        "cluster_count": len(clusters),
        "rising": rising[:MAX_RISING],
        "hubs": hubs[:MAX_HUBS],
        "hub_count": len(hubs),
    }


def _day(value: Any) -> date | None:
    text = str(value or "")
    if _DAY.fullmatch(text):
        return date.fromisoformat(text)
    if _MONTH.fullmatch(text):
        return date.fromisoformat(text + "-01")
    return None


def load_entries(index_path: Path | None = None, decisions_paths: list[Path] | None = None) -> list[Entry]:
    path = DEFAULT_INDEX if index_path is None else Path(index_path)
    rows = {r["number"]: r for r in json.loads(path.read_text(encoding="utf-8"))["decisions"]}
    parsed = {r["decision_id"]: r for r in parse_decisions_md(decisions_paths)}
    dated = {n: _day(r.get("decided_date")) for n, r in rows.items()}

    def decided(n: int) -> date | None:
        for k in sorted((k for k in dated if k <= n), reverse=True):
            if dated[k]:
                return dated[k]
        return None

    out: list[Entry] = []
    for n in sorted(rows):
        row = rows[n]
        if not row.get("live") or n not in parsed:
            continue
        p = parsed[n]
        body = " ".join(x for x in (p.get("intent"), p.get("problem")) if x)
        text = f"{p['title']} {body or row.get('triage_excerpt') or ''}"
        serves = row.get("serves")
        out.append(
            Entry(
                number=n,
                text=text,
                amends=tuple(row.get("amends") or ()),
                supersedes=tuple(row.get("supersedes") or ()),
                related=tuple(p.get("related_decisions") or ()),
                kind=(row.get("kind") or {}).get("value"),
                serves=None if "serves" not in row else tuple(serves or ()),
                decided=decided(n),
            )
        )
    return out


def preflight_bucket(
    index_path: Path | None = None,
    decisions_paths: list[Path] | None = None,
    north_star_path: Path | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    try:
        entries = load_entries(index_path, decisions_paths)
        carried = {k: frozenset(v) for k, v in north_star_carriers(load_north_star(north_star_path)).items()}
        return detect(entries, carried, today or datetime.now(timezone.utc).date())
    except Exception as exc:  # noqa: BLE001 - surfacing, never gating
        return {"detector_version": DETECTOR_VERSION, "error": f"{type(exc).__name__}: {exc}"}
