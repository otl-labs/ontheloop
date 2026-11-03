"""The core: open recommendations related to a set of files, ranked server-side, in exactly one bounded reader call.

A module helper over the existing `named` port verb (Decision 212; no new adapter method). The repo-side
neighbourhood expansion (imports, siblings, tests mirror, patch_string, text mentions, git change coupling, rename
aliases, lexical terms) builds the recs_related verb's five string binds; the verb ranks (direct anchor hits first,
then priority, score, recency) and bounds the page (Decision 215 cl.1: a relevance lookup, never a bulk path). The
returned Envelope discloses completeness against post-exclusion totals (cl.2); a reader failure raises, never [].
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.recs_related import history, mentions, neighbourhood, terms
from scripts.recs_related.envelope import (
    CHANNEL_OK,
    FILE_INVALID,
    FILE_OUTSIDE_REPO,
    FILE_TRACKED,
    FILE_UNTRACKED,
    TIER_DIRECT,
    TIER_NEAR,
    TIER_TERM,
    UNAVAILABLE_PREFIX,
    Envelope,
    Hit,
    unavailable,
)
from src.common.ducklake_reader_client import make_reader

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_EXCLUDE = 200
MAX_NEAR = 400

# Measured weights (840 linked closed recs, 288 fix commits, graphs at each fix's parent, chronological 60/40 split).
# near per path = the sum over query files of the relation weights below.
NEAR_WEIGHTS: dict[str, float] = {
    neighbourhood.RELATION_UPSTREAM: 4.0,
    neighbourhood.RELATION_DOWNSTREAM: 1.0,
    neighbourhood.RELATION_SIBLING: 1.0,
    neighbourhood.RELATION_TESTS_MIRROR: 1.0,
    neighbourhood.RELATION_PATCH_STRING: 1.0,
}
MENTION_WEIGHT = 0.5
COUPLING_SHARED_WEIGHT = 0.5
COUPLING_SHARED_CAP = 5
COUPLING_CONFIDENCE_WEIGHT = 0.5
# Interim directory scope (rec-4279 deletes this once directory anchors are repaired): parent, then grandparent.
ANCESTOR_DIR_WEIGHTS: tuple[float, ...] = (0.5, 0.25)
ANCESTOR_WHY = "ancestor_dir"
TERM_WEIGHTS: dict[str, float] = {
    terms.KIND_STEM: 16.0,
    terms.KIND_MODULE: 1.0,
    terms.KIND_BASENAME: 0.5,
    terms.KIND_PATH: 0.5,
    terms.KIND_SYMBOL: 0.5,
    terms.KIND_USER: 4.0,
}

_UNSAFE_BIND = (",", "|")
_UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class QueryFile:
    raw: str
    path: str
    status: str


def tracked_files(repo_root: Path) -> frozenset[str]:
    """Every tracked path of the repo (git ls-files); a failure raises -- the channels cannot be judged without it."""
    done = history.run_git(repo_root, "ls-files", "-z")
    if isinstance(done, str) or done.returncode != 0:
        raise RuntimeError(
            f"recs_related: cannot list tracked files in {repo_root}: {done if isinstance(done, str) else done.stderr}"
        )
    return frozenset(p for p in done.stdout.split("\0") if p)


def normalise_query_file(raw: str, repo_root: Path, tracked: frozenset[str]) -> QueryFile:
    """Repo-relative POSIX form of *raw* plus its status: tracked, untracked, outside_repo or invalid (never silent)."""
    text = raw.strip()
    if not text or any(ch in text for ch in _UNSAFE_BIND):
        return QueryFile(raw, text, FILE_INVALID)
    flat = posixpath.normpath(text.replace("\\", "/"))
    if flat.startswith("/"):
        try:
            flat = Path(flat).relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return QueryFile(raw, flat, FILE_OUTSIDE_REPO)
    if flat == "." or not flat:
        return QueryFile(raw, text, FILE_INVALID)
    if flat == ".." or flat.startswith("../"):
        return QueryFile(raw, flat, FILE_OUTSIDE_REPO)
    return QueryFile(raw, flat, FILE_TRACKED if flat in tracked else FILE_UNTRACKED)


def _dir_scope(path: str) -> list[tuple[str, float]]:
    """The parent and grandparent directories of *path* (never a single-segment root), each in both spellings."""
    out: list[tuple[str, float]] = []
    here = posixpath.dirname(path)
    for weight in ANCESTOR_DIR_WEIGHTS:
        if "/" not in here:
            break
        out.extend([(here, weight), (f"{here}/", weight)])
        here = posixpath.dirname(here)
    return out


def _bind_safe(value: str) -> bool:
    return not any(ch in value for ch in _UNSAFE_BIND)


def _join(pairs: Iterable[tuple[str, float]]) -> str:
    return ",".join(f"{key}|{weight}" for key, weight in pairs)


def _aggregate(per_file: dict[str, str]) -> str:
    """One channel status from the per-file statuses: ok, or every distinct reason with the files it hit."""
    reasons: dict[str, list[str]] = {}
    for path, status in per_file.items():
        if status != CHANNEL_OK:
            reasons.setdefault(status.removeprefix(UNAVAILABLE_PREFIX), []).append(path)
    if not reasons:
        return CHANNEL_OK
    return unavailable("; ".join(f"{reason} [{', '.join(paths)}]" for reason, paths in reasons.items()))


class _Expansion:
    """Everything the repo-side expansion learned about the query: binds' inputs and the why-attribution maps."""

    def __init__(self) -> None:
        self.near: dict[str, float] = {}
        self.near_why: dict[str, list[str]] = {}
        self.aliases: dict[str, str] = {}
        self.dir_spellings: set[str] = set()
        self.statuses: dict[str, dict[str, str]] = {"imports": {}, "mentions": {}, "history": {}}
        self.term_weights: dict[str, float] = {}

    def add_near(self, path: str, weight: float, why: str) -> None:
        self.near[path] = self.near.get(path, 0.0) + weight
        if why not in self.near_why.setdefault(path, []):
            self.near_why[path].append(why)


