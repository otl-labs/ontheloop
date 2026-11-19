"""Consolidation helpers (Decision 278; rec-5265): candidates, nomination text, body partition, manifest checks.

The rule lives in docs/contracts/decision-compaction.yaml `consolidation`; this module reads it and holds the checks
validate_delegated_key_changes, validate_supersession_annotations and plan_decision_drafts call. It imports no git
helper: origin/main text arrives through the required `base_reader` argument.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from scripts.checks.decisions.envelope_rules import dotted_value, split_delegated_key
from scripts.decisions_md import extract_entry_envelope, extract_superseded_by, is_compacted_stub, iter_decision_sections

BaseReaderFn = Callable[[Path, str], Optional[str]]
CONTRACT = "docs/contracts/decision-compaction.yaml"
MANIFEST_DIR = "docs/decision-migration/consolidations"
_FILES = ("docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md")
_DISPOSITIONS = ("restated", "delegated", "moved", "held", "stanza", "retired")
_META = ("status", "date", "warehouse-id", "related", "superseded-by")
_MARKER = re.compile(r"^\*\*([^*]+?):\*\*")
_POINT = re.compile(r"^\s*(\d+)\.\s")
_NOTE = re.compile(r"^(>\s*\*\*Amend|\[Amendment|\*\*\[Amendment)")
_ENVELOPE = re.compile(r"\A#{2,3}\s+Decision\s+\d+:[^\n]*\n+```yaml\n.*?\n```", re.DOTALL)
_STANZA = re.compile(r"```yaml reversal-conditions\n(.*?)\n```", re.DOTALL)
_HEADER = re.compile(r"^#{2,3}\s+Decision\s+(\d+):", re.MULTILINE)


def _yaml(text: str | None) -> Any:
    try:
        return yaml.safe_load(text) if text is not None else None
    except yaml.YAMLError:
        return None


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None


def load_contract(root: Path) -> dict:
    block = (_yaml(_read(root / CONTRACT)) or {}).get("consolidation")
    return block if isinstance(block, dict) else {}


def load_threshold(root: Path) -> int | None:
    value = load_contract(root).get("threshold")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def load_rows(root: Path) -> list[dict]:
    try:
        rows = json.loads((root / "docs/decisions-index.json").read_text(encoding="utf-8"))["decisions"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _compacted(row: dict | None) -> bool:
    return bool(row) and row.get("currency") == "superseded_compacted"


def _amenders(rows: list[dict]) -> dict[int, set[Any]]:
    by_number = {r.get("number"): r for r in rows}
    found: dict[int, set[Any]] = {}
    for row in rows:
        if not row.get("live") or _compacted(row):
            continue
        for root in row.get("amends") or []:
            target = by_number.get(root)
            if target is not None and target.get("live") and not _compacted(target):
                found.setdefault(root, set()).add(row["number"])
    return found


def live_amender_counts(rows: list[dict]) -> dict[int, int]:
    return {root: len(who) for root, who in _amenders(rows).items()}


def candidates(rows: list[dict], threshold: int) -> dict[int, int]:
    return {r: c for r, c in live_amender_counts(rows).items() if c >= threshold}


def entry_sizes(root: Path) -> dict[int, int]:
    text = _read(root / _FILES[0])
    return {int(m.group(1)): len(b.encode("utf-8")) for m, b in iter_decision_sections(text or "")}


def nomination_info(
    number: int | str, amends: list[int], rows: list[dict], threshold: int, sizes: dict[int, int]
) -> list[str]:
    who = _amenders(rows)
    lines = []
    for root in sorted(set(amends)):
        if root not in who:
            continue
        amenders = who[root] | {number}
        if len(amenders) < threshold:
            continue
        chain = sizes.get(root, 0) + sum(sizes.get(a, 0) for a in amenders if isinstance(a, int))
        lines.append(
            f"NOMINATION: Decision {number} amends Decision {root}, a consolidation candidate: {len(amenders)} live "
            f"amenders (threshold {threshold}), chain {chain} bytes, {len(amenders) + 1} entries to open. Optional: the "
            "operator names a subject; nothing fails or files a rec (Decision 278 cl.2)."
        )
    return lines


def candidates_warn_line(rows: list[dict], threshold: int) -> str | None:
    found = candidates(rows, threshold)
    if not found:
        return None
    top = max(found, key=lambda r: (found[r], -r))
    return (
        f"  WARN: {len(found)} Decision root(s) at or over the consolidation threshold ({threshold} live amenders); "
        f"largest Decision {top} with {found[top]}. Nomination only: nothing fails (Decision 278 cl.2)."
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class _Parts:
    """Accumulates a body's units line by line (the state partition() threads through its paragraphs)."""

    def __init__(self, number: int) -> None:
        self.number, self.units = number, {}
        self.section, self.lists, self.notes, self.last = "intro", 0, 0, None
        self.paras: dict[str, int] = {}
        self.para: list[str] = []
        self.meta = False

    def _paragraph_unit(self, text: str) -> None:
        self.paras[self.section] = self.paras.get(self.section, 0) + 1
        self.units[f"{self.number} {self.section} p{self.paras[self.section]}"] = text

    def _plain(self, lines: list[str]) -> None:
        if self.last and lines[0][:1].isspace():
            self.units[self.last] += "\n" + "\n".join(lines)
            return
        body = "\n".join(lines)
        letters = re.split(r"(?=\([a-z]\)\s)", body) if self.section.startswith("reversal") else []
        if len(letters) > 1:
            for chunk in letters[1:]:
                self.units[f"{self.number} reversal {chunk[1]}"] = (letters[0] + chunk) if chunk is letters[1] else chunk
        else:
            self._paragraph_unit(body)
        self.last = None

    def _points(self, lines: list[str]) -> None:
        lead: list[str] = []
        for x in lines:
            point = _POINT.match(x)
            if point:
                k = int(point.group(1))
                self.lists += k == 1
                prefix = f"{self.number} pt" if self.lists <= 1 else f"{self.number} {self.section} pt"
                self.last = f"{prefix}{k}"
                self.units[self.last] = x
            elif self.last:
                self.units[self.last] += "\n" + x
            else:
                lead.append(x)
        if lead:
            self._paragraph_unit("\n".join(lead))

    def emit(self) -> None:
        lines = [x for x in self.para if x.strip()]
        meta, self.para, self.meta = self.meta, [], False
        if not lines or meta:
            return
        if _NOTE.match(lines[0].strip()):
            self.notes += 1
            self.units[f"{self.number} note {self.notes}"] = "\n".join(lines)
            self.last = None
        elif any(_POINT.match(x) for x in lines):
            self._points(lines)
        else:
            self._plain(lines)

    def feed(self, line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped == "---":
            return self.emit()
        marker = _MARKER.match(stripped)
        if marker:
            self.emit()
            self.section = _slug(marker.group(1))
            self.meta = self.section.startswith(_META)
            if re.fullmatch(r"\*\*[^*]+?:\*\*", stripped):
                return None
        self.para.append(line)
        return None


def partition(number: int, block: str) -> dict[str, str]:
    """Unit id -> text for one Decision body, per consolidation.manifest.units."""
    text = _ENVELOPE.sub("", block, count=1)
    text = re.sub(r"\A#{2,3}[^\n]*\n", "", text)
    parts = _Parts(number)
    stanza = _STANZA.search(text)
    if stanza:
        parsed = _yaml(stanza.group(1))
        for item in (parsed.get("conditions") if isinstance(parsed, dict) else None) or []:
            if isinstance(item, dict) and item.get("id"):
                parts.units[f"{number} stanza {item['id']}"] = yaml.safe_dump(item)
        text = text[: stanza.start()] + text[stanza.end() :]
    for line in text.splitlines():
        parts.feed(line)
    parts.emit()
    return parts.units


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {_flatten(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("*", "").replace("`", "")).strip()


def _key_text(root: Path, key: Any) -> str | None:
    split = split_delegated_key(key)
    if split is None:
        return None
    found, value = dotted_value(_yaml(_read(root / split[0])), split[1])
    return _flatten(value) if found else None


def _under(key: str, parent: str) -> bool:
    return key == parent or key.startswith((parent + ".", parent + "#"))


def _covering(key: str, delegations: list[Any]) -> dict | None:
    for item in delegations:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            ours, theirs = key.split("#", 1), item["key"].split("#", 1)
            if len(ours) == 2 == len(theirs) and ours[0] == theirs[0] and _under(ours[1], theirs[1]):
                return item
    return None


def _check_holds(root: Path, check: Any) -> bool:
    path, _, name = check.partition("::") if isinstance(check, str) else ("", "", "")
    text = _read(root / path) if path.startswith("tests/") and name else None
    return text is not None and re.search(rf"def {re.escape(name)}\b", text) is not None


def load_manifests(root: Path) -> list[tuple[str, dict]]:
    found = []
    for path in sorted((root / MANIFEST_DIR).glob("*.yaml")):
        data = _yaml(_read(path))
        found.append((f"{MANIFEST_DIR}/{path.name}", data if isinstance(data, dict) else {}))
    return found


def _targets(unit: dict) -> list[str]:
    out = [f"cl.{c}" for c in unit.get("restated") or []]
    for field in ("delegated", "moved", "held"):
        out += [str(k).rpartition("#")[2].rsplit(".", 1)[-1] if "#" in str(k) else str(k) for k in unit.get(field) or []]
    out += [f"stanza {s}" for s in unit.get("stanza") or []]
    return out + (["retired"] if unit.get("retired") else [])


def render_point_map(units: list[dict], number: int) -> str:
    own = []
    for unit in units:
        match = re.fullmatch(rf"{number} pt(\d+)", str(unit.get("id")))
        if match:
            own.append((int(match.group(1)), unit))
    return "; ".join(f"pt{k} -> " + " and ".join(_targets(u)) for k, u in sorted(own, key=lambda p: p[0]))


def _stub_map(block: str) -> str | None:
    _, found, rest = block.partition("Point map: ")
    if not found:
        return None
    line = rest.split("\n", 1)[0]
    return line.split(". ", 1)[0].rstrip(".")


def _point_map_stubs(entries: dict[int, str]) -> list[tuple[int, str]]:
    return [(n, b) for n, b in sorted(entries.items()) if "Point map: " in b and is_compacted_stub(b)]


def _entries(root: Path) -> dict[int, str]:
    found: dict[int, str] = {}
    for rel in _FILES:
        for match, block in iter_decision_sections(_read(root / rel) or ""):
            found.setdefault(int(match.group(1)), block)
    return found


def _superseded(rows: dict[int, dict], number: int, block: str) -> bool:
    row = rows.get(number)
    return str(row.get("currency", "")).startswith("superseded") if row else bool(extract_superseded_by(block))


def _unit_problems(root: Path, tag: str, unit: dict, ctx: dict) -> list[str]:
    uid, problems = unit.get("id"), []
    given = [d for d in _DISPOSITIONS if unit.get(d)]
    if not given:
        return [f"{tag}: unit {uid!r} has no disposition"]
    if "retired" in given and len(given) > 1:
        problems.append(f"{tag}: unit {uid!r} is retired beside another disposition")
    if "retired" in given and len(str(unit["retired"]).split()) < 10:
        problems.append(f"{tag}: unit {uid!r} retirement reason is under ten words")
    for c in unit.get("restated") or []:
        if c not in ctx["clauses"]:
            problems.append(f"{tag}: unit {uid!r} restated clause {c} is not in Decision {ctx['number']}")
    for s in unit.get("stanza") or []:
        if f"{ctx['number']} stanza {s}" not in ctx["stanzas"]:
            problems.append(f"{tag}: unit {uid!r} stanza {s} is not in Decision {ctx['number']}")
    for field in ("delegated", "moved", "held"):
        for key in unit.get(field) or []:
            problems += _target_problems(root, tag, unit, field, str(key), ctx)
    return problems


def _target_problems(root: Path, tag: str, unit: dict, field: str, key: str, ctx: dict) -> list[str]:
    uid, token = unit.get("id"), unit.get("token")
    if field == "held" and key.startswith("Decision "):
        n = key.split()[-1]
        block = ctx["entries"].get(int(n)) if n.isdigit() else None
        if block is None or _superseded(ctx["rows"], int(n), block) or int(n) in ctx["victims"]:
            return [f"{tag}: unit {uid!r} held {key} is absent, superseded or a victim"]
        return [] if token and _norm(str(token)) in _norm(block) else [f"{tag}: unit {uid!r} token is absent from {key}"]
    delegations = ctx["own"] if field != "held" else ctx["others"]
    cover = _covering(key, delegations)
    text = _key_text(root, key)
    if text is None:
        return [f"{tag}: unit {uid!r} {field} key {key} does not resolve"]
    if cover is None:
        return [f"{tag}: unit {uid!r} {field} key {key} sits outside every delegated key"]
    if field == "delegated" and not any(d.get("key") == key for d in delegations if isinstance(d, dict)):
        return [f"{tag}: unit {uid!r} delegated key {key} is not a key the entry delegates"]
    problems = []
    if field != "delegated" and not (token and _norm(str(token)) in _norm(text)):
        problems.append(f"{tag}: unit {uid!r} token is absent from {key}")
    bound = cover.get("bounds")
    if cover.get("change_rule") == "contract_amendment" and isinstance(bound, dict) and "text" in bound:
        if not _check_holds(root, unit.get("check")):
            problems.append(f"{tag}: unit {uid!r} {key} is text-bound under contract_amendment and names no resolving check")
    return problems


def _context(root: Path, number: int, supersedes: list[int], entries: dict[int, str], rows: dict[int, dict]) -> dict:
    block = entries[number]
    mine = (extract_entry_envelope(block) or {}).get("delegates")
    others: list[Any] = []
    for n, other in entries.items():
        if n != number and not _superseded(rows, n, other) and n not in supersedes:
            others += (extract_entry_envelope(other) or {}).get("delegates") or []
    own = mine if isinstance(mine, list) else []
    parts = partition(number, block)
    clauses = {int(m.group(1)) for k in parts if (m := re.fullmatch(rf"{number} pt(\d+)", k))}
    return {
        "number": number,
        "own": own,
        "others": others,
        "entries": entries,
        "rows": rows,
        "victims": set(supersedes),
        "clauses": clauses,
        "stanzas": set(parts),
    }


def _units_of(data: dict) -> list[dict]:
    return [u for u in data.get("units") or [] if isinstance(u, dict)]


def _frozen_problems(
    root: Path, rel: str, tag: str, data: dict, supersedes: list[int], base_reader: BaseReaderFn
) -> list[str]:
    base_manifest = base_reader(root, rel)
    if base_manifest is not None:
        old = _yaml(base_manifest) or {}
        lost = {u.get("id") for u in _units_of(old)} - {u.get("id") for u in _units_of(data)}
        gone = set(old.get("supersedes") or []) - set(supersedes)
        return (
            [f"{tag}: the merged manifest lost unit(s) {sorted(map(str, lost))} or victim(s) {sorted(gone)}"]
            if lost or gone
            else []
        )
    base = base_reader(root, _FILES[0])
    if base is None:
        print(f"  SKIP: {tag} freeze and base-text checks (origin/main unreachable); resolve checks still ran.")
        return []
    texts = {int(m.group(1)): b for m, b in iter_decision_sections(base)}
    expected: dict[str, str] = {}
    for n in supersedes:
        expected.update(partition(n, texts[n]) if n in texts else {})
    have = [str(u.get("id")) for u in _units_of(data)]
    problems = [f"{tag}: unit {i!r} is missing from the manifest" for i in expected if i not in have]
    problems += [f"{tag}: unit {i!r} is not a unit of any victim body at origin/main" for i in have if i not in expected]
    for unit in _units_of(data):
        if [d for d in _DISPOSITIONS if unit.get(d)] == ["moved"] and str(unit.get("id")) in expected:
            wanted = _norm(_POINT.sub("", expected[str(unit["id"])], count=1))
            pool = " ".join(_norm(_key_text(root, k) or "") for k in unit["moved"])
            if wanted not in pool:
                problems.append(f"{tag}: unit {unit['id']!r} moved alone, but its base text is not in its target")
    return problems


def _manifest_problems(root: Path, rel: str, data: dict, ctx_in: dict, base_reader: BaseReaderFn) -> list[str]:
    tag, entries, rows = f"consolidation manifest {rel}", ctx_in["entries"], ctx_in["rows"]
    supersedes = data.get("supersedes")
    if not isinstance(supersedes, list) or not isinstance(data.get("units"), list):
        return [f"{tag}: supersedes and units must be lists"]
    cons = next(
        (n for n, b in entries.items() if set((extract_entry_envelope(b) or {}).get("supersedes") or []) == set(supersedes)),
        None,
    )
    if cons is None:
        return [f"{tag}: no entry's envelope supersedes exactly {sorted(supersedes)}"]
    if _superseded(rows, cons, entries[cons]):
        return []
    problems = []
    for n in supersedes:
        block = entries.get(n)
        if block is None or not is_compacted_stub(block) or extract_superseded_by(block) != f"dec-{cons:03d}":
            problems.append(f"{tag}: victim Decision {n} is not a compacted stub pointing at Decision {cons}")
    for n in data.get("carried_forward") or []:
        row, block = rows.get(n), entries.get(n)
        if (
            block is None
            or _superseded(rows, n, block)
            or not (row or {}).get("amends")
            or f"Decision {n}" not in entries[cons]
        ):
            problems.append(f"{tag}: carried_forward Decision {n} is superseded, absent, lost its amends edge or is unnamed")
    ctx = _context(root, cons, supersedes, entries, rows)
    seen: set[str] = set()
    for unit in data["units"]:
        uid = str(unit.get("id")) if isinstance(unit, dict) else ""
        if uid in seen:
            problems.append(f"{tag}: duplicate unit id {uid!r}")
        seen.add(uid)
        problems += _unit_problems(root, tag, unit, ctx) if isinstance(unit, dict) else [f"{tag}: a unit is not a mapping"]
    for n in supersedes:
        want, got = render_point_map([u for u in data["units"] if isinstance(u, dict)], n), _stub_map(entries.get(n, ""))
        if want and got != want:
            problems.append(f"{tag}: Decision {n}'s point map {got!r} differs from the manifest's {want!r}")
    return problems + _frozen_problems(root, rel, tag, data, supersedes, base_reader)


def _unlisted_stub_problems(
    manifests: list[tuple[str, dict]], entries: dict[int, str], rows: dict[int, dict]
) -> tuple[list[str], int]:
    listed = {n for _, d in manifests for n in (d.get("supersedes") or []) if isinstance(d, dict)}
    problems, scanned = [], 0
    for n, block in _point_map_stubs(entries):
        target = extract_superseded_by(block).removeprefix("dec-")
        if not target.isdigit() or int(target) == 277:
            continue
        scanned += 1
        top = entries.get(int(target))
        live = top is not None and not _superseded(rows, int(target), top)
        if live and n in set((extract_entry_envelope(top) or {}).get("supersedes") or []) and n not in listed:
            problems.append(
                f"consolidation manifest: Decision {n}'s stub has a point map for Decision {target}; no manifest lists it"
            )
    return problems, scanned


def manifest_problems(root: Path, base_reader: BaseReaderFn) -> list[str]:
    """Problems with every manifest under docs/decision-migration/consolidations/ and with orphaned point-map stubs."""
    return scan(root, base_reader)[0]


def scan(root: Path, base_reader: BaseReaderFn) -> tuple[list[str], int]:
    """(problems, examined): manifest units checked, else point-map stubs scanned outside Decision 277's victims."""
    manifests = load_manifests(root)
    entries = _entries(root) if manifests or "Point map: " in (_read(root / _FILES[0]) or "") else {}
    rows = {r["number"]: r for r in load_rows(root) if isinstance(r.get("number"), int)}
    problems: list[str] = []
    for rel, data in manifests:
        problems += _manifest_problems(root, rel, data, {"entries": entries, "rows": rows}, base_reader)
    extra, scanned = _unlisted_stub_problems(manifests, entries, rows)
    units = sum(len(d.get("units") or []) for _, d in manifests if isinstance(d.get("units"), list))
    return problems + extra, units if manifests else scanned


def examined(root: Path, base_reader: BaseReaderFn) -> int:
    """Manifest units checked, or the point-map stubs scanned outside Decision 277's victims when there is no manifest."""
    return scan(root, base_reader)[1]
