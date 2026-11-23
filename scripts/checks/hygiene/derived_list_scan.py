"""Weekly report of hand-kept lists the repo can already enumerate (rec-5516). A report, never a registered check.

Walks tracked YAML under config/ and docs/contracts/, src/lambdas/*/manifest.yaml, every scripts/checks/*/_manifest.py
(Entry(name=...) literals, parsed by ast: no check module is ever imported), .github/CODEOWNERS (path column) and each
workflow's on.<event>.paths list (kind trigger_paths). Files the structural budgets class as generated are skipped.
A collection is a mapping or list of at least 10 siblings (3 for trigger paths); an entry's token is its key or first
scalar, also tried by its first "::" segment. Collection class: derived_candidate (at least 60% of tokens name an
enumerable repo entity and at most half the values are numeric), baseline (tokens resolve and at least 80% of values
are numeric: a ratchet, reported, never filed) or none; a file takes its strongest collection's class. A comment marker
in the first 15 lines ("hand-kept: rec-NNNN <reason>", "verified-against: <check name>" or a GENERATED banner)
dispositions a candidate. Exit 0 on a completed scan, 2 when it cannot complete; --assert-dispositioned <path> exits 1
when <path> is an unmarked derived_candidate (the acceptance of the recs derived_list_recs files).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import yaml

from scripts.checks.hygiene import derived_list_rank

ROOT = Path(__file__).resolve().parents[3]
MIN_ENTRIES = 10
MIN_TRIGGER_PATHS = 3
RESOLVED_SHARE = 0.6
BASELINE_NUMERIC = 0.8
CANDIDATE_MAX_NUMERIC = 0.5
MARKER_LINES = 15
BANNER_LINES = 10
CLASS_RANK = {"derived_candidate": 2, "baseline": 1, "none": 0}
KIND_WORDS = {
    "path": "tracked paths",
    "glob": "path globs",
    "check": "check names",
    "module": "modules",
    "workflow": "workflows",
    "stem": "file stems",
    "trigger_paths": "trigger paths",
    "none": "unresolved entries",
}
_HAND_KEPT_RE = re.compile(r"hand-kept:\s*(rec-\d+)\s+\S")
_VERIFIED_RE = re.compile(r"verified-against:\s*([A-Za-z0-9_.-]+)")
_BANNER_RE = re.compile(r"GENERATED\s*--\s*DO NOT EDIT")
_MODULE_RE = re.compile(r"(?:scripts|src)(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_GLOB_CHARS = frozenset("*?[")
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class ScanError(Exception):
    """The scan cannot complete (exit 2)."""


@dataclass(frozen=True)
class Manifest:
    names: tuple[str, ...]
    modules: tuple[str, ...]


@dataclass
class Resolver:
    files: frozenset[str]
    dirs: frozenset[str]
    stems: frozenset[str]
    checks: frozenset[str]
    workflows: frozenset[str]
    _globs: dict[str, bool] = field(default_factory=dict)

    def kind(self, token: Any) -> Optional[str]:
        if not isinstance(token, str):
            return None
        variants = [token] + ([token.split("::", 1)[0]] if "::" in token else [])
        for variant in variants:
            hit = self._kind(_norm(variant))
            if hit is not None:
                return hit
        return None

    def _kind(self, token: str) -> Optional[str]:
        if not token:
            return None
        if token in self.files or token in self.dirs:
            return "path"
        if _GLOB_CHARS & set(token):
            if token not in self._globs:
                self._globs[token] = any(fnmatch.fnmatchcase(f, token) for f in self.files)
            return "glob" if self._globs[token] else None
        if token in self.checks:
            return "check"
        if _MODULE_RE.fullmatch(token):
            base = token.replace(".", "/")
            if f"{base}.py" in self.files or f"{base}/__init__.py" in self.files:
                return "module"
        if token in self.workflows:
            return "workflow"
        return "stem" if token in self.stems else None


@dataclass
class Collection:
    entries: int
    resolved: int
    numeric: int
    kinds: Counter[str]
    trigger: bool = False

    @property
    def share(self) -> float:
        return self.resolved / self.entries

    @property
    def klass(self) -> str:
        if self.share < RESOLVED_SHARE:
            return "none"
        numeric = self.numeric / self.entries
        if numeric >= BASELINE_NUMERIC:
            return "baseline"
        return "derived_candidate" if numeric <= CANDIDATE_MAX_NUMERIC else "none"

    @property
    def kind(self) -> str:
        if self.trigger:
            return "trigger_paths"
        return self.kinds.most_common(1)[0][0] if self.kinds else "none"


def _norm(token: str) -> str:
    token = token.strip().lstrip("!")
    while token.startswith("./"):
        token = token[2:]
    return token.strip("/")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _list_pair(item: Any) -> tuple[Any, Any]:
    if isinstance(item, dict):
        scalars = [v for v in item.values() if _is_scalar(v)]
        rest = scalars[1:]
        value = rest[0] if rest and all(_is_number(v) for v in rest) else None
        return (scalars[0] if scalars else None), value
    if isinstance(item, list):
        scalars = [v for v in item if _is_scalar(v)]
        return (scalars[0] if scalars else None), None
    return item, None


def collect(pairs: list[tuple[Any, Any]], resolver: Resolver, trigger: bool = False) -> Collection:
    kinds: Counter[str] = Counter()
    resolved = numeric = 0
    for token, value in pairs:
        hit = resolver.kind(token)
        if hit is not None:
            resolved += 1
            kinds[hit] += 1
        numeric += _is_number(value)
    return Collection(len(pairs), resolved, numeric, kinds, trigger)


def yaml_collections(node: Any, resolver: Resolver) -> list[Collection]:
    found: list[Collection] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if len(current) >= MIN_ENTRIES:
                found.append(collect(list(current.items()), resolver))
            stack.extend(current.values())
        elif isinstance(current, list):
            if len(current) >= MIN_ENTRIES:
                found.append(collect([_list_pair(item) for item in current], resolver))
            stack.extend(current)
    return found


def trigger_collections(doc: Any, resolver: Resolver) -> list[Collection]:
    on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    found: list[Collection] = []
    for event in on.values() if isinstance(on, dict) else []:
        paths = event.get("paths") if isinstance(event, dict) else None
        if isinstance(paths, list) and len(paths) >= MIN_TRIGGER_PATHS:
            found.append(collect([(p, None) for p in paths], resolver, trigger=True))
    return found


def codeowners_collections(text: str, resolver: Resolver) -> list[Collection]:
    pairs = [(s.split()[0], None) for s in (line.strip() for line in text.splitlines()) if s and not s.startswith("#")]
    return [collect(pairs, resolver)] if len(pairs) >= MIN_ENTRIES else []


def parse_manifest(text: str, rel: str) -> Manifest:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise ScanError(f"{rel} does not parse: {exc}") from exc
    names: list[str] = []
    modules: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "Entry") or (isinstance(func, ast.Attribute) and func.attr == "Entry"):
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            name, module = kw.get("name"), kw.get("module")
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                names.append(name.value)
                modules.append(module.value if isinstance(module, ast.Constant) and isinstance(module.value, str) else "")
    return Manifest(tuple(names), tuple(modules))


def read_marker(text: str, checks: frozenset[str]) -> Optional[str]:
    for line in text.splitlines()[:MARKER_LINES]:
        comment = line.strip()
        if not comment.startswith("#"):
            continue
        if _BANNER_RE.search(comment):
            return "generated"
        hand = _HAND_KEPT_RE.search(comment)
        if hand:
            return f"hand-kept: {hand.group(1)}"
        verified = _VERIFIED_RE.search(comment)
        if verified and verified.group(1) in checks:
            return f"verified-against: {verified.group(1)}"
    return None


def tracked_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise ScanError(f"git ls-files: {exc}") from exc
    if proc.returncode != 0:
        raise ScanError(f"git ls-files exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return sorted(p for p in proc.stdout.split("\0") if p)


def _scan_kind(rel: str) -> Optional[str]:
    if fnmatch.fnmatch(rel, "scripts/checks/*/_manifest.py") and rel.count("/") == 3:
        return "manifest"
    if rel == ".github/CODEOWNERS":
        return "codeowners"
    if rel.startswith(".github/workflows/") and rel.count("/") == 2 and rel.endswith((".yml", ".yaml")):
        return "workflow"
    if rel.endswith((".yml", ".yaml")) and (
        rel.startswith(("config/", "docs/contracts/")) or fnmatch.fnmatch(rel, "src/lambdas/*/manifest.y*ml")
    ):
        return "yaml"
    return None


def _read(root: Path, rel: str) -> Optional[str]:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _generated(root: Path) -> set[str]:
    text = _read(root, "config/structural_size_budgets.yaml")
    try:
        data = yaml.load(text, Loader=_LOADER) if text else None
    except yaml.YAMLError:
        data = None
    pinned = data.get("generated_pinned") if isinstance(data, dict) else None
    return set(pinned) if isinstance(pinned, list) else set()


def _parse_yaml(text: str) -> tuple[bool, Any]:
    try:
        return True, yaml.load(text, Loader=_LOADER)
    except yaml.YAMLError:
        return False, None


def _record(rel: str, collections: list[Collection], marker: Optional[str], parsed: bool) -> dict[str, Any]:
    best = max(collections, key=lambda c: (CLASS_RANK[c.klass], c.entries), default=None)
    klass = best.klass if best is not None else "none"
    return {
        "path": rel,
        "class": klass,
        "kind": (best.kind if best is not None else "none") if parsed else "unparsed",
        "entries": best.entries if best is not None else 0,
        "resolved_share": round(best.share, 3) if best is not None else 0.0,
        "coupled_appends": 0,
        "size_pressure": 0.0,
        "score": 0.0,
        "marker": marker,
        "step": 0,
    }


def static_scan(root: Path) -> list[dict[str, Any]]:
    tracked = tracked_files(root)
    generated = _generated(root)
    texts: dict[str, str] = {}
    for rel in tracked:
        kind = _scan_kind(rel)
        text = _read(root, rel) if kind and rel not in generated else None
        if text is not None and not _BANNER_RE.search("\n".join(text.splitlines()[:BANNER_LINES])):
            texts[rel] = text
    manifests = {rel: parse_manifest(text, rel) for rel, text in texts.items() if _scan_kind(rel) == "manifest"}
    docs: dict[str, tuple[bool, Any]] = {
        rel: _parse_yaml(t) for rel, t in texts.items() if _scan_kind(rel) in ("yaml", "workflow")
    }
    workflow_names = {PurePosixPath(r).stem for r in docs if _scan_kind(r) == "workflow"}
    workflow_names |= {
        d["name"]
        for r, (_, d) in docs.items()
        if _scan_kind(r) == "workflow" and isinstance(d, dict) and isinstance(d.get("name"), str)
    }
    resolver = Resolver(
        files=frozenset(tracked),
        dirs=frozenset(str(p) for rel in tracked for p in PurePosixPath(rel).parents if str(p) != "."),
        stems=frozenset(s for s in (PurePosixPath(r).stem for r in tracked) if s not in derived_list_rank.GENERIC_STEMS),
        checks=frozenset(n for m in manifests.values() for n in m.names),
        workflows=frozenset(workflow_names),
    )
    check_texts = [
        t for m in manifests.values() for mod in m.modules if mod and (t := _read(root, mod.replace(".", "/") + ".py"))
    ]
    records = []
    for rel, text in texts.items():
        kind = _scan_kind(rel)
        parsed = True
        if kind == "manifest":
            names = manifests[rel].names
            collections = [collect([(n, None) for n in names], resolver)] if len(names) >= MIN_ENTRIES else []
        elif kind == "codeowners":
            collections = codeowners_collections(text, resolver)
        else:
            parsed, doc = docs[rel]
            collections = trigger_collections(doc, resolver) if kind == "workflow" else yaml_collections(doc, resolver)
        record = _record(rel, collections, read_marker(text, resolver.checks), parsed)
        if record["class"] == "derived_candidate":
            record["step"] = 2 if any(PurePosixPath(rel).name in t for t in check_texts) else 1
        records.append(record)
    return records


def scan(root: Path, *, history: bool = True, history_days: int = 90, min_commits: int = 30) -> dict[str, Any]:
    records = static_scan(root)
    note: Optional[str] = None if history else "history not read (--no-history), so every score is 0"
    if history:
        ranked = [r["path"] for r in records if r["class"] != "none"]
        ranking = derived_list_rank.rank(root, ranked, history_days=history_days, min_commits=min_commits)
        note = ranking.note
        for record in records:
            score = ranking.scores.get(record["path"])
            if score is not None:
                record.update(coupled_appends=score.coupled_appends, size_pressure=score.size_pressure, score=score.score)
    records.sort(key=lambda r: (-r["score"], -CLASS_RANK[r["class"]], r["path"]))
    return {"schema": 1, "note": note, "files": records}


def summary_markdown(result: dict[str, Any]) -> str:
    rows = [r for r in result["files"] if r["class"] != "none"]
    lines = [
        "## Derived-list scan (rec-5516)",
        "",
        "| path | class | kind | entries | resolved | coupled appends | size pressure | score | marker | step |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['path']} | {r['class']} | {r['kind']} | {r['entries']} | {r['resolved_share']} | {r['coupled_appends']}"
            f" | {r['size_pressure']} | {r['score']} | {r['marker'] or '-'} | {r['step'] or '-'} |"
        )
    counts = Counter(r["class"] for r in result["files"])
    marked = sum(1 for r in result["files"] if r["class"] == "derived_candidate" and r["marker"])
    lines += [
        "",
        (
            f"{len(result['files'])} files scanned: {counts['derived_candidate']} derived_candidate ({marked} marked), "
            f"{counts['baseline']} baseline, {counts['none']} none."
        ),
    ]
    if result["note"]:
        lines.append(f"Note: {result['note']}.")
    return "\n".join(lines) + "\n"


def assert_dispositioned(root: Path, rel: str) -> int:
    record = next((r for r in static_scan(root) if r["path"] == _norm(rel)), None)
    if record is None or record["class"] != "derived_candidate":
        print(f"[derived_list] not_a_candidate {rel}")
        return 0
    if record["marker"]:
        print(f"[derived_list] dispositioned {rel} marker={record['marker']}")
        return 0
    print(f"[derived_list] undispositioned {rel} ({record['entries']} entries naming {KIND_WORDS[record['kind']]})")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Report hand-kept lists the repo can derive (rec-5516)")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--result", default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--history-days", type=int, default=90)
    parser.add_argument("--assert-dispositioned", default=None, metavar="PATH")
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        if args.assert_dispositioned is not None:
            return assert_dispositioned(root, args.assert_dispositioned)
        result = scan(root, history=not args.no_history, history_days=args.history_days)
    except (ScanError, derived_list_rank.RankError) as exc:
        print(f"[derived_list] scan error: {exc}")
        return 2
    if args.result:
        Path(args.result).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    markdown = summary_markdown(result)
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    counts = Counter(r["class"] for r in result["files"])
    print(
        f"[derived_list] scan files={len(result['files'])} derived_candidate={counts['derived_candidate']} "
        f"baseline={counts['baseline']} none={counts['none']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