def _expand(files: Sequence[str], repo_root: Path, tracked: frozenset[str]) -> _Expansion:
    exp = _Expansion()
    index = history.load_history(repo_root)
    for path in files:
        nb = neighbourhood.neighbours_of(path, repo_root, tracked)
        exp.statuses["imports"][path] = nb.imports
        for neighbour, relations in nb.relations.items():
            for relation in sorted(relations):
                exp.add_near(neighbour, NEAR_WEIGHTS[relation], f"{relation}:{path}")
        mention = mentions.mentions_of(path, repo_root, tracked)
        exp.statuses["mentions"][path] = mention.status
        for neighbour in sorted(mention.paths):
            exp.add_near(neighbour, MENTION_WEIGHT, f"mention:{path}")
        past = index.for_file(path, tracked)
        exp.statuses["history"][path] = past.status
        for coupled in past.coupling:
            weight = (
                COUPLING_SHARED_WEIGHT * min(coupled.shared, COUPLING_SHARED_CAP)
                + COUPLING_CONFIDENCE_WEIGHT * coupled.confidence
            )
            exp.add_near(coupled.path, weight, f"coupling:{path} ({coupled.shared} shared commits)")
        for alias in past.aliases:
            exp.aliases.setdefault(alias, path)
        for directory, weight in _dir_scope(path):
            exp.dir_spellings.add(directory)
            exp.add_near(directory, weight, f"{ANCESTOR_WHY}:{directory.rstrip('/')}")
        for term in terms.derive_terms(path, repo_root):
            exp.term_weights[term.text] = max(exp.term_weights.get(term.text, 0.0), TERM_WEIGHTS[term.kind])
    return exp


def _why(
    row: dict[str, Any], tier: str, direct_of: dict[str, str], alias_of: dict[str, str], near_why: dict[str, list[str]]
) -> list[str]:
    file = row.get("file") or ""
    why: list[str] = []
    if tier == TIER_DIRECT:
        if file in alias_of:
            why.append(f"alias:{file} (earlier name of {alias_of[file]})")
        if file in direct_of:
            why.append(f"direct:{file}")
    why.extend(near_why.get(file, []))
    why.extend(f"term:{t}" for t in (row.get("matched_terms") or "").split())
    return why or [_UNATTRIBUTED]


def related_recs(
    files: Sequence[str],
    terms_text: str = "",
    limit: int = DEFAULT_LIMIT,
    exclude_ids: Sequence[str] = (),
    reader: Any = None,
    *,
    repo_root: Path | None = None,
) -> Envelope:
    """Open recs related to *files* (and free-text *terms_text*), as one ranked, bounded, explained page."""
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"recs_related: limit must be 1..{MAX_LIMIT}; got {limit}")
    excluded = list(dict.fromkeys(i.strip() for i in exclude_ids if i.strip()))
    if len(excluded) > MAX_EXCLUDE:
        raise ValueError(f"recs_related: at most {MAX_EXCLUDE} excluded ids per call; got {len(excluded)} -- narrow the query")
    root = (repo_root or REPO_ROOT).resolve()
    tracked = tracked_files(root)
    queried = [normalise_query_file(f, root, tracked) for f in files]
    usable = list(dict.fromkeys(q.path for q in queried if q.status in (FILE_TRACKED, FILE_UNTRACKED)))
    user_terms = terms.free_text_terms(terms_text)
    if not usable and not user_terms:
        dropped = ", ".join(f"{q.raw!r} ({q.status})" for q in queried) or "no files given"
        raise ValueError(f"recs_related: empty query -- pass at least one usable file or --terms; {dropped}")

    exp = _expand(usable, root, tracked)
    for term in user_terms:
        exp.term_weights[term.text] = max(exp.term_weights.get(term.text, 0.0), TERM_WEIGHTS[term.kind])
    alias_paths = [a for a in exp.aliases if a not in usable and _bind_safe(a)]
    direct_of = {p: p for p in usable}
    notes: list[str] = []
    known = set(usable) | set(alias_paths)
    candidates = [(p, w) for p, w in exp.near.items() if p not in known]
    unsafe = [p for p, _ in candidates if not _bind_safe(p)]
    if unsafe:
        notes.append(f"{len(unsafe)} near path(s) dropped: they contain ',' or '|' and cannot be bound")
    ranked = sorted(((p, w) for p, w in candidates if _bind_safe(p)), key=lambda pw: (-pw[1], pw[0]))
    near_truncated = len(ranked) > MAX_NEAR
    near = ranked[:MAX_NEAR]
    capped_terms, terms_truncated = terms.cap_terms(exp.term_weights)

    source: Any = reader if reader is not None else make_reader()
    rows = source.named(
        "recs_related",
        files=",".join(usable + alias_paths),
        near=_join(near),
        terms=_join(capped_terms.items()),
        exclude_ids=",".join(excluded),
        max_rows=str(limit),
    )

    near_map = dict(near)
    hits: list[Hit] = []
    for row in rows:
        file = row.get("file") or ""
        tier = TIER_DIRECT if row.get("direct") else TIER_NEAR if file in near_map else TIER_TERM
        hits.append(
            Hit(
                id=row["id"],
                title=row.get("title") or "",
                priority=row.get("priority"),
                status=row.get("status") or "",
                effort=row.get("effort"),
                source=row.get("source"),
                file=row.get("file"),
                tier=tier,
                score=float(row.get("score") or 0.0),
                why=_why(row, tier, direct_of, exp.aliases, exp.near_why),
                snippet=row.get("snippet"),
            )
        )
    total = int(rows[0]["total"]) if rows else 0
    direct_total = int(rows[0]["direct_total"]) if rows else 0
    seen = list(dict.fromkeys([*excluded, *(h.id for h in hits)]))
    next_exclude: list[str] | None = None
    if len(hits) < total:
        if len(seen) > MAX_EXCLUDE:
            notes.append(f"paging stopped: {len(seen)} ids seen exceeds the {MAX_EXCLUDE}-id exclude cap -- narrow the query")
        else:
            next_exclude = seen

    return Envelope(
        query={"files": usable, "aliases": alias_paths, "terms": list(capped_terms)},
        query_files=[{"path": q.path, "status": q.status} for q in queried],
        hits=hits,
        total=total,
        direct_total=direct_total,
        channels={name: _aggregate(per_file) for name, per_file in exp.statuses.items()},
        dir_anchor_hits=sum(1 for h in hits if h.file in exp.dir_spellings),
        near_truncated=near_truncated,
        terms_truncated=terms_truncated,
        next_exclude=next_exclude,
        notes=notes,
    )
